import numpy as np

class DamageIntegrator:
    """
    Integrates physical damage from wear (Archard) and thermal aging (Arrhenius).
    Calibrated such that a continuously faulted engine reaches D=1 in ~30-90 mins.
    """
    def __init__(self):
        self.D = 0.0
        self.dD_dt = 0.0

        # Archard wear: k_archard * T_fric * w / H_material
        # Baseline T_fric * w ~ 10000 W
        self.k_archard = 1.35e-8
        self.H_material = 1.0

        # Arrhenius thermal aging: k_arrhenius * exp(-E_a / (R * T_cht))
        # Baseline T_cht ~ 413 K
        self.R_GAS = 8.314
        self.E_a = 5000.0 * self.R_GAS  # so E_a / R_GAS = 5000
        self.k_arrhenius = 24.5

    def step(self, dt: float, T_fric: float, w: float, T_cht_per_cyl: list[float]):
        if self.D >= 1.0:
            self.dD_dt = 0.0
            return

        # Adhesive/abrasive wear
        dD_dt_archard = self.k_archard * max(T_fric * w, 0.0) / self.H_material

        # Thermally activated aging (worst-case cylinder)
        T_cht_max = max(T_cht_per_cyl)
        dD_dt_arrhenius = self.k_arrhenius * np.exp(-self.E_a / (self.R_GAS * max(T_cht_max, 1.0)))

        self.dD_dt = dD_dt_archard + dD_dt_arrhenius
        self.D += self.dD_dt * dt
        self.D = min(self.D, 1.0)

    @property
    def rul_h(self) -> float:
        if self.D >= 1.0:
            return 0.0
        return (1.0 - self.D) / max(self.dD_dt, 1e-9) / 3600.0
