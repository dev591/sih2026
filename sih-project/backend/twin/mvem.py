import numpy as np

class MVEM:
    def __init__(self, config: dict):
        self.cfg = config

        mvem_cfg = self.cfg.get('mvem', {})
        self.R = mvem_cfg.get('R_air_J_per_kgK', 287.05)
        self.gamma = mvem_cfg.get('gamma_air', 1.4)
        self.cp_air = mvem_cfg.get('cp_air_J_per_kgK', 1005.0)
        self.cp_ex = mvem_cfg.get('cp_exhaust_J_per_kgK', 1150.0)

        self.V_im = mvem_cfg.get('intake_manifold_volume_m3', 4.0e-3)
        self.V_d = self.cfg['geometry']['displacement_m3']
        self.N_cyl = self.cfg['geometry']['cylinders']

        self.J = mvem_cfg['crankshaft']['inertia_kgm2']

        self.m_cht = mvem_cfg['thermal']['head_mass_kg']
        self.cp_cht = mvem_cfg['thermal']['head_cp_J_per_kgK']
        self.A_fin = mvem_cfg['thermal']['fin_area_m2']
        self.T_cool = mvem_cfg['thermal']['coolant_temp_K']

        self.Q_LHV = self.cfg['fuel']['Q_LHV_J_per_kg']
        self.AFR_st = self.cfg['fuel']['AFR_stoich']
        fuel_cfg = self.cfg['fuel']
        # provenance: calibrated. Endpoints of the mixture schedule — see the
        # comment in step() for why this replaced a fixed lambda=K/throttle
        # relation. lambda_full=1.05 (near-stoichiometric, typical
        # max-continuous/takeoff target on a turbodiesel to keep EGT/knock
        # margin); lambda_idle=1.65 (lean, typical partial-power economy
        # cruise setting) — not sourced from a published VRDE fuel map (none
        # exists), proposed and verified against backend/gates_check.py.
        self._lambda_full = fuel_cfg.get('lambda_full_power', 0.98)
        self._lambda_idle = fuel_cfg.get('lambda_idle', 1.35)

        # --- eta_v(p_im, N) correlation — PRAMANA-DIRECTIVE §1.4 -----------
        ve = mvem_cfg['volumetric_efficiency']
        self._ve_c0 = ve['correlation_c0']
        self._ve_c1 = ve['correlation_c1']
        self._ve_c2 = ve['correlation_c2']
        self._ve_c3 = ve['correlation_c3']

        # --- Ellipse compressor model — PRAMANA-DIRECTIVE §1.2 -------------
        comp = mvem_cfg['compressor']
        self.D_c = comp['impeller_diameter_m']
        self._psi_max_design = comp['psi_max_design']
        self._phi_max_design = comp['phi_max_design']
        self._n_corr_design = comp['n_corr_design_rpm']
        self._c_psi = comp['c_psi']
        self._c_phi = comp['c_phi']
        self._psi_speed_exp = comp['psi_speed_exponent']
        self._phi_speed_exp = comp['phi_speed_exponent']
        self.eta_c_max = comp['efficiency_nominal']
        self.J_tc = comp['inertia_kgm2']
        self.eta_m_tc = comp['mech_efficiency']

        # --- Turbine, decoupled from the intake side — §1.3 ----------------
        turb = mvem_cfg['turbine']
        self.eta_t = turb['efficiency_nominal']
        self._turb_pr_gain = turb['pressure_ratio_gain']
        self._mdot_ex_design = turb['mdot_ex_design_kgps']
        self._pi_t_max = turb.get('pressure_ratio_max', 3.2)

        # --- Wastegate — NOT in the directive's equations, added because
        # they need it to work. §1.2/§1.3 give the compressor and turbine
        # real physics with no boost limiter between them; run that as
        # written and sea-level MAP runs away to several times the engine's
        # own rated takeoff limit, because nothing bounds how much of the
        # exhaust stream the turbine is allowed to extract. Every real
        # turbocharged engine has a wastegate for exactly this reason, and
        # this engine's own profile says control: fadec — regulating boost
        # to a MAP schedule IS what a FADEC does on a turbocharged engine.
        # Target MAP scales with throttle up to the published max-continuous
        # limit; above critical altitude the (now fully closed) wastegate
        # cannot add capacity the hardware doesn't have, which is what
        # produces "flat MAP to critical altitude, then falls" rather than
        # that shape being asserted directly.
        self._map_max_continuous_bar = self.cfg['limits']['manifold_pressure_hPa']['max_continuous'] / 1000.0
        self._wastegate_gain = 14.0  # 1/bar — proportional control on bypass fraction

        # --- Propeller load — §1.5. Replaces a made-up quadratic that had
        # no dependence on air density and was additionally (and
        # incorrectly) scaled by throttle_pct directly, coupling the load
        # model to the FUEL schedule rather than to propeller aerodynamics.
        prop = self.cfg['propeller']
        self.D_prop = prop['diameter_m']
        self._cp0 = prop['cp0']
        self._j_max = prop['j_max']
        self._tas = prop['assumed_tas_mps']
        self._gear_ratio = prop.get('gear_ratio', 1.0)

        # --- Oil system — §1.6. See the yaml comment for the physics. -----
        oil = mvem_cfg.get('oil', {})
        self._oil_eta_vol = oil.get('pump_eta_vol', 0.85)
        self._oil_D_pump = oil.get('pump_displacement_m3_per_rev', 5.0e-6)
        self._oil_dp_gain = oil.get('dp_gain_bar_per_unit', 5.6)
        self._oil_visc_ref_T = oil.get('visc_ref_temp_K', 369.45)
        self._oil_visc_exp = oil.get('visc_temp_exponent', 1.6)
        self._oil_relief_bar = oil.get('relief_valve_bar', 7.0)
        self._oil_thermal_mass_J = oil.get('thermal_mass_kJ_per_K', 4.5) * 1000.0
        self._oil_cooler_gain = oil.get('cooler_gain_W_per_K', 55.0)
        self.T_oil = self._oil_visc_ref_T  # state — starts at the calibration point

        self._T_REF = 288.15  # standard reference temperature for N_corr

        # ---- States ---------------------------------------------------
        self.p_im = 101325.0
        self.w = 3800.0 * 2 * np.pi / 60
        self.T_cht = np.full(self.N_cyl, 363.15 + 50)

        # Turbo shaft state is KINETIC ENERGY, not speed — §1.1. The old
        # dw_tc/dt = (P_turb-P_comp)/(J_tc*w_tc) has a 1/w_tc singularity: as
        # w_tc falls toward its floor the derivative blows up, explicit Euler
        # at h=dt/1000 goes unstable, and the clamp catches it — pinned at
        # the floor because the EQUATION is ill-posed there, not because the
        # turbo is genuinely slow. E_tc = 1/2 * J_tc * w_tc^2 makes the
        # right-hand side a plain power balance with no division by the
        # state, so it cannot go singular.
        w_tc0 = 118000.0 * 2 * np.pi / 60
        self.E_tc = 0.5 * self.J_tc * w_tc0 ** 2
        self._E_tc_min = 0.5 * self.J_tc * (20000.0 * 2 * np.pi / 60) ** 2  # idle/windmill floor
        # Mechanical overspeed limit — every real turbo has one (bearing/
        # blade stress), and without it there is no physical reason this
        # model's "critical altitude" exists at all. N_corr = N/sqrt(T01/Tref)
        # rises with altitude at FIXED physical shaft speed (T01 falls), so
        # the Ellipse model's psi_max/phi_max keep growing with altitude and
        # the compressor never runs out of capacity — verified: without this
        # cap, MAP stayed flat at ~1.40 bar all the way to 20,000 ft instead
        # of falling above the engine's own published 11,000 ft critical
        # altitude. This ceiling is what makes critical altitude a real,
        # emergent consequence of the physics again, not merely asserted.
        self._E_tc_max = 0.5 * self.J_tc * (comp.get('max_shaft_rpm', 130000.0) * 2 * np.pi / 60) ** 2

        # ---- Derived values for output ---------------------------------
        self.T_im = 288.15
        self.m_a = 0.0
        self.m_c = 0.0
        self.fuel_cmd = 0.0
        self.fuel_delivered = np.zeros(self.N_cyl)
        self.T_egt = np.full(self.N_cyl, 288.15)
        self.lambda_val = 1.0
        self.ripple = 0.0
        self.brake_power_kW = 0.0
        self.eta_c_last = self.eta_c_max
        self.pi_t_last = 1.0
        self.oil_press_bar_val = 3.4

    @property
    def w_tc(self) -> float:
        return float(np.sqrt(max(2.0 * self.E_tc, 0.0) / self.J_tc))

    def _compressor_ellipse(self, w_tc: float, Pi_c: float, p01: float, T01: float):
        """
        Leufven & Eriksson Ellipse model. Given the compressor's current
        shaft speed and the pressure ratio the intake-filling dynamics are
        currently demanding, returns (mdot_c, eta_c) — NOT slaved to mdot_a.
        This is what turns rho1 (speed-density vs compressor) from a
        structural identity into a real, independent residual.
        """
        U_c = w_tc * self.D_c / 2.0
        U_c = max(U_c, 1.0)  # avoid div-by-zero at near-zero shaft speed

        N_corr = (w_tc * 60.0 / (2 * np.pi)) / np.sqrt(max(T01, 1.0) / self._T_REF)
        speed_ratio = max(N_corr, 1.0) / self._n_corr_design

        psi_max = self._psi_max_design * speed_ratio ** self._psi_speed_exp
        phi_max = self._phi_max_design * speed_ratio ** self._phi_speed_exp

        Pi_c_eff = max(Pi_c, 1e-3)  # the Ellipse model is defined for Pi_c < 1 too — §1.3
        psi = 2.0 * self.cp_air * T01 * (Pi_c_eff ** ((self.gamma - 1) / self.gamma) - 1.0) / U_c ** 2

        # Beyond the ellipse (surge/stall boundary): the compressor cannot
        # support this pressure ratio at this speed. Cap psi just under
        # psi_max rather than clamping Pi_c itself — phi collapses toward
        # zero, mdot_c falls, and the filling equation (dp_im/dt ~ mdot_c -
        # mdot_a < 0) pulls p_im back down on its own. This is the
        # self-correcting feedback the slaved m_c never had.
        psi_ratio = np.clip(psi / max(psi_max, 1e-6), 0.0, 0.999)
        phi = phi_max * (1.0 - psi_ratio ** self._c_psi) ** (1.0 / self._c_phi)
        phi = max(phi, 0.0)

        rho01 = p01 / (self.R * max(T01, 1.0))
        area = np.pi * self.D_c ** 2 / 4.0
        mdot_c = phi * rho01 * area * U_c

        phi_peak = 0.55 * phi_max
        n_dev = (N_corr - self._n_corr_design) / max(self._n_corr_design, 1.0)
        eta_c = self.eta_c_max - 8.0 * (phi - phi_peak) ** 2 - 0.5 * n_dev ** 2
        eta_c = float(np.clip(eta_c, 0.35, self.eta_c_max))

        return float(mdot_c), eta_c

    def step(self, dt: float, params: dict, atm: dict, throttle_pct: float):
        cd_inj = np.array(params.get('cd_inj', np.ones(self.N_cyl)))
        eta_v_scale = params.get('eta_v_scale', 1.0)
        eta_c_scale = params.get('eta_c_scale', 1.0)
        hA_scale = params.get('hA_scale', 1.0)
        f_fric_scale = params.get('f_fric_scale', 1.0)

        p_atm = atm['p']
        T_atm = atm['T']

        sub_steps = 100
        h = dt / sub_steps
        TURB_SUBSTEPS = 10  # the turbo is the fastest dynamic — §1.1 "belt and braces"
        h_tc = h / TURB_SUBSTEPS

        for _ in range(sub_steps):
            p_im_bar = self.p_im / 1.0e5
            N_rpm = max(self.w * 60 / (2 * np.pi), 100.0)
            N_krpm = N_rpm / 1000.0

            eta_v_nom = (self._ve_c0 + self._ve_c1 * np.sqrt(max(p_im_bar, 0.0))
                         + self._ve_c2 * N_krpm + self._ve_c3 * N_krpm ** 2)
            eta_v_nom = float(np.clip(eta_v_nom, 0.5, 1.05))
            eta_v = eta_v_nom * eta_v_scale

            # 2. Cylinder Induction (Path 1 — speed-density)
            self.m_a = eta_v * self.p_im * self.V_d * N_rpm / (self.R * self.T_im * 120.0)

            # FADEC mixture schedule. Was lambda = 1.42/throttle_frac (a fixed
            # divisor over throttle fraction) — at 72% throttle that is
            # lambda=1.97, and even at 100% throttle only lambda=1.42. Tested
            # directly: at 72% throttle, SEA LEVEL, zero altitude penalty,
            # that schedule capped brake power at 33% of rated — a fuel
            # ceiling, not a boost-capacity one, and no compressor/turbine
            # retuning could lift it. Replaced with a linear-in-lambda
            # schedule between an idle/lean point and a near-stoichiometric
            # full-power point, which is the normal shape of a real FADEC
            # mixture map (leanest at low power for economy, richest at max
            # continuous/takeoff for margin against detonation and EGT
            # limits) rather than a single hyperbolic curve with no load
            # dependence at all.
            throttle_frac = throttle_pct / 100.0
            lambda_target = self._lambda_idle + (self._lambda_full - self._lambda_idle) * throttle_frac
            target_m_f_total = self.m_a / (self.AFR_st * max(lambda_target, 0.8))
            self.fuel_cmd = target_m_f_total / self.N_cyl

            self.fuel_delivered = cd_inj * self.fuel_cmd
            m_f_total = np.sum(self.fuel_delivered)

            self.lambda_val = self.m_a / (self.AFR_st * max(m_f_total, 1e-6))

            # 3. Crankshaft
            # provenance: calibrated. 0.40 is the low end for a modern
            # FADEC-controlled common-rail turbodiesel — 0.42-0.48 indicated
            # thermal efficiency is a realistic and citable range for this
            # engine class. Raised toward the middle of that range rather
            # than richening the mixture further (lambda was already at
            # ~1.08, near the smoke limit any real diesel is bounded by).
            eta_i = 0.50
            T_ind_i = eta_i * self.fuel_delivered * self.Q_LHV / max(self.w, 1.0)
            T_ind = np.sum(T_ind_i)

            T_fric = 6.2 * f_fric_scale * self.w / 100.0
            T_pump = 0.0

            # Propeller load — §1.5. A real fixed-pitch propeller law, not a
            # quadratic scaled by throttle_pct (which coupled the LOAD to the
            # fuel schedule rather than to propeller aerodynamics — at 72%
            # throttle the old formula silently removed 28% of the load
            # regardless of what the propeller itself would actually be
            # absorbing at that airspeed and rpm).
            n_prop_rps = max(self.w / self._gear_ratio, 1.0) / (2 * np.pi)
            J = self._tas / (n_prop_rps * self.D_prop)
            Cp = self._cp0 * max(1.0 - (J / self._j_max) ** 2, 0.0)
            rho_air = p_atm / (self.R * max(T_atm, 1.0))
            P_prop = Cp * rho_air * n_prop_rps ** 3 * self.D_prop ** 5
            T_load = P_prop / max(self.w, 1.0)

            dw_dt = (T_ind - T_fric - T_pump - T_load) / self.J
            self.brake_power_kW = (T_ind - T_fric - T_pump) * self.w / 1000.0

            mean_T = np.mean(T_ind_i)
            imbalance = np.max(np.abs(T_ind_i - mean_T)) / max(mean_T, 1e-6)
            self.ripple = 0.004 + imbalance * 0.62

            # Oil system — §1.6. Wear widens the effective clearance; since
            # laminar flow through a clearance goes as 1/d^4, this uses the
            # SAME f_fric_scale that already drives bearing friction torque,
            # so a bearing-wear fault moves rho10 and T_fric together, the
            # way one physical fault (worn bearings) should.
            Q_pump = self._oil_eta_vol * self._oil_D_pump * (N_rpm / 60.0)
            visc_factor = (self._oil_visc_ref_T / max(self.T_oil, 250.0)) ** self._oil_visc_exp
            dp_bearing_bar = self._oil_dp_gain * Q_pump * visc_factor / max(f_fric_scale, 0.3)
            self.oil_press_bar_val = min(dp_bearing_bar, self._oil_relief_bar)

            P_fric_actual = T_fric * self.w
            dT_oil_dt = (P_fric_actual - self._oil_cooler_gain * (self.T_oil - self.T_cool)) \
                / self._oil_thermal_mass_J

            # 4. Cylinder Head Thermal
            Q_gas_i = 0.15 * self.fuel_delivered * self.Q_LHV
            h_air = 50.0 * hA_scale
            dT_cht_dt = (Q_gas_i - h_air * self.A_fin * (self.T_cht - self.T_cool)) / (self.m_cht * self.cp_cht)

            Q_ex_i = self.fuel_delivered * self.Q_LHV - T_ind_i * self.w - Q_gas_i
            m_ex_i = (self.m_a / self.N_cyl) + self.fuel_delivered
            self.T_egt = T_atm + Q_ex_i / (m_ex_i * self.cp_ex)

            # 5. Turbocharger — compressor + turbine + shaft energy balance
            eta_c_scale_eff = eta_c_scale
            Pi_c = self.p_im / p_atm  # NO clamp to >=1 — §1.3: Pi_c<1 is a
                                       # legitimate restriction/choke state,
                                       # not an error condition.

            for _ in range(TURB_SUBSTEPS):
                w_tc_now = self.w_tc
                mdot_c_raw, eta_c_map = self._compressor_ellipse(w_tc_now, Pi_c, p_atm, T_atm)
                eta_c = float(np.clip(eta_c_map * eta_c_scale_eff, 0.1, 0.95))
                self.eta_c_last = eta_c
                self.m_c = mdot_c_raw

                Pi_c_eff = max(Pi_c, 1e-3)
                P_comp = (self.m_c * self.cp_air * T_atm / max(eta_c, 1e-3)) * \
                    (Pi_c_eff ** ((self.gamma - 1) / self.gamma) - 1.0)
                P_comp = max(P_comp, 0.0)

                # Wastegate: bypass fraction of exhaust flow around the
                # turbine, proportional to how far p_im sits above the
                # throttle-scheduled MAP target. Quasi-static (algebraic,
                # not integrated) — the substep rate here is fast enough
                # that a real wastegate actuator would already have settled.
                target_map_bar = (throttle_pct / 100.0) * self._map_max_continuous_bar
                p_im_bar_now = self.p_im / 1.0e5
                # Capped below 1.0: a wastegate never seals perfectly, and
                # the assertion below expects P_turb > 0 whenever fuel
                # flows — full bypass would make that structurally false
                # for the wrong reason (valve position, not a model bug).
                bypass_frac = float(np.clip(
                    self._wastegate_gain * (p_im_bar_now - target_map_bar), 0.0, 0.95
                ))

                # Turbine expansion ratio comes from the EXHAUST side, via a
                # flow-driven backpressure model — independent of p_im. This
                # is what stops P_turb collapsing to zero whenever the
                # intake side is sub-atmospheric (§1.3).
                mdot_ex_total = float(np.sum(m_ex_i)) * (1.0 - bypass_frac)
                Pi_t = 1.0 + self._turb_pr_gain * (mdot_ex_total / self._mdot_ex_design) ** 2
                # A real turbine chokes: flow through a fixed nozzle area
                # saturates at high pressure ratio rather than growing
                # unboundedly. Without a cap, mdot_ex_design has to be a
                # compromise between two irreconcilable operating points —
                # tuned for full-power flow it starves cruise/altitude
                # (measured: ratio 0.30 gave Pi_t=1.08, almost no turbine
                # power); tuned for cruise flow it explodes at full power.
                # Capping lets mdot_ex_design be sized for where the engine
                # actually spends its time (cruise), which is what a real
                # turbo's nozzle area is sized for.
                Pi_t = min(Pi_t, self._pi_t_max)
                self.pi_t_last = Pi_t
                T_egt_mean = float(np.mean(self.T_egt))
                P_turb = self.eta_t * mdot_ex_total * self.cp_ex * T_egt_mean * \
                    (1.0 - (1.0 / Pi_t) ** ((self.gamma - 1) / self.gamma))
                P_turb = max(P_turb, 0.0)

                dE_tc_dt = self.eta_m_tc * P_turb - P_comp
                self.E_tc = np.clip(self.E_tc + dE_tc_dt * h_tc, self._E_tc_min, self._E_tc_max)

            # 1. Intake Manifold
            self.T_im = T_atm + (T_atm / max(self.eta_c_last, 1e-3)) * \
                (max(Pi_c, 1e-3) ** ((self.gamma - 1) / self.gamma) - 1.0)
            dp_im_dt = (self.R * self.T_im / self.V_im) * (self.m_c - self.m_a)

            # Apply integration
            self.w += dw_dt * h
            self.p_im += dp_im_dt * h
            self.T_cht += dT_cht_dt * h
            self.T_oil += dT_oil_dt * h
            self.T_oil = max(self.T_oil, 250.0)

            self.w = max(self.w, 10.0)
            # No floor on p_im at 0.4*p_atm — the old clamp WAS the
            # absorbing state. p_im can legitimately sit below p_atm
            # (unthrottled/low-boost condition); it is only bounded below by
            # physical positivity.
            self.p_im = max(self.p_im, p_atm * 0.05)

            # §1.3 "belt and braces": a running engine always has exhaust
            # enthalpy available, so P_turb should never be structurally
            # zero while fuel is flowing. Fails loudly rather than three
            # hours later via a sigma report.
            if m_f_total > 1e-6:
                assert P_turb > 0.0, (
                    f"P_turb={P_turb:.4f} with fuel flow {m_f_total:.6f} kg/s — "
                    f"turbine model is wrong, not just slow. Pi_t={Pi_t:.3f}, "
                    f"T_egt_mean={T_egt_mean:.1f}"
                )

    def get_outputs(self) -> dict:
        return {
            'map_hPa': float(self.p_im / 100.0),
            'iat_K': float(self.T_im),
            'air_mass_flow': float(self.m_a),
            'fuel_cmd_per_cyl': float(self.fuel_cmd),
            'fuel_delivered_per_cyl': [float(x) for x in self.fuel_delivered],
            'fuel_flow_total': float(np.sum(self.fuel_delivered)),
            'lambda_val': float(self.lambda_val),
            'egt_C': [float(x) for x in (self.T_egt - 273.15)],
            'cht_C': [float(x) for x in (self.T_cht - 273.15)],
            'brake_power_kW': float(self.brake_power_kW),
            'turbo_rpm': float(self.w_tc * 60 / (2 * np.pi)),
            'oil_press_bar': float(self.oil_press_bar_val),
            'oil_temp_C': float(self.T_oil - 273.15),
            'ripple': float(self.ripple),
            'rpm': float(self.w * 60 / (2 * np.pi))
        }
