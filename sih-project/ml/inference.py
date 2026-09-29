"""
Live inference — one instance per engine, run once per simulated second.

Everything here is computed from what the twin observes. Nothing reads the
injected fault configuration: the frame the dashboard shows is what the
system inferred, and the fault a judge injected is only ever revealed by the
separate "Reveal" control.

Pipeline, per tick:
  features  ρ + extended residuals (ml/features.py), minus this installation's
            commissioning baseline
  M2        LSTM autoencoder over a 32 s window → reconstruction error →
            N-of-M persistence → alarm            (detects; never names)
  M3 v2     only when alarmed: fault probabilities + per-fault severity
  KF        health parameters θ on a measured engine-model sensitivity
  novelty   how much of the current deviation no library fault explains
  prognosis severity trend → time-to-redline → Monte Carlo (M = 200) →
            RUL p10/p50/p90 and P(complete) for continue / derate / RTB

Models and every threshold come from ml/weights/v2 (python -m ml.train_mvem);
the numbers they were validated to are in ml/weights/v2/report.json.
"""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path

import numpy as np
import torch

from ml.features import CHT_DEV, EGT_DEV, FEATURE_NAMES, N_FEATURES, feature_vector
from ml.m2_autoencoder.model import LSTMAutoencoder
from ml.m3_classifier.model import M3v2, compress
from ml.prognostics import ENGINE_FAULTS, SENSOR_FAULTS, MissionMonteCarlo
from ml.ukf.filter import HealthKF, THETA_NAMES

WEIGHTS = Path(__file__).parent / "weights" / "v2"
PER_CYL = {"injector_fouling", "injection_misfire", "detonation",
           "egt_sensor_drift", "cht_sensor_drift"}
# Which health parameter each engine fault degrades — lets the physics head
# (the Kalman filter) produce its own severity estimate, independent of M3.
THETA_OF_FAULT = {
    "injector_fouling": ("cd_inj", -1), "turbo_degradation": ("eta_c_scale", -1),
    "cooling_fouling": ("rad_eff_scale", -1), "coolant_pump_degradation": ("cool_pump_scale", -1),
    "bearing_wear": ("f_fric_scale", +1), "ring_wear": ("eta_v_scale", -1),
    "oil_pump_wear": ("oil_pump_scale", -1), "fuel_filter_clog": ("fuel_rail_scale", -1),
}
TREND_S = 60           # seconds of severity history the degradation rate is fitted on
AMBIG_GAP = 0.15       # top-2 probability gap below which the call is ambiguous


