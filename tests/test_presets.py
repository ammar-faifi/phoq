"""Part presets: the files, the registry and search path, what applying one does to a
component, the model additions they needed, and the API and command line."""

import json
import math

import numpy as np
import pytest

from phoq import (
    ArbitraryWaveformGenerator,
    Component,
    Coupler,
    DCPowerSupply,
    IntensityModulator,
    Laser,
    Photodiode,
    Setup,
    SinglePhotonDetector,
    presets,
)
from phoq.engine import report
from phoq.presets import PresetError

SHIPPED = {
    "exail/mx-ln-10",
    "excelitas/spcm-aqrh-13-fc",
    "hamamatsu/koheras-mikro-a15",
    "hamamatsu/koheras-mikro-e15",
    "idq/id-qube-nir-fr-ln",
    "idq/id-qube-nir-fr-uln",
    "idq/id281-standard-1550",
    "mpd/pdm-ir",
    "mpd/pdm-ir-sff",
    "thorlabs/det08cfc",
    "thorlabs/dx25cf",
    "thorlabs/dx25hf",
}


def test_every_class_has_a_generic_and_shipped_presets_are_sound():
    reg = presets.registry()
    for cls in Component.registry.values():
        assert reg[presets.generic_id(cls)].generic
    shipped = [p for p in reg.values() if not p.generic]
    assert SHIPPED <= {p.id for p in shipped}
    for p in shipped:
        assert presets.check(p) == [], p.id
        assert p.source.get("document") and p.source.get("revision"), p.id
        assert all(prov.kind != "unstated" for prov in p.provenance.values()), p.id
        assert all(
            c.provenance.kind == "datasheet-graph" for c in p.curves.values()
        ), p.id
    assert Photodiode().preset == "generic" and Photodiode().to_dict() == {
        "name": None,
        "type": "Photodiode",
        "params": {},
    }


def test_preset_sets_only_its_params_and_round_trips():
    im = IntensityModulator("IM-1", preset="exail/mx-ln-10")
    assert (im.v_pi, im.v_pi_bias, im.insertion_loss_db, im.extinction_db) == (
        5.5,
        4.2,
        3.5,
        22.0,
    )
    assert im.to_dict() == {
        "name": "IM-1",
        "type": "IntensityModulator",
        "preset": "exail/mx-ln-10",
        "params": {},
    }
    im.extinction_db = 25.0
    assert im.to_dict()["params"] == {"extinction_db": 25.0}
    prov = im.provenance()
    assert prov["extinction_db"]["overridden"] and not prov["v_pi"]["overridden"]
    assert prov["v_pi"] == {
        "kind": "datasheet-typ",
        "spec": "Vπ RF electrodes @ 50 kHz, 1550 nm",
        "max": 6.0,
        "overridden": False,
    }

    setup = Setup("presets")
    setup.add(im)
    again = Setup.from_dict(json.loads(json.dumps(setup.to_dict())))["IM-1"]
    assert (
        again.preset == "exail/mx-ln-10"
        and again.extinction_db == 25.0
        and again.v_pi_bias == 4.2
    )
    code = setup.to_python()
    assert "preset='exail/mx-ln-10', extinction_db=25.0" in code
    ns = {}
    exec(code.split("if __name__")[0], ns)
    assert ns["setup"]["IM-1"].v_pi == 5.5

    im.apply_preset("generic")  # everything the preset had set returns to the defaults
    assert im.preset == "generic" and (im.v_pi, im.v_pi_bias, im.extinction_db) == (
        4.0,
        0.0,
        30.0,
    )
    im.apply_preset(presets.get("exail/mx-ln-40"))
    assert im.v_pi == 5.0 and "preset='exail/mx-ln-40'" in repr(im)

    with pytest.raises(PresetError, match="not a Laser"):
        Laser().apply_preset("exail/mx-ln-10")
    with pytest.raises(KeyError, match="did you mean"):
        Photodiode(preset="thorlabs/det08cf")


def test_subclass_inherits_its_bases_presets():
    class FastPD(Photodiode):
        pass

    ids = [p.id for p in presets.search(FastPD)]
    assert ids[0] == "generic/fast-pd" and "generic/photodiode" not in ids
    assert "thorlabs/det08cfc" in ids
    assert FastPD(preset="thorlabs/det08cfc").bandwidth == 5e9


