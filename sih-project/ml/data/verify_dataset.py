"""
Read the generated dataset BACK OFF DISK and fail loudly if anything is wrong.

Why this is a separate script and not a print at the end of generation: the
last dataset this project produced (data/_old_2026-09-11_corrupt/) reported a
successful write for all 11 files and every one of them is unreadable. A write
call returning cleanly proves nothing. This opens the files as a consumer
would, in chunks, and checks the properties the training code depends on.

Checks:
  1. Every split file exists, parses, and has the schema's columns in order.
  2. Row counts match flights.csv.
  3. NO FLIGHT APPEARS IN TWO SPLITS — the leakage check that matters, because
     windows from one flight share probe offsets, a fault ramp and an operating
     point.
  4. Label sanity: label_class_idx is 0 exactly when fault_active is 0; every
     class in the library is present; per-cylinder faults carry a cylinder.
  5. Numeric sanity: no NaN/inf in the residual columns or the operating point;
     RUL_h empty only where damage is zero.
  6. Class balance and healthy/faulted row counts per split, printed.

Run:
    cd sih-project
    python -m ml.data.verify_dataset --dir data/sim_v1
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

CHUNK = 50_000
RESIDUAL_N = [f"rho{i+1}_n" for i in range(11)]
OP_COLUMNS = ["rpm", "map_hPa", "iat_K", "fuel_flow_kgps", "turbo_rpm",
              "coolant_temp_C", "comp_out_p_hPa", "tas_mps", "altitude_ft"]

failures: list[str] = []
warnings_: list[str] = []


def fail(msg: str) -> None:
    failures.append(msg)
    print(f"  FAIL  {msg}")


def warn(msg: str) -> None:
    warnings_.append(msg)
    print(f"  warn  {msg}")


def ok(msg: str) -> None:
    print(f"  ok    {msg}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    args = ap.parse_args()
    d = Path(args.dir).resolve()

    print(f"Verifying {d}\n")
    schema = json.loads((d / "schema.json").read_text())
    expected_cols = [c["name"] for c in schema["columns"]]
    flights = pd.read_csv(d / "flights.csv")

    seen_flight_split: dict[str, set[str]] = {}
    totals: dict[str, int] = {}
    class_counts: dict[str, dict] = {}

    for split in ("train", "val", "test"):
        path = d / f"{split}.csv"
        print(f"[{split}] {path.name}")
        if not path.exists():
            fail(f"{path.name} missing")
            continue
        # An empty split file is a real failure at full scale (the stratifier
        # should place flights in all three), but it must be REPORTED rather
        # than raising EmptyDataError and aborting before the other checks —
        # including the leakage check — ever run.
        if path.stat().st_size == 0:
            fail(f"{path.name} is empty — no flights assigned to this split")
            totals[split] = 0
            class_counts[split] = {}
            print()
            continue

        n_rows = 0
        n_bad_label = 0
        n_nan_resid = 0
        n_nan_op = 0
        n_rul_bad = 0
        counts: dict[int, int] = {}
        cyl_missing = 0
        first_chunk = True

        for chunk in pd.read_csv(path, chunksize=CHUNK, low_memory=False):
            if first_chunk:
                if list(chunk.columns) != expected_cols:
                    missing = set(expected_cols) - set(chunk.columns)
                    extra = set(chunk.columns) - set(expected_cols)
                    fail(f"column mismatch; missing={sorted(missing)[:5]} extra={sorted(extra)[:5]}")
                else:
                    ok(f"{len(chunk.columns)} columns, order matches schema.json")
                first_chunk = False

            n_rows += len(chunk)
            for fid, sp in zip(chunk["flight_id"], chunk["split"]):
                seen_flight_split.setdefault(fid, set()).add(sp)

            # label_class_idx must be 0 exactly when the fault is not active
            bad = ((chunk["fault_active"] == 0) & (chunk["label_class_idx"] != 0)) | \
                  ((chunk["fault_active"] == 1) & (chunk["label_class_idx"] == 0))
            n_bad_label += int(bad.sum())

            r = chunk[RESIDUAL_N].to_numpy(dtype=float)
            n_nan_resid += int((~np.isfinite(r)).sum())
            o = chunk[OP_COLUMNS].to_numpy(dtype=float)
            n_nan_op += int((~np.isfinite(o)).sum())

            # RUL_h may be empty ONLY where no damage is accumulating.
            rul_empty = chunk["RUL_h"].isna()
            n_rul_bad += int((rul_empty & (chunk["dD_dt_per_s"] > 1e-15)).sum())

            for k, v in chunk["label_class_idx"].value_counts().items():
                counts[int(k)] = counts.get(int(k), 0) + int(v)

            per_cyl = chunk[chunk["fault_key"].isin(
                ["injector", "misfire", "detonation", "chtSensor", "egtSensor"])]
            cyl_missing += int((per_cyl["fault_cyl"] < 0).sum())

        totals[split] = n_rows
        class_counts[split] = counts
        ok(f"{n_rows:,} rows read back")
        if n_bad_label:
            fail(f"{n_bad_label} rows where label_class_idx disagrees with fault_active")
        else:
            ok("label_class_idx consistent with fault_active on every row")
        if n_nan_resid:
            fail(f"{n_nan_resid} non-finite values in normalised residuals")
        else:
            ok("residuals finite everywhere (rho3 excluded — structurally absent)")
        if n_nan_op:
            fail(f"{n_nan_op} non-finite values in operating-point columns")
        else:
            ok("operating point finite everywhere")
        if n_rul_bad:
            fail(f"{n_rul_bad} rows with damage accumulating but empty RUL_h")
        else:
            ok("RUL_h empty only where damage rate is zero")
        if cyl_missing:
            fail(f"{cyl_missing} per-cylinder fault rows with no cylinder assigned")
        print()

    # THE leakage check.
    leaked = {f: s for f, s in seen_flight_split.items() if len(s) > 1}
    print("[splits]")
    if leaked:
        fail(f"{len(leaked)} flight(s) appear in more than one split: "
             f"{list(leaked.items())[:3]}")
    else:
        ok(f"{len(seen_flight_split)} flights, each in exactly one split — no leakage")

    expected_rows = int(flights["rows"].sum())
    if sum(totals.values()) != expected_rows:
        fail(f"row total {sum(totals.values()):,} != flights.csv total {expected_rows:,}")
    else:
        ok(f"row total matches flights.csv ({expected_rows:,})")

    print("\n[class balance — label_class_idx, rows]")
    names = schema["class_names"]
    allc = sorted({k for c in class_counts.values() for k in c})
    print(f"  {'class':<32} {'train':>10} {'val':>8} {'test':>8}")
    for k in allc:
        nm = names[k] if k < len(names) else f"?{k}"
        print(f"  {nm:<32} {class_counts.get('train', {}).get(k, 0):>10,} "
              f"{class_counts.get('val', {}).get(k, 0):>8,} "
              f"{class_counts.get('test', {}).get(k, 0):>8,}")

    missing_classes = [names[i] for i in range(len(names))
                       if i not in class_counts.get("train", {})]
    if missing_classes:
        warn(f"classes absent from train split: {missing_classes}")

    print("\n[flights per split]")
    print(flights.groupby("split")["flight_id"].count().to_string())

    print()
    if failures:
        print(f"VERIFICATION FAILED — {len(failures)} problem(s):")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print(f"VERIFICATION PASSED ({len(warnings_)} warning(s))")


if __name__ == "__main__":
    main()
