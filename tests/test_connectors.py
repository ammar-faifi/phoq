"""Fiber connectors: FC/UPC (blue) and FC/APC (green) end faces, what a mated pair transmits and
reflects, open ends, the mismatch warning, and the types carried by cords, parts and presets.
"""

import json
import math

import pytest

from pathlib import Path
from runpy import run_path

from phoq import (
    Coupler,
    Fiber,
    FiberTerminator,
    IntensityModulator,
    Laser,
    MatingSleeve,
    Photodiode,
    PowerMeter,
    Setup,
    SinglePhotonDetector,
)
from phoq.engine import report, trace
from phoq.units import (
    CONNECTOR_LOSS_DB,
    FRESNEL,
    MISMATCH_LOSS_DB,
    RETURN_LOSS_DB,
    db_to_t,
    mating,
)


def reading(setup, name="OPM"):
    return report(setup)["detectors"][name]["reading"]


def back_db(setup, laser="LD"):
    src = report(setup)["sources"][laser]
    return 10 * math.log10(src["back_reflection"] / setup[laser].power)


def test_mating_table():
    t, ra, rb = mating("FC/UPC", "FC/UPC")
    assert (t, ra, rb) == (db_to_t(CONNECTOR_LOSS_DB), db_to_t(50.0), db_to_t(50.0))
    assert mating("FC/APC", "FC/APC")[1] == db_to_t(RETURN_LOSS_DB["FC/APC"])
    assert mating("FC/PC", "FC/UPC")[1] == db_to_t(
        40.0
    )  # the worse of the two flat faces
    t, r_apc, r_flat = mating("FC/APC", "FC/UPC")
    assert (
        t == db_to_t(MISMATCH_LOSS_DB) and r_apc == db_to_t(60.0) and r_flat == FRESNEL
    )
    assert 10 * math.log10(FRESNEL) == pytest.approx(-14.45, abs=0.01)


@pytest.mark.parametrize("kind", ["FC/UPC", "FC/APC"])
def test_matching_connectors_lose_the_mated_pair_loss(kind):
    s = Setup.from_chain(
        Laser("LD", mode="cw", power_dbm=0.0, connector=kind),
        PowerMeter("OPM", connector=kind),
    )
    assert (s["FIB-1"].connector_a, s["FIB-1"].connector_b) == (
        kind,
        kind,
    )  # the cord a lab would take
    assert 10 * math.log10(reading(s) / 1e-3) == pytest.approx(
        -2 * CONNECTOR_LOSS_DB, abs=0.01
    )
    assert not report(s)["warnings"]
    # the laser sees its two matings; the far one's reflection crosses the near one twice
    expected = 10 * math.log10(
        db_to_t(RETURN_LOSS_DB[kind]) * (1 + db_to_t(2 * CONNECTOR_LOSS_DB))
    )
    assert back_db(s) == pytest.approx(expected, abs=0.05)
    assert report(s)["sources"]["LD"]["readouts"]["Strongest reflection"].startswith(
        f"LD.out ({kind}) ↔ FIB-1.a ({kind})"
    )


def test_auto_cord_between_different_ports_is_a_hybrid():
    s = Setup.from_chain(Laser("LD", mode="cw", connector="FC/APC"), PowerMeter("OPM"))
    assert (s["FIB-1"].connector_a, s["FIB-1"].connector_b) == ("FC/APC", "FC/UPC")
    assert not report(s)["warnings"]


