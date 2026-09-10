# HPC migration handoff — N-CMAPSS real-data validation

Read this first if you're picking up the N-CMAPSS benchmark on the college
HPC (or any machine with more RAM than a 16GB laptop). Context below is
everything that session needs.

## The bug this is fixing

`sih-project/ml/benchmarks/ncmapss_baseline.ipynb`'s committed results
(RMSE=49.43 cycles, NASA score=20317658.0, within-±10%=39.4%,
α–λ accuracy=0.394, false alarm proxy=0.104) came from a **synthetic
fallback**, not real NASA data. The notebook's own captured output says so
plainly if you look:

```
File not found: data/data_set/N-CMAPSS_DS01-005.h5
Generating synthetic stand-in so the notebook still executes...
REAL_DATA = False — any score below is only quotable to judges when this is True.
```

Root cause: the notebook's relative path to the h5 file only resolves when
the kernel's working directory is `ml/benchmarks/` itself. Whoever executed
it originally had a different cwd, so it silently fell back to synthetic
data. This is the slide meant to answer "why should we trust your synthetic
data?" — right now it doesn't actually prove that, because it never ran on
real data.

## What's already done (2026-09-11, on a borrowed M5/16GB Mac)

- All 10 real N-CMAPSS DS0x files (~27GB uncompressed) were extracted from
  `sih-project/ml/benchmarks/data/outer/17. Turbofan Engine Degradation
  Simulation Data Set 2/data_set.zip` into
  `sih-project/ml/benchmarks/data/data_set/`. If you're continuing from the
  same external SSD, this extraction should already be there — check before
  re-extracting (`ls sih-project/ml/benchmarks/data/data_set/*.h5`, should
  show 10 files, largest ~3.75GB).
- Real row counts were measured directly (not guessed): DS01's dev split
  alone is 4.9M rows across 6 units; DS04's dev split is 6.4M rows across 6
  units. `X_s` is stored as **float64** on disk. Naive full-resolution
  windowing (window=32) across all 10 files would produce tens of GB of
  windowed arrays — this is almost certainly why the *original* M3/8GB
  laptop OOM-crashed on this same dataset.

## The fix (patch scripts, not yet applied to the committed notebook)

Two problems, two fixes, both validated with real dry-run numbers on the M5
before the machine got pulled out from under the job (not a crash — the
laptop's *other* open apps had already used 15GB/16GB before the job even
started; work was paused for exactly this reason, see
`sih-project/docs/team/` context or ask the user).

**1. Load one DS0x file at a time, not all ten at once.** Load a file's
dev+test splits, immediately window them (`make_windows`), discard the raw
per-file DataFrame, then move to the next file. Peak memory is bounded by
one file's raw data plus the running list of small windowed arrays, not all
ten files' raw data simultaneously.

**2. Use `stride` on the h5py read.** `load_ncmapss()` already supports this
— `sl = slice(lo, hi, stride)` is a *native strided HDF5 read*, so it cuts
disk I/O and memory, not just array size after the fact. Measured on the M5
(dry run, DS04, the biggest file, `max_units=10`):

| stride | DS04 dev+test windowed size | peak RSS |
|---|---|---|
| 50 | ~200K windows | ~0.65GB |
| 20 | ~499K windows | ~1.1GB |

Extrapolated across all 10 files by disk-size share: stride=20 → ~7GB
combined, stride=50 → ~2.6GB combined. **On HPC-grade RAM you have real
headroom to go denser than stride=20** — try stride=10 or even stride=5 if
you want more real data per window, re-measure peak RSS the same way before
committing to a full run.

**3. Batch size / epoch count.** Benchmarked on the M5 (Apple Silicon,
small ~42K-param GRU model): **CPU beat MPS** here — GPU dispatch overhead
dominated for a model this small (CPU: ~16.9k samples/s at batch=512; MPS:
~10k/s at the same batch, worse at smaller batches). On HPC, if there's a
real discrete GPU, re-benchmark rather than assuming GPU wins — this model
is small enough that it might not. With stride=20 across all 10 files,
total windowed samples land around ~3M+ (vs. the ~11K the notebook's
original `EPOCHS=40` was designed around) — one epoch now is ~250x more
gradient updates, so **EPOCHS=15 with BATCH=512** was the M5's working
number; adjust once you know the HPC's actual throughput.

## What to actually do

1. Extract the dataset if not already present (see path above).
2. Rewrite `ncmapss_baseline.ipynb`'s loader cell (`load_ncmapss` call site)
   to loop over all 10 `N-CMAPSS_DS*.h5` files in `data/data_set/`, windowing
   and discarding each file's raw DataFrame before moving to the next —
   don't concatenate raw per-file DataFrames across files before windowing,
   that's the memory-unsafe version.
3. Pick a `stride` based on available RAM — start with a dry run on the
   biggest file only (`N-CMAPSS_DS04.h5` was biggest on this dataset,
   3.75GB) to measure real peak RSS before committing to a full 10-file run.
4. Run from `ml/benchmarks/` as the working directory (or fix the path to be
   robust to cwd — this is the actual root cause of the original bug).
5. Confirm `REAL_DATA = True` prints, and get honest RMSE/NASA-score/etc.
   numbers to replace the synthetic ones on the slide.
6. Update `sih-project/ncmapss_rul_result.png` and commit both the notebook
   and the image with a commit message that says plainly these are now real
   NASA-data numbers, not synthetic.

## Demo day

**2026-09-12.** This is the "why trust synthetic data" slide — high value,
but don't let a multi-hour training run eat all your remaining time. If
early epochs already show sane loss curves and REAL_DATA=True, that's more
important than squeezing out the absolute best score.
