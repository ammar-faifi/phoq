"""phoq run: a script with a main block runs as ``python`` runs it, a protocol package
runs its parties, and any other setup is acquired once. phoq's own options go before the
file; after a setup that is no script they may follow it. phoq serve starts a protocol
package's parties too."""

import json
import subprocess
import sys

import pytest

import phoq.analysis
import phoq.parties
import phoq.server
from phoq import Setup
from phoq.__main__ import main

SCRIPT = """\
import sys

import phoq_cli_sibling

print("name", __name__)
print("args", sys.argv[1:])
print("file", __file__)
print("sibling", phoq_cli_sibling.VALUE)

if __name__ == "__main__":
    print("main block")
    if sys.argv[1:] == ["fail"]:
        sys.exit(3)
"""

SETUP = """\
from phoq import Fiber, Laser, Oscilloscope, Photodiode, Setup

setup = Setup.from_chain(
    Laser("LD-1", mode="pulsed", rep_rate=10e6, pulse_width=5e-9),
    Fiber(length=1.0),
    Photodiode("PD-1", bandwidth=1e9),
    Oscilloscope("OSC-1", timebase=20e-9),
    name="link",
    seed=1,
)
"""

PROTOCOL = """\
from phoq import Setup
from phoq.parties import Party


class Alice(Party):
    name = "ALICE"


PARTIES = [Alice]


def build():
    return Setup("proto")
"""

TASKS = """\
from phoq.analysis import task


@task
def count(batch):
    pass
"""


@pytest.fixture
def script(tmp_path):
    (tmp_path / "phoq_cli_sibling.py").write_text("VALUE = 42\n")
    path = tmp_path / "script.py"
    path.write_text(SCRIPT)
    yield path
    sys.modules.pop("phoq_cli_sibling", None)


def test_a_script_runs_as_python_runs_it(script, capsys):
    argv, path = sys.argv[:], sys.path[:]
    main(["run", str(script), "--plot", "x.png", "-v"])
    got = capsys.readouterr().out
    want = subprocess.run(
        [sys.executable, str(script), "--plot", "x.png", "-v"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert got == want and "main block" in got and "sibling 42" in got
    assert sys.argv == argv and sys.path == path  # put back afterward


def test_the_scripts_exit_code_and_errors_pass_through(script, tmp_path):
    with pytest.raises(SystemExit) as e:
        main(["run", str(script), "fail"])
    assert e.value.code == 3
    bad = tmp_path / "bad.py"
    bad.write_text('if __name__ == "__main__":\n    raise ValueError("boom")\n')
    with pytest.raises(ValueError, match="boom"):
        main(["run", str(bad)])


def test_a_setup_or_a_saved_json_is_acquired_once(tmp_path, capsys):
    path = tmp_path / "link.py"
    path.write_text(SETUP)
    main(["run", str(path)])
    out = capsys.readouterr().out
    assert "Setup 'link'" in out and "ch1 ← PD-1" in out
    saved = tmp_path / "link.json"
    saved.write_text(json.dumps(Setup.load(path).to_dict()))
    main(["run", str(saved)])
    assert "ch1 ← PD-1" in capsys.readouterr().out


def test_instrument_or_plot_before_a_script_acquire_its_setup_once(tmp_path, capsys):
    path = tmp_path / "both.py"
    path.write_text(SETUP + '\nif __name__ == "__main__":\n    print("main block")\n')
    main(["run", str(path)])
    assert "main block" in capsys.readouterr().out
    main(["run", "-i", "OSC-1", str(path)])
    out = capsys.readouterr().out
    assert "ch1 ← PD-1" in out and "main block" not in out
    png = tmp_path / "traces.png"
    main(["run", "--plot", str(png), str(path)])
    assert png.stat().st_size > 0 and "main block" not in capsys.readouterr().out


def test_options_a_script_cannot_take_are_refused_with_a_hint(script):
    with pytest.raises(SystemExit) as e:
        main(["run", "--seed", "3", str(script)])
    assert "after its name" in str(e.value.code)


@pytest.fixture
def protocol(tmp_path, monkeypatch):
    """A protocol package, with the experiment and the analysis session stubbed."""
    monkeypatch.setattr(
        phoq.parties, "PARTY_REGISTRY", list(phoq.parties.PARTY_REGISTRY)
    )
    monkeypatch.setattr(phoq.analysis, "REGISTRY", list(phoq.analysis.REGISTRY))
    ran = {}

    class Experiment:
        def __init__(self, setup, parties, tasks, seed=None):
            ran.update(setup=setup.name, parties=[p.name for p in parties], seed=seed)

        def run(self, blocks):
            ran["blocks"] = blocks

    class Session:
        def __init__(self, setup, tasks, seed=None):
            ran.update(setup=setup.name, tasks=[t.name for t in tasks], seed=seed)

        def run(self, blocks):
            ran["blocks"] = blocks

    monkeypatch.setattr(phoq.parties, "Experiment", Experiment)
    monkeypatch.setattr(phoq.analysis, "Session", Session)
    pkg = tmp_path / "proto"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(PROTOCOL)
    return pkg, ran


def test_a_protocol_package_runs_its_parties(protocol):
    pkg, ran = protocol
    main(["run", str(pkg), "--blocks", "2", "--seed", "5"])
    assert ran == {"setup": "proto", "parties": ["ALICE"], "seed": 5, "blocks": 2}


def test_analysis_after_a_package_runs_the_tasks_alone(protocol, tmp_path):
    """As before packages ran their protocol: `--analysis` keeps the setup's tasks
    without the parties."""
    pkg, ran = protocol
    tasks = tmp_path / "tasks.py"
    tasks.write_text(TASKS)
    main(["run", str(pkg), "--analysis", str(tasks), "--batches", "3"])
    assert ran == {"setup": "proto", "tasks": ["count"], "seed": None, "blocks": 3}


def test_serve_starts_a_protocol_packages_parties_as_run_does(
    protocol, tmp_path, monkeypatch
):
    """The lab gets the parties' laptops; with --analysis the directory is the setup
    alone, with live tasks and no parties."""
    pkg, _ = protocol
    served = {}
    monkeypatch.setattr(
        phoq.server, "serve", lambda setup, **kw: served.update(setup=setup.name, **kw)
    )
    main(["serve", str(pkg), "--no-save"])
    assert served["setup"] == "proto"
    assert [p.name for p in served["parties"]] == ["ALICE"]
    tasks = tmp_path / "tasks.py"
    tasks.write_text(TASKS)
    main(["serve", str(pkg), "--no-save", "--analysis", str(tasks)])
    assert served["parties"] is None and [t.name for t in served["tasks"]] == ["count"]
