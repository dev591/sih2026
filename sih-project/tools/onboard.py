"""
Engine onboarding: turn a few specs into an engine profile YAML, and (optionally) run the whole
ML pipeline for that engine — safely, in an isolated copy of the project.

LOW LEVEL  (seconds to a couple of minutes, writes files only)
    generate_profile(spec)  -> YAML with per-field provenance (user / derived / assumed)
    check_profile(cfg)      -> validate_profile errors + warnings, plus a 60 s physics smoke test
    Output goes to runs/onboarding/<id>/ ; nothing in config/ or ml/weights/ is touched
    until install_profile(id) is called (and it refuses to overwrite an existing engine).

HIGH LEVEL (minutes to hours: noise levels -> dataset -> training -> prognostics -> commissioning)
    plan(id, size)          -> ordered steps with an ESTIMATED time (from speeds measured on this PC)
    run(plan)               -> executes the steps in runs/onboarding/<id>/workspace, a COPY of sih-project,
                               because the pipeline scripts write to fixed paths (ml/weights/v2, data/mvem_v1)
                               and would otherwise overwrite the live model.

Nothing here changes a running backend, injects a fault, or writes to the live model.
Honest limits are returned with every plan (see LIMITS).
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]                 # sih-project/
RUNS = ROOT.parent / "runs" / "onboarding"
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

LIMITS = [
    "Fields you did not give are copied from a base engine and labelled 'assumed' (physics constants such as "
    "compressor and turbine sizes are NOT rescaled to your engine), so results are provisional.",
    "The residual layout, ML and 3D model are built for 2 to 8 cylinders (all in-line for the 3D view).",
    "Fault behaviour is simulated by the twin; there is no real-engine validation for a new engine.",
]

# What the user can give. key -> (yaml path, description, unit)
SPEC_FIELDS = {
    "id": ("profile.id", "short engine id, lowercase, e.g. myengine_120", ""),
    "name": ("profile.name", "display name", ""),
    "cycle": ("profile.cycle", "diesel or spark_ignition", ""),
    "aspiration": ("profile.aspiration", "turbocharged or naturally_aspirated", ""),
    "cylinders": ("geometry.cylinders", "number of cylinders (2 to 8)", ""),
    "displacement_L": ("geometry.displacement_m3", "swept volume", "litres"),
    "bore_mm": ("geometry.bore_m", "bore", "mm"),
    "stroke_mm": ("geometry.stroke_m", "stroke", "mm"),
    "compression_ratio": ("geometry.compression_ratio", "compression ratio", ""),
    "rated_power_kW": ("ratings.rated_power_kW", "rated power", "kW"),
    "rated_speed_rpm": ("ratings.rated_speed_rpm", "rated crank speed", "rpm"),
    "max_continuous_rpm": ("ratings.max_continuous_rpm", "max continuous crank speed", "rpm"),
    "critical_altitude_ft": ("ratings.critical_altitude_ft", "altitude up to which rated power holds (0 for NA)", "ft"),
    "cht_limit_C": ("limits.cht_C", "cylinder head temperature redline", "C"),
}
REQUIRED_SPEC = ["id", "cycle", "aspiration", "cylinders", "displacement_L", "rated_power_kW", "rated_speed_rpm"]
UNIT = {"displacement_L": 1e-3, "bore_mm": 1e-3, "stroke_mm": 1e-3}          # -> SI in the YAML


# ------------------------------------------------------------------ low level
def _set(cfg: dict, path: str, value) -> None:
    node = cfg
    parts = path.split(".")
    for p in parts[:-1]:
        node = node.setdefault(p, {})
    node[parts[-1]] = value


def base_for(cycle: str) -> str:
    return "vrde_180" if cycle == "diesel" else "rotax_914"


def missing_spec(spec: dict) -> list[str]:
    return [k for k in REQUIRED_SPEC if spec.get(k) in (None, "")]


def generate_profile(spec: dict) -> tuple[dict, dict]:
    """(profile dict, provenance report). Raises ValueError if required fields are missing/invalid."""
    miss = missing_spec(spec)
    if miss:
        raise ValueError("missing: " + ", ".join(miss))
    if spec["cycle"] not in ("diesel", "spark_ignition"):
        raise ValueError("cycle must be diesel or spark_ignition")
    if spec["aspiration"] not in ("turbocharged", "naturally_aspirated"):
        raise ValueError("aspiration must be turbocharged or naturally_aspirated")
    if int(spec["cylinders"]) < 1:
        raise ValueError("cylinders must be >= 1")
    eid = str(spec["id"]).strip().lower()
    if not eid.replace("_", "").isalnum():
        raise ValueError("id must be letters, digits and underscores")

    from twin.profiles import load_engine_profile
    base_id = base_for(spec["cycle"])
    cfg = copy.deepcopy(load_engine_profile(base_id))
    fields: dict[str, dict] = {}

    for key, (path, _desc, _unit) in SPEC_FIELDS.items():
        if spec.get(key) in (None, ""):
            continue
        v = spec[key]
        v = float(v) * UNIT.get(key, 1.0) if key in UNIT else (int(v) if key == "cylinders" else v)
        _set(cfg, path, v)
        fields[path] = {"value": v, "source": "user", "assumed": False}
    if "ratings.rated_power_kW" in fields:
        _set(cfg, "ratings.rated_power_hp", round(float(spec["rated_power_kW"]) / 0.7457, 1))
    if spec.get("name") in (None, ""):
        _set(cfg, "profile.name", f"{eid} (generated)")

    # bore/stroke must agree with the swept volume you gave: keep the base bore, derive the stroke
    if "geometry.displacement_m3" in fields and "geometry.stroke_m" not in fields:
        import math
        bore = cfg["geometry"]["bore_m"] if "geometry.bore_m" not in fields else fields["geometry.bore_m"]["value"]
        stroke = fields["geometry.displacement_m3"]["value"] / (int(cfg["geometry"]["cylinders"]) * math.pi / 4 * bore ** 2)
        _set(cfg, "geometry.stroke_m", round(stroke, 5))
        fields["geometry.stroke_m"] = {"value": round(stroke, 5), "assumed": True,
                                       "source": f"derived: displacement / (cylinders x pi/4 x bore^2), bore {bore} m from base"}

    # The propeller governor holds PROP speed, so crank speed = prop speed x gear ratio. Without this a slower or
    # faster engine would be run at the base engine's crank speed — above its own stated limit (seen: a 3,200 rpm
    # engine held at 3,589 rpm). Scale the gear ratio by (your rated speed / base rated speed).
    from twin.profiles import load_engine_profile as _lep
    _b = _lep(base_id)
    base_rated = (_b.get("ratings") or {}).get("rated_speed_rpm") or (_b.get("ratings") or {}).get("max_continuous_rpm")
    base_gear = (_b.get("geometry") or {}).get("gear_ratio")
    if "ratings.rated_speed_rpm" in fields and base_rated and base_gear:
        g = round(float(base_gear) * float(fields["ratings.rated_speed_rpm"]["value"]) / float(base_rated), 4)
        _set(cfg, "geometry.gear_ratio", g)
        _set(cfg, "propeller.gear_ratio", g)
        note = f"derived: {base_id} gear ratio {base_gear} x (rated speed / {base_id} rated speed {base_rated}) so cruise crank speed scales with rated speed"
        fields["geometry.gear_ratio"] = {"value": g, "source": note, "assumed": True}
        fields["propeller.gear_ratio"] = {"value": g, "source": note, "assumed": True}
    if "ratings.max_continuous_rpm" not in fields and "ratings.rated_speed_rpm" in fields:
        # not given: keep the base engine's ratio of max-continuous to rated speed
        _br = (_b.get("ratings") or {})
        if _br.get("max_continuous_rpm") and _br.get("rated_speed_rpm"):
            m = round(float(fields["ratings.rated_speed_rpm"]["value"]) * _br["max_continuous_rpm"] / _br["rated_speed_rpm"])
            _set(cfg, "ratings.max_continuous_rpm", m)
            fields["ratings.max_continuous_rpm"] = {"value": m, "assumed": True,
                "source": f"derived: {base_id} ratio of max-continuous to rated speed applied to your rated speed"}

    # rated fuel flow follows rated power at the base engine's own brake-specific fuel consumption
    bp = cfg["ratings"].get("rated_power_kW")
    b0 = load_engine_profile(base_id)
    p0, f0 = b0["ratings"].get("rated_power_kW") or b0["ratings"].get("max_continuous_power_kW"), b0["fuel"].get("rated_fuel_flow_kgps")
    if bp and p0 and f0:
        _set(cfg, "fuel.rated_fuel_flow_kgps", round(f0 * float(bp) / float(p0), 6))
        fields["fuel.rated_fuel_flow_kgps"] = {"value": cfg["fuel"]["rated_fuel_flow_kgps"],
                                               "source": f"derived: {base_id} fuel flow x (rated power / {base_id} power)", "assumed": True}

    # everything not given by the user is inherited -> conservative section-level provenance
    given_sections = {p.rsplit(".", 1)[0] for p, f in fields.items() if f["source"] == "user"}
    for sec, block in cfg.items():
        if isinstance(block, dict) and sec not in ("profile",):
            block["provenance"] = "assumed"
    cfg.setdefault("profile", {})["id"] = eid
    cfg["profile"]["provenance"] = "user_specified"
    cfg["profile"]["notes"] = (f"GENERATED by tools/onboard.py on {dt.date.today()}. Fields not listed under "
                               f"generated.fields were copied from '{base_id}' and are assumed, not published for this engine.")
    cfg["generated"] = {"by": "tools/onboard.py", "date": str(dt.date.today()), "base_profile": base_id,
                        "fields": fields, "limits": LIMITS}
    return cfg, {"base": base_id, "user_fields": [p for p, f in fields.items() if f["source"] == "user"],
                 "derived_fields": [p for p, f in fields.items() if f["source"] != "user"]}


def check_profile(cfg: dict, smoke_seconds: int = 60) -> dict:
    """validate_profile errors/warnings + a short steady-state run of the physics with this profile."""
    from twin.validate_profile import validate
    errors, warnings = validate(cfg)
    out = {"errors": [e.strip() for e in errors], "warnings": [w.strip() for w in warnings], "smoke": None}
    n_cyl = int((cfg.get("geometry") or {}).get("cylinders") or 0)
    if not 2 <= n_cyl <= 8:
        out["errors"].append(f"UNSUPPORTED  geometry.cylinders = {n_cyl}: the residual layout, ML and 3D model are built for 2 to 8 cylinders")
        return out
    if errors:
        return out
    try:
        import numpy as np
        from twin.session import EngineSession
        b = ROOT / "backend" / "config"
        sig = json.loads((b / "sigma_vector.json").read_text())
        sige = json.loads((b / "sigma_ext.json").read_text())["sigma"]
        t0 = time.time()
        s = EngineSession(cfg, sig, sige, with_engine_b=False, altitude_fn=lambda t, c: 5000.0,
                          throttle_fn=lambda t: 80.0, tas_fn=lambda t, c: 61.2)
        last = None
        for _ in range(smoke_seconds):
            last = s.step()
        m = last.measuredA
        finite = all(np.isfinite(v) for v in [m["rpm"], m["map_hPa"], m["fuel_flow_kgps"]])
        out["smoke"] = {"ok": bool(finite), "seconds": smoke_seconds, "wall_s": round(time.time() - t0, 1),
                        "rpm": round(float(m["rpm"])), "map_hPa": round(float(m["map_hPa"])),
                        "brake_kW": round(float(last.out_twinA["brake_power_kW"]), 1),
                        "note": "sanity only: sigma vectors here are the base engine's, not this engine's"}
    except Exception as e:  # a profile that cannot run is a finding, not a crash
        out["smoke"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return out


def write_low_level(spec: dict) -> dict:
    """Generate + check + write files. Returns paths and the check result."""
    cfg, prov = generate_profile(spec)
    eid = cfg["profile"]["id"]
    d = RUNS / eid
    d.mkdir(parents=True, exist_ok=True)
    yml = d / f"engine_{eid}.yaml"
    yml.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
    chk = check_profile(cfg)
    # the validator reads section-level provenance only; fields the user gave are not assumed
    user = set(prov["user_fields"])
    chk["warnings"] = [w for w in chk["warnings"] if not any(f"  {p}  " in w + "  " or w.split()[1] == p for p in user)]
    chk["user_specified"] = sorted(user)
    (d / "validation.json").write_text(json.dumps({"provenance": prov, **chk, "limits": LIMITS}, indent=1), encoding="utf-8")
    return {"dir": str(d), "yaml": str(yml), "validation": str(d / "validation.json"),
            "provenance": prov, "check": chk}


def install_profile(eid: str) -> Path:
    """Copy a generated profile into config/ so the backend/pipeline can select it. Never overwrites."""
    src = RUNS / eid / f"engine_{eid}.yaml"
    dst = ROOT / "config" / f"engine_{eid}.yaml"
    if not src.exists():
        raise FileNotFoundError(f"no generated profile for '{eid}' — generate it first")
    if dst.exists():
        raise FileExistsError(f"config/engine_{eid}.yaml already exists; refusing to overwrite an engine")
    shutil.copy2(src, dst)
    return dst


def _require_supported(eid: str) -> None:
    src = ROOT / "config" / f"engine_{eid}.yaml"
    src = src if src.exists() else RUNS / eid / f"engine_{eid}.yaml"
    if src.exists():
        n = int(((yaml.safe_load(src.read_text(encoding="utf-8")) or {}).get("geometry") or {}).get("cylinders") or 0)
        if not 2 <= n <= 8:
            raise ValueError(f"engine '{eid}' has {n} cylinders; the residual layout, ML and 3D model are built for 2 to 8 cylinders")


def install_engine(eid: str, log=print) -> dict:
    """Make a generated engine selectable in the app: profile -> config/, noise levels -> backend/config/engines/<id>
    (generated if missing), trained model -> ml/weights/engines/<id> (only if a high-level run produced one).
    Only ever ADDS files under the new engine's own id; refuses to touch an engine that already has any."""
    done = {"profile": None, "sigma": None, "model": None}
    _require_supported(eid)
    cfg_path = ROOT / "config" / f"engine_{eid}.yaml"
    if cfg_path.exists():
        done["profile"] = "already installed"
    else:
        done["profile"] = str(install_profile(eid))
    sig_dir = ROOT / "backend" / "config" / "engines" / eid
    if (sig_dir / "sigma_vector.json").exists():
        done["sigma"] = "already present"
    else:
        ws_sig = RUNS / eid / "workspace" / "sih-project" / "backend" / "config" / "engines" / eid
        if (ws_sig / "sigma_vector.json").exists():
            shutil.copytree(ws_sig, sig_dir, dirs_exist_ok=True)
            done["sigma"] = "copied from the training run"
        else:
            log("generating healthy noise levels (about 35 s)")
            rc = subprocess.call([sys.executable, "-m", "parity.sigma_generator", "--engine", eid], cwd=ROOT / "backend",
                                 env={**os.environ, "PYTHONUTF8": "1"}, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
            if rc != 0:
                raise RuntimeError("noise-level generation failed (sigma_generator)")
            done["sigma"] = "generated"
    ws_w = RUNS / eid / "workspace" / "sih-project" / "ml" / "weights"
    dst = ROOT / "ml" / "weights" / "engines" / eid
    if (dst / "config.json").exists():
        done["model"] = "already present"
    elif (ws_w / "v2" / "config.json").exists():
        dst.mkdir(parents=True, exist_ok=True)
        for f in (ws_w / "v2").iterdir():
            if f.is_file():
                shutil.copy2(f, dst / f.name)
        if (ws_w / "redline_table.json").exists():
            shutil.copy2(ws_w / "redline_table.json", dst / "redline_table.json")
        done["model"] = "installed from the training run"
    else:
        done["model"] = "none (no high-level run yet): the app will show 'no diagnosis' for this engine"
    return done


# ------------------------------------------------------------------ high level
# Measured on this PC (recording PC, 24 cores, RTX 4500 Ada):
#   dataset: 4000 runs x 300 s in 3534 s on 22 workers  -> 19.4 core-seconds per 300 s run
#   training: 103 s for the 4000-run set (GPU)
CORE_S_PER_SIM_S = (3534 * 22) / (4000 * 300)
TRAIN_S_PER_RUN = 103 / 4000
SIZES = {"quick": (30, 20, 240), "standard": (80, 50, 300)}      # installations, runs each, seconds
# measured on this PC in a real "quick" run of a generated engine (demo_120, 600 runs, 890 s in total):
#   sigma 33 s, sensitivity 29 s, prognostics 362 s, commission 5 s, training 22 s, dataset 439 s
FIXED_S = {"sigma": 35, "sensitivity": 30, "prognostics": 365, "commission": 10, "copy": 5}


def _fmt(s: float) -> str:
    s = int(round(s))
    return f"{s} s" if s < 90 else (f"{s/60:.0f} min" if s < 5400 else f"{s/3600:.1f} h")


def plan(eid: str, size: str = "quick", device: str | None = None, workers: int | None = None) -> dict:
    inst, per, secs = SIZES[size]
    workers = workers or max(1, (os.cpu_count() or 4) - 2)
    runs = inst * per
    ds = runs * secs * CORE_S_PER_SIM_S / workers + 30
    tr = 15 + runs * TRAIN_S_PER_RUN * (1 if (device or "cuda") == "cuda" else 8)
    steps = [
        {"name": "copy", "desc": "isolated workspace copy of the project (live model untouched)", "est_s": FIXED_S["copy"], "cmd": None},
        {"name": "sigma", "desc": "healthy noise levels for this engine", "est_s": FIXED_S["sigma"],
         "cmd": [sys.executable, "-m", "parity.sigma_generator", "--engine", eid], "cwd": "backend"},
        {"name": "dataset", "desc": f"simulated training data: {inst} installations x {per} runs x {secs} s on {workers} workers",
         "est_s": ds, "cmd": [sys.executable, "-u", "-m", "ml.data.mvem_dataset", "--installations", str(inst),
                              "--runs-per-installation", str(per), "--seconds", str(secs), "--workers", str(workers)]},
        {"name": "train", "desc": "train anomaly gate + fault classifier", "est_s": tr,
         "cmd": [sys.executable, "-m", "ml.train_mvem", "--device", device or "cuda", "--threshold-pct", "99.9"]},
        {"name": "sensitivity", "desc": "measure fault sensitivity (jacobian)", "est_s": FIXED_S["sensitivity"],
         "cmd": [sys.executable, "-m", "ml.ukf.sensitivity"]},
        {"name": "prognostics", "desc": "redline / remaining-life table", "est_s": FIXED_S["prognostics"],
         "cmd": [sys.executable, "-m", "ml.prognostics"]},
        {"name": "commission", "desc": "healthy baseline for installation 42", "est_s": FIXED_S["commission"],
         "cmd": [sys.executable, "-m", "ml.commission", "--installation", "42"]},
    ]
    tot = sum(s["est_s"] for s in steps)
    return {"engine": eid, "size": size, "steps": steps, "total_s": tot, "total": _fmt(tot),
            "basis": "estimated from speeds measured on this PC (4000 runs took 59 min on 22 workers; training took 103 s); "
                     "the fixed steps were measured once, on a 600-run quick run",
            "limits": LIMITS}


def describe(p: dict) -> str:
    lines = [f"Plan for '{p['engine']}' ({p['size']}): about {p['total']} in total"]
    for i, s in enumerate(p["steps"], 1):
        lines.append(f"  {i}. {s['desc']} — about {_fmt(s['est_s'])}")
    lines.append("Estimate basis: " + p["basis"] + ".")
    return "\n".join(lines)


def _workspace(eid: str) -> Path:
    ws = RUNS / eid / "workspace" / "sih-project"
    if ws.exists():
        shutil.rmtree(ws.parent)
    skip_paths = {"data", "docs", "frontend", "ml/weights", "tools/codemap.json"}   # top-level only: ml/data is CODE

    def ig(d, names):
        rel = Path(d).relative_to(ROOT)
        return {n for n in names if n in ("node_modules", "__pycache__", "dist") or (rel / n).as_posix() in skip_paths}
    shutil.copytree(ROOT, ws, ignore=ig)
    (ws / "ml" / "weights").mkdir(parents=True, exist_ok=True)
    return ws


def run(p: dict, log=print) -> dict:
    """Execute a plan in an isolated workspace. Call only after the user confirmed the plan."""
    eid = p["engine"]
    _require_supported(eid)
    prof = ROOT / "config" / f"engine_{eid}.yaml"
    src = prof if prof.exists() else RUNS / eid / f"engine_{eid}.yaml"
    if not src.exists():
        raise FileNotFoundError(f"profile for '{eid}' not found; generate it first")
    t0, results = time.time(), []
    ws = _workspace(eid)
    shutil.copy2(src, ws / "config" / f"engine_{eid}.yaml")
    env = {**os.environ, "PRAMANA_ENGINE": eid, "PYTHONUTF8": "1"}
    logdir = RUNS / eid / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    for s in p["steps"]:
        if not s["cmd"]:
            results.append({"step": s["name"], "ok": True, "s": 0})
            continue
        st = time.time()
        log(f"[{s['name']}] {s['desc']} (est {_fmt(s['est_s'])})")
        with open(logdir / f"{s['name']}.log", "w", encoding="utf-8") as lf:
            rc = subprocess.call(s["cmd"], cwd=ws / s.get("cwd", "."), env=env, stdout=lf, stderr=subprocess.STDOUT)
        r = {"step": s["name"], "ok": rc == 0, "s": round(time.time() - st, 1), "log": str(logdir / f"{s['name']}.log")}
        results.append(r)
        log(f"[{s['name']}] {'done' if rc == 0 else 'FAILED (exit %d)' % rc} in {_fmt(r['s'])}")
        if rc != 0:
            break
    rep = ws / "ml" / "weights" / "v2" / "report.json"
    out = {"engine": eid, "workspace": str(ws), "steps": results, "wall_s": round(time.time() - t0, 1),
           "report": str(rep) if rep.exists() else None}
    (RUNS / eid / "run_result.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="engine onboarding (low/high level)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate"); g.add_argument("--spec", required=True, help="JSON file or JSON string")
    pl = sub.add_parser("plan"); pl.add_argument("--engine", required=True); pl.add_argument("--size", default="quick", choices=SIZES)
    ru = sub.add_parser("run"); ru.add_argument("--engine", required=True); ru.add_argument("--size", default="quick", choices=SIZES)
    ru.add_argument("--yes", action="store_true", help="confirm: run the plan (it is slow)")
    a = ap.parse_args()
    if a.cmd == "generate":
        s = json.loads(Path(a.spec).read_text() if Path(a.spec).exists() else a.spec)
        print(json.dumps(write_low_level(s), indent=1))
    elif a.cmd == "plan":
        print(describe(plan(a.engine, a.size)))
    else:
        pp = plan(a.engine, a.size)
        print(describe(pp))
        if not a.yes:
            sys.exit("not running: add --yes to confirm")
        print(json.dumps(run(pp), indent=1))
