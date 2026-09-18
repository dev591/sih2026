"""
ML inference pipeline — called once per 1 Hz health frame.

Loads pre-trained weights at startup; exposes one function:

    result = InferencePipeline.run(rho, damage_state, damage_rate_per_hr)

Returns anomaly, diagnosis, rul, novelty, twin_confidence, theta blocks
of pramana.health.v1 (docs/spec/telemetry-schema.md).

Residuals arrive normalised by sigma_vector.json (sigma units). This file
assumes that normalisation has already been applied by BE-1's parity layer.
Do NOT divide by sigma again here.

The pipeline never trains or modifies weights.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import numpy as np
import torch

from ml.m2_autoencoder.model import LSTMAutoencoder, PersistenceRule
from ml.m3_classifier.model import M3ClassifierModel, diagnose
from ml.m3_rul.model import RULModel, rul_report, physics_rul
from ml.novelty.projection import NoveltyProjector
from ml.ukf.filter import HealthUKF
from ml.incidence import N_RESIDUALS, FAULT_NAMES, AMBIGUITY_GROUPS

WEIGHTS_DIR = Path(__file__).parent / "weights"

# Significance calibration — re-derive from healthy_flights/*.parquet when available.
RHO_FLOOR = 1.5    # 99th pct of ‖ρ‖ on healthy engine
RHO_FULL  = 6.0    # "clearly anomalous" level

# M3 classifies the fault TYPE only — its class list carries no cylinder. The
# localisation is already in the residual vector: rho6..rho9 ARE the
# per-cylinder thermal deviations (telemetry-schema / residual-spec), so the
# cylinder carrying a single-cylinder fault is the one whose deviation
# dominates. Below this floor the deviations are inside healthy noise and we
# report no cylinder rather than naming one on a coin-flip.
PER_CYLINDER_FAULTS = {
    "injector_fouling",
    "ignition_misfire",
    "detonation",
    "egt_sensor_drift",
    "cht_sensor_drift",
}
CYL_LOCALISE_FLOOR_SIGMA = 1.0


class InferencePipeline:
    """
    Stateful inference wrapper. One instance per engine (A / B).
    Load once at startup; call run() at 1 Hz.
    """

    def __init__(
        self,
        window_len: int = 32,
        device: str = "cpu",
        weights_dir: Path = WEIGHTS_DIR,
    ):
        self.device     = device
        self.window_len = window_len
        self._rho_buf: list[list[float]] = []

        # ── M2 ───────────────────────────────────────────────────────────
        self.m2 = LSTMAutoencoder(
            input_dim=N_RESIDUALS, hidden_dim=64,
            latent_dim=16, seq_len=window_len
        )
        _load(self.m2, weights_dir / "m2_weights.pt", "M2", device)
        self.m2.eval()

        td = _load_json(weights_dir / "m2_threshold.json",
                        {"threshold": 0.5, "persistence": {"n": 4, "of": 5}})
        self.anomaly_threshold = float(td["threshold"])
        self.persistence = PersistenceRule(
            n=td["persistence"]["n"],
            m=td["persistence"]["of"],
        )

        # ── M3 classifier ─────────────────────────────────────────────────
        self.m3_cls = M3ClassifierModel(n_fault_classes=len(FAULT_NAMES),
                                        input_dim=N_RESIDUALS)
        _load(self.m3_cls, weights_dir / "m3_classifier_weights.pt", "M3-cls", device)
        self.m3_cls.eval()

        # ── M3 RUL ───────────────────────────────────────────────────────
        self.m3_rul = RULModel(input_dim=N_RESIDUALS, seq_len=window_len)
        _load(self.m3_rul, weights_dir / "m3_rul_weights.pt", "M3-rul", device)
        self.m3_rul.eval()

        # ── Novelty ──────────────────────────────────────────────────────
        self.projector = NoveltyProjector()
        nd = _load_json(weights_dir / "novelty_report.json", {"threshold_99p5": 0.42})
        self.novelty_threshold = float(nd.get("threshold_99p5", 0.42))

        # `reset()` is what actually creates self.ukf and clears rolling
        # state. It was previously only called in response to a client's
        # {"type": "reset"} websocket message, which meant a freshly
        # constructed InferencePipeline had no self.ukf at all — every
        # run() call raised AttributeError until a client happened to send
        # reset, and main.py silently caught that and served the physics-
        # rule stub instead, with no visible sign anything was wrong.
        self.reset()

    def reset(self) -> None:
        """
        Clear rolling state so a cleared fault reads as healthy on the NEXT
        tick, not ~window_len seconds later.

        Without this, `{"type": "reset"}` cleared the fault in the physics
        (twin/backend), but M2's window keeps the last `window_len` residual
        samples and M3 classifies over that same window — both still full of
        fault-contaminated residuals until enough clean ticks arrive to push
        them out. Measured: ~30s for anomaly score and diagnosis to settle back
        to healthy after a reset, on a 10-tick window. The demo's "Restart" /
        "Healthy" controls need this to be immediate.
        """
        self._rho_buf = []
        self.persistence.reset()

        # ── UKF ──────────────────────────────────────────────────────────
        self.ukf = HealthUKF()

        print(
            f"[Inference] Ready  "
            f"anomaly_thresh={self.anomaly_threshold:.4f}  "
            f"novelty_thresh={self.novelty_threshold:.4f}  "
            f"null_space_dim={self.projector.null_space_dim}"
        )

    def run(
        self,
        rho: list[Optional[float]],
        damage_state: float = 0.0,
        damage_rate_per_hr: float = 0.0,
        top_k: int = 2,
    ) -> dict:
        """
        Parameters
        ----------
        rho : 11-element list in sigma units (None for unavailable paths)
        damage_state : D ∈ [0,1] from BE-1's damage integrator
        damage_rate_per_hr : dD/dt from BE-1
        """
        rho_arr = np.array([x if x is not None else np.nan for x in rho], dtype=float)
        # TRAIN/SERVE CONTRACT: unavailable residuals (rho3 today: no Path 4 on
        # an unthrottled engine, so the parity code returns None) become 0.0
        # here. Every training loader MUST zero-fill NaN residuals identically —
        # data/sim_v1 carries rho3_n as NaN by design — or the model trains on
        # NaN / a different constant than it is served and quietly underperforms.
        # See ml/train.py.
        rho_nn  = np.where(np.isnan(rho_arr), 0.0, rho_arr).astype(np.float32)

        # Rolling window
        self._rho_buf.append(rho_nn.tolist())
        if len(self._rho_buf) > self.window_len:
            self._rho_buf.pop(0)

        pad      = self.window_len - len(self._rho_buf)
        window_np = np.array(
            [[0.0] * N_RESIDUALS] * pad + self._rho_buf,
            dtype=np.float32
        )
        window_t = torch.from_numpy(window_np).unsqueeze(0)  # (1, T, 11)

        # M2 — anomaly
        with torch.no_grad():
            ae_err = float(self.m2.reconstruction_error(window_t).item())
        alarm = self.persistence.update(ae_err > self.anomaly_threshold)

        # M3 — classifier
        with torch.no_grad():
            logits = self.m3_cls(window_t)
            probs  = torch.softmax(logits, dim=1).squeeze(0).cpu().numpy()

        # M3 — RUL
        with torch.no_grad():
            rul_q = self.m3_rul(window_t).squeeze(0).cpu().numpy()  # [p10,p50,p90]

        top_diag  = diagnose(rho_nn, probs)[:top_k]

        # M2 DETECTS, M3 ISOLATES — and M3's class list contains only faults,
        # so left ungated it always names one. On a healthy engine it returned
        # whichever fault fit the noise best (fuel_filter_clog at p=0.385 in
        # testing) while every limit sat green, which is precisely the frame
        # the demo opens on. Isolation is only meaningful once detection has
        # actually fired: until the N-of-M persistence rule is met, the
        # finding is 'healthy'. This is the architecture in ML-DEEP-DIVE §2,
        # not a display hack.
        if not alarm:
            top_diag = [{"fault": "healthy", "p_combined": 1.0 - min(ae_err / self.anomaly_threshold, 1.0),
                         "source": "classifier+matrix"}]

        top_fault = top_diag[0]["fault"] if top_diag else "unknown"

        instr = {"map_sensor_drift", "egt_sensor_drift",
                 "cht_sensor_drift", "lambda_sensor_drift"}
        is_sensor = top_fault in instr

        # STRUCTURAL ambiguity, checked against our own matrix rather than
        # assumed. detonation and egt_sensor_drift have identical incidence
        # rows, so no amount of confidence in the classifier justifies calling
        # one over the other — and that particular pair straddles the
        # component/instrumentation line the whole diagnosis rests on.
        # Asserting either would mean either clearing an engine that is
        # detonating, or pulling a serviceable one for a drifting thermocouple.
        # When the top hypothesis sits in an inseparable group we refuse the
        # call, flag it, and hand it to the active-diagnosis probe, which is
        # what residual-spec.md §5 exists for.
        group = AMBIGUITY_GROUPS.get(top_fault)
        structurally_ambiguous = group is not None

        numeric_ambiguous = bool(
            len(top_diag) >= 2 and
            abs(top_diag[0]["p_combined"] - top_diag[1]["p_combined"]) < 0.15
        )

        # Which cylinder is carrying the deviation. Engine-wide faults get an
        # explicit null: the schema declares `cylinder` on every hypothesis, and
        # a MISSING key reads as `undefined` on the GCS, which is not the same
        # thing as "this fault has no cylinder".
        cyl_dev = np.abs(rho_nn[5:9])
        localised_cyl = (
            int(np.argmax(cyl_dev))
            if cyl_dev.size and float(cyl_dev.max()) >= CYL_LOCALISE_FLOOR_SIGMA
            else None
        )

        diagnosis_block = {
            "top": [
                {"fault": d["fault"], "p": round(d["p_combined"], 3),
                 "cylinder": (
                     localised_cyl if d["fault"] in PER_CYLINDER_FAULTS else None
                 ),
                 "source": d["source"]}
                for d in top_diag
            ],
            # Only assert instrumentation when the structure can actually
            # support it. Inside an inseparable group it cannot.
            "is_sensor_fault": bool(is_sensor and not structurally_ambiguous),
            "ambiguous": bool(
                top_fault != "healthy"
                and (structurally_ambiguous or numeric_ambiguous)
            ),
        }

        if top_fault != "healthy" and structurally_ambiguous:
            diagnosis_block["inseparable_from"] = sorted(group - {top_fault})

        phys = physics_rul(damage_state, damage_rate_per_hr)
        rul_block = rul_report(
            p10=float(rul_q[0]), p50=float(rul_q[1]), p90=float(rul_q[2]),
            physics_h=phys, component=top_fault,
        )

        # UKF
        self.ukf.step(rho_arr)
        theta_block = self.ukf.health_frame()

        # Novelty
        nov      = self.projector.project(rho_arr)
        nov_blks = nov.to_health_frame(
            threshold=self.novelty_threshold,
            rho_floor=RHO_FLOOR, rho_full=RHO_FULL,
        )

        return {
            "anomaly":   {
                "score":       round(ae_err, 4),
                "threshold":   round(self.anomaly_threshold, 4),
                "persistence": {"n": self.persistence.hits,
                                "of": self.persistence.m,
                                "met": alarm},
            },
            "diagnosis": diagnosis_block,
            "rul":       rul_block,
            "theta":     theta_block,
            **nov_blks,
        }


# ── Helpers ───────────────────────────────────────────────────────────────

def _load(model: torch.nn.Module, path: Path, name: str, device: str) -> None:
    if path.exists():
        model.load_state_dict(
            torch.load(path, map_location=device, weights_only=True)
        )
        print(f"[Inference] Loaded {name} ← {path.name}")
    else:
        print(f"[Inference] WARNING: {name} weights not found at {path} — using untrained model")


def _load_json(path: Path, default: dict) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return default