def test_bias_electrode_vpi_is_referred_to_the_rf_electrode():
    setup = Setup("bias")
    # Vπ 5.5 V RF, 4.2 V DC
    im = setup.add(IntensityModulator("IM", preset="exail/mx-ln-10"))
    psu = setup.add(DCPowerSupply("PSU", ch1_voltage=im.v_pi_bias / 2))
    setup.connect(psu.ch1, im.bias)
    # quadrature at Vπ_DC/2
    assert im.transmission(im.static_voltage()) == pytest.approx(0.5, abs=1e-9)
    psu.ch1_voltage = im.v_pi_bias
    assert im.transmission(im.static_voltage()) == pytest.approx(im.t_min)  # the null
    fg = setup.add(ArbitraryWaveformGenerator("FG"))
    setup.connect(fg.ch1, im.rf)
    # rf voltage for quadrature on top of the null bias
    v_rf = float(im.voltage_for(0.5))
    assert im.transmission(v_rf + im.bias_voltage()) == pytest.approx(0.5)
    assert im.info()["Bias electrode"].startswith("Vπ 4.2 V")
    assert IntensityModulator().bias_scale() == 1.0  # generic: one electrode


def test_photodiode_presets_responsivity_and_optical_limit():
    pd = Photodiode("PD", preset="thorlabs/dx25cf")
    assert (pd.material, pd.bandwidth, pd.load) == ("GaAs", 25e9, 25.0)
    assert pd.responsivity(850e-9) == pytest.approx(0.5, abs=0.02)
    assert pd.responsivity(1550e-9) == 0.0
    det = Photodiode("PD", preset="thorlabs/det08cfc")
    assert det.responsivity(1550e-9) == pytest.approx(0.90) and det.saturation == 40e-3

    setup = Setup.from_chain(
        Laser("LD", mode="cw", power_dbm=12.0, wavelength=850e-9), pd
    )
    texts = [w["text"] for w in report(setup)["warnings"]]
    assert any(
        "absolute maximum optical input" in t and t.startswith("PD ") for t in texts
    )
    setup["LD"].power_dbm = 0.0
    assert not any("absolute maximum" in t for t in report(setup)["warnings"])


def test_spectral_curves_set_efficiency_and_responsivity_versus_wavelength():
    # Si: 400–1060 nm
    spcm = SinglePhotonDetector("SPD", preset="excelitas/spcm-aqrh-13-fc")
    # anchored at the table's wavelength
    assert spcm.efficiency_at(650e-9) == pytest.approx(0.62)
    # the figure's shape
    assert spcm.efficiency_at(830e-9) == pytest.approx(0.51, abs=0.01)
    assert spcm.efficiency_at(1310e-9) == spcm.efficiency_at(1550e-9) == 0.0
    assert (
        "ref_wavelength" in spcm.ui_fields()
        and spcm.info()["Spectral PDE"] == "400–1060 nm · datasheet graph"
    )
    # InGaAs/InP: peaks near 1300–1475 nm
    pdm = SinglePhotonDetector(preset="mpd/pdm-ir")
    assert pdm.efficiency_at(1550e-9) == pytest.approx(0.25)
    assert 0.34 < pdm.efficiency_at(1310e-9) < 0.37 and pdm.efficiency_at(850e-9) < 0.01
    generic = SinglePhotonDetector(efficiency=0.3)  # no curve: flat, as before
    assert (
        generic.spectral_factor(850e-9) == 1.0 and generic.efficiency_at(1550e-9) == 0.3
    )
    assert "ref_wavelength" not in generic.ui_fields() and generic.info() == {}

    # peaks near 1.15 µm, falls off past 1.55 µm
    hf = Photodiode(preset="thorlabs/dx25hf")
    assert hf.responsivity(1550e-9) == pytest.approx(0.45)
    assert hf.responsivity(1310e-9) == pytest.approx(0.509, abs=0.005)
    assert hf.responsivity(900e-9) == hf.responsivity(1650e-9) == 0.0
    det = Photodiode(preset="thorlabs/det08cfc")
    assert det.responsivity(1550e-9) == pytest.approx(0.90)
    # the generic InGaAs table: 0.57
    assert det.responsivity(1650e-9) == pytest.approx(0.79, abs=0.01)
    # material curve
    assert Photodiode(ref_responsivity=0.8).responsivity(1550e-9) == pytest.approx(0.8)