def test_apc_mated_to_upc_loses_power_reflects_and_warns():
    s = Setup("mismatch")
    ld = s.add(Laser("LD", mode="cw", power_dbm=0.0, connector="FC/APC"))
    opm, cord = s.add(
        PowerMeter("OPM"), Fiber("FIB-1")
    )  # an FC/UPC cord: the classic mistake
    s.connect(ld.out, cord.a)
    s.connect(cord.b, opm.inp)
    assert 10 * math.log10(reading(s) / 1e-3) == pytest.approx(
        -(MISMATCH_LOSS_DB + CONNECTOR_LOSS_DB), abs=0.01
    )
    texts = [w["text"] for w in report(s)["warnings"]]
    assert len(texts) == 1 and texts[0].startswith(
        "LD.out (FC/APC) is mated to FIB-1.a (FC/UPC)"
    )
    assert "3.6% reflected back into FIB-1.a" in texts[0]
    # the angled laser side reflects little; the meter's −50 dB mating returns through the gap twice
    expected = db_to_t(60.0) + db_to_t(50.0 + 2 * MISMATCH_LOSS_DB)
    assert back_db(s) == pytest.approx(10 * math.log10(expected), abs=0.05)

    s["LD"].connector, s["FIB-1"].connector_a = (
        "FC/UPC",
        "FC/APC",
    )  # now the laser is the flat side
    assert back_db(s) == pytest.approx(
        10 * math.log10(FRESNEL), abs=0.05
    )  # 3.6 % straight back
    assert "reflected back into LD.out" in report(s)["warnings"][0]["text"]


def test_open_ends_reflect_by_polish():
    s = Setup("open")
    ld, cord = s.add(Laser("LD", mode="cw", power_dbm=0.0), Fiber("FIB-1"))
    s.connect(ld.out, cord.a)
    r = report(s)
    assert back_db(s) == pytest.approx(
        10 * math.log10(FRESNEL) - 2 * CONNECTOR_LOSS_DB, abs=0.05
    )
    assert r["sources"]["LD"]["readouts"]["Strongest reflection"].startswith(
        "open FC/UPC end FIB-1.b"
    )
    assert r["open_ends"][0]["power"] == pytest.approx(
        1e-3 * db_to_t(CONNECTOR_LOSS_DB) * (1 - FRESNEL), rel=1e-3
    )
    s["FIB-1"].connector_b = "FC/APC"
    assert back_db(s) < -49  # only the −50 dB mating at the laser is left


def test_a_reflection_reaches_a_detector_through_a_coupler():
    """Reflectometry: the laser feeds a coupler, a cord hangs off one output, a meter sits on
    the coupler's other input. Only reflected light can reach the meter. The coupler is angled
    and its spare output capped, so the tail's flat end is the only strong reflector."""
    s = Setup("reflectometer")
    ld, bs, opm = s.add(
        Laser("LD", mode="cw", power_dbm=0.0),
        Coupler("BS", connector="FC/APC"),
        PowerMeter("OPM"),
    )
    tail = s.add(Fiber("TAIL", connector_a="FC/APC"))
    s.connect(ld.out, bs.a1)
    s.connect(bs.a2, opm.inp)
    s.connect(bs.b1, tail.a)
    s.terminate(bs.b2)  # left open, its flat 3.6 % would interfere with the tail's
    loss_db = (
        5 * CONNECTOR_LOSS_DB
        + 2 * (10 * math.log10(2) + bs.excess_loss_db)
        - 10 * math.log10(FRESNEL)
    )
    assert reading(s) == pytest.approx(
        1e-3 * db_to_t(loss_db + CONNECTOR_LOSS_DB), rel=0.05
    )  # ± mating-reflection fringes
    s["TAIL"].connector_b = "FC/APC"  # the angled end hides it
    assert reading(s) < 1e-3 * db_to_t(loss_db + 30)


def test_connector_types_persist_and_reach_the_lab():
    s = Setup("persist")
    ld = s.add(Laser("LD", connector="FC/APC"))
    pd = s.add(Photodiode("PD"))
    cord = s.connect(ld.out, pd.inp)
    d = s.to_dict()
    assert {"name": "LD", "type": "Laser", "params": {"connector": "FC/APC"}} in d[
        "components"
    ]
    again = Setup.from_dict(json.loads(json.dumps(d)))
    assert (
        again[cord.name].connector_a == "FC/APC"
        and again[cord.name].connector_b == "FC/UPC"
    )
    assert "connector_a='FC/APC'" in s.to_python()
    assert (
        ld.ports["out"].to_dict()["connector"] == "FC/APC"
        and ld.ports["out"].connector == "FC/APC"
    )
    assert "connector" not in ld.ui_fields()  # the lab adds it at the end, once

    from fastapi.testclient import TestClient
    from phoq.server import create_app

    with TestClient(create_app(s, web_dir=None)) as c:
        comps = {x["name"]: x for x in c.get("/api/state").json()["components"]}
        assert comps["LD"]["fields"][-1] == "connector" and comps[cord.name]["fields"][
            -2:
        ] == ["connector_a", "connector_b"]
        assert comps["PD"]["ports"][0]["connector"] == "FC/UPC"
        assert (
            c.patch(
                f"/api/components/{cord.name}",
                json={"params": {"connector_b": "FC/LC"}},
            ).status_code
            == 422
        )
        rep = c.get("/api/report").json()
        assert rep["sources"]["LD"]["readouts"]["Back-reflection"].startswith("-")


