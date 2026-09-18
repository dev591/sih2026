# QUARANTINED — do not use

These 11 parquet files were written 2026-09-11 02:11 by
`backend/parity/sigma_generator.py --with-dataset --with-faults`.

They are unusable for two independent reasons:

1. **Corrupt / unreadable.** Every data read fails with
   `OSError: Repetition level histogram size mismatch` under pyarrow 19.0.0.
   Probed column-by-column on `fault_runs/bearing_wear.parquet`: 0 of 29
   columns readable, all 29 fail. Footer metadata still parses (300 rows,
   29 cols per fault run; 270 rows, 23 cols healthy), which is why the files
   look fine in a directory listing. They were written by
   `parquet-cpp-arrow version 25.0.1` — a newer Arrow than the one installed.
2. **Physically obsolete.** They predate the 2179 cc geometry, the 1.69
   reduction gearbox, the constant-speed propeller and governor, the PI
   wastegate, the liquid cooling loop, and the intercooler.

Replaced by `data/sim_v1/` (see its MANIFEST.md). Delete this folder once
nobody is confused about which dataset is real.
