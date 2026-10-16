"""Parties — the computers of Alice, Bob, Charlie … — each in its own process.

A :class:`Party` has two links, which is how the lab separates the physical layer from the
classical network:

* ``self.hw`` — the **hardware link** to the instruments its own station owns (``instruments``):
  program its AWG, read its own parameters, call methods such as ``voltage_for``. This is the
  local USB/PCIe-style connection of a real station, not a public channel, and a party cannot
  reach another station's hardware through it. The light itself is simulated in the lab, because
  the fields of several stations interfere at the same beam splitter.
* ``self.net`` — the **classical channel** to the other parties: real TCP sockets on localhost
  (``multiprocessing.connection``), one listening port per party, messages pickled, every
  connection authenticated with a pre-shared key (HMAC challenge/response). That is what QKD
  assumes: an *authenticated but public* channel — nothing is encrypted.

The framework drives the parties block by block (:class:`Experiment`): every party's
``prepare(block, n)`` programs its transmitter for the next ``n`` pulses, the lab then records the
block, hands the time-tagger batch to the party owning the tagger (``measure(batch)``), and whatever
the parties say to each other arrives through ``receive(message)``. Printed output goes to the
party's laptop console in the virtual lab; ``report(**values)`` shows live read-outs.

Write each party in its own file and load them with ``--party alice.py``, or bundle a protocol as
a package exposing ``PARTIES`` and ``build()`` and use ``--protocol examples/mpqkd``.
"""

from __future__ import annotations

import pickle
import queue
import secrets
import socket
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from multiprocessing.connection import Client, Listener
from pathlib import Path
from typing import Any, ClassVar

import numpy as np

from .analysis import ProcessWorker, QueueWriter, TaskWorker, echo_lines
from .core import Component, Setup
from .engine import trace
from .instruments import CounterResult, PhotonCounter
from .loading import import_source, locate

PARTY_REGISTRY: list[type[Party]] = []
BASE_PORT = 5550
HOST = "127.0.0.1"