def test_silicon_spad_on_a_1550_nm_link_is_blind():
    spcm = SinglePhotonDetector(
        "SPD", preset="excelitas/spcm-aqrh-16-fc", dark_count_rate=0.0, dead_time=0.0
    )
    n, dt = 20_000, 1e-9
    # 0.1 nW: 0.43 photons per ns at 850 nm
    field = np.full(n, math.sqrt(1e-10), dtype=complex)
    assert spcm.detect({1550e-9: field}, dt, np.random.default_rng(1), n).sum() == 0
    clicks = spcm.detect({850e-9: field}, dt, np.random.default_rng(1), n).sum()
    assert clicks == pytest.approx(
        n
        * (
            1
            - math.exp(
                -spcm.efficiency_at(850e-9)
                * 1e-10
                * dt
                / (6.62607015e-34 * 299792458 / 850e-9)
            )
        ),
        rel=0.06,
    )

    setup = Setup.from_chain(
        Laser("LD", mode="pulsed", rep_rate=10e6, power_dbm=-60.0),
        SinglePhotonDetector("SPD", preset="excelitas/spcm-aqrh-13-fc"),
    )
    r = report(setup)
    texts = " ".join(w["text"] for w in r["warnings"])
    assert "SPD (excelitas/spcm-aqrh-13-fc) does not detect 1550 nm" in texts
    assert r["detectors"]["SPD"]["readouts"]["PDE at 1550 nm"] == "0"
    setup["LD"].wavelength = 850e-9
    r = report(setup)
    assert (
        r["detectors"]["SPD"]["readouts"]["PDE at 850 nm"] == "0.473"
        and not r["warnings"]
    )
    setup["LD"].power_dbm = -40.0  # ≈ 4e4 photons per pulse; rated 1e4
    assert "photons per pulse, above the absolute maximum of 10000" in " ".join(
        w["text"] for w in report(setup)["warnings"]
    )


def test_pdm_ir_desktop_and_small_form_factor_differ_only_in_dark_counts():
    """MPD PDM-IR datasheet v2.1: the SMF-28 desktop instrument (DI) and small form
    factor (SFF) share the 10 µm SPAD's rows, and p. 3 gives the SFF twice the DCR:
    0.5 kcps typ and 1.2 max against 0.25 and 0.6, at 2 V, a 100 µs hold-off and, for
    the SFF, a 25 °C case."""
    di, sff = presets.get("mpd/pdm-ir"), presets.get("mpd/pdm-ir-sff")
    assert (di.part_number, sff.part_number) == ("$PI-010-SM-DI", "$PI-010-SM")
    assert sff.source["revision"] == di.source["revision"] == "v2.1, February 2025"
    assert sff.source["document"] == di.source["document"]
    differ = {
        k for k in di.params | sff.params if di.params.get(k) != sff.params.get(k)
    }
    assert differ == {"dark_count_rate"}
    dcr = {
        p.id: (p.params["dark_count_rate"], p.provenance["dark_count_rate"].max)
        for p in (di, sff)
    }
    assert dcr == {"mpd/pdm-ir": (250.0, 600.0), "mpd/pdm-ir-sff": (500.0, 1200.0)}
    # one PDE-vs-wavelength plot for both (p. 1)
    di_pde, sff_pde = di.curves["efficiency"], sff.curves["efficiency"]
    assert (sff_pde.wavelength, sff_pde.value) == (di_pde.wavelength, di_pde.value)
    for p in (di, sff):  # every value names its datasheet page
        specs = [v.spec for v in p.provenance.values()]
        specs += [c.provenance.spec for c in p.curves.values()]
        assert all(s.startswith("p. ") for s in specs), p.id

    spad = SinglePhotonDetector("SPD", preset="mpd/pdm-ir-sff")
    assert spad.dark_count_rate == 500.0 and spad.connector == "FC/PC"
    assert spad.efficiency_at(1550e-9) == pytest.approx(0.25)


