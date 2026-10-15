"""Your own post-processing of time-tagger clicks, run alongside the experiment.

Write a separate script, decorate a function, and point the lab at the file::

    # my_analysis.py
    from phoq.analysis import task

    @task("TAGGER")                      # the counter(s) feeding it; omit the name for every counter
    def rates(batch):
        t = batch.timestamps("ch1")      # absolute click times [s], continuous across batches
        print(f"batch {batch.seq}: {t.size} clicks on ch1, {batch.rates['ch1']:.3g} cps")
        return {"ch1 rate [cps]": batch.rates["ch1"]}     # a dict becomes a live read-out

    $ uv run phoq serve examples/mpqkd --analysis my_analysis.py   # live, with a console
    $ uv run phoq run   examples/mpqkd --analysis my_analysis.py --batches 10

or, from the script itself, ``analysis.main("examples/mpqkd")`` under ``if __name__ == "__main__":``.
``@task("TAGGER", party="CHARLIE")`` puts the task's console on that party's laptop in the lab.

Why a process per task
----------------------
Each task runs in its own worker process (``multiprocessing``, *spawn*). A thread would share the
interpreter lock with the simulation, could not be stopped if the user's code looped forever, and
``print`` output cannot be captured per thread. Python 3.14's sub-interpreters
(``concurrent.interpreters``) would give parallelism in one process, but NumPy's support for them is
still immature and nearly every analysis uses NumPy. A process gives full isolation (a crash or hang
never touches the lab), its own interpreter lock, a plain pipe for stdout/stderr that feeds the
console, and a clean kill/restart that also reloads the edited script. A batch of 100 000 click flags
per second is a trivial amount of data for a pipe. State kept in the function's globals or closure
persists between batches, since the worker process lives for the whole experiment.
"""

from __future__ import annotations

import inspect
import multiprocessing as mp
import queue
import sys
import threading
import time
import traceback
from collections import deque
from pathlib import Path

import numpy as np

from .core import Setup
from .engine import trace
from .instruments import CounterResult, PhotonCounter
from .loading import (
    import_source,
    module_name_for,
)  # noqa: F401  (re-exported for callers)

ClickBatch = CounterResult
REGISTRY: list[Task] = []
MAX_BACKLOG = 2  # batches a slow worker may fall behind before new ones are dropped


# ---- registration -------------------------------------------------------------------------------
class Task:
    """A registered post-processing function: ``fn(batch)`` for every batch of its sources."""

    def __init__(
        self, fn, sources=(), name: str | None = None, party: str | None = None
    ):
        self.fn = fn
        self.name = name or fn.__name__
        self.sources = tuple(sources)
        self.party = party
        src = inspect.getsourcefile(fn) or getattr(
            sys.modules.get(fn.__module__), "__file__", None
        )
        self.file = Path(src).resolve() if src else None
        self.__doc__ = fn.__doc__

    def __call__(self, batch):
        return self.fn(batch)

    def accepts(self, instrument: str) -> bool:
        return not self.sources or instrument in self.sources

    def __repr__(self):
        return f"<task {self.name} ← {', '.join(self.sources) or 'all counters'}>"


def task(*sources, name: str | None = None, party: str | None = None):
    """Register a function to run on every click batch: ``@task`` or ``@task("TAGGER", party="CHARLIE")``."""
    if (
        sources and callable(sources[0]) and not isinstance(sources[0], str)
    ):  # bare @task
        t = Task(sources[0], (), name, party)
        REGISTRY.append(t)
        return t

    def decorate(fn):
        t = Task(fn, sources, name, party)
        REGISTRY.append(t)
        return t

    return decorate


def load_tasks(path: str | Path) -> list[Task]:
    """Import a script and return the tasks it registers."""
    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"analysis script not found: {path}")
    before = len(REGISTRY)
    import_source(path)
    found = [t for t in REGISTRY[before:] if t.file == path or t.file is None]
    if not found:
        raise ValueError(f"{path} registers no @task functions")
    return found


# ---- worker processes ------------------------------------------------------------------------------
class QueueWriter:
    """A file-like stdout/stderr replacement that ships whole lines to the parent."""

    def __init__(self, out_q):
        self.out_q = out_q
        self.buf = ""

    def write(self, s: str) -> int:
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            self.out_q.put(("line", line))
        return len(s)

    def flush(self) -> None:
        if self.buf:
            self.out_q.put(("line", self.buf))
            self.buf = ""

    def isatty(self) -> bool:
        return False


