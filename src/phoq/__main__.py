"""Command line: ``phoq {run,serve,export,catalog} ...``."""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

from .core import Setup


def _load(path: str | None) -> Setup:
    return Setup.load(path) if path else Setup("untitled")


def _tasks(paths):
    from .analysis import load_tasks

    return [t for p in paths for t in load_tasks(p)]


def _parties_and_setup(args):
    """Parties from --protocol / --party, and the setup from the file or the protocol's
    build()."""
    from .parties import load_parties, load_protocol

    parties, protocol = [], None
    if getattr(args, "protocol", None):
        protocol = load_protocol(args.protocol)
        parties += list(protocol.PARTIES)
    for path in getattr(args, "party", None) or []:
        parties += load_parties(path)
    if args.file:
        setup = _load(args.file)
    elif protocol is not None:
        setup = protocol.build()
    else:
        setup = Setup("untitled")
    return parties, setup


def cmd_run(args) -> None:
    parties, setup = _parties_and_setup(args)
    print(setup.describe(), "\n")
    tasks = _tasks(args.analysis)
    if parties:
        from .parties import Experiment

        print(
            f"parties: {', '.join(p.name for p in parties)}"
            + (f" · tasks: {', '.join(t.name for t in tasks)}" if tasks else "")
            + f" — {args.blocks} blocks\n"
        )
        Experiment(setup, parties, tasks, seed=args.seed).run(args.blocks)
        return
    if tasks:
        from .analysis import Session

        print(f"analysis: {', '.join(t.name for t in tasks)} — {args.blocks} batches\n")
        Session(setup, tasks, seed=args.seed).run(args.blocks)
        return
    results = setup.run(*args.instrument, seed=args.seed)
    print(results.summary())
    if args.plot:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        plt.style.use("dark_background")

        scopes = [r for r in results.values() if hasattr(r, "plot")]
        if not scopes:
            print("no oscilloscope in this setup; nothing to plot", file=sys.stderr)
            return
        fig, axes = plt.subplots(
            len(scopes), 1, figsize=(8, 3.4 * len(scopes)), squeeze=False
        )
        for ax, r in zip(axes[:, 0], scopes):
            r.plot(ax)
        fig.tight_layout()
        fig.savefig(args.plot, dpi=140)
        print(f"\nsaved {args.plot}")


def cmd_serve(args) -> None:
    from .server import serve

    parties, setup = _parties_and_setup(args)
    if args.no_save:
        save = None
    elif args.save:
        save = Path(args.save)
    elif args.file and args.file.endswith(".json"):
        save = Path(args.file)
    else:
        save = (
            Path(args.file).with_suffix(".json")
            if args.file
            else Path("lab_setup.json")
        )
    url = f"http://{args.host}:{args.port}"
    print(f"phoq · {setup!r}")
    print(f"  virtual lab  {url}")
    print(f"  API docs     {url}/docs")
    print(f"  autosave     {save or 'off'}")
    if args.open:
        webbrowser.open(url)
    tasks = _tasks(args.analysis)
    if tasks:
        print(f"  analysis     {', '.join(t.name for t in tasks)}")
    if parties:
        print(
            f"  parties      {', '.join(p.name for p in parties)} (classical channel on"
            " localhost TCP)"
        )
    serve(
        setup,
        host=args.host,
        port=args.port,
        save_path=save,
        web_dir=Path(args.web) if args.web else None,
        seed=args.seed,
        tasks=tasks,
        parties=parties or None,
    )


def cmd_export(args) -> None:
    setup = _load(args.file)
    text = setup.to_python() if args.format == "python" else None
    if args.format == "json":
        import json

        text = json.dumps(setup.to_dict(), indent=2, ensure_ascii=False) + "\n"
    if args.output:
        Path(args.output).write_text(text)
        print(f"wrote {args.output}")
    else:
        print(text)


def cmd_catalog(args) -> None:
    from .server import catalog

    for t in catalog()["types"]:
        print(f"{t['type']:<22} {t['label']} — {t['description']}")
        ports = ", ".join(
            p["name"] + ("*" if p["kind"] == "electrical" else "") for p in t["ports"]
        )
        print(f"{'':<22} ports: {ports}")
        print(f"{'':<22} params: {', '.join(p['name'] for p in t['params'])}")
        parts = [p["id"] for p in t["presets"] if p["tier"] != "generic"]
        print(f"{'':<22} presets: generic{', ' + ', '.join(parts) if parts else ''}\n")