@pytest.mark.parametrize(
    "preset, ends, warns",
    [
        (
            "fs/smfcsx-apc-upc",
            ("FC/APC", "FC/UPC"),
            False,
        ),  # the hybrid cord this bench needs
        ("fs/smfcsx-upc-upc", ("FC/UPC", "FC/UPC"), True),
        ("fs/smfcsx-apc-apc", ("FC/APC", "FC/APC"), True),
    ],
)
def test_fs_patch_cords_between_an_apc_laser_and_a_upc_meter(preset, ends, warns):
    s = Setup(preset)
    ld = s.add(Laser("LD", mode="cw", power_dbm=0.0, connector="FC/APC"))
    opm, cord = s.add(PowerMeter("OPM"), Fiber("FIB", preset=preset))
    assert (cord.connector_a, cord.connector_b, cord.length) == (*ends, 1.0)
    s.connect(ld.out, cord.a)
    s.connect(cord.b, opm.inp)
    assert bool(report(s)["warnings"]) is warns
    loss = CONNECTOR_LOSS_DB + (MISMATCH_LOSS_DB if warns else CONNECTOR_LOSS_DB)
    assert 10 * math.log10(reading(s) / 1e-3) == pytest.approx(-loss, abs=0.01)
    # its temperature range is kept for reference; nothing the lab checks live
    assert (
        cord.limits["operating_temperature_c"] == [-10.0, 70.0]
        and not cord.preset_info.summary()["has_limits"]
    )


def test_presets_carry_the_datasheet_connector():
    assert Photodiode(preset="thorlabs/dx25ha").connector == "FC/APC"
    assert Photodiode(preset="thorlabs/dx25hf").connector == "FC/PC"
    assert SinglePhotonDetector(preset="mpd/pdm-ir").connector == "FC/PC"
    assert IntensityModulator(preset="exail/mx-ln-10").connector == "FC/APC"
    # an FC/PC detector behind an FC/APC modulator gets a hybrid cord automatically
    s = Setup.from_chain(
        IntensityModulator("IM", preset="exail/mx-ln-10"),
        Photodiode("PD", preset="thorlabs/dx25hf"),
    )
    assert (s["FIB-1"].connector_a, s["FIB-1"].connector_b) == ("FC/APC", "FC/PC")


# ---- FC mating sleeves ----------------------------------------------------------------------
def through_sleeves(kinds, plugged=True, laser="FC/UPC"):
    """LD → cords of the given polishes, joined by sleeves → OPM (FC/UPC)."""
    s = Setup("sleeves")
    prev = s.add(Laser("LD", mode="cw", power_dbm=0.0, connector=laser)).out
    for i, k in enumerate(kinds, 1):
        cord = s.add(Fiber(f"FIB-{i}", connector_a=k, connector_b=k))
        if i > 1:
            sleeve = s.add(MatingSleeve(f"SLV-{i - 1}"))
            s.connect(
                prev, sleeve.p1
            )  # cord ↔ sleeve: plugged straight in, no cord inserted
            prev = sleeve.p2
        s.connect(prev, cord.a)
        prev = cord.b
    if plugged:
        s.connect(prev, s.add(PowerMeter("OPM")).inp)
    return s