def _task_main(path: str, name: str, in_q, out_q) -> None:
    sys.stdout = sys.stderr = QueueWriter(out_q)
    try:
        before = len(REGISTRY)
        import_source(Path(path))
        fn = next(t for t in REGISTRY[before:] if t.name == name)
    except Exception:
        traceback.print_exc()
        sys.stdout.flush()
        out_q.put(("status", "error"))
        return
    out_q.put(("status", "idle"))
    while True:
        msg = in_q.get()
        if msg is None:
            break
        kind, batch = msg
        if kind != "batch":
            continue
        out_q.put(("status", "running"))
        try:
            result = fn(batch)
        except Exception:
            traceback.print_exc()
            sys.stdout.flush()
            out_q.put(("done", batch.seq, None, "error"))
            continue
        sys.stdout.flush()
        out_q.put(
            ("done", batch.seq, result if isinstance(result, dict) else None, "idle")
        )
    sys.stdout.flush()
    out_q.put(("status", "stopped"))


class ProcessWorker:
    """A child process whose console output, status and read-outs the lab collects.

    Subclasses say what to run (``target``) and may handle extra messages from the child.
    """

    kind = "process"

    def __init__(self, name: str, laptop: str = "LAB", keep_lines: int = 2000):
        self.name = name
        self.laptop = laptop
        self.status = "stopped"
        self.lines: deque[str] = deque(maxlen=keep_lines)
        self.new: list[str] = []
        self.batches = 0
        self.dropped = 0
        self.sent = 0
        self.done = 0
        self.result: dict | None = None
        self.proc = None
        self.lock = threading.Lock()
        self._announced = None

    # ---- what runs ---------------------------------------------------------------------------
    def target(self):
        """(function, args) run in the child; the args end with (in_q, out_q)."""
        raise NotImplementedError

    def extra(self) -> dict:
        """Extra fields for the snapshot."""
        return {}

    def handle(self, msg: tuple) -> None:
        """A message kind the base class does not know."""

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.is_alive()

    # ---- lifecycle ---------------------------------------------------------------------------
    def start(self) -> None:
        if self.alive:
            return
        ctx = mp.get_context("spawn")
        self.in_q, self.out_q = ctx.Queue(), ctx.Queue()
        self.sent = self.done = 0
        self.status = "starting"
        fn, args = self.target()
        self.proc = ctx.Process(
            target=fn,
            args=(*args, self.in_q, self.out_q),
            name=f"phoq-{self.kind}-{self.name}",
            daemon=True,
        )
        self.proc.start()
        self._log(f"── started {self.name} (pid {self.proc.pid})")
        threading.Thread(
            target=self._drain, name=f"phoq-{self.kind}-{self.name}-reader", daemon=True
        ).start()

    def stop(self, timeout: float = 1.0) -> None:
        proc = self.proc
        if proc is None:
            return
        if proc.is_alive():
            try:
                self.in_q.put(None)
            except Exception:
                pass
            proc.join(timeout)
            if proc.is_alive():
                proc.terminate()
                proc.join(1.0)
                self._log("── killed (did not stop in time)")
        self.status = "stopped"
        self.proc = None

    def restart(self) -> None:
        """Stop and start again — this re-imports the script, so edits take effect."""
        self.stop()
        self.result = None
        self.start()

    def clear(self) -> None:
        with self.lock:
            self.lines.clear()
            self.new = []

    # ---- data -----------------------------------------------------------------------------------
    def send(self, batch: CounterResult) -> bool:
        """Queue a batch; a worker more than MAX_BACKLOG batches behind drops it (buffer overflow)."""
        if not self.alive:
            return False
        if self.sent - self.done > MAX_BACKLOG:
            self.dropped += 1
            self._log(
                f"⚠ dropped batch {batch.seq} ({self.name} is {self.sent - self.done} batches behind)"
            )
            return False
        self.in_q.put(("batch", batch))
        self.sent += 1
        return True

    def wait_idle(self, timeout: float = 30.0) -> bool:
        """Block until every sent batch has been processed (or the worker died)."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.done >= self.sent or not self.alive:
                return self.done >= self.sent
            time.sleep(0.02)
        return False

    def _drain(self) -> None:
        proc, out_q = self.proc, self.out_q
        while True:
            try:
                msg = out_q.get(timeout=0.25)
            except queue.Empty:
                if proc is not self.proc or not proc.is_alive():
                    if self.status not in ("stopped", "error"):
                        self.status = "stopped"
                        self._log("── worker exited")
                    return
                continue
            kind = msg[0]
            if kind == "line":
                self._log(msg[1])
            elif kind == "status":
                self.status = msg[1]
            elif kind == "done":
                _, seq, result, status = msg
                self.done += 1
                self.batches += 1
                if result is not None:
                    self.result = result
                self.status = status
            elif kind == "result":
                self.result = msg[1]
            else:
                try:
                    self.handle(msg)
                except Exception as exc:  # never let a bad message kill the reader
                    self._log(f"⚠ {type(exc).__name__}: {exc}")

    def _log(self, line: str) -> None:
        with self.lock:
            self.lines.append(line)
            self.new.append(line)

    # ---- views ----------------------------------------------------------------------------------
    def has_news(self) -> bool:
        return bool(self.new) or self.status != self._announced

    def snapshot(self, full: bool = False) -> dict:
        with self.lock:
            lines = list(self.lines) if full else list(self.new)
            self.new = []
        self._announced = self.status
        return {
            "task": self.name,
            "kind": self.kind,
            "laptop": self.laptop,
            "status": self.status,
            "batches": self.batches,
            "dropped": self.dropped,
            "result": jsonable(self.result),
            "lines": lines,
            "full": full,
            **self.extra(),
        }


class TaskWorker(ProcessWorker):
    """Runs one :class:`Task` in a child process."""

    kind = "task"

    def __init__(self, task: Task, keep_lines: int = 2000):
        super().__init__(task.name, task.party or "LAB", keep_lines)
        self.task = task

    def target(self):
        if self.task.file is None:
            raise ValueError(
                f"task {self.task.name} has no source file to run in a worker"
            )
        return _task_main, (str(self.task.file), self.task.name)

    def extra(self) -> dict:
        return {"file": str(self.task.file), "sources": list(self.task.sources)}


def jsonable(d):
    if d is None:
        return None
    out = {}
    for k, v in d.items():
        if isinstance(v, bool):
            out[str(k)] = v
        elif isinstance(v, (np.floating, float)):
            out[str(k)] = float(v) if np.isfinite(v) else None
        elif isinstance(v, (np.integer, int)):
            out[str(k)] = int(v)
        else:
            out[str(k)] = str(v)
    return out


# ---- running headless ---------------------------------------------------------------------------
class Session:
    """Acquire the setup's counters batch after batch and feed the batches to the tasks.

    ``with Session(setup) as s: s.run(batches=10)`` — uses every registered task unless ``tasks``
    is given. Time stamps continue from one batch to the next.
    """

    def __init__(
        self, setup: Setup, tasks: list[Task] | None = None, seed: int | None = None
    ):
        self.setup = setup
        self.rng = np.random.default_rng(seed if seed is not None else setup.seed)
        self.workers = [
            TaskWorker(t) for t in (list(REGISTRY) if tasks is None else tasks)
        ]
        self.seq = 0

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()

    def start(self) -> None:
        for w in self.workers:
            w.start()

    def stop(self) -> None:
        for w in self.workers:
            w.stop()

    def counters(self) -> list[PhotonCounter]:
        return [
            c
            for c in self.setup.of_type(PhotonCounter)
            if c.running and any(p.peer for p in c.ports.values())
        ]

    def step(self) -> dict[str, CounterResult]:
        """One acquisition of every counter, dispatched to the tasks."""
        net = trace(self.setup)
        results = {}
        for cnt in self.counters():
            res = cnt.acquire(net, self.rng)
            res.seq = self.seq
            results[cnt.name] = res
            for w in self.workers:
                if w.task.accepts(cnt.name):
                    w.send(res)
        self.seq += 1
        return results

    def echo(self, stream=None) -> None:
        """Print the workers' new console lines, prefixed with the task name."""
        echo_lines(self.workers, stream)

    def run(
        self, batches: int = 10, echo: bool = True, timeout: float = 60.0
    ) -> list[TaskWorker]:
        """Start the workers, acquire ``batches`` batches, wait for the tasks to finish, stop."""
        self.start()
        try:
            for _ in range(batches):
                self.step()
                if echo:
                    self.echo()
            for w in self.workers:
                w.wait_idle(timeout)
        finally:
            self.stop()
            if echo:
                self.echo()
        return self.workers


def echo_lines(workers, stream=None) -> None:
    stream = stream or sys.stdout
    for w in workers:
        for line in w.snapshot()["lines"]:
            print(f"[{w.name}] {line}", file=stream)


def main(
    setup_path: str | Path, batches: int = 10, serve: bool = False, port: int = 8000
) -> None:
    """Run the tasks registered in the calling script against a setup file (headless or served)."""
    setup = Setup.load(setup_path)
    tasks = list(REGISTRY)
    if serve:
        from .server import serve as _serve

        print(
            f"virtual lab: http://127.0.0.1:{port}   tasks: {', '.join(t.name for t in tasks)}"
        )
        _serve(setup, port=port, tasks=tasks)
    else:
        Session(setup, tasks).run(batches)