def cmd_presets(args) -> None:
    from . import presets

    if args.paths:
        for origin, d in presets.search_path():
            print(f"{origin:<16} {d}{'' if d.is_dir() else '   (absent)'}")
        return
    if args.id:
        try:
            print(presets.get(args.id).describe())
        except KeyError as e:
            sys.exit(str(e).strip("'\""))
        return
    rows = presets.search(
        args.type, vendor=args.vendor, text=args.search, generic=not args.no_generic
    )
    problems = []
    for p in rows:
        bad = presets.check(p)
        problems += bad
        print(f"{p.id:<28} {p.component:<22} {p.title}{'   !' if bad else ''}")
        if p.description:
            print(f"{'':<28} {p.description}")
    if problems:
        print("\nproblems:\n  " + "\n  ".join(problems))
        sys.exit(1)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        prog="phoq",
        description=(
            "phoq — headless photonics & quantum-communication lab with an optional 3D"
            " visualizer."
        ),
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--presets",
        action="append",
        default=[],
        metavar="DIR",
        help=(
            "directory of part presets (.toml, searched recursively); repeatable, also"
            " $PHOQ_PRESETS"
        ),
    )

    p = sub.add_parser(
        "run",
        parents=[common],
        help="run a setup headlessly and print the measurements",
    )
    p.add_argument(
        "file",
        nargs="?",
        help=(
            "setup: a .py defining `setup` or `build()`, a .json, or a package"
            " directory such as examples/mpqkd; optional with --protocol"
        ),
    )
    p.add_argument(
        "-i",
        "--instrument",
        action="append",
        default=[],
        help="only acquire these instruments",
    )
    p.add_argument("--seed", type=int, default=None)
    p.add_argument(
        "--plot", metavar="PNG", help="save the oscilloscope traces as an image"
    )
    p.add_argument(
        "--analysis",
        action="append",
        default=[],
        metavar="SCRIPT.py",
        help="run the @task functions of this script on the counter batches",
    )
    p.add_argument(
        "--protocol",
        metavar="NAME",
        help=(
            "protocol package with PARTIES and build(): a path, a folder under"
            " ./examples (mpqkd), or a module"
        ),
    )
    p.add_argument(
        "--party",
        action="append",
        default=[],
        metavar="FILE.py",
        help="a file defining a Party subclass (repeatable)",
    )
    p.add_argument(
        "--blocks",
        "--batches",
        dest="blocks",
        type=int,
        default=10,
        help="blocks of pulses to run",
    )
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser(
        "serve", parents=[common], help="serve a setup to the 3D virtual lab"
    )
    p.add_argument(
        "file",
        nargs="?",
        help="setup: a .py, a .json or a package directory; omit for an empty table",
    )
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument(
        "--save",
        metavar="JSON",
        help="autosave edits here (default: next to the setup file)",
    )
    p.add_argument("--no-save", action="store_true", help="don't write edits to disk")
    p.add_argument(
        "--web",
        metavar="DIR",
        help="virtual lab folder (default: the one shipped in the package)",
    )
    p.add_argument("--seed", type=int, default=None)
    p.add_argument(
        "--analysis",
        action="append",
        default=[],
        metavar="SCRIPT.py",
        help="run the @task functions of this script alongside, each with a console",
    )
    p.add_argument(
        "--protocol",
        metavar="NAME",
        help=(
            "protocol package with PARTIES and build(): a path, a folder under"
            " ./examples (mpqkd), or a module"
        ),
    )
    p.add_argument(
        "--party",
        action="append",
        default=[],
        metavar="FILE.py",
        help="a file defining a Party subclass (repeatable)",
    )
    p.add_argument("--open", action="store_true", help="open the browser")
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser(
        "export", parents=[common], help="convert a setup to JSON or Python"
    )
    p.add_argument("file")
    p.add_argument("--format", choices=("json", "python"), default="python")
    p.add_argument("-o", "--output")
    p.set_defaults(fn=cmd_export)

    p = sub.add_parser(
        "catalog",
        parents=[common],
        help="list component types, ports, parameters and presets",
    )
    p.set_defaults(fn=cmd_catalog)

    p = sub.add_parser(
        "presets",
        parents=[common],
        help=(
            "list part presets, or show one with its provenance (phoq presets"
            " exail/mx-ln-10)"
        ),
    )
    p.add_argument("id", nargs="?", help="a preset id to show in full")
    p.add_argument(
        "--type", metavar="CLASS", help="only presets for this component class"
    )
    p.add_argument("--vendor")
    p.add_argument(
        "--search", metavar="TEXT", help="match id, title, part number, tags"
    )
    p.add_argument(
        "--no-generic",
        action="store_true",
        help="hide the generic (class default) presets",
    )
    p.add_argument("--paths", action="store_true", help="print the preset search path")
    p.set_defaults(fn=cmd_presets)

    args = ap.parse_args(argv)
    if getattr(args, "presets", None):
        from . import presets

        for d in args.presets:
            presets.add_path(d)
    args.fn(args)


if __name__ == "__main__":
    main()