def test_id_qube_low_noise_and_ultra_low_noise_differ_only_in_dark_counts():
    """IDQ ID Qube Free-Running brochure G.192.0151-PB-1.1 (July 2026): p. 4 gives the
    low-noise (LN) and ultra-low-noise (ULN) models the same rows except the noise and
    the cooling time. At the 25 % efficiency level, where the timing jitter is specified
    (150 ps typ, 200 ps max), their maximum dark count rates are 2 and 1.2 kHz, measured
    with a 50 µs dead time (note 3)."""
    ln = presets.get("idq/id-qube-nir-fr-ln")
    uln = presets.get("idq/id-qube-nir-fr-uln")
    assert (ln.part_number, uln.part_number) == (
        "ID Qube-NIR-FR-MMF-LN",
        "ID Qube-NIR-FR-MMF-ULN",
    )
    revision = "G.192.0151-PB-1.1, specifications as of July 2026"
    assert ln.source["revision"] == uln.source["revision"] == revision
    assert ln.source["document"] == uln.source["document"]
    differ = {
        k for k in ln.params | uln.params if ln.params.get(k) != uln.params.get(k)
    }
    assert differ == {"dark_count_rate"}
    dcr = {
        p.id: (p.params["dark_count_rate"], p.provenance["dark_count_rate"].kind)
        for p in (ln, uln)
    }
    assert dcr == {
        "idq/id-qube-nir-fr-ln": (2000.0, "datasheet-max"),
        "idq/id-qube-nir-fr-uln": (1200.0, "datasheet-max"),
    }
    # one spectral-response plot for both (p. 2)
    ln_pde, uln_pde = ln.curves["efficiency"], uln.curves["efficiency"]
    assert (uln_pde.wavelength, uln_pde.value) == (ln_pde.wavelength, ln_pde.value)
    for p in (ln, uln):
        # every value names its brochure page
        specs = [v.spec for v in p.provenance.values()]
        specs += [c.provenance.spec for c in p.curves.values()]
        assert all(s.startswith("p. ") for s in specs), p.id
        jitter, dead = p.provenance["timing_jitter"], p.provenance["dead_time"]
        assert (p.params["timing_jitter"], jitter.kind, jitter.max) == (
            150e-12,
            "datasheet-typ",
            200e-12,
        )
        # the dark counts' 50 µs, within the settable 100 ns … 80 µs
        assert (p.params["dead_time"], dead.kind, dead.min, dead.max) == (
            50e-6,
            "datasheet-condition",
            100e-9,
            80e-6,
        )

    spad = SinglePhotonDetector("SPD", preset="idq/id-qube-nir-fr-uln")
    assert spad.dark_count_rate == 1200.0 and spad.connector == "FC/PC"
    assert spad.efficiency_at(1550e-9) == pytest.approx(0.25)
    # the 20 % series' shape: 20.7 % at 1310 nm against 20.1 % at 1550 nm
    assert spad.efficiency_at(1310e-9) == pytest.approx(0.2575, abs=1e-4)
    assert spad.efficiency_at(850e-9) == 0.0  # outside 900–1700 nm


def test_id281_snspd_takes_the_brochures_guaranteed_ends_and_a_hard_dead_time():
    """IDQ ID281 brochure G.192.0112-PB-1.2 (June 2026), p. 4: peak SDE 80% to 95% (or
    better); maximum dark count rate < 100 cps to < 1 cps from 1300 nm to > 1600 nm;
    timing jitter (FWHM) typ. < 30 ps; standard SNSPDs > 30 Mcps with a recovery time
    typ. < 30 ns (to 50 % of the efficiency, note 2); output pulse > 5 ns."""
    p = presets.get("idq/id281-standard-1550")
    assert p.source["revision"] == "G.192.0112-PB-1.2, specifications as of June 2026"
    assert {k: v.kind for k, v in p.provenance.items()} == {
        "efficiency": "datasheet-min",
        "dark_count_rate": "datasheet-max",
        "dead_time": "derived",
        "timing_jitter": "datasheet-typ",
        "pulse_width": "datasheet-min",
    }
    assert all(v.spec.startswith("p. ") for v in p.provenance.values())
    # no connector named and no curve (the p. 2 bands are approximate); what the model
    # cannot do is kept
    assert "connector" not in p.params and not p.curves
    assert {"efficiency_recovery", "bias_current", "polarization"} <= set(p.unsupported)

    snspd = SinglePhotonDetector("SNSPD", preset="idq/id281-standard-1550")
    assert (snspd.efficiency, snspd.dark_count_rate, snspd.dead_time) == (
        0.80,
        100.0,
        30e-9,
    )
    assert (snspd.timing_jitter, snspd.pulse_width) == (30e-12, 5e-9)
    assert snspd.connector == "FC/UPC"  # phoq's default
    assert snspd.efficiency_at(1310e-9) == snspd.efficiency_at(1550e-9) == 0.80
    # saturating light: one click per 30 ns, 33 Mcps, the "> 30 Mcps" of the table
    n, dt = 3000, 1e-9
    field = np.full(n, math.sqrt(1e-6), dtype=complex)  # 1 µW, ≈ 7800 photons per ns
    clicks = snspd.detect({1550e-9: field}, dt, np.random.default_rng(1), n)
    assert clicks.sum() / (n * dt) == pytest.approx(1 / 30e-9, rel=1e-9)


