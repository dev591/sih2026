"""
Train M2 (anomaly gate) and M3 v2 (fault + severity) on engine-model data and
evaluate them the way the live system serves them.

    cd sih-project && python -m ml.train_mvem [--device mps]

Data: data/mvem_v1 from ml/data/mvem_dataset.py. Split BY INSTALLATION (sensor
offset seed): test installations never appear in training or threshold
calibration, so every reported number is for an engine the models never saw.

Outputs (ml/weights/v2/): m2.pt, m3.pt, config.json (classes, features,
threshold, persistence, ambiguity groups, signatures, cylinder rule) and
report.json (every metric below, with its n).
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

from ml.features import cht_dev, egt_dev, feature_names, n_cyl_from_features
from ml.m2_autoencoder.model import LSTMAutoencoder
from ml.m3_classifier.model import M3v2, compress
from ml.prognostics import ENGINE_FAULTS, SENSOR_FAULTS

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "mvem_v1"
OUT = ROOT / "ml" / "weights" / "v2"
W = 32
T_RUN = 300  # set from the data in load()
PERSIST_N, PERSIST_M = 4, 5
THR_PCT = 99.5   # M2 alarm threshold percentile of healthy validation error (--threshold-pct)
# Cylinder-count dependent names and slices. load() sets them from the dataset (so any cylinder count works);
# these 4-cylinder defaults are what every function sees before then.
N_CYL = 4
FEATURE_NAMES = feature_names(4)
EGT_DEV, CHT_DEV = egt_dev(4), cht_dev(4)

PER_CYL = {"injector_fouling", "injection_misfire", "detonation",
           "egt_sensor_drift", "cht_sensor_drift"}


def load():
    d = np.load(DATA / "runs.npz")
    meta = json.loads((DATA / "meta.json").read_text())
    X = d["X"].astype(np.float32)
    inst = d["installation"]
    base = np.array([meta["baselines"][str(i)] for i in inst], np.float32)
    X = X - base[:, None, :]
    global T_RUN, N_CYL, FEATURE_NAMES, EGT_DEV, CHT_DEV
    T_RUN = X.shape[1]
    N_CYL = n_cyl_from_features(X.shape[2])
    FEATURE_NAMES = list(meta.get("features") or feature_names(N_CYL))
    assert len(FEATURE_NAMES) == X.shape[2], "dataset features do not match its cylinder count"
    EGT_DEV, CHT_DEV = egt_dev(N_CYL), cht_dev(N_CYL)
    return X, d["label"].astype(np.int64), d["severity"], inst, meta


def norm_severity(sev, label, classes):
    """Fault severity as a fraction of that fault's modelled range. Sensor
    drifts and healthy ticks carry no engine severity (masked out)."""
    out = np.zeros_like(sev)
    mask = np.zeros_like(sev, dtype=bool)
    for c, name in enumerate(classes):
        if name in ENGINE_FAULTS:
            m = label == c
            out[m] = sev[m] / ENGINE_FAULTS[name][1]
            mask |= m
    return out.astype(np.float32), mask


def windows(runs, stride):
    idx = []
    for r in runs:
        for e in range(W - 1, T_RUN, stride):
            idx.append((r, e))
    return np.array(idx)


def gather(Xt, idx):
    r = torch.as_tensor(idx[:, 0])[:, None]
    e = torch.as_tensor(idx[:, 1])[:, None] + torch.arange(-W + 1, 1)[None, :]
    return Xt[r, e]


def train_m2(Xt, label, tr, va, dev, epochs):
    def healthy(idx):
        keep = [(r, e) for r, e in idx if label[r, e - W + 1:e + 1].max() == 0]
        return np.array(keep)
    tr_h, va_h = healthy(windows(tr, 3)), healthy(windows(va, 2))
    ae = LSTMAutoencoder(input_dim=len(FEATURE_NAMES), hidden_dim=64, latent_dim=16, seq_len=W).to(dev)
    opt = torch.optim.Adam(ae.parameters(), lr=2e-3)
    for ep in range(epochs):
        ae.train()
        perm = np.random.permutation(len(tr_h))
        tot = 0.0
        for b in range(0, len(perm), 256):
            x = compress(gather(Xt, tr_h[perm[b:b + 256]])).to(dev)
            loss = F.mse_loss(ae(x), x)
            opt.zero_grad(); loss.backward(); opt.step()
            tot += loss.item() * len(x)
        print(f"  [M2] epoch {ep + 1}/{epochs} loss {tot / len(perm):.4f}", flush=True)
    ae.eval()
    errs = []
    with torch.no_grad():
        for b in range(0, len(va_h), 1024):
            errs.append(ae.reconstruction_error(compress(gather(Xt, va_h[b:b + 1024])).to(dev)).cpu())
    thr = float(np.percentile(torch.cat(errs).numpy(), THR_PCT))
    return ae, thr, len(tr_h), len(va_h)


def train_m3(Xt, label, sev_n, sev_mask, tr, n_cls, dev, epochs):
    idx = windows(tr, 3)
    y = label[idx[:, 0], idx[:, 1]]
    counts = np.bincount(y, minlength=n_cls).astype(float)
    wts = torch.tensor((counts.sum() / np.maximum(counts, 1)) / n_cls, dtype=torch.float32).to(dev)
    m3 = M3v2(len(FEATURE_NAMES), n_cls).to(dev)
    opt = torch.optim.AdamW(m3.parameters(), lr=2e-3, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, 3e-3, total_steps=epochs * (len(idx) // 256 + 1))
    yt = torch.as_tensor(y)
    st = torch.as_tensor(sev_n[idx[:, 0], idx[:, 1]])
    mt = torch.as_tensor(sev_mask[idx[:, 0], idx[:, 1]])
    for ep in range(epochs):
        m3.train()
        perm = np.random.permutation(len(idx))
        tot = 0.0
        for b in range(0, len(perm), 256):
            p = perm[b:b + 256]
            x = gather(Xt, idx[p]).to(dev)
            logits, sev = m3(x)
            yb = yt[p].to(dev)
            loss = F.cross_entropy(logits, yb, weight=wts)
            mb = mt[p].to(dev)
            if mb.any():
                s_pred = sev.gather(1, yb[:, None]).squeeze(1)
                loss = loss + F.smooth_l1_loss(s_pred[mb], st[p].to(dev)[mb])
            opt.zero_grad(); loss.backward(); opt.step(); sched.step()
            tot += loss.item() * len(p)
        print(f"  [M3] epoch {ep + 1}/{epochs} loss {tot / len(perm):.4f}", flush=True)
    return m3.eval()


def localise(win: np.ndarray, sig_egt: np.ndarray, sig_cht: np.ndarray) -> int:
    """Cylinder carrying a single-cylinder fault: the largest combined EGT+CHT
    deviation over the last 16 s — averaged, not one frame (a single frame
    got the cylinder right 38% of the time on the old pipeline)."""
    m = win[-16:]
    score = np.abs(m[:, EGT_DEV].mean(0)) / sig_egt + np.abs(m[:, CHT_DEV].mean(0)) / sig_cht
    return int(np.argmax(score))


def serve_eval(ae, m3, thr, Xt, label, sev_n, runs, meta, classes, dev):
    """Replay each run through the served logic: AE error -> N-of-M alarm ->
    M3 only when alarmed. Returns per-tick and per-run records."""
    healthy_std = Xt[runs][:, :60].reshape(-1, Xt.shape[2]).std(0).numpy() + 1e-6
    sig_egt, sig_cht = healthy_std[EGT_DEV], healthy_std[CHT_DEV]
    recs, ticks = [], []
    with torch.no_grad():
        for r in runs:
            idx = np.array([(r, e) for e in range(W - 1, T_RUN)])
            x = gather(Xt, idx)
            err = ae.reconstruction_error(compress(x).to(dev)).cpu().numpy()
            logits, sev = m3(x.to(dev))
            prob = torch.softmax(logits, 1).cpu().numpy()
            sev = sev.cpu().numpy()
            hist, alarm = [], []
            for v in err:
                hist = (hist + [v > thr])[-PERSIST_M:]
                alarm.append(sum(hist) >= PERSIST_N)
            mrun = meta["runs"][r]
            true = classes.index(mrun["class"])
            onset = mrun["onset"]
            first_alarm = next((e for e, a in zip(idx[:, 1], alarm) if a), None)
            for k, e in enumerate(idx[:, 1]):
                lab = label[r, e]
                if not alarm[k]:
                    served = 0
                else:
                    order = np.argsort(-prob[k])
                    served = int(order[0]) if order[0] != 0 else -1   # -1: anomaly, unclassified
                cyl_ok = None
                if alarm[k] and lab != 0 and classes[lab] in PER_CYL:
                    cyl_ok = localise(Xt[r, e - W + 1:e + 1].numpy(), sig_egt, sig_cht) == mrun["cyl"]
                ticks.append({"run": r, "e": int(e), "label": int(lab), "alarm": bool(alarm[k]),
                              "served": served, "top2": [int(i) for i in np.argsort(-prob[k])[:2]],
                              "p": prob[k].tolist(), "sev_pred": float(sev[k, lab]) if lab else 0.0,
                              "sev_true": float(sev_n[r, e]), "cyl_ok": cyl_ok})
            recs.append({"run": r, "class": mrun["class"], "onset": onset,
                         "first_alarm": None if first_alarm is None else int(first_alarm),
                         "false_alarm_ticks": int(sum(a for e, a in zip(idx[:, 1], alarm)
                                                      if onset < 0 or e < onset))})
    return recs, ticks


def summarise(recs, ticks, classes):
    rep: dict = {}
    healthy_runs = [r for r in recs if r["class"] == "healthy"]
    pre_ticks = sum(1 for t in ticks if t["label"] == 0)
    fa_ticks = sum(r["false_alarm_ticks"] for r in recs)
    rep["false_alarm_tick_rate"] = fa_ticks / max(pre_ticks, 1)
    episodes = 0
    by_run = {}
    for t in ticks:
        by_run.setdefault(t["run"], []).append(t)
    for r in recs:
        prev = False
        for t in by_run[r["run"]]:
            fa = t["alarm"] and t["label"] == 0
            episodes += fa and not prev
            prev = fa
    rep["false_alarm_episodes_per_hour"] = episodes / max(pre_ticks / 3600.0, 1e-9)
    rep["healthy_ticks_evaluated"] = pre_ticks
    rep["n_healthy_runs"] = len(healthy_runs)

    per_class = {}
    for name in classes[1:]:
        rr = [r for r in recs if r["class"] == name]
        if not rr:
            continue
        det = [r for r in rr if r["first_alarm"] is not None and r["first_alarm"] >= r["onset"]]
        lat = [r["first_alarm"] - r["onset"] for r in det]
        c = classes.index(name)
        tk = [t for t in ticks if t["label"] == c and t["alarm"]]
        per_class[name] = {
            "runs": len(rr),
            "detected_runs": len(det),
            "detection_rate": len(det) / len(rr),
            "median_latency_s": float(np.median(lat)) if lat else None,
            "alarmed_ticks": len(tk),
            "top1_after_alarm": (sum(t["served"] == c for t in tk) / len(tk)) if tk else None,
            "top2_after_alarm": (sum(c in t["top2"] for t in tk) / len(tk)) if tk else None,
            "cylinder_correct": (lambda v: sum(v) / len(v) if v else None)(
                [t["cyl_ok"] for t in tk if t["cyl_ok"] is not None]),
        }
    rep["per_class"] = per_class
    fault_tk = [t for t in ticks if t["label"] != 0 and t["alarm"]]
    rep["top1_after_alarm_all"] = sum(t["served"] == t["label"] for t in fault_tk) / max(len(fault_tk), 1)
    rep["top2_after_alarm_all"] = sum(t["label"] in t["top2"] for t in fault_tk) / max(len(fault_tk), 1)
    rep["unclassified_after_alarm"] = sum(t["served"] == -1 for t in fault_tk) / max(len(fault_tk), 1)
    cy = [t["cyl_ok"] for t in fault_tk if t["cyl_ok"] is not None]
    rep["cylinder_correct_all"] = sum(cy) / max(len(cy), 1)
    k = len(classes)
    cm = np.zeros((k, k + 1), int)            # last column: unclassified anomaly
    for t in fault_tk:
        cm[t["label"], t["served"] if t["served"] >= 0 else k] += 1
    rep["confusion_after_alarm"] = cm.tolist()
    eng = [t for t in fault_tk if classes[t["label"]] in ENGINE_FAULTS]
    if eng:
        err = np.array([t["sev_pred"] - t["sev_true"] for t in eng])
        rep["severity_mae"] = float(np.abs(err).mean())
    return rep


def ambiguity_groups(cm, classes, thresh=0.25):
    cm = np.array(cm)[:, :len(classes)].astype(float)
    rows = cm.sum(1, keepdims=True)
    P = np.divide(cm, rows, out=np.zeros_like(cm), where=rows > 0)
    groups = []
    for a in range(1, len(classes)):
        for b in range(a + 1, len(classes)):
            if P[a, b] + P[b, a] > thresh:
                groups.append(sorted([classes[a], classes[b]]))
    return groups


def signatures(X, label, sev_n, classes, runs, meta):
    """Measured fault directions in feature space: one per fault, and one per
    fault PER CYLINDER for single-cylinder faults (averaging those across
    cylinders would cancel the very deviation that localises them)."""
    sig = {}
    cyl_of_run = np.array([meta["runs"][r]["cyl"] for r in runs])
    for c, name in enumerate(classes[1:], start=1):
        m = label == c
        if name in ENGINE_FAULTS:
            m &= (sev_n > 0.15) & (sev_n < 0.7)
        groups = [(name, m)]
        if name in PER_CYL:
            groups = [(f"{name}@{k}", m & (cyl_of_run[:, None] == k)) for k in range(N_CYL)]
        for key, mm in groups:
            if mm.sum() < 20:
                continue
            v = X[mm].mean(0)
            sig[key] = (v / max(np.linalg.norm(v), 1e-9)).round(4).tolist()
    return sig


def novelty_index(win_mean: np.ndarray, S: np.ndarray) -> float:
    """1 − best cosine to any measured fault direction: how much of what the
    engine is doing now is NOT explained by a fault in the library."""
    n = np.linalg.norm(win_mean)
    if n < 1e-9:
        return 0.0
    return float(1.0 - np.max(S @ (win_mean / n)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="mps" if torch.backends.mps.is_available() else "cpu")
    ap.add_argument("--threshold-pct", type=float, default=99.5)
    ap.add_argument("--epochs-m2", type=int, default=12)
    ap.add_argument("--epochs-m3", type=int, default=20)
    a = ap.parse_args()
    global THR_PCT
    THR_PCT = a.threshold_pct
    torch.manual_seed(0); np.random.seed(0)
    t0 = time.time()
    X, label, sev, inst, meta = load()
    classes = meta["classes"]
    sev_n, sev_mask = norm_severity(sev, label, classes)
    Xt = torch.from_numpy(X)
    insts = sorted(set(inst.tolist()))
    test_i, val_i = set(insts[-6:]), set(insts[-10:-6])
    runs = np.arange(len(inst))
    tr = runs[[i not in test_i and i not in val_i for i in inst]]
    va = runs[[i in val_i for i in inst]]
    te = runs[[i in test_i for i in inst]]
    print(f"runs train {len(tr)} val {len(va)} test {len(te)}  device {a.device}", flush=True)

    ae, thr, n_h_tr, n_h_va = train_m2(Xt, label, tr, va, a.device, a.epochs_m2)
    print(f"  [M2] threshold ({THR_PCT}th pct, val installations) = {thr:.5f}", flush=True)
    m3 = train_m3(Xt, label, sev_n, sev_mask, tr, len(classes), a.device, a.epochs_m3)

    ae_c, m3_c = ae.cpu(), m3.cpu()
    sigs = signatures(X[tr], label[tr], sev_n[tr], classes, tr, meta)
    S = np.array(list(sigs.values()))
    nov_val = []
    for r in va:
        for e in range(W - 1, T_RUN, 4):
            if label[r, e] != 0 and label[r, e - 20] != 0:
                nov_val.append(novelty_index(X[r, e - 15:e + 1].mean(0), S))
    nov_thr = float(np.percentile(nov_val, 99.5)) if nov_val else 0.5
    rank = int(np.linalg.matrix_rank(S, tol=1e-3))
    va_recs, va_ticks = serve_eval(ae_c, m3_c, thr, Xt, label, sev_n, va, meta, classes, "cpu")
    va_rep = summarise(va_recs, va_ticks, classes)
    groups = ambiguity_groups(va_rep["confusion_after_alarm"], classes)
    te_recs, te_ticks = serve_eval(ae_c, m3_c, thr, Xt, label, sev_n, te, meta, classes, "cpu")
    te_rep = summarise(te_recs, te_ticks, classes)
    grp_ok = [t for t in te_ticks if t["label"] != 0 and t["alarm"]]
    def in_group(t):
        if t["served"] == t["label"]:
            return True
        if t["served"] < 0:
            return False
        pair = sorted([classes[t["label"]], classes[t["served"]]])
        return pair in groups
    te_rep["top1_or_declared_ambiguity_pair"] = sum(in_group(t) for t in grp_ok) / max(len(grp_ok), 1)

    healthy_std = Xt[tr][:, :60].reshape(-1, Xt.shape[2]).std(0).numpy() + 1e-6
    OUT.mkdir(parents=True, exist_ok=True)
    torch.save(ae_c.state_dict(), OUT / "m2.pt")
    torch.save(m3_c.state_dict(), OUT / "m3.pt")
    (OUT / "config.json").write_text(json.dumps({
        "classes": classes, "features": FEATURE_NAMES, "n_cyl": N_CYL, "window": W,
        "m2_threshold": thr, "persistence": {"n": PERSIST_N, "of": PERSIST_M},
        "ambiguity_groups": groups,
        "signatures": sigs,
        "novelty": {"threshold": nov_thr, "effective_rank": rank,
                    "null_space_dim": int(S.shape[1] - rank),
                    "calibration": "99.5th pct of 1-max-cosine on held-out known-fault windows"},
        "healthy_std": healthy_std.tolist(),
        "baselines": meta["baselines"],
        "data": {"generated": meta["generated"], "runs": len(inst),
                 "installations": {"train": len(insts) - 10, "val": 4, "test": 6}},
    }, indent=1))
    (OUT / "report.json").write_text(json.dumps({
        "evaluated_on": "held-out installations (never trained or calibrated on)",
        "n_runs_test": int(len(te)), "m2_healthy_windows_train": n_h_tr,
        "m2_healthy_windows_val": n_h_va, "test": te_rep, "val": va_rep,
        "ambiguity_groups_from_val": groups, "train_seconds": round(time.time() - t0),
    }, indent=1))
    print(json.dumps({k: v for k, v in te_rep.items() if k not in ("per_class", "confusion_after_alarm")}, indent=1))
    for name, v in te_rep["per_class"].items():
        print(f"  {name:26s} det {v['detection_rate']:.2f} lat {v['median_latency_s']}  "
              f"top1 {v['top1_after_alarm']}  cyl {v['cylinder_correct']}")
    print("ambiguity groups:", groups)


if __name__ == "__main__":
    main()