def test_a_sleeve_is_one_mated_pair_between_two_cords():
    s = through_sleeves(["FC/UPC", "FC/UPC"])
    assert len(s.of_type(Fiber)) == 2  # nothing inserted around the sleeve
    assert 10 * math.log10(reading(s) / 1e-3) == pytest.approx(
        -3 * CONNECTOR_LOSS_DB, abs=0.01
    )
    T, R = db_to_t(CONNECTOR_LOSS_DB), db_to_t(50.0)
    assert back_db(s) == pytest.approx(10 * math.log10(R * (1 + T**2 + T**4)), abs=0.05)
    sleeve = s["SLV-1"]
    assert (
        sleeve.ports["p1"].connector == sleeve.ports["p2"].connector == "FC/UPC"
    )  # what the other side holds
    assert sleeve.info() == {
        "Mating": "FIB-1.b (FC/UPC) ↔ FIB-2.a (FC/UPC)",
        "Insertion loss": "0.30 dB",
        "Reflected back": "-50.0 dB into FIB-1.b · -50.0 dB into FIB-2.a",
    }
    sites = report(s)["sources"]["LD"]
    assert sites["back_reflection"] > 0 and not report(s)["warnings"]


@pytest.mark.parametrize("n", [1, 2, 4, 8])
def test_every_sleeve_in_a_cascade_costs_one_mating(n):
    s = through_sleeves(["FC/UPC"] * n)
    assert len(s.of_type(MatingSleeve)) == n - 1
    assert 10 * math.log10(reading(s) / 1e-3) == pytest.approx(
        -(n + 1) * CONNECTOR_LOSS_DB, abs=0.01
    )


def test_mixed_polish_inside_sleeves_warns_once_per_sleeve():
    s = through_sleeves(["FC/UPC", "FC/UPC", "FC/APC", "FC/UPC"])
    texts = [w["text"] for w in report(s)["warnings"]]
    assert len(texts) == 2
    assert texts[0].startswith("FIB-2.b (FC/UPC) is mated to FIB-3.a (FC/APC) in SLV-2")
    assert texts[1].startswith("FIB-3.b (FC/APC) is mated to FIB-4.a (FC/UPC) in SLV-3")
    loss = 3 * CONNECTOR_LOSS_DB + 2 * MISMATCH_LOSS_DB
    assert 10 * math.log10(reading(s) / 1e-3) == pytest.approx(-loss, abs=0.01)
    assert (
        "in SLV-2" in report(s)["sources"]["LD"]["readouts"]["Strongest reflection"]
    )  # the Fresnel 3.6 %
    assert s["SLV-2"].info()["Insertion loss"] == f"{MISMATCH_LOSS_DB:.2f} dB"


def test_a_cord_in_an_empty_sleeve_is_an_open_end():
    s = through_sleeves(["FC/UPC"], plugged=False)
    sleeve = s.add(MatingSleeve("SLV-9"))
    s.connect(s["FIB-1"].b, sleeve.p1)
    r = report(s)
    assert [e["port"] for e in r["open_ends"]] == ["FIB-1.b"]
    assert r["sources"]["LD"]["readouts"]["Strongest reflection"].startswith(
        "open FC/UPC end FIB-1.b in SLV-9"
    )
    assert (
        sleeve.ports["p1"].connector is None
        and sleeve.ports["p2"].connector == "FC/UPC"
    )
    assert sleeve.info() == {"Mating": "FIB-1.b (FC/UPC) meets an empty side"}


def test_sleeves_between_bulkheads_get_cords_and_round_trip():
    s = Setup.from_chain(
        Laser("LD", mode="cw", power_dbm=0.0, connector="FC/APC"),
        MatingSleeve("SLV"),
        PowerMeter("OPM"),
    )
    assert [(f.connector_a, f.connector_b) for f in s.of_type(Fiber)] == [
        ("FC/APC", "FC/UPC"),
        ("FC/UPC", "FC/UPC"),
    ]
    assert not report(s)["warnings"]
    assert 10 * math.log10(reading(s) / 1e-3) == pytest.approx(
        -3 * CONNECTOR_LOSS_DB, abs=0.01
    )
    again = Setup.from_dict(json.loads(json.dumps(s.to_dict())))
    assert reading(again) == pytest.approx(reading(s))
    s2 = Setup("two sleeves")
    a, b = s2.add(MatingSleeve("A"), MatingSleeve("B"))
    assert isinstance(
        s2.connect(a.p2, b.p1), Fiber
    )  # two female sides need a cord between them


