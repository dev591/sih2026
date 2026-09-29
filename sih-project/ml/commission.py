"""
Commission an engine installation: record its healthy baseline.

Every installation carries its own sensor offsets (probe tolerances,
transducer zero errors). Measured on 2026-09-24, a healthy engine sat up to
2σ away from zero on some channels, constant across altitude and throttle.
Condition-trend monitoring removes that the way the industry does — a baseline
recorded on a known-healthy engine at installation — so a fault is measured
as a change from THIS engine's normal, not from a textbook one.

The procedure is the same one the training data used (ml/data/mvem_dataset.py
`commission`): 45 s at each of three operating points, first 10 s of each
discarded. Stored with its date so the dashboard can show it.

    cd sih-project && python -m ml.commission --installation 42
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from ml.data.mvem_dataset import COMMISSION_POINTS, commission

PATH = Path(__file__).parent / "weights" / "v2" / "commissioning.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--installation", type=int, required=True)
    a = ap.parse_args()
    data = json.loads(PATH.read_text()) if PATH.exists() else {}
    data[str(a.installation)] = {
        "baseline": commission(a.installation),
        "recorded": time.strftime("%Y-%m-%d %H:%M:%S"),
        "procedure": {"points_ft_throttle": COMMISSION_POINTS, "seconds_each": 45, "discard_s": 10},
    }
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(data, indent=1))
    print(f"commissioned installation {a.installation} -> {PATH}")


if __name__ == "__main__":
    main()