def test_spad_presets_set_the_datasheets_10_ns_output_pulse():
    """PDM-IR datasheet v2.1, p. 3, PHOTON OUT: 10 ns typ. SPCM-AQRH Rev 2020-10,
    Table 1: 10 ns typ for the -1X pulse model, ± 2 ns by note 8. ID Qube brochure
    G.192.0151-PB-1.1, p. 4: Output pulse width 10 ns."""
    ids = ["mpd/pdm-ir", "mpd/pdm-ir-sff"]
    ids += ["idq/id-qube-nir-fr-ln", "idq/id-qube-nir-fr-uln"]
    ids += [f"excelitas/spcm-aqrh-1{grade}-fc" for grade in range(7)]
    for pid in ids:
        assert presets.get(pid).provenance["pulse_width"].kind == "datasheet-typ"
        assert SinglePhotonDetector(preset=pid).pulse_width == 10e-9, pid
    assert SinglePhotonDetector().pulse_width == 5e-9  # without a preset, as before


def test_curve_schema_is_checked(tmp_path):
    head = 'component = "SinglePhotonDetector"\ntitle = "x"\n[curves.efficiency]\n'
    p = presets.parse(
        head
        + 'from = "datasheet-graph"\nwavelength_nm = [500, 600]\nvalue = [0.1, 0.2]\n',
        path=tmp_path / "x.toml",
    )
    assert (
        p.curves["efficiency"](550e-9) == pytest.approx(0.15)
        and p.curves["efficiency"](700e-9) == 0.0
    )
    assert p.to_dict()["curves"]["efficiency"]["wavelength_nm"] == [
        500.0,
        600.0,
    ] and p.summary()["curves"] == ["efficiency"]
    for body, msg in (
        ("wavelength_nm = [600, 500]\nvalue = [0.1, 0.2]\n", "must increase"),
        ("wavelength_nm = [500, 600]\nvalue = [0.1]\n", "same length"),
        ("wavelength_nm = [500, 600]\nvalue = [0.1, -0.2]\n", "negative"),
        (
            'wavelength_nm = [500, 600]\nvalue = [0.1, 0.2]\nfrom = "guess"\n',
            "unknown provenance",
        ),
        (
            "wavelength_nm = [500, 600]\nvalue = [0.1, 0.2]\nunits = 1\n",
            "unexpected keys",
        ),
    ):
        with pytest.raises(PresetError, match=msg):
            presets.parse(head + body)
    wrong = presets.parse(
        head.replace("SinglePhotonDetector", "Laser")
        + "wavelength_nm = [1, 2]\nvalue = [1, 1]\n"
    )
    assert presets.check(wrong) == ["generic/x: Laser takes no 'efficiency' curve"]
    with pytest.raises(PresetError, match="takes no 'efficiency' curve"):
        presets.validate_for(wrong, Laser)