def test_connector_cascade_example():
    build = run_path(
        str(Path(__file__).resolve().parents[1] / "examples" / "connector_cascade.py")
    )["build"]
    clean, apc = report(build()), report(
        build(cords=("FC/APC",) * 4, laser="FC/APC", detector="FC/APC")
    )
    assert w_dbm(clean["detectors"]["PD-1"]["power"]) == pytest.approx(
        -5 * CONNECTOR_LOSS_DB, abs=0.01
    )
    assert clean["sources"]["LD-1"]["back_reflection"] / 1e-3 == pytest.approx(
        apc["sources"]["LD-1"]["back_reflection"] / 1e-3 * 10,
        rel=1e-6,  # −50 vs −60 dB per mating
    )
    unplugged = report(build(plugged=False))
    assert (
        unplugged["detectors"]["PD-1"]["power"] == 0
        and unplugged["open_ends"][0]["port"] == "FIB-4.b"
    )


def w_dbm(w):
    return 10 * math.log10(w / 1e-3)


def homodyne(vacuum_cap=None, ratio=0.5):
    """Laser → coupler → a photodiode on b1, b2 capped (FC/UPC); a2 is left open, or capped."""
    s = Setup("homodyne")
    ld, bs, pd = s.add(
        Laser("LD", mode="cw", power_dbm=0.0),
        Coupler("BS", ratio=ratio),
        Photodiode("PD"),
    )
    s.connect(ld.out, bs.a1)
    s.connect(bs.b1, pd.inp)
    s.terminate(bs.b2)
    if vacuum_cap:
        s.terminate(bs.a2, vacuum_cap)
    return s


def test_an_open_part_port_is_an_open_end():
    """A coupler output left unconnected emits into the air and reflects like an open cord end;
    the reflection comes back out of the coupler's other input."""
    s = Setup("open port")
    ld, bs, opm = s.add(
        Laser("LD", mode="cw", power_dbm=0.0), Coupler("BS"), PowerMeter("OPM")
    )
    s.connect(ld.out, bs.a1)
    s.connect(bs.a2, opm.inp)
    s.terminate(bs.b1)
    t, half = db_to_t(CONNECTOR_LOSS_DB), 0.5 * db_to_t(bs.excess_loss_db)
    r = report(s)
    out = {e["port"]: e["power"] for e in r["open_ends"]}
    assert out["BS.b2"] == pytest.approx(1e-3 * t**2 * half * (1 - FRESNEL), rel=1e-3)
    assert any(
        w["text"].startswith("Open connector BS.b2 emits") for w in r["warnings"]
    )
    assert reading(s) == pytest.approx(
        1e-3 * t**4 * half**2 * FRESNEL, rel=0.05
    )  # ± the −50 dB caps' fringes
    s.terminate(bs.b2)
    assert reading(s) < 1e-3 * t**4 * half**2 * db_to_t(RETURN_LOSS_DB["FC/UPC"]) * 4
    angled = Setup("angled port")
    ld, bs, opm = angled.add(
        Laser("LD", mode="cw", power_dbm=0.0),
        Coupler("BS", connector="FC/APC"),
        PowerMeter("OPM"),
    )
    angled.connect(ld.out, bs.a1)
    angled.connect(bs.a2, opm.inp)
    angled.terminate(bs.b1)
    assert (
        reading(angled) < 1e-3 * t**4 * half**2 * db_to_t(RETURN_LOSS_DB["FC/APC"]) * 4
    )  # −60 dB, not 3.6 %


