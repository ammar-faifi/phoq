"""The instrument walkthroughs the lab's Help window links to: each page embeds the
source of the methods it walks through, and that source must still be the code's
(rebuild with ``uv run python tools/walkthroughs/build.py``)."""

import importlib
import inspect
import json
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "src" / "phoq" / "web"
PAGES = ("spectrum-analyzer.html", "oscilloscope.html", "photon-counter.html")


def _data(page: str) -> dict:
    html = (WEB / "walkthroughs" / page).read_text(encoding="utf-8")
    blob = html.split('<script id="data" type="application/json">')[1].split(
        "</script>"
    )[0]
    return json.loads(blob.replace("<\\/", "</"))


def _source(ref: str) -> str:
    module, qual = ref.split(":")
    obj = importlib.import_module(module)
    for part in qual.split("."):
        obj = getattr(obj, part)
    return inspect.getsource(obj.fget if isinstance(obj, property) else obj)


@pytest.mark.parametrize("page", PAGES)
def test_walkthrough_shows_the_current_code(page):
    data = _data(page)
    assert data["refs"] and set(data["refs"]) == set(data["src"])
    for key, ref in data["refs"].items():
        assert data["src"][key] == _source(ref), (
            f"{page}: {ref} changed; rebuild with"
            " `uv run python tools/walkthroughs/build.py`"
        )


def test_help_window_links_every_walkthrough_and_the_server_serves_them():
    from fastapi.testclient import TestClient

    from phoq import Setup
    from phoq.server import create_app

    index = (WEB / "index.html").read_text(encoding="utf-8")
    with TestClient(create_app(Setup("empty"))) as client:
        for page in PAGES:
            assert f'href="walkthroughs/{page}"' in index
            r = client.get(f"/walkthroughs/{page}")
            assert r.status_code == 200 and "<title>" in r.text
