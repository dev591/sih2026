"""
Engine registry.

An engine is a profile — config/engine_<id>.yaml — and nothing else. Every
artifact derived from it (healthy σ, training data, trained models, measured
sensitivity, redline table, commissioning baselines) lives under that engine's
id, so onboarding a new engine never touches another engine's files, and no
code path names a specific engine.

    load_engine_profile("vrde_180")          # by id
    load_engine_profile("engine_vrde_180.yaml")  # by filename (older callers)
    artifact_dir("vrde_180", "weights")      # sih-project/ml/weights/vrde_180
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]           # sih-project/
CONFIG_DIR = ROOT / "config"
DEFAULT_ENGINE = os.environ.get("PRAMANA_ENGINE", "vrde_180")

_ARTIFACT_ROOTS = {
    "sigma": ROOT / "backend" / "config" / "engines",   # healthy σ vectors
    "data": ROOT / "data" / "engines",                  # generated training data
    "weights": ROOT / "ml" / "weights" / "engines",     # models, tables, baselines
}


def list_engines() -> dict[str, Path]:
    """{engine id: profile path} for every profile in config/."""
    out = {}
    for p in sorted(CONFIG_DIR.glob("engine_*.yaml")):
        with open(p, encoding="utf-8") as f:
            pid = (yaml.safe_load(f).get("profile") or {}).get("id")
        if pid:
            out[pid] = p
    return out


def profile_path(engine: str) -> Path:
    if engine.endswith((".yaml", ".yml")):
        return CONFIG_DIR / engine
    engines = list_engines()
    if engine not in engines:
        raise KeyError(f"no profile with id '{engine}' in {CONFIG_DIR} (have: {sorted(engines)})")
    return engines[engine]


def load_engine_profile(engine: str = DEFAULT_ENGINE) -> dict:
    with open(profile_path(engine), "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def engine_id(cfg: dict) -> str:
    return cfg["profile"]["id"]


def artifact_dir(engine: str, kind: str) -> Path:
    """Where one kind of derived artifact lives for one engine."""
    return _ARTIFACT_ROOTS[kind] / engine


def capabilities(cfg: dict) -> dict:
    """What this engine's installation can observe and what can fail on it —
    read from the profile, so nothing downstream assumes one engine's layout."""
    mv = cfg.get("mvem", {})
    cooling = mv.get("cooling") or {}
    return {
        "n_cyl": int(cfg["geometry"]["cylinders"]),
        "cycle": cfg.get("profile", {}).get("cycle", "diesel"),
        "aspiration": cfg.get("profile", {}).get("aspiration", "turbocharged"),
        "liquid_cooled": cooling.get("type") == "liquid",
        "intercooler": bool(cooling.get("intercooler")),
        "constant_speed_prop": cfg.get("propeller", {}).get("type") == "constant_speed",
    }
