"""
The model input vector — ONE definition, used by training and live inference,
sized by the engine profile (cylinder count), never by a constant.

Built from the σ-normalised parity residuals (backend/parity/residuals.py):
ρ₁ ρ₂ ρ₄ ρ₅ ρ₁₀ ρ₁₁ plus the extended residuals. Left out on purpose:
  ρ₃   — structurally unavailable on an unthrottled engine (always None)
  ρ₆–ρ₉ — a fixed 0.45·CHT + 0.55·EGT blend; the separate egt_dev/cht_dev
          channels carry the same information without destroying the
          difference between a CHT and an EGT probe fault.
A channel an installation has no sensor for becomes 0.0 — identically in
training and in serving.
"""
from __future__ import annotations

BASE_NAMES = ["rho1", "rho2", "rho4", "rho5", "rho10", "rho11"]
BASE_IDX = [0, 1, 3, 4, 9, 10]
COMMON_EXTRA = ["coolant", "head_mean", "egt_mean", "oil_temp", "fuel_delivery", "boost_path"]


def extra_names(n_cyl: int) -> list[str]:
    return ([f"egt_dev_{i + 1}" for i in range(n_cyl)]
            + [f"cht_dev_{i + 1}" for i in range(n_cyl)] + COMMON_EXTRA)


def feature_names(n_cyl: int) -> list[str]:
    return BASE_NAMES + extra_names(n_cyl)


def egt_dev(n_cyl: int) -> slice:
    return slice(len(BASE_NAMES), len(BASE_NAMES) + n_cyl)


def cht_dev(n_cyl: int) -> slice:
    return slice(len(BASE_NAMES) + n_cyl, len(BASE_NAMES) + 2 * n_cyl)


def feature_vector(rho: list, rho_ext: list) -> list[float]:
    vals = [rho[i] for i in BASE_IDX] + list(rho_ext)
    return [0.0 if v is None else float(v) for v in vals]


# 4-cylinder aliases — kept only so the dataset job already running against
# this module finishes; everything new calls the functions above.
EXTRA_NAMES = extra_names(4)
FEATURE_NAMES = feature_names(4)
N_FEATURES = len(FEATURE_NAMES)
EGT_DEV = egt_dev(4)
CHT_DEV = cht_dev(4)