def free_port(preferred: int) -> int:
    """The preferred TCP port if nobody listens on it, else one the OS hands out."""
    for port in (preferred, 0):
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((HOST, port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise OSError("no free TCP port on localhost")


# ---- messages on the classical channel --------------------------------------------------------------
@dataclass
class Message:
    sender: str
    topic: str
    payload: dict = field(default_factory=dict)
    seq: int = 0

    @property
    def block(self):
        return self.payload.get("block")

    def __getattr__(self, item):
        payload = self.__dict__.get(
            "payload"
        )  # absent while pickle rebuilds the object
        if payload is None or item.startswith("_") or item not in payload:
            raise AttributeError(item)
        return payload[item]

    def __repr__(self):
        return f"<{self.sender} {self.topic} #{self.seq} {sorted(self.payload)}>"


class ClassicalChannel:
    """Authenticated public channel between the parties: TCP sockets on localhost.

    ``send(to, topic, **payload)`` / ``broadcast(topic, **payload)``; incoming messages are queued
    and handed to the party's ``receive``. Everything is sent in clear and counted in ``stats``.
    """

    def __init__(self, me: str, peers: dict[str, tuple[str, int]], authkey: bytes):
        self.me = me
        self.peers = peers
        self.authkey = authkey
        self.inbox: queue.Queue = queue.Queue()
        self.conns: dict[str, Any] = {}
        self.seq = 0
        self.stats = {
            "sent": 0,
            "recv": 0,
            "bytes_out": 0,
            "bytes_in": 0,
            "peers": {p: {"sent": 0, "recv": 0} for p in peers if p != me},
        }
        self.lock = threading.Lock()
        self.listener = Listener(peers[me], family="AF_INET", authkey=authkey)
        threading.Thread(
            target=self._accept_loop, name=f"{me}-listener", daemon=True
        ).start()

    @property
    def address(self) -> tuple[str, int]:
        return self.peers[self.me]

    def _accept_loop(self) -> None:
        while True:
            try:
                conn = self.listener.accept()
            except OSError, EOFError:
                return
            threading.Thread(target=self._reader, args=(conn,), daemon=True).start()

    def _reader(self, conn) -> None:
        while True:
            try:
                raw = conn.recv_bytes()
            except EOFError, OSError:
                return
            msg = pickle.loads(raw)
            with self.lock:
                self.stats["recv"] += 1
                self.stats["bytes_in"] += len(raw)
                if msg.sender in self.stats["peers"]:
                    self.stats["peers"][msg.sender]["recv"] += 1
            self.inbox.put(msg)

    def _conn(self, peer: str):
        conn = self.conns.get(peer)
        if conn is None:
            if peer not in self.peers:
                raise KeyError(
                    f"{self.me}: unknown party {peer!r} (known: {sorted(self.peers)})"
                )
            deadline = time.monotonic() + 20.0
            while True:
                try:
                    conn = Client(
                        self.peers[peer], family="AF_INET", authkey=self.authkey
                    )
                    break
                except ConnectionRefusedError, OSError:
                    if time.monotonic() > deadline:
                        raise ConnectionError(
                            f"{self.me}: {peer} is not listening on {self.peers[peer]}"
                        ) from None
                    time.sleep(0.1)
            self.conns[peer] = conn
        return conn

    def send(self, to: str, topic: str, **payload) -> int:
        """Send one message; returns its size in bytes."""
        self.seq += 1
        raw = pickle.dumps(
            Message(self.me, topic, payload, self.seq), protocol=pickle.HIGHEST_PROTOCOL
        )
        self._conn(to).send_bytes(raw)
        with self.lock:
            self.stats["sent"] += 1
            self.stats["bytes_out"] += len(raw)
            self.stats["peers"][to]["sent"] += 1
        return len(raw)

    def broadcast(self, topic: str, **payload) -> int:
        return sum(self.send(p, topic, **payload) for p in self.peers if p != self.me)

    def poll(self) -> list[Message]:
        out = []
        while True:
            try:
                out.append(self.inbox.get_nowait())
            except queue.Empty:
                return out

    def close(self) -> None:
        for c in self.conns.values():
            try:
                c.close()
            except Exception:
                pass
        try:
            self.listener.close()
        except Exception:
            pass


# ---- the hardware link ------------------------------------------------------------------------------
class Hardware:
    """Proxy to this station's instruments in the lab (request/reply over the worker's pipe)."""

    def __init__(self, out_q, reply_q, allowed: tuple[str, ...]):
        self.out_q, self.reply_q, self.allowed = out_q, reply_q, tuple(allowed)
        self._rid = 0

    def _request(self, op: str, instrument: str, *args, **kw):
        if instrument not in self.allowed:
            raise PermissionError(
                f"{instrument} is not part of this station (owns: {', '.join(self.allowed) or 'nothing'})"
            )
        self._rid += 1
        self.out_q.put(("hw", self._rid, op, instrument, args, kw))
        while True:
            rid, ok, value = self.reply_q.get(timeout=60)
            if rid == self._rid:
                break
        if not ok:
            raise RuntimeError(f"{instrument}.{op}: {value}")
        return value

    def get(self, instrument: str, param: str):
        """Read a parameter or property, e.g. ``hw.get("ALICE-LD", "rep_rate")``."""
        return self._request("get", instrument, param)

    def set(self, instrument: str, **params):
        return self._request("set", instrument, **params)

    def call(self, instrument: str, method: str, *args, **kw):
        """Call a method, e.g. ``hw.call("ALICE-AWG", "set_pattern", "ch1", volts, rate)``."""
        return self._request("call", instrument, method, *args, **kw)


# ---- the party ---------------------------------------------------------------------------------------
class Party:
    """Base class for a participant. Subclass it in a file of its own and override the hooks."""

    name: ClassVar[str] = "PARTY"
    port: ClassVar[int] = 0  # classical-channel port (0: BASE_PORT + index)
    instruments: ClassVar[tuple[str, ...]] = ()  # lab instruments this station owns
    abstract: ClassVar[bool] = False
    seed: ClassVar[int | None] = None
    stage: ClassVar[int] = 0
    """Where the station sits along the light path when it re-encodes what it detects.

    A relay, a trusted node or an eavesdropper detects the block *before* the stations further
    down the line see it and re-programs its own transmitter from the result — a feed-forward
    with some latency that a real receiver's clock calibration absorbs. Give such a station
    ``stage = 1`` (2 for a relay behind it, …): the lab records its counters first, hands it the
    batch, waits for its ``measure`` to return (that is where it programs its transmitter) and
    only then records the next stage and, last, the plain stations (stage 0).
    """

    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)
        if not cls.__dict__.get("abstract", False):
            PARTY_REGISTRY.append(cls)

    def __init__(
        self, hw: Hardware, net: ClassicalChannel, rng: np.random.Generator, report
    ):
        self.hw = hw
        self.net = net
        self.rng = rng
        self._report = report

    # ---- hooks --------------------------------------------------------------------------------
    def setup(self) -> None:
        """Once, in the party's process, before the first block."""

    def prepare(self, block: int, n: int) -> None:
        """Physical layer: program this station's transmitter for block ``block`` of ``n`` pulses."""

    def measure(self, batch: CounterResult) -> None:
        """Physical layer: a time-tagger batch recorded by this station's instruments."""

    def receive(self, msg: Message) -> None:
        """Classical channel: a message from another party."""

    def idle(self) -> None:
        """Called regularly when nothing else is pending."""

    # ---- helpers ------------------------------------------------------------------------------
    def report(self, **readouts) -> None:
        """Live read-outs shown on this party's laptop."""
        self._report(readouts)

    @classmethod
    def owns(cls, instrument: str) -> bool:
        return instrument in cls.instruments


def load_parties(source: str | Path) -> list[type[Party]]:
    """Import a party file (or module) and return the Party classes it defines."""
    before = len(PARTY_REGISTRY)
    module = import_source(source)
    found = PARTY_REGISTRY[before:]
    if not found:
        found = [
            c
            for c in vars(module).values()
            if isinstance(c, type)
            and issubclass(c, Party)
            and c is not Party
            and not c.__dict__.get("abstract")
        ]
    if not found:
        raise ValueError(f"{source} defines no Party subclass")
    found = list(dict.fromkeys(found))
    text = str(source)
    for cls in found:  # the worker re-imports the same source
        cls.source = (
            str(Path(text).resolve())
            if Path(text).suffix == ".py" or Path(text).exists()
            else text
        )
    return found


def load_protocol(name: str):
    """A protocol package exposing ``PARTIES`` and ``build()``.

    ``name`` is a directory or ``.py`` path, the name of a folder under ``./examples`` (so
    ``--protocol mpqkd`` works from the repository root), or a dotted module name.
    """
    module, locator = locate(name)
    if not hasattr(module, "PARTIES") or not callable(getattr(module, "build", None)):
        raise ValueError(f"{name} must define PARTIES and build()")
    for cls in module.PARTIES:
        cls.source = locator  # a party's process re-imports the protocol the same way
    return module


# ---- the party's process -----------------------------------------------------------------------------
def _party_main(
    source: str, clsname: str, peers: dict, authkey: bytes, seed, reply_q, in_q, out_q
) -> None:
    sys.stdout = sys.stderr = QueueWriter(out_q)
    net = None
    try:
        before = len(PARTY_REGISTRY)
        import_source(source)
        pool = PARTY_REGISTRY[before:] or PARTY_REGISTRY
        cls = next(c for c in pool if c.__name__ == clsname or c.name == clsname)
        hw = Hardware(out_q, reply_q, cls.instruments)
        net = ClassicalChannel(cls.name, peers, authkey)
        rng = np.random.default_rng(seed)
        party = cls(hw, net, rng, lambda readouts: out_q.put(("result", readouts)))
        party.setup()
    except Exception:
        traceback.print_exc()
        sys.stdout.flush()
        out_q.put(("status", "error"))
        return
    print(
        f"listening on {net.address[0]}:{net.address[1]} · peers: {', '.join(p for p in peers if p != cls.name)}"
    )
    sys.stdout.flush()
    out_q.put(("ready", net.address[1]))
    out_q.put(("status", "idle"))
    last_stats, last_idle = None, 0.0

    def push_stats():
        nonlocal last_stats
        snap = (net.stats["sent"], net.stats["recv"])
        if snap != last_stats:
            last_stats = snap
            with net.lock:
                out_q.put(
                    (
                        "net",
                        {k: v for k, v in net.stats.items() if k != "peers"}
                        | {
                            "peers": {p: dict(s) for p, s in net.stats["peers"].items()}
                        },
                    )
                )

    while True:
        try:
            msg = in_q.get(timeout=0.02)
        except queue.Empty:
            msg = ()
        if msg is None:
            break
        try:
            if msg:
                kind = msg[0]
                if kind == "prepare":
                    _, block, n = msg
                    out_q.put(("status", "running"))
                    party.prepare(block, n)
                    out_q.put(("prepared", block))
                    out_q.put(("status", "idle"))
                elif kind == "batch":
                    batch = msg[1]
                    out_q.put(("status", "running"))
                    party.measure(batch)
                    out_q.put(("done", batch.seq, None, "idle"))
            for m in net.poll():
                party.receive(m)
            now = time.monotonic()
            if now - last_idle > 0.5:
                last_idle = now
                party.idle()
        except Exception:
            traceback.print_exc()
            out_q.put(("status", "error"))
        sys.stdout.flush()
        push_stats()
    net.close()
    sys.stdout.flush()
    out_q.put(("status", "stopped"))


class PartyWorker(ProcessWorker):
    """Runs one :class:`Party` in a child process and serves its hardware requests."""

    kind = "party"

    def __init__(
        self,
        cls: type[Party],
        peers: dict,
        authkey: bytes,
        hw_handler,
        source: str | None = None,
        seed=None,
    ):
        super().__init__(cls.name, cls.name)
        self.cls = cls
        self.peers = peers
        self.authkey = authkey
        self.hw_handler = hw_handler
        self.source = source or getattr(cls, "source", None) or cls.__module__
        self.seed = seed
        self.port = peers[cls.name][1]
        self.prepared = 0  # blocks prepared so far
        self.net = None
        self.reply_q = None

    def target(self):
        import multiprocessing as mp

        self.reply_q = mp.get_context("spawn").Queue()
        return _party_main, (
            self.source,
            self.cls.__name__,
            self.peers,
            self.authkey,
            self.seed,
            self.reply_q,
        )

    def extra(self) -> dict:
        return {
            "port": self.port,
            "host": HOST,
            "instruments": list(self.cls.instruments),
            "net": self.net,
            "prepared": self.prepared,
            "file": self.source,
        }

    def handle(self, msg: tuple) -> None:
        kind = msg[0]
        if kind == "hw":
            _, rid, op, instrument, args, kw = msg
            try:
                value = self.hw_handler(op, instrument, args, kw)
                self.reply_q.put((rid, True, value))
            except Exception as exc:
                self.reply_q.put((rid, False, f"{type(exc).__name__}: {exc}"))
        elif kind == "prepared":
            self.prepared = msg[1] + 1
        elif kind == "net":
            self.net = msg[1]
        elif kind == "ready":
            self.status = "idle"

    def send_prepare(self, block: int, n: int) -> None:
        if self.alive:
            self.in_q.put(("prepare", block, n))


# ---- the experiment: parties + tasks driven block by block ----------------------------------------
class Experiment:
    """Runs parties (and analysis tasks) against a setup, one block of pulses at a time.

    Per block: every party prepares, the lab records the block, the batch goes to the party
    owning each counter and to the tasks. ``poll()`` advances at most one block and is safe to
    call repeatedly (the server does so on its ticks); ``run(blocks)`` drives it headless.
    """

    def __init__(
        self,
        setup: Setup,
        parties=None,
        tasks=(),
        seed: int | None = None,
        authkey: bytes | None = None,
        blocks_ahead: int = 2,
        lock=None,
        sources: dict | None = None,
    ):
        self.setup = setup
        self.rng = np.random.default_rng(seed if seed is not None else setup.seed)
        self.lock = lock or threading.RLock()
        self.authkey = authkey or secrets.token_bytes(16)
        self.blocks_ahead = blocks_ahead
        classes = list(PARTY_REGISTRY) if parties is None else list(parties)
        peers = {
            cls.name: (HOST, free_port(cls.port or BASE_PORT + i))
            for i, cls in enumerate(classes)
        }
        sources = sources or {}
        self.party_workers = [
            PartyWorker(
                cls,
                peers,
                self.authkey,
                self._hw,
                sources.get(cls.name),
                seed=None if seed is None else seed + i + 1,
            )
            for i, cls in enumerate(classes)
        ]
        self.task_workers = [TaskWorker(t) for t in tasks]
        self.block = 0
        self.phase = "prepare"
        self._asked = False
        self._stage = 1  # the relay stage being recorded (see Party.stage)
        self._recorded: set[int] = set()  # relay stages already recorded in this block
        self._results: dict[str, CounterResult] = {}  # this block's batches so far
        self.last_acquire = 0.0

    @property
    def workers(self) -> list[ProcessWorker]:
        return [*self.party_workers, *self.task_workers]

    def laptops(self) -> list[dict]:
        out = [
            {"name": w.cls.name, "kind": "party", "port": w.port, "host": HOST}
            for w in self.party_workers
        ]
        if any(t.laptop == "LAB" for t in self.task_workers):
            out.append({"name": "LAB", "kind": "lab"})
        return out

    # ---- hardware requests from the parties (run in the readers' threads) --------------------
    def _hw(self, op: str, instrument: str, args, kw):
        with self.lock:
            comp = self.setup[instrument]
            if op == "get":
                value = getattr(comp, args[0])
            elif op == "set":
                comp.set(**kw)
                value = None
            elif op == "call":
                value = getattr(comp, args[0])(*args[1:], **kw)
            else:
                raise ValueError(f"unknown hardware operation {op!r}")
        if isinstance(
            value, Component
        ):  # a method returned the instrument itself (fluent API)
            return value.name
        try:
            pickle.dumps(value)
        except Exception as exc:
            raise TypeError(
                f"result of {instrument}.{args[0] if args else op} cannot be sent to the party: {exc}"
            ) from None
        return value

    # ---- lifecycle ----------------------------------------------------------------------------------
    def start(self, timeout: float = 60.0) -> None:
        for w in self.workers:
            w.start()
        end = time.monotonic() + timeout
        while time.monotonic() < end and any(
            w.status == "starting" and w.alive for w in self.party_workers
        ):
            time.sleep(0.05)

    def stop(self) -> None:
        for w in self.workers:
            w.stop()

    def counters(self) -> list[PhotonCounter]:
        return [
            c
            for c in self.setup.of_type(PhotonCounter)
            if c.running and any(p.peer for p in c.ports.values())
        ]

    def block_size(self) -> int:
        return max([c.slots for c in self.counters()] + [0])

    # ---- stepping ------------------------------------------------------------------------------------
    def poll(self) -> dict[str, CounterResult] | None:
        """Advance by at most one block; returns the block's batches when one was recorded.

        Relay stations (``Party.stage`` ≥ 1) have their counters recorded first, stage by stage,
        and the block only goes on once their ``measure`` has returned; the remaining counters
        are recorded last.
        """
        senders = [w for w in self.party_workers if w.alive]
        if self.phase == "prepare":
            if not self._asked:
                for w in senders:
                    w.send_prepare(self.block, self.block_size())
                self._asked = True
            if any(w.prepared <= self.block for w in senders):
                return None
            self.phase, self._stage, self._recorded, self._results = (
                "relay",
                1,
                set(),
                {},
            )
        consumers = [
            w
            for w in self.workers
            if w.alive
            and any(
                w.laptop == c.name or getattr(w, "cls", None) and w.cls.owns(c.name)
                for c in self.counters()
            )
        ]
        if any(w.done < self.block - self.blocks_ahead for w in consumers):
            return None  # let the slowest consumer catch up
        if self.phase == "relay":
            last = max((w.cls.stage for w in senders), default=0)
            while self._stage <= last:
                staged = [w for w in senders if w.cls.stage == self._stage]
                if self._stage not in self._recorded:
                    self._recorded.add(self._stage)
                    self._record(
                        [
                            c
                            for c in self.counters()
                            if any(w.cls.owns(c.name) for w in staged)
                        ]
                    )
                if any(w.done < w.sent for w in staged):
                    return None  # their measure() — where they re-encode — must return first
                self._stage += 1
            self.phase = "acquire"
        self._record([c for c in self.counters() if c.name not in self._results])
        results, self._results = self._results, {}
        self.last_acquire = time.monotonic()
        self.block += 1
        self.phase, self._asked = "prepare", False
        return results

    def _record(self, counters) -> None:
        """Record this block on ``counters`` and hand the batches to their owners and the tasks."""
        with self.lock:
            net = trace(self.setup)
            for cnt in counters:
                res = cnt.acquire(net, self.rng, start=self.block * cnt.slots)
                res.seq = self.block
                self._results[cnt.name] = res
                for w in self.party_workers:
                    if w.cls.owns(cnt.name):
                        w.send(res)
                for t in self.task_workers:
                    if t.task.accepts(cnt.name):
                        t.send(res)

    def echo(self, stream=None) -> None:
        echo_lines(self.workers, stream)

    def wait_quiet(self, quiet: float = 0.75, timeout: float = 30.0) -> bool:
        """Block until no party has sent or received anything for ``quiet`` seconds (a dialogue such
        as error correction keeps going after the last block); False if ``timeout`` ran out first.
        """
        end = time.monotonic() + timeout
        last, since = None, time.monotonic()
        while time.monotonic() < end:
            snap = tuple(
                ((w.net or {}).get("sent", 0), (w.net or {}).get("recv", 0))
                for w in self.party_workers
                if w.alive
            )
            now = time.monotonic()
            if snap != last:
                last, since = snap, now
            elif now - since >= quiet:
                return True
            time.sleep(0.05)
        return False

    def run(
        self, blocks: int = 10, echo: bool = True, timeout: float = 120.0
    ) -> list[ProcessWorker]:
        """Start everything, run ``blocks`` blocks, let the parties finish talking, stop."""
        self.start()
        try:
            end = time.monotonic() + timeout
            while self.block < blocks and time.monotonic() < end:
                if self.poll() is None:
                    time.sleep(0.01)
                if echo:
                    self.echo()
                if not any(w.alive for w in self.party_workers):
                    break
            for w in self.workers:
                w.wait_idle(timeout)
            self.wait_quiet(
                timeout=min(timeout, 30.0)
            )  # the parties' last classical messages
            if echo:
                self.echo()
        finally:
            self.stop()
            if echo:
                self.echo()
        return self.workers