class InferencePipeline:
    def __init__(self, installation: int, device: str = "cpu", weights_dir: Path = WEIGHTS):
        cfg = json.loads((weights_dir / "config.json").read_text())
        self.cfg = cfg
        self.classes: list[str] = cfg["classes"]
        self.window = int(cfg["window"])
        comm_path = weights_dir / "commissioning.json"
        comm = json.loads(comm_path.read_text()) if comm_path.exists() else {}
        entry = comm.get(str(installation))
        self.commissioned = entry["recorded"] if entry else None
        base = entry["baseline"] if entry else cfg["baselines"].get(str(installation))
        if base is None:
            raise RuntimeError(
                f"installation {installation} has no commissioning baseline — run "
                f"`python -m ml.commission --installation {installation}` first. "
                "Serving diagnoses without one would mean treating this engine's "
                "sensor offsets as faults.")
        self.baseline = np.asarray(base, np.float32)
        self.device = device
        self.m2 = LSTMAutoencoder(input_dim=N_FEATURES, hidden_dim=64, latent_dim=16,
                                  seq_len=self.window)
        self.m2.load_state_dict(torch.load(weights_dir / "m2.pt", map_location=device))
        self.m2.eval()
        self.m3 = M3v2(N_FEATURES, len(self.classes))
        self.m3.load_state_dict(torch.load(weights_dir / "m3.pt", map_location=device))
        self.m3.eval()
        self.thr = float(cfg["m2_threshold"])
        self.persist_n, self.persist_m = cfg["persistence"]["n"], cfg["persistence"]["of"]
        self.groups = [set(g) for g in cfg["ambiguity_groups"]]
        self.sig_keys = list(cfg["signatures"])
        self.S = np.array([cfg["signatures"][k] for k in self.sig_keys])
        self.nov = cfg["novelty"]
        self.hstd = np.asarray(cfg["healthy_std"])
        jac = json.loads((weights_dir / "jacobian.json").read_text())
        self.kf = HealthKF(np.array(jac["J"]), self.hstd)
        self.mc = MissionMonteCarlo(self.classes)
        self.reset()

    def reset(self) -> None:
        self.buf: deque = deque(maxlen=self.window)
        self.hits: deque = deque(maxlen=self.persist_m)
        self.sev_hist: dict[str, deque] = {}
        self.theta_hist: deque = deque(maxlen=TREND_S)
        self.t = 0
        self.kf.reset()

    # ── helpers ──────────────────────────────────────────────────────────────
    def _localise(self) -> int | None:
        w = np.array(self.buf)[-16:]
        score = (np.abs(w[:, EGT_DEV].mean(0)) / self.hstd[EGT_DEV]
                 + np.abs(w[:, CHT_DEV].mean(0)) / self.hstd[CHT_DEV])
        return int(np.argmax(score)) if score.max() > 3.0 else None

    @staticmethod
    def _trend(hist) -> tuple[float, float]:
        """Least-squares slope (per second) and its standard error."""
        if len(hist) < 10:
            return 0.0, 0.0
        t, s = np.array(hist).T
        A = np.vstack([t - t.mean(), np.ones_like(t)]).T
        coef, res, *_ = np.linalg.lstsq(A, s, rcond=None)
        dof = max(len(t) - 2, 1)
        s2 = float(res[0]) / dof if res.size else float(np.var(s - A @ coef))
        se = np.sqrt(s2 / max(((t - t.mean()) ** 2).sum(), 1e-9))
        return float(coef[0]), float(se)

    def _physics_severity(self, fault: str, cyl: int | None) -> float | None:
        if fault not in THETA_OF_FAULT:
            return None
        name, sign = THETA_OF_FAULT[fault]
        if name == "cd_inj":
            if cyl is None:
                return None
            name = f"cd_inj_{cyl + 1}"
        v = self.kf.theta[THETA_NAMES.index(name)]
        return max(0.0, sign * (v - 1.0)) / ENGINE_FAULTS[fault][1]

    # ── one tick ─────────────────────────────────────────────────────────────
    def run(self, rho: list, rho_ext: list, altitude_ft: float, throttle_pct: float,
            needs_s: dict[str, float]) -> dict:
        self.t += 1
        z = np.asarray(feature_vector(rho, rho_ext), np.float32) - self.baseline
        self.buf.append(z)
        w = np.zeros((self.window, N_FEATURES), np.float32)
        w[-len(self.buf):] = np.array(self.buf)
        x = torch.from_numpy(w)[None]
        with torch.no_grad():
            err = float(self.m2.reconstruction_error(compress(x)).item())
            logits, sev = self.m3(x)
        probs = torch.softmax(logits, 1)[0].numpy()
        sev = sev[0].numpy()
        warm = len(self.buf) >= self.window
        self.hits.append(warm and err > self.thr)
        alarm = sum(self.hits) >= self.persist_n

        theta = self.kf.step(z)
        self.theta_hist.append((self.t, theta.copy()))

        wm = np.array(self.buf)[-16:].mean(0)
        n = np.linalg.norm(wm)
        cos = self.S @ (wm / n) if n > 1e-9 else np.zeros(len(self.S))
        nu = float(1.0 - cos.max()) if n > 1e-9 else 0.0
        novel = bool(alarm and nu > self.nov["threshold"])
        matrix_best = self.sig_keys[int(np.argmax(cos))].split("@")[0] if n > 1e-9 else None

        cyl = self._localise()
        order = np.argsort(-probs)
        fault_order = [i for i in order if self.classes[i] != "healthy"]
        if not alarm:
            top = [{"fault": "healthy", "p": round(1.0 - min(err / self.thr, 1.0), 3),
                    "cylinder": None, "source": "classifier"}]
        elif novel or self.classes[order[0]] == "healthy":
            # Detection fired but the classifier cannot name it, or what the
            # engine is doing lies outside every measured fault direction.
            top = [{"fault": "unknown", "p": round(float(max(nu, probs[order[0]])), 3),
                    "cylinder": None, "source": "classifier"}]
            top += [{"fault": self.classes[i], "p": round(float(probs[i]), 3),
                     "cylinder": cyl if self.classes[i] in PER_CYL else None,
                     "source": "classifier+matrix" if self.classes[i] == matrix_best else "classifier"}
                    for i in fault_order[:1]]
        else:
            top = [{"fault": self.classes[i], "p": round(float(probs[i]), 3),
                    "cylinder": cyl if self.classes[i] in PER_CYL else None,
                    "source": "classifier+matrix" if self.classes[i] == matrix_best else "classifier"}
                   for i in fault_order[:3]]

        f1 = top[0]["fault"]
        f2 = top[1]["fault"] if len(top) > 1 else None
        group = next((g for g in self.groups if f1 in g and (f2 in g if f2 else True)), None)
        gap_small = len(top) > 1 and abs(top[0]["p"] - top[1]["p"]) < AMBIG_GAP
        ambiguous = bool(alarm and f1 not in ("healthy",) and (group is not None or gap_small))
        diagnosis = {
            "top": top,
            "is_sensor_fault": bool(alarm and f1 in SENSOR_FAULTS and not ambiguous),
            "ambiguous": ambiguous,
            "probe": None,
        }
        if ambiguous and group:
            diagnosis["inseparable_from"] = sorted(group - {f1})

        # ── prognosis ──────────────────────────────────────────────────────
        for i, name in enumerate(self.classes):
            if name in ENGINE_FAULTS:
                self.sev_hist.setdefault(name, deque(maxlen=TREND_S)).append(
                    (self.t, float(sev[i]) if alarm else 0.0))
        k = len(self.classes)
        sev_mean = np.zeros(k); sev_std = np.full(k, 0.02)
        rate_mean = np.zeros(k); rate_std = np.zeros(k)
        for i, name in enumerate(self.classes):
            h = self.sev_hist.get(name)
            if h:
                sev_mean[i] = h[-1][1]
                rate_mean[i], rate_std[i] = self._trend(h)
        p_cls = probs.copy()
        if not alarm:
            p_cls = np.zeros(k); p_cls[self.classes.index("healthy")] = 1.0
        mc = self.mc.run(p_cls, sev_mean, sev_std, rate_mean, rate_std,
                         altitude_ft, throttle_pct, needs_s)

        component = f1 if f1 in ENGINE_FAULTS else "none"
        ttl = mc["ttl_continue_s"]
        finite = ttl[np.isfinite(ttl)]
        rul = {"component": component, "physics_h": None, "network_h": None,
               "p10_h": None, "p50_h": None, "p90_h": None, "reported_h": None,
               "heads_disagree": False, "unbounded_fraction": float(np.mean(~np.isfinite(ttl)))}
        if component != "none" and finite.size >= 0.5 * ttl.size:
            p10, p50, p90 = (np.percentile(ttl, q) / 3600.0 for q in (10, 50, 90))
            rul.update(network_h=round(float(p50), 3), p10_h=round(float(p10), 3),
                       p50_h=round(float(p50), 3), p90_h=round(float(p90), 3))
            # Physics head: the same redline, reached by the Kalman filter's
            # own severity estimate and its own fitted rate.
            s_now = self._physics_severity(component, cyl)
            lim = self.mc.s_limit(component, altitude_ft, throttle_pct)
            if s_now is not None and lim is not None and len(self.theta_hist) >= 10:
                hist = [(t, self._sev_from_theta(component, th, cyl)) for t, th in self.theta_hist]
                r, _ = self._trend(hist)
                if r > 0:
                    rul["physics_h"] = round(max(0.0, (lim - s_now) / r) / 3600.0, 3)
            heads = [v for v in (rul["physics_h"], rul["network_h"]) if v is not None]
            rul["reported_h"] = round(min(heads), 3)
            if rul["physics_h"] is not None:
                rul["heads_disagree"] = not (p10 <= rul["physics_h"] <= p90)

        # What the explain drawer shows: the live evidence and the MEASURED
        # signature of the fault being called (per cylinder where that exists).
        explain = None
        if alarm and f1 not in ("healthy", "unknown"):
            key = f"{f1}@{top[0]['cylinder']}" if top[0]["cylinder"] is not None else f1
            if key not in self.sig_keys:
                key = next((k for k in self.sig_keys if k.split("@")[0] == f1), None)
            if key is not None:
                sig_v = self.S[self.sig_keys.index(key)]
                explain = {"features": FEATURE_NAMES, "live": [round(float(v), 2) for v in wm],
                           "signature": [round(float(v), 3) for v in sig_v],
                           "signature_key": key,
                           "match_cosine": round(float(cos[self.sig_keys.index(key)]), 3)}

        return {
            "explain": explain,
            "anomaly": {"score": round(err, 5), "threshold": round(self.thr, 5),
                        "persistence": {"n": int(sum(self.hits)), "of": self.persist_m,
                                        "met": bool(alarm)}},
            "diagnosis": diagnosis,
            "theta": self.kf.health_frame(),
            "rul": rul,
            "mission_p": mc["p"],
            # P(no redline yet) at 10 %, 20 % … 100 % of the remaining mission
            # if it continues — the route on the risk map is coloured by this.
            "survival_continue": [round(float(np.mean(ttl > f * needs_s["continue"])), 3)
                                  for f in np.linspace(0.1, 1.0, 10)],
            "novelty": {"index": round(nu, 4), "residual_norm": round(float(np.linalg.norm(z)), 3),
                        "unexplained_norm": round(float(nu * np.linalg.norm(wm)), 3),
                        "threshold": round(float(self.nov["threshold"]), 4), "exceeded": novel,
                        "effective_rank": int(self.nov["effective_rank"]),
                        "null_space_dim": int(self.nov["null_space_dim"])},
            "twin_confidence": {"value": round(float(max(0.0, 1.0 - nu)), 3),
                                "basis": "measured_fault_signatures",
                                "note": "1 − (1 − best cosine to any measured fault direction)"},
            "kf_nis": round(self.kf.mean_nis(), 2),
        }

    def _sev_from_theta(self, fault, theta, cyl):
        name, sign = THETA_OF_FAULT[fault]
        if name == "cd_inj":
            name = f"cd_inj_{(cyl or 0) + 1}"
        v = theta[THETA_NAMES.index(name)]
        return max(0.0, sign * (v - 1.0)) / ENGINE_FAULTS[fault][1]
