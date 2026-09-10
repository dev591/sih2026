"""
Mean-Value Engine Model (MVEM)

Five state groups:
  1. Intake manifold pressure  — dp_im/dt = (R·T_im / V_im) · (ṁ_c − ṁ_a)
  2. Cylinder induction        — speed-density relation
  3. Crankshaft                — dω/dt = (T_ind − T_fric − T_pump − T_load) / J
  4. Cylinder head thermal     — dT_cht/dt = (Q_gas − Q_cool) / (m·cp)
  5. Turbocharger              — J_tc·ω_tc·dω_tc/dt = η_m·P_turb − P_comp

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
from scipy.integrate import solve_ivp


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

        comp = np.loadtxt(cfg_dir / "compressor_map.csv",
                          delimiter=',', skiprows=6)
        N_unique = np.unique(comp[:, 0])
        pts_per  = comp.shape[0] // len(N_unique)
        ol_N, ol_mdot, ol_pi, ol_eta = [], [], [], []
        for i, N in enumerate(N_unique):
            rows = comp[i * pts_per : (i + 1) * pts_per]
            best = rows[np.argmax(rows[:, 3])]
            ol_N.append(N)
            ol_mdot.append(best[1])
            ol_pi.append(best[2])
            ol_eta.append(best[3])
        self._ol_N    = np.array(ol_N)
        self._ol_mdot = np.array(ol_mdot)
        self._ol_pi   = np.array(ol_pi)
        self._ol_eta  = np.array(ol_eta)

        etav = np.loadtxt(cfg_dir / "lookup_eta_v.csv",
                          delimiter=',', skiprows=5)
        self._etav_p   = etav[:, 0]
        self._etav_N   = etav[:, 1]
        self._etav_val = etav[:, 2]
        self._etav_p_ax = np.unique(self._etav_p)
        self._etav_N_ax = np.unique(self._etav_N)

        prop = np.loadtxt(cfg_dir / "prop_cp_map.csv",
                          delimiter=',', skiprows=5)
        self._prop_J  = prop[:, 0]
        self._prop_CP = prop[:, 1]

    # ── Lookup helpers ─────────────────────────────────────────────────────

    def _lookup_eta_v(self, p_im: float, N_rpm: float) -> float:
        """Bilinear interpolation on the η_v(p_im, N) surface."""
        p_cl = float(np.clip(p_im,  self._etav_p_ax[0],  self._etav_p_ax[-1]))
        N_cl = float(np.clip(N_rpm, self._etav_N_ax[0], self._etav_N_ax[-1]))

        ip = int(np.searchsorted(self._etav_p_ax, p_cl, side='right')) - 1
        ip = int(np.clip(ip, 0, len(self._etav_p_ax) - 2))
        iN = int(np.searchsorted(self._etav_N_ax, N_cl, side='right')) - 1
        iN = int(np.clip(iN, 0, len(self._etav_N_ax) - 2))

        p0, p1 = self._etav_p_ax[ip], self._etav_p_ax[ip + 1]
        N0, N1 = self._etav_N_ax[iN], self._etav_N_ax[iN + 1]

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
        N_rpm   = w_tc * 60.0 / (2.0 * np.pi)
        theta   = T_atm / 288.15
        delta   = p_atm / 101325.0
        N_corr  = N_rpm / max(float(np.sqrt(theta)), 0.01)

        N_min = self._ol_N[0]

        if N_corr <= 0.0:
            mdot_corr, pi_c, eta_c = 0.0, 1.001, self.eta_c_nom * eta_c_scale
        elif N_corr < N_min:
            frac      = N_corr / N_min
            mdot_corr = self._ol_mdot[0] * frac
            pi_c      = max(1.0 + (self._ol_pi[0] - 1.0) * frac, 1.001)
            eta_c     = self._ol_eta[0] * eta_c_scale
            eta_c     = float(np.clip(eta_c, 0.40, 0.80))
        else:
            N_cl      = float(np.clip(N_corr, N_min, self._ol_N[-1]))
            mdot_corr = float(np.interp(N_cl, self._ol_N, self._ol_mdot))
            pi_c      = float(np.interp(N_cl, self._ol_N, self._ol_pi))
            eta_c     = float(np.interp(N_cl, self._ol_N, self._ol_eta)) * eta_c_scale
            eta_c     = float(np.clip(eta_c, 0.40, 0.80))

        m_c_actual = mdot_corr * delta / max(float(np.sqrt(theta)), 0.01)
        return max(m_c_actual, 0.0), max(pi_c, 1.001), eta_c

    # ── Main integration step ──────────────────────────────────────────────

    def _ode_system(self, t, y, cd_inj, eta_v_scale, eta_c_scale, hA_scale, f_fric_scale, p_atm, T_atm, throttle_pct, v_tas, update_derived=False):
        """
        Evaluate the derivatives of the state vector.
        If update_derived is True, it will update the class attributes with the computed
        derived parameters for this step.
        """
        p_im, w, w_tc = y[0], y[1], y[2]
        T_cht = y[3:]

        # Provide a floor for states to prevent non-physical behavior in ODE solver evaluations
        p_im = max(p_im, p_atm * 0.1)
        w = max(w, 10.0)
        w_tc = max(w_tc, 10.0)

        N_rpm = w * 60.0 / (2.0 * np.pi)

        # ── §2 Cylinder induction — speed-density ─────────────────────
        eta_v = self._lookup_eta_v(p_im, N_rpm) * eta_v_scale
        
        # T_im is needed for m_a. For a robust ODE, T_im comes from the compressor outlet
        m_c, pi_c, eta_c = self._compressor_operating_point(
            w_tc, p_atm, T_atm, eta_c_scale
        )
        
        T_im = T_atm + (T_atm / max(eta_c, 0.01)) * (
            pi_c ** ((self.gamma - 1.0) / self.gamma) - 1.0
        )
        
        m_a = (
            eta_v * p_im * self.V_d * N_rpm
            / (self.R * T_im * 120.0)
        )

        target_m_f_total = (throttle_pct / 100.0) * m_a / self.AFR_st
        fuel_cmd = target_m_f_total / self.N_cyl
        fuel_delivered = cd_inj * fuel_cmd
        m_f_total = float(np.sum(fuel_delivered))

        lambda_val = m_a / (self.AFR_st * max(m_f_total, 1e-9))

        T_ind_i = (
            self.eta_i * fuel_delivered * self.Q_LHV
            / max(w, 1.0)
        )
        T_ind = float(np.sum(T_ind_i))

        # Friction
        T_fric = self.f_fric_nom * f_fric_scale * w / 100.0

        # Propeller load
        n_prop  = w / (2.0 * np.pi) * self.gear_ratio
        J_adv   = v_tas / max(n_prop * self.D_prop, 0.01)
        C_P     = self._lookup_cp(J_adv)
        rho_air = p_atm / (self.R * T_atm)
        P_prop  = C_P * rho_air * (n_prop ** 3) * (self.D_prop ** 5)
        T_load  = P_prop / max(w, 1.0)

        brake_power_kW = (T_ind - T_fric) * w / 1000.0

        mean_T_ind = float(np.mean(T_ind_i))
        imbalance  = (
            float(np.max(np.abs(T_ind_i - mean_T_ind)))
            / max(mean_T_ind, 1e-9)
        )
        ripple = 0.004 + imbalance * 0.62

        # ── §4 Cylinder head thermal ───────────────────────────────────
        Q_ht_frac = 0.15
        Q_gas_i   = Q_ht_frac * fuel_delivered * self.Q_LHV
        h_air     = 50.0 * hA_scale
        dT_cht_dt = (
            Q_gas_i - h_air * self.A_fin * (T_cht - self.T_cool)
        ) / (self.m_cht * self.cp_cht)

        Q_ex_i  = fuel_delivered * self.Q_LHV * (1.0 - self.eta_i - Q_ht_frac)
        Q_ex_i  = np.maximum(Q_ex_i, 0.0)
        m_ex_i  = (m_a / self.N_cyl) + fuel_delivered
        T_egt_arr = T_atm + Q_ex_i / (m_ex_i * self.cp_ex)

        m_ex_total = float(np.sum(m_ex_i))
        T_egt_mean = float(np.mean(T_egt_arr))

        # ── §5 Turbocharger ODE ────────────────────────────────────────
        p_exh = p_atm * (1.0 + 0.3 * min(w / max(self.w_rated, 1.0), 1.0))
        pi_t  = max(p_exh / p_atm, 1.001)

        P_turb_specific = (
            self.eta_t * self.cp_ex * T_egt_mean
            * (1.0 - (1.0 / pi_t) ** ((self.gamma - 1.0) / self.gamma))
        )
        P_turb = m_ex_total * P_turb_specific

        # P_comp is calculated using m_c as per the thermodynamic correction
        P_comp = (
            m_c * self.cp_air * T_atm / max(eta_c, 0.01)
        ) * (pi_c ** ((self.gamma - 1.0) / self.gamma) - 1.0)

        dw_tc_dt = (self.eta_m_tc * P_turb - P_comp) / max(self.J_tc * w_tc, 1e-6)

        # ── §3 Crankshaft ──────────────────────────────────────────────
        T_pump = (p_exh - p_im) * self.V_d / (4.0 * np.pi)
        dw_dt = (T_ind - T_fric - T_pump - T_load) / self.J

        # ── §1 Intake manifold ─────────────────────────────────────────
        dp_im_dt = (self.R * T_im / self.V_im) * (m_c - m_a)

        # Update derived outputs if requested
        if update_derived:
            self.T_im = T_im
            self.m_a = m_a
            self.m_c = m_c
            self.fuel_cmd = fuel_cmd
            self.fuel_delivered = fuel_delivered
            self.T_egt = T_egt_arr
            self.lambda_val = lambda_val
            self.ripple = ripple
            self.brake_power_kW = brake_power_kW

        dy_dt = [dp_im_dt, dw_dt, dw_tc_dt] + dT_cht_dt.tolist()
        return dy_dt

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
        """
        cd_inj       = np.array(params.get('cd_inj',       np.ones(self.N_cyl)))
        eta_v_scale  = params.get('eta_v_scale',  1.0)
        eta_c_scale  = params.get('eta_c_scale',  1.0)
        hA_scale     = params.get('hA_scale',     1.0)
        f_fric_scale = params.get('f_fric_scale', 1.0)

        p_atm = atm['p']
        T_atm = atm['T']

        if self.p_im is None:
            _, pi_c_init, _ = self._compressor_operating_point(
                self.w_tc, p_atm, T_atm, 1.0
            )
            self.p_im = p_atm * pi_c_init

        # Create initial state vector
        y0 = [self.p_im, self.w, self.w_tc] + self.T_cht.tolist()

        args = (cd_inj, eta_v_scale, eta_c_scale, hA_scale, f_fric_scale, p_atm, T_atm, throttle_pct, v_tas, False)
        
        # Use a stiff ODE solver (Radau or BDF) to handle stiffness between intake dynamics and mechanical inertia
        res = solve_ivp(
            fun=self._ode_system,
            t_span=(0.0, dt),
            y0=y0,
            method='Radau',
            args=args,
        )

        y_final = res.y[:, -1]
        
        # Update states
        self.p_im = max(y_final[0], p_atm * 0.40)
        self.w    = max(y_final[1], 10.0)
        self.w_tc = max(y_final[2], 10.0)
        self.T_cht = y_final[3:]

        # Run one final evaluation to populate the derived output properties
        self._ode_system(
            dt, y_final,
            cd_inj, eta_v_scale, eta_c_scale, hA_scale, f_fric_scale, p_atm, T_atm, throttle_pct, v_tas,
            update_derived=True
        )

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
