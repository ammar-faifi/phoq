"""Build the walkthroughs the virtual lab links from its Help (?) window.

    uv run python tools/walkthroughs/build.py
    uv run python tools/walkthroughs/build.py --fragments out/

Each walkthrough steps through code line by line, an instrument's acquire() and the
methods it calls or a station reading its tagger, beside a screen that draws what each
line produced. ``<name>.py`` replays those lines on a demo bench, checks the replay
against the code itself and returns every intermediate array; ``<name>.html`` is the
page, with ``/*DATA*/`` where the data go. The methods' source is embedded as it is
now, so rebuild after changing them (tests/test_walkthroughs.py fails until you do).
"""

import argparse
import importlib
import inspect
import json
from pathlib import Path
from runpy import run_path

from phoq.loading import import_source

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WEB = ROOT / "src" / "phoq" / "web" / "walkthroughs"
PAGES = {  # capture module and template → published page
    "spectrum_analyzer": "spectrum-analyzer.html",
    "oscilloscope": "oscilloscope.html",
    "photon_counter": "photon-counter.html",
    "time_stamps": "time-stamps.html",
}


def source(ref: str) -> str:
    """The source of ``"module:Qual.name"``; a property's getter for a property. A module
    given as a path, ``examples/cow_vacuum``, is imported from the repository."""
    module, qual = ref.split(":")
    if "/" in module:
        obj = import_source(ROOT / module)
    else:
        obj = importlib.import_module(module)
    for part in qual.split("."):
        obj = getattr(obj, part)
    return inspect.getsource(obj.fget if isinstance(obj, property) else obj)


def fill(template: str, data: dict) -> str:
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    return template.replace("/*DATA*/", blob)


def document(fragment: str) -> str:
    """A fragment (title, styles, then the body's content) as a standalone page."""
    head, body = fragment.split("</style>\n", 1)
    head = head.replace('<meta name="viewport-fit-sides" content="cover">\n', "")
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1,'
        ' viewport-fit=cover">\n'
        f"{head}body {{ margin: 0; }}\n</style>\n</head>\n<body>\n{body}</body>\n"
        "</html>\n"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--fragments", metavar="DIR", help="also write artifact fragments")
    args = ap.parse_args()
    WEB.mkdir(parents=True, exist_ok=True)
    for name, page in PAGES.items():
        mod = run_path(str(HERE / f"{name}.py"))
        data = mod["capture"]()
        data["refs"] = mod["REFS"]
        data["src"] = {key: source(ref) for key, ref in mod["REFS"].items()}
        filled = fill((HERE / f"{name}.html").read_text(encoding="utf-8"), data)
        (WEB / page).write_text(document(filled), encoding="utf-8")
        print(f"{WEB / page}: replay matches the code")
        if args.fragments:
            out = Path(args.fragments)
            out.mkdir(parents=True, exist_ok=True)
            (out / page).write_text(filled, encoding="utf-8")


if __name__ == "__main__":
    main()
