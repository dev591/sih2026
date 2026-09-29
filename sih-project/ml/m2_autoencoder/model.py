"""
M2 — LSTM Autoencoder on residuals.

Job: flag "this does not look like a healthy engine" WITHOUT being told
what the fault is. First line of defence before the classifier runs.

Why LSTM, not Transformer:
  • Data volume: healthy budget is synthetic + short bench run. LSTM's
    stronger inductive bias toward local sequential dependence pays off here.
  • Window length: seconds at 1 Hz — well inside the regime where recurrent
    architectures are not at a disadvantage.
  • Latency: 1 Hz health frame, running alongside M3 + UKF.

Why sequence model, not static autoencoder:
  • ρ₁₁ (crank ripple) is a spectral quantity that exists over a window.
  • Harmonic content of ρ₁₁ separates injector fouling from injection misfire
    on the same cylinder — a property of a sequence, not a sample.

Threshold discipline:
  • 99.5th percentile of reconstruction error on HELD-OUT HEALTHY data.
  • Never a hand-picked constant.
  • N-of-M persistence rule suppresses transients.

See docs/backend/ML-DEEP-DIVE.md §4 for the full argument.
"""

from __future__ import annotations

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader


# ── Architecture ────────────────────────────────────────────────────────────

class LSTMEncoder(nn.Module):
    def __init__(self, input_dim: int, hidden_dim: int, latent_dim: int, num_layers: int = 2):
        super().__init__()
        self.lstm = nn.LSTM(
            input_size=input_dim,
            hidden_size=hidden_dim,
            num_layers=num_layers,
            batch_first=True,
            dropout=0.1 if num_layers > 1 else 0.0,
        )
        self.fc = nn.Linear(hidden_dim, latent_dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (B, T, input_dim)
        _, (h_n, _) = self.lstm(x)      # h_n: (num_layers, B, hidden_dim)
        z = self.fc(h_n[-1])            # (B, latent_dim)
        return z


class LSTMDecoder(nn.Module):
    def __init__(self, latent_dim: int, hidden_dim: int, output_dim: int,
                 seq_len: int, num_layers: int = 2):
        super().__init__()
        self.seq_len = seq_len
        self.fc = nn.Linear(latent_dim, hidden_dim)
        self.lstm = nn.LSTM(
            input_size=hidden_dim,
            hidden_size=hidden_dim,
            num_layers=num_layers,
            batch_first=True,
            dropout=0.1 if num_layers > 1 else 0.0,
        )
        self.out = nn.Linear(hidden_dim, output_dim)

    def forward(self, z: torch.Tensor) -> torch.Tensor:
        # z: (B, latent_dim)
        h = self.fc(z).unsqueeze(1).expand(-1, self.seq_len, -1)  # (B, T, hidden_dim)
        out, _ = self.lstm(h)           # (B, T, hidden_dim)
        return self.out(out)            # (B, T, output_dim)


class LSTMAutoencoder(nn.Module):
    """
    LSTM autoencoder that operates on windows of ρ ∈ R^(T × 11).

    Reconstruction error per-window is used as the anomaly score.
    """

    def __init__(
        self,
        input_dim: int = 11,
        hidden_dim: int = 64,
        latent_dim: int = 16,
        seq_len: int = 32,
        num_layers: int = 2,
    ):
        super().__init__()
        self.seq_len = seq_len
        self.encoder = LSTMEncoder(input_dim, hidden_dim, latent_dim, num_layers)
        self.decoder = LSTMDecoder(latent_dim, hidden_dim, input_dim, seq_len, num_layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (B, T, 11)
        z = self.encoder(x)             # (B, latent_dim)
        x_hat = self.decoder(z)         # (B, T, 11)
        return x_hat

    def reconstruction_error(self, x: torch.Tensor) -> torch.Tensor:
        """Per-window MSE reconstruction error. Shape: (B,)."""
        x_hat = self.forward(x)
        return ((x - x_hat) ** 2).mean(dim=(1, 2))


# ── Threshold calibration ──────────────────────────────────────────────────

def calibrate_threshold(
    model: LSTMAutoencoder,
    healthy_val_loader: DataLoader,
    percentile: float = 99.5,
    device: str = "cpu",
) -> float:
    """
    Compute the anomaly threshold as the `percentile`-th percentile of
    reconstruction error on held-out HEALTHY data.

    Never a hand-picked constant — derived from data, portable to any engine
    profile by re-running this function on its healthy data.
    """
    model.eval()
    errors = []
    with torch.no_grad():
        for (x,) in healthy_val_loader:
            x = x.to(device)
            e = model.reconstruction_error(x)
            errors.extend(e.cpu().numpy().tolist())
    threshold = float(np.percentile(errors, percentile))
    print(f"[M2] Threshold calibrated at {percentile}th pct: {threshold:.4f}")
    return threshold


# ── N-of-M persistence rule ────────────────────────────────────────────────

class PersistenceRule:
    """
    Suppresses transient false alarms: alarm fires only when N of the last M
    consecutive windows exceeded the threshold.

    Usage:
        rule = PersistenceRule(n=4, m=5)
        for score in stream:
            is_alarm = rule.update(score > threshold)
    """

    def __init__(self, n: int = 4, m: int = 5):
        self.n = n
        self.m = m
        self._history: list[bool] = []

    @property
    def hits(self) -> int:
        """Windows over threshold among the last M seen — the live count."""
        return sum(self._history)

    def update(self, exceeded: bool) -> bool:
        self._history.append(exceeded)
        if len(self._history) > self.m:
            self._history.pop(0)
        return sum(self._history) >= self.n

    def state(self) -> dict:
        return {
            "n": self.n,
            "of": self.m,
            "met": self.update.__self__._history if hasattr(self.update, '__self__') else [],
        }

    def reset(self) -> None:
        self._history.clear()


# ── Training helper ────────────────────────────────────────────────────────

def train_autoencoder(
    model: LSTMAutoencoder,
    train_loader: DataLoader,
    epochs: int = 50,
    lr: float = 1e-3,
    device: str = "cpu",
) -> list[float]:
    """
    Train on healthy windows only (unsupervised reconstruction).
    Returns per-epoch loss history.
    """
    model.to(device)
    optimiser = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-5)
    criterion = nn.MSELoss()
    history = []

    for epoch in range(1, epochs + 1):
        model.train()
        epoch_loss = 0.0
        for (x,) in train_loader:
            x = x.to(device)
            x_hat = model(x)
            loss = criterion(x_hat, x)
            optimiser.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimiser.step()
            epoch_loss += loss.item() * x.size(0)
        avg = epoch_loss / len(train_loader.dataset)
        history.append(avg)
        if epoch % 10 == 0:
            print(f"[M2] epoch {epoch:3d}/{epochs}  loss={avg:.5f}")

    return history
