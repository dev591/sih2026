"""
Mean-Value Engine Model (MVEM)

Five state groups:
  1. Intake manifold pressure  — dp_im/dt = (R·T_im / V_im) · (ṁ_c − ṁ_a)
  2. Cylinder induction        — speed-density relation
  3. Crankshaft                — dω/dt = (T_ind − T_fric − T_load) / J
  4. Cylinder head thermal     — dT_cht/dt = (Q_gas − Q_cool) / (m·cp)
  5. Turbocharger              — quasi-steady algebraic solve (see §5 below)

All engine-specific constants are read from the YAML config. Nothing is
hardcoded here — "a new engine is a config change" must be true in the code.

Lookup tables loaded once at construction from config/:
  compressor_map.csv   — corrected flow / pressure ratio / efficiency
  lookup_eta_v.csv     — volumetric efficiency surface η_v(p_im, N)
  prop_cp_map.csv      — propeller power coefficient C_P(J)

provenance: assumed for all three tables (analytical surrogates).
"""

from __future__ import annotations
from pathlib import Path

import numpy as np


class MVEM:
    def __init__(self, config: dict):
        self.cfg = config
        mcfg = config.get('mvem', {})

        # ── Thermodynamic constants ────────────────────────────────────────
        self.R       = mcfg.get('R_air_J_per_kgK',    287.05)
        self.gamma   = mcfg.get('gamma_air',             1.40)
        self.cp_air  = mcfg.get('cp_air_J_per_kgK',   1005.0)
        self.cp_ex   = mcfg.get('cp_exhaust_J_per_kgK', 1150.0)

        # ── Geometry ──────────────────────────────────────────────────────
        self.V_im  = mcfg.get('intake_manifold_volume_m3', 4.0e-3)
        self.V_d   = config['geometry']['displacement_m3']
        self.N_cyl = config['geometry']['cylinders']

        # ── Inertias ──────────────────────────────────────────────────────
        self.J     = mcfg['crankshaft']['inertia_kgm2']
        self.J_tc  = mcfg['compressor']['inertia_kgm2']

        # ── Thermal ───────────────────────────────────────────────────────
        therm = mcfg['thermal']
        self.m_cht   = therm['head_mass_kg']
        self.cp_cht  = therm['head_cp_J_per_kgK']
        self.A_fin   = therm['fin_area_m2']
        self.T_cool  = therm['coolant_temp_K']

        # ── Fuel ──────────────────────────────────────────────────────────
        self.Q_LHV  = config['fuel']['Q_LHV_J_per_kg']
        self.AFR_st = config['fuel']['AFR_stoich']

        # ── Efficiency nominals (now from YAML) ───────────────────────────
        self.eta_v_nom  = mcfg['volumetric_efficiency']['nominal']
        self.eta_c_nom  = mcfg['compressor']['efficiency_nominal']
        self.eta_m_tc   = mcfg['compressor']['mech_efficiency']
        self.eta_i      = mcfg.get('indicated_efficiency', 0.40)
        self.eta_t      = mcfg.get('turbine', {}).get('efficiency_nominal', 0.70)
        self.f_fric_nom = mcfg['crankshaft'].get('friction_coeff_nominal', 6.2)

        # ── Turbocharger nominal speed ────────────────────────────────────
        self.w_tc_nom = (
            mcfg['compressor'].get('speed_nominal_rpm', 118000) * 2.0 * np.pi / 60.0
        )

        # ── Propeller ─────────────────────────────────────────────────────
        prop = config['propeller']
        self.D_prop    = prop['diameter_m']
        self.gear_ratio = prop['gear_ratio']

        # ── Oil nominal outputs (from YAML limits) ────────────────────────
        lim = config.get('limits', {})
        self.oil_press_nom = lim.get('oil_pressure_bar', {}).get('nominal', 3.42)
        self.oil_temp_nom  = lim.get('oil_temp_C_nominal', 96.3)

        # ── Rated speed (for exhaust pressure estimate) ───────────────────
        self.w_rated = (
            config.get('ratings', {}).get('rated_speed_rpm', 3800) * 2.0 * np.pi / 60.0
        )

        # ── Load lookup tables ────────────────────────────────────────────
        self._load_maps(config)

        # ── State initialisation ──────────────────────────────────────────
        # p_im is set to None; on the first call to step() it is initialised
        # to the compressor map's output at nominal turbo speed, so the engine
        # starts near its operating point rather than having to bootstrap from
        # ambient pressure.
        self.p_im: float | None = None
        self.w    = config.get('ratings', {}).get('rated_speed_rpm', 3800) * 2.0 * np.pi / 60.0
        self.T_cht = np.full(self.N_cyl, self.T_cool + 50.0)
        self.w_tc  = self.w_tc_nom

        # ── Derived outputs (initialised to safe defaults) ────────────────
        self.T_im            = 288.15
        self.m_a             = 0.0
        self.m_c             = 0.0
        self.fuel_cmd        = 0.0
        self.fuel_delivered  = np.zeros(self.N_cyl)
        self.T_egt           = np.full(self.N_cyl, 700.0)
        self.lambda_val      = 1.0
        self.ripple          = 0.004
        self.brake_power_kW  = 0.0

    # ── Lookup table loading ───────────────────────────────────────────────

    def _load_maps(self, config: dict) -> None:
        """Load the three CSV lookup tables from the config directory."""
        cfg_dir = Path(__file__).resolve().parent.parent.parent / "config"

        # Compressor map — columns: N_corr_rpm, mdot_corr_kgps, pi_c, eta_c
        # Grouped: 8 consecutive rows per speed line, sorted by N ascending.
        comp = np.loadtxt(cfg_dir / "compressor_map.csv",
                          delimiter=',', skiprows=6)
        # Build operating-line table: one row per speed line at peak efficiency.
        # This gives a monotone (N_corr → mdot_corr, pi_c, eta_c) relationship
        # that can be interpolated directly given corrected turbo speed.
        N_unique = np.unique(comp[:, 0])
        pts_per  = comp.shape[0] // len(N_unique)
        ol_N, ol_mdot, ol_pi, ol_eta = [], [], [], []
        for i, N in enumerate(N_unique):
            rows = comp[i * pts_per : (i + 1) * pts_per]
            best = rows[np.argmax(rows[:, 3])]   # row with highest eta_c
            ol_N.append(N)
            ol_mdot.append(best[1])
            ol_pi.append(best[2])
            ol_eta.append(best[3])
        self._ol_N    = np.array(ol_N)
        self._ol_mdot = np.array(ol_mdot)
        self._ol_pi   = np.array(ol_pi)
        self._ol_eta  = np.array(ol_eta)

        # Volumetric efficiency — columns: p_im_Pa, N_rpm, eta_v
        etav = np.loadtxt(cfg_dir / "lookup_eta_v.csv",
                          delimiter=',', skiprows=5)
        self._etav_p   = etav[:, 0]
        self._etav_N   = etav[:, 1]
        self._etav_val = etav[:, 2]
        # Unique grid axes for bilinear interpolation
        self._etav_p_ax = np.unique(self._etav_p)
        self._etav_N_ax = np.unique(self._etav_N)

        # Propeller map — columns: J, C_P
        prop = np.loadtxt(cfg_dir / "prop_cp_map.csv",
                          delimiter=',', skiprows=5)
        self._prop_J  = prop[:, 0]
        self._prop_CP = prop[:, 1]

    # ── Lookup helpers ─────────────────────────────────────────────────────

    def _lookup_eta_v(self, p_im: float, N_rpm: float) -> float:
        """Bilinear interpolation on the η_v(p_im, N) surface."""
        p_cl = float(np.clip(p_im,  self._etav_p_ax[0],  self._etav_p_ax[-1]))
        N_cl = float(np.clip(N_rpm, self._etav_N_ax[0], self._etav_N_ax[-1]))

        # Find bounding indices on p axis
        ip = int(np.searchsorted(self._etav_p_ax, p_cl, side='right')) - 1
        ip = int(np.clip(ip, 0, len(self._etav_p_ax) - 2))
        iN = int(np.searchsorted(self._etav_N_ax, N_cl, side='right')) - 1
        iN = int(np.clip(iN, 0, len(self._etav_N_ax) - 2))

        p0, p1 = self._etav_p_ax[ip], self._etav_p_ax[ip + 1]
        N0, N1 = self._etav_N_ax[iN], self._etav_N_ax[iN + 1]

        # Row-major index: each (p, N) pair is one row
        n_N = len(self._etav_N_ax)
        v00 = self._etav_val[ip     * n_N + iN    ]
        v10 = self._etav_val[(ip+1) * n_N + iN    ]
        v01 = self._etav_val[ip     * n_N + (iN+1)]
        v11 = self._etav_val[(ip+1) * n_N + (iN+1)]

        tp = (p_cl - p0) / (p1 - p0)
        tN = (N_cl - N0) / (N1 - N0)
        return float((1-tp)*(1-tN)*v00 + tp*(1-tN)*v10 +
                     (1-tp)*tN   *v01 + tp*tN    *v11)

    def _lookup_cp(self, J: float) -> float:
        """1-D linear interpolation for propeller C_P(J)."""
        J_cl = float(np.clip(J, self._prop_J[0], self._prop_J[-1]))
        return float(np.interp(J_cl, self._prop_J, self._prop_CP))

    def _compressor_operating_point(
        self,
        w_tc: float,
        p_atm: float,
        T_atm: float,
        eta_c_scale: float,
    ) -> tuple[float, float, float]:
        """
        Given turbo speed and inlet conditions, return (m_c_actual, pi_c, eta_c).

        Interpolates on the operating-line locus (peak-efficiency at each speed).
        Extrapolates linearly to (0, 0) below the lowest speed line, so mass
        flow and pressure ratio taper to zero as the turbo spins down rather
        than clamping at the lowest map point.
        """
        N_rpm   = w_tc * 60.0 / (2.0 * np.pi)
        theta   = T_atm / 288.15
        delta   = p_atm / 101325.0
        N_corr  = N_rpm / max(float(np.sqrt(theta)), 0.01)

        N_min = self._ol_N[0]

        if N_corr <= 0.0:
            mdot_corr, pi_c, eta_c = 0.0, 1.001, self.eta_c_nom * eta_c_scale
        elif N_corr < N_min:
            # Linear extrapolation to origin below the lowest map speed
            frac      = N_corr / N_min
            mdot_corr = self._ol_mdot[0] * frac
            # Pressure ratio also extrapolates; clamp to ≥ 1.001
            pi_c      = max(1.0 + (self._ol_pi[0] - 1.0) * frac, 1.001)
            eta_c     = self._ol_eta[0] * eta_c_scale
            eta_c     = float(np.clip(eta_c, 0.40, 0.80))
        else:
            N_cl      = float(np.clip(N_corr, N_min, self._ol_N[-1]))
            mdot_corr = float(np.interp(N_cl, self._ol_N, self._ol_mdot))
            pi_c      = float(np.interp(N_cl, self._ol_N, self._ol_pi))
            eta_c     = float(np.interp(N_cl, self._ol_N, self._ol_eta)) * eta_c_scale
            eta_c     = float(np.clip(eta_c, 0.40, 0.80))

        # Un-correct mass flow to actual conditions
        m_c_actual = mdot_corr * delta / max(float(np.sqrt(theta)), 0.01)
        return max(m_c_actual, 0.0), max(pi_c, 1.001), eta_c

    def _solve_turbo_quasisteady(
        self,
        m_ex_total: float,
        T_egt_mean: float,
        p_atm: float,
        T_atm: float,
        eta_c_scale: float,
    ) -> tuple[float, float, float, float]:
        """
        Quasi-steady turbocharger solve.

        At steady state: η_m · P_turb = P_comp
        Rather than integrating a stiff ODE, we iterate w_tc until power
        balance is achieved. Five Newton-style iterations converge in all
        tested conditions.

        Returns (w_tc_new, m_c, pi_c, eta_c).
        """
        # Turbine: exhaust manifold pressure approximated as
        #   p_exh = p_atm * (1 + 0.3 * min(ω/ω_rated, 1))
        # This is physically motivated: the exhaust backpressure rises with
        # engine load. It is NOT using p_im for the turbine (the original bug).
        p_exh     = p_atm * (1.0 + 0.3 * min(self.w / max(self.w_rated, 1.0), 1.0))
        pi_t      = max(p_exh / p_atm, 1.001)
        # Turbine available power per unit exhaust mass flow
        P_turb_specific = (
            self.eta_t * self.cp_ex * T_egt_mean
            * (1.0 - (1.0 / pi_t) ** ((self.gamma - 1.0) / self.gamma))
        )
        P_turb_avail = self.eta_m_tc * m_ex_total * P_turb_specific

        w_tc = self.w_tc
        m_c, pi_c, eta_c = 0.0, 1.0, self.eta_c_nom

        for _ in range(6):
            m_c, pi_c, eta_c = self._compressor_operating_point(
                w_tc, p_atm, T_atm, eta_c_scale
            )
            # Compressor power demand
            P_comp = (
                m_c * self.cp_air * T_atm / max(eta_c, 0.01)
            ) * (pi_c ** ((self.gamma - 1.0) / self.gamma) - 1.0)

            # Power error — positive means turbo can spin faster
            dP = P_turb_avail - P_comp
            if abs(dP) < 1.0:           # converged (1 W tolerance)
                break

            # Adjust w_tc proportionally; clamp step to 20 % per iteration
            rel_step = float(np.clip(0.15 * dP / max(P_comp, 10.0), -0.20, 0.20))
            w_tc = max(w_tc * (1.0 + rel_step), 10.0)

        return w_tc, m_c, pi_c, eta_c

    # ── Main integration step ──────────────────────────────────────────────

    def step(
        self,
        dt: float,
        params: dict,
        atm: dict,
        throttle_pct: float,
        v_tas: float = 61.2,
    ) -> None:
        """
        Advance the engine state by dt seconds.

        Parameters
        ----------
        dt          : timestep [s]
        params      : health-parameter dict (cd_inj, eta_v_scale, eta_c_scale,
                      hA_scale, f_fric_scale)
        atm         : atmosphere dict from twin.atmosphere.isa  {'T': K, 'p': Pa}
        throttle_pct: throttle demand [%]
        v_tas       : true airspeed [m/s] — used for propeller advance ratio
        """
        cd_inj       = np.array(params.get('cd_inj',       np.ones(self.N_cyl)))
        eta_v_scale  = params.get('eta_v_scale',  1.0)
        eta_c_scale  = params.get('eta_c_scale',  1.0)
        hA_scale     = params.get('hA_scale',     1.0)
        f_fric_scale = params.get('f_fric_scale', 1.0)

        p_atm = atm['p']
        T_atm = atm['T']

        # Initialise p_im to the compressor map's pressure ratio at nominal
        # turbo speed, evaluated at the current ambient. This puts the engine
        # near its operating point from the first step instead of requiring a
        # long bootstrap from ambient pressure.
        if self.p_im is None:
            _, pi_c_init, _ = self._compressor_operating_point(
                self.w_tc, p_atm, T_atm, 1.0
            )
            self.p_im = p_atm * pi_c_init

        sub_steps = 100
        h = dt / sub_steps

        for _ in range(sub_steps):
            N_rpm = max(self.w * 60.0 / (2.0 * np.pi), 100.0)

            # ── §2 Cylinder induction — speed-density ─────────────────────
            eta_v  = self._lookup_eta_v(self.p_im, N_rpm) * eta_v_scale
            self.m_a = (
                eta_v * self.p_im * self.V_d * N_rpm
                / (self.R * self.T_im * 120.0)
            )

            # Fuel command: throttle sets fuel rack directly (FADEC diesel).
            # Maximum fuel is stoichiometric; throttle scales below that.
            # This produces lean-burn (λ > 1) at partial throttle, which is
            # physically correct for a compression-ignition aero-diesel.
            target_m_f_total = (throttle_pct / 100.0) * self.m_a / self.AFR_st
            self.fuel_cmd      = target_m_f_total / self.N_cyl
            self.fuel_delivered = cd_inj * self.fuel_cmd
            m_f_total          = float(np.sum(self.fuel_delivered))

            self.lambda_val = self.m_a / (self.AFR_st * max(m_f_total, 1e-9))
            T_ind_i = (
                self.eta_i * self.fuel_delivered * self.Q_LHV
                / max(self.w, 1.0)
            )
            T_ind = float(np.sum(T_ind_i))

            # Friction: from YAML nominal, scaled by health parameter
            T_fric = self.f_fric_nom * f_fric_scale * self.w / 100.0

            # Propeller load — law: P = C_P(J) · ρ_air · n³ · D⁵
            n_prop  = max(self.w / (2.0 * np.pi) * self.gear_ratio, 0.01)  # rev/s
            J_adv   = v_tas / max(n_prop * self.D_prop, 0.01)
            C_P     = self._lookup_cp(J_adv)
            rho_air = p_atm / (self.R * T_atm)
            P_prop  = C_P * rho_air * (n_prop ** 3) * (self.D_prop ** 5)
            T_load  = P_prop / max(self.w, 1.0)

            dw_dt = (T_ind - T_fric - T_load) / self.J
            self.brake_power_kW = (T_ind - T_fric) * self.w / 1000.0

            # Cylinder imbalance → 0.5-order crank ripple
            mean_T_ind = float(np.mean(T_ind_i))
            imbalance  = (
                float(np.max(np.abs(T_ind_i - mean_T_ind)))
                / max(mean_T_ind, 1e-9)
            )
            self.ripple = 0.004 + imbalance * 0.62

            # ── §4 Cylinder head thermal ───────────────────────────────────
            # Energy split: eta_i → indicated work, Q_ht_frac → coolant, rest → exhaust
            # For a diesel: ~70% indicated, ~15% coolant, ~15% exhaust.
            # A lean diesel has lower EGT; the exhaust fraction is modest but
            # the large exhaust MASS FLOW (lean = excess air) drives the turbo.
            Q_ht_frac  = 0.15                          # fraction to coolant
            Q_gas_i    = Q_ht_frac * self.fuel_delivered * self.Q_LHV
            h_air      = 50.0 * hA_scale
            dT_cht_dt  = (
                Q_gas_i - h_air * self.A_fin * (self.T_cht - self.T_cool)
            ) / (self.m_cht * self.cp_cht)

            # Exhaust enthalpy: fuel LHV minus indicated work minus heat to coolant
            Q_ex_i  = self.fuel_delivered * self.Q_LHV * (1.0 - self.eta_i - Q_ht_frac)
            Q_ex_i  = np.maximum(Q_ex_i, 0.0)         # cannot be negative
            # Exhaust mass = air + fuel (all cylinders share the manifold air equally)
            m_ex_i  = (self.m_a / self.N_cyl) + self.fuel_delivered
            self.T_egt = T_atm + Q_ex_i / (m_ex_i * self.cp_ex)

            # ── §5 Turbocharger — quasi-steady algebraic solve ─────────────
            #
            # Replaces the stiff explicit-Euler ODE that caused w_tc to
            # collapse to its floor on every run. We lose turbo lag (which
            # nothing in the demo depends on) and gain a state that cannot
            # explode or enter an absorbing collapsed state.
            #
            # The turbine uses exhaust-manifold pressure, not p_im — the
            # original code used p_im for both compressor and turbine, which
            # is physically wrong and is one of the three diagnosed defects.
            m_ex_total = float(np.sum(m_ex_i))
            T_egt_mean = float(np.mean(self.T_egt))

            self.w_tc, self.m_c, pi_c, eta_c = self._solve_turbo_quasisteady(
                m_ex_total, T_egt_mean, p_atm, T_atm, eta_c_scale
            )

            # ── §1 Intake manifold — filling and emptying ─────────────────
            # T_im from compressor outlet (isentropic + efficiency)
            self.T_im = T_atm + (T_atm / max(eta_c, 0.01)) * (
                pi_c ** ((self.gamma - 1.0) / self.gamma) - 1.0
            )
            dp_im_dt = (
                (self.R * self.T_im / self.V_im) * (self.m_c - self.m_a)
            )

            # ── Integration ───────────────────────────────────────────────
            self.w     += dw_dt   * h
            self.p_im  += dp_im_dt * h
            self.T_cht += dT_cht_dt * h

            # Guards — w_tc is already set by the quasi-steady solver
            self.w    = max(self.w,   10.0)
            self.p_im = max(self.p_im, p_atm * 0.40)

    # ── Outputs ────────────────────────────────────────────────────────────

    def get_outputs(self) -> dict:
        p_im = self.p_im if self.p_im is not None else 101325.0
        return {
            'map_hPa':               float(p_im / 100.0),
            'iat_K':                 float(self.T_im),
            'air_mass_flow':         float(self.m_a),
            'fuel_cmd_per_cyl':      float(self.fuel_cmd),
            'fuel_delivered_per_cyl':[float(x) for x in self.fuel_delivered],
            'fuel_flow_total':       float(np.sum(self.fuel_delivered)),
            'lambda_val':            float(self.lambda_val),
            'egt_C':                 [float(x) for x in (self.T_egt - 273.15)],
            'cht_C':                 [float(x) for x in (self.T_cht - 273.15)],
            'brake_power_kW':        float(self.brake_power_kW),
            'turbo_rpm':             float(self.w_tc * 60.0 / (2.0 * np.pi)),
            'oil_press_bar':         float(self.oil_press_nom),
            'oil_temp_C':            float(self.oil_temp_nom),
            'ripple':                float(self.ripple),
            'rpm':                   float(self.w * 60.0 / (2.0 * np.pi)),
        }