@pytest.mark.parametrize("cap", ["FC/UPC", "FC/APC"])
def test_a_terminator_plugs_in_without_a_cord_and_reflects_as_its_mated_pair(cap):
    s = Setup("cap")
    ld = s.add(Laser("LD", mode="cw", power_dbm=0.0))
    trm = s.add(FiberTerminator("TRM", connector=cap))
    assert s.connect(ld.out, trm.ports["p"]) is None  # no cord
    r = report(s)
    assert r["open_ends"] == [] and r["detectors"] == {}
    _, back, _ = mating("FC/UPC", cap)
    assert back_db(s) == pytest.approx(10 * math.log10(back), abs=0.01)
    assert trm.info()["Reflected back"].startswith(f"{10 * math.log10(back):.1f} dB")
    mismatch = [w for w in r["warnings"] if "air gap" in w["text"]]
    assert bool(mismatch) == (cap == "FC/APC")  # a green cap on a blue port
    again = Setup.from_dict(json.loads(json.dumps(s.to_dict())))
    assert [(a.path, b.path) for a, b in again.connections()] == [("LD.out", "TRM.p")]
    assert "setup.connect(ld.out, trm.p)" in s.to_python()
    from fastapi.testclient import TestClient
    from phoq.server import create_app

    with TestClient(
        create_app(s, web_dir=None)
    ) as c:  # the lab draws a plug on the port it is mated to
        comps = {x["name"]: x for x in c.get("/api/state").json()["components"]}
        assert (
            comps["TRM"]["plug"]
            and not comps["LD"]["plug"]
            and comps["TRM"]["ports"][0]["peer"] == "LD.out"
        )
    with pytest.raises(ConnectionError, match="both are plugs"):
        s.connect(
            s.add(FiberTerminator("TRM-2")).ports["p"],
            s.add(FiberTerminator("TRM-3")).ports["p"],
        )


def test_a_double_bounce_reaches_the_detector_at_the_product_of_its_gains():
    """Light reflected at the mating on the coupler's output (−50 dB) leaves by the coupler's open
    input, the flat end sends 3.6 % of it back in, and it goes on to the photodiode: the strongest
    stray, r·F·(½)² of the direct light (the one off the photodiode's own mating passes two more
    connectors). A matching cap drops these below the −90 dB floor, and the warning with them.
    """
    s = homodyne()
    pd = s["PD"]
    paths = trace(s).paths[pd]
    direct = max(abs(p.amp) ** 2 for p in paths if not p.reflections)
    strays = [
        p
        for p in paths
        if p.reflections == 2 and any(port == "BS.a2" for _, port in p.sites)
    ]
    half = 0.5 * db_to_t(s["BS"].excess_loss_db)
    want = db_to_t(RETURN_LOSS_DB["FC/UPC"]) * FRESNEL * half**2
    assert max(abs(p.amp) ** 2 for p in strays) / direct == pytest.approx(
        want, rel=1e-3
    )
    warned = [
        w["text"]
        for w in report(s)["warnings"]
        if w["text"].startswith("Open connector BS.a2 reflects")
    ]
    assert warned and "PD" in warned[0]
    capped = homodyne(vacuum_cap="FC/UPC")
    assert not [
        p for p in trace(capped).paths[capped["PD"]] if p.reflections == 2
    ]  # r² ≈ −100 dB
    assert not [
        w for w in report(capped)["warnings"] if "reflects light on" in w["text"]
    ]


def test_terminate_spares_caps_the_unused_ports_of_parts_only():
    """A coupler's spare port gets a cap of its own type; a laser with nothing on it, a free
    detector input and an unplugged cord end are not spares. The lab's button does the same.
    """

    def bench():
        s = Setup("spares")
        ld, bs, pd = s.add(
            Laser("LD"), Coupler("BS", connector="FC/APC"), Photodiode("PD")
        )
        s.add(Laser("LD-2"), Photodiode("PD-2"), Fiber("TAIL"))
        s.connect(ld.out, bs.a1)
        s.connect(bs.b1, pd.inp)
        s.connect(bs.b2, s["TAIL"].ports["a"])
        return s

    s = bench()
    caps = s.terminate_spares()
    assert [(c.ports["p"].peer.path, c.connector) for c in caps] == [
        ("BS.a2", "FC/APC")
    ]
    assert s.terminate_spares() == []  # nothing left to cap
    from fastapi.testclient import TestClient
    from phoq.server import create_app

    assert [p.path for p in bench().spare_ports()] == ["BS.a2"]
    with TestClient(create_app(bench(), web_dir=None)) as c:
        assert c.get("/api/state").json()["spares"] == [
            "BS.a2"
        ]  # the lab's count of open spare ports
        assert c.post("/api/terminate-spares").json() == {"ok": True, "caps": ["TRM-1"]}
        assert c.get("/api/state").json()["spares"] == []
        comps = {x["name"]: x for x in c.get("/api/state").json()["components"]}
        assert comps["TRM-1"]["plug"] and comps["TRM-1"]["ports"][0]["peer"] == "BS.a2"