def test_modulator_limits_warn_on_bias_and_rf_drive():
    setup = Setup("limits")
    laser = setup.add(Laser("LD", mode="cw", power_dbm=0.0))
    im = setup.add(IntensityModulator("IM", preset="exail/mx-ln-05"))
    setup.chain(laser, im, Photodiode("PD"))
    psu = setup.add(DCPowerSupply("PSU", ch1_voltage=25.0))  # rated ±20 V
    setup.connect(psu.ch1, im.bias)
    texts = " ".join(w["text"] for w in report(setup)["warnings"])
    assert "IM bias is 25 V, outside the absolute maximum -20 … 20 V" in texts
    psu.ch1_voltage = 2.0
    fg = setup.add(
        ArbitraryWaveformGenerator("FG", ch1_shape="sine", ch1_freq=1e6, ch1_vpp=20.0)
    )
    setup.connect(fg.ch1, im.rf)  # 10 V peak; 28 dBm into 50 Ω is 7.9 V peak
    texts = " ".join(w["text"] for w in report(setup)["warnings"])
    assert "IM RF drive peaks at 10 V" in texts and "bias" not in texts


def test_user_preset_directories_and_overrides(tmp_path, monkeypatch):
    mine = tmp_path / "mylab"
    (mine / "acme").mkdir(parents=True)
    (mine / "acme" / "spd-1.toml").write_text(
        'component = "SinglePhotonDetector"\ntitle = "Acme SPD-1"\nvendor = "Acme'
        ' Photonics"\n[params]\nefficiency = { value = 0.8, from = "measured", spec ='
        ' "our unit at 1550 nm" }\ndead_time = 50e-9\n'
    )
    (mine / "pdm-ir.toml").write_text(  # a lab's measured override of a shipped id
        'id = "mpd/pdm-ir"\ncomponent = "SinglePhotonDetector"\ntitle = "MPD PDM-IR,'
        ' our unit"\nvendor = "Micro Photon Devices"\ntier ='
        ' "validated"\n[params]\nefficiency = { value = 0.21, from = "measured", spec ='
        ' "V_EX 5 V" }\n'
    )
    monkeypatch.setenv(presets.ENV_VAR, str(mine))
    presets.reload()
    try:
        p = presets.get("acme-photonics/spd-1")
        assert p.origin == "env" and p.provenance["efficiency"].kind == "measured"
        assert p.provenance["dead_time"].kind == "unstated"
        assert SinglePhotonDetector(preset="acme-photonics/spd-1").efficiency == 0.8
        assert presets.get("mpd/pdm-ir").tier == "validated"
        assert SinglePhotonDetector(preset="mpd/pdm-ir").efficiency == 0.21

        bad = tmp_path / "bad"
        bad.mkdir()
        (bad / "x.toml").write_text(
            'component = "Laser"\ntitle = "x"\n[params]\npower_dbm = { from ='
            ' "assumed" }\n'
        )
        with pytest.raises(PresetError, match="needs a 'value'"):
            presets.add_path(bad)
        assert bad not in presets.search_path()[-1]  # not kept after the failure
        (bad / "x.toml").write_text(
            'component = "Laser"\ntitle = "x"\n[params]\nnope = 1\n'
        )
        presets.add_path(bad)
        assert presets.check(presets.get("generic/x")) == [
            "generic/x: Laser has no parameter 'nope' (valid: "
            + ", ".join(Laser.param_specs())
            + ")"
        ]
        with pytest.raises(PresetError, match="no parameter 'nope'"):
            Laser(preset="generic/x")
    finally:
        monkeypatch.delenv(presets.ENV_VAR)
        presets._extra_paths.clear()
        presets.reload()
    with pytest.raises(KeyError):
        presets.get("acme-photonics/spd-1")


def test_api_lists_applies_and_reports_presets():
    from fastapi.testclient import TestClient
    from phoq.server import create_app

    s = Setup.from_chain(Laser(), Photodiode(), name="presets api")
    with TestClient(create_app(s, web_dir=None)) as c:
        types = {t["type"]: t for t in c.get("/api/catalog").json()["types"]}
        ids = [p["id"] for p in types["Photodiode"]["presets"]]
        assert ids[0] == "generic/photodiode" and {
            "thorlabs/det08cfc",
            "thorlabs/dx25cf",
        } <= set(ids)
        listed = c.get(
            "/api/presets", params={"component": "IntensityModulator", "q": "40 GHz"}
        ).json()["presets"]
        assert [p["id"] for p in listed] == ["exail/mx-ln-40"]
        full = c.get("/api/presets/exail/mx-ln-10").json()
        assert (
            full["provenance"]["v_pi_bias"]["kind"] == "datasheet-typ"
            and full["limits"]["optical_input_dbm"] == 20
        )
        assert "eo_bandwidth_hz" in full["unsupported"]
        assert c.get("/api/presets/nope/x").status_code == 404

        assert (
            c.post(
                "/api/components/PD-1/preset", json={"id": "thorlabs/det08cfc"}
            ).json()["preset"]
            == "thorlabs/det08cfc"
        )
        assert s["PD-1"].bandwidth == 5e9
        comp = next(
            x for x in c.get("/api/state").json()["components"] if x["name"] == "PD-1"
        )
        assert (
            comp["preset"] == "thorlabs/det08cfc"
            and comp["preset_title"] == "Thorlabs DET08CFC"
        )
        assert comp["provenance"]["saturation"]["kind"] == "derived" and comp[
            "limits"
        ] == {"output_voltage_v": 2.0}
        assert (
            c.post(
                "/api/components/PD-1/preset", json={"id": "exail/mx-ln-10"}
            ).status_code
            == 422
        )
        assert (
            c.post("/api/components/PD-1/preset", json={"id": "generic"}).json()[
                "preset"
            ]
            == "generic"
        )

        r = c.post(
            "/api/components",
            json={
                "type": "IntensityModulator",
                "preset": "exail/mx-ln-20",
                "params": {"extinction_db": 30},
            },
        )
        assert r.status_code == 200
        im = s[r.json()["name"]]
        assert (im.v_pi, im.extinction_db, im.preset) == (5.0, 30.0, "exail/mx-ln-20")


def test_cli_lists_and_shows_presets(capsys):
    from phoq.__main__ import main

    main(["presets"])
    out = capsys.readouterr().out
    assert "exail/mx-ln-10" in out and "generic/laser" in out and "problems" not in out
    main(["presets", "--type", "Photodiode", "--no-generic"])
    out = capsys.readouterr().out
    assert "thorlabs/dx25cf" in out and "exail" not in out and "generic" not in out
    main(["presets", "exail/mx-ln-10"])
    out = capsys.readouterr().out
    assert (
        "Vπ RF electrodes" in out
        and "optical_input_dbm" in out
        and "eo_bandwidth_hz" in out
    )
    main(["presets", "thorlabs/dx25hf"])
    out = capsys.readouterr().out
    assert (
        "responsivity         915–1625 nm, 28 points, peak 0.559 · datasheet graph"
        in out
    )
    main(["presets", "--paths"])
    assert "phoq" in capsys.readouterr().out
    main(["catalog"])
    assert "presets: generic, thorlabs/det08cfc" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["presets", "nope/x"])


def test_presets_written_for_the_old_beamsplitter_name_apply_to_couplers(
    tmp_path, monkeypatch
):
    mine = tmp_path / "old"
    mine.mkdir()
    (mine / "tap.toml").write_text(
        'component = "BeamSplitter"\ntitle = "Our 90:10 tap"\n[params]\nratio = 0.9\n'
    )
    monkeypatch.setenv(presets.ENV_VAR, str(mine))
    presets.reload()
    try:
        p = presets.get("generic/tap")
        assert p.component == "Coupler" and not presets.check(p)
        assert p in presets.search(Coupler) and p in presets.search("BeamSplitter")
        assert Coupler(preset="generic/tap").ratio == 0.9
    finally:
        monkeypatch.delenv(presets.ENV_VAR)
        presets.reload()


def test_presets_written_for_the_old_functiongenerator_name_apply_to_awgs(
    tmp_path, monkeypatch
):
    mine = tmp_path / "old"
    mine.mkdir()
    (mine / "sine.toml").write_text(
        'component = "FunctionGenerator"\ntitle = "Our 1 MHz sine"\n[params]\n'
        'ch1_shape = "sine"\nch1_freq = 1e6\n'
    )
    monkeypatch.setenv(presets.ENV_VAR, str(mine))
    presets.reload()
    try:
        p = presets.get("generic/sine")
        assert p.component == "ArbitraryWaveformGenerator" and not presets.check(p)
        assert p in presets.search(ArbitraryWaveformGenerator)
        assert p in presets.search("FunctionGenerator")
        awg = ArbitraryWaveformGenerator(preset="generic/sine")
        assert awg.ch1_shape == "sine" and awg.ch1_freq == 1e6
    finally:
        monkeypatch.delenv(presets.ENV_VAR)
        presets.reload()
