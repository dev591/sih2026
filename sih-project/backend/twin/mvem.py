import numpy as np

from .airpath import compressor_ellipse, propeller_cp
import random

class MVEM:
    def __init__(self, config: dict, seed: int = 42):
        self.cfg = config
        self.rng = random.Random(seed)

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
        self.h_head = mvem_cfg['thermal']['head_htc_W_per_m2K']

        # --- Cooling system. A profile with no `cooling:` block (the Rotax
        # hybrid air/liquid transfer profile) keeps the fixed-coolant air-path
        # model above, bit-identical.
        cool = mvem_cfg.get('cooling')
        self._liquid_cooling = bool(cool) and cool.get('type') == 'liquid'
        if self._liquid_cooling:
            self._cool_C = cool['coolant_thermal_mass_kJ_per_K'] * 1000.0
            self._cool_UA_head = cool['head_to_coolant_UA_W_per_K']
            self._cool_flow_exp = cool['coolant_flow_exponent']
            self._stat_open = cool['thermostat']['open_C'] + 273.15
            self._stat_full = cool['thermostat']['full_open_C'] + 273.15
            rad = cool['radiator']
            self._rad_A = rad['frontal_area_m2']
            self._rad_capture = rad['capture_fraction']
            self._rad_eff = rad['effectiveness']
            ic = cool['intercooler']
            self._ic_eff = ic['effectiveness']
            self._ic_dp_rated_pa = ic['dp_hPa_at_rated_flow'] * 100.0
            self._rated_rpm = self.cfg['ratings'].get('rated_speed_rpm', 3800.0)
            # Rated air flow, for scaling the intercooler's pressure drop.
            # Read straight from cfg: self.AFR_st is assigned further down, and
            # this block runs before it.
            self._mdot_rated = (self.cfg['fuel']['rated_fuel_flow_kgps']
                                * self.cfg['fuel']['AFR_stoich'] * 1.15)
        # Reported diagnostics — meaningless until the first step() on a
        # profile without liquid cooling, so they stay None there.
        self.thermostat_frac = 0.0
        self.T_comp_out = 288.15
        self.p_comp_out = 101325.0

        self.Q_LHV = self.cfg['fuel']['Q_LHV_J_per_kg']
        self.AFR_st = self.cfg['fuel']['AFR_stoich']
        fuel_cfg = self.cfg['fuel']

        # ---------------------------------------------------------------
        # COMBUSTION STRATEGY — selected by profile.cycle, not one model bent
        # to cover both. A compression-ignition diesel and a spark-ignition
        # engine schedule fuel by fundamentally different logic; see step()
        # for the branch. Defaults to 'diesel' (VRDE is the primary profile).
        # ---------------------------------------------------------------
        self._cycle = self.cfg.get('profile', {}).get('cycle', 'diesel')

        if self._cycle == 'spark_ignition':
            # AIR-LED: hold a target lambda, derive fuel from MEASURED air
            # mass. Genuinely correct here — a spark-ignition engine has a
            # real stoichiometric setpoint to aim at. provenance: assumed,
            # see the comment in engine_rotax_914.yaml's fuel block.
            self._lambda_full = fuel_cfg.get('lambda_full_power', 0.98)
            self._lambda_idle = fuel_cfg.get('lambda_idle', 1.35)
        else:
            # FUEL-LED: FADEC power-lever position commands an injection
            # quantity directly; boost is a consequence, not an input. See the
            # comment in engine_vrde_180.yaml's fuel block for why, and
            # step() for the mechanism (including the smoke limiter, which is
            # how boost still constrains available power on a diesel).
            self._rated_fuel_kgps = fuel_cfg.get('rated_fuel_flow_kgps', 0.006242)
            self._idle_fuel_frac = fuel_cfg.get('idle_fuel_frac', 0.12)
            self._lambda_smoke_limit = fuel_cfg.get('lambda_smoke_limit', 1.15)

        # Indicated thermal efficiency — was a bare local `eta_i = 0.50` in
        # step(), duplicated by hand wherever a nominal value was needed. Now
        # the single source of truth shared with parity Path A
        # (twin/airpath.py::indicated_power_kw) — see the comment on
        # mvem.combustion.eta_i_nominal in the profile for why sharing it is
        # not degenerate.
        self._eta_i = mvem_cfg.get('combustion', {}).get('eta_i_nominal', 0.50)

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
        # 'takeoff' has been in the profile from the start (a real two-tier
        # FADEC boost limit: a higher short-duration rating for takeoff/max
        # power, a lower one for sustained cruise, protecting the engine from
        # thermal/mechanical fatigue at sustained high boost) but was never
        # read anywhere — the wastegate scaled toward max_continuous at every
        # throttle setting, including 100%. Falls back to max_continuous if a
        # profile doesn't define it, so this is not a behaviour change for any
        # config that lacks the key.
        self._map_takeoff_bar = self.cfg['limits']['manifold_pressure_hPa'].get(
            'takeoff', self.cfg['limits']['manifold_pressure_hPa']['max_continuous']
        ) / 1000.0
        # Boost controller gains. A profile without a wastegate block (Rotax)
        # keeps the original proportional-only law, bit-identical.
        wg = mvem_cfg.get('wastegate', {})
        self._wastegate_gain = wg.get('proportional_gain_per_bar', 14.0)
        t_i = wg.get('integral_time_s')
        self._wastegate_ki = (self._wastegate_gain / t_i) if t_i else 0.0
        self._wg_int = 0.0

        # --- Propeller load — §1.5, now through a reduction gearbox and, where
        # the profile declares one, a constant-speed propeller with a governor.
        # The power coefficient is twin/airpath.py::propeller_cp, shared with
        # parity Path B of rho5 so plant and estimator evaluate one law.
        prop = self.cfg['propeller']
        self._prop = prop
        self.D_prop = prop['diameter_m']
        self._tas = prop['assumed_tas_mps']
        self._tas_last = float(self._tas)
        self._gear_ratio = prop.get('gear_ratio', 1.0)
        self._constant_speed = prop.get('type') == 'constant_speed'

        # Reduction gearbox. A profile without a gearbox block (the Rotax
        # transfer profile) keeps efficiency 1.0 and no oil node, so its
        # numbers are bit-identical to before this change.
        gb = mvem_cfg.get('gearbox')
        self._has_gearbox = gb is not None
        self._eta_gb = gb.get('efficiency', 1.0) if gb else 1.0
        if gb:
            self._gb_thermal_mass_J = gb['oil_thermal_mass_kJ_per_K'] * 1000.0
            self._gb_UA = gb['oil_to_coolant_UA_W_per_K']
        self.P_prop_last = 0.0

        if self._constant_speed:
            gov = prop['governor']
            self._gov_x = list(gov['schedule_throttle_frac'])
            self._gov_y = list(gov['schedule_prop_rpm'])
            self._gov_kp = gov['kp_deg_per_unit_error']
            self._gov_ki = gov['ki_deg_per_s_per_unit_error']
            self._pitch_rate = gov['pitch_rate_limit_deg_per_s']
            self._beta_min = prop['beta_fine_stop_deg']
            self._beta_max = prop['beta_coarse_stop_deg']
            # Propeller inertia, reflected to the crank through the gearbox.
            self.J = self.J + prop.get('inertia_kgm2', 0.0) / self._gear_ratio ** 2

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
        self.w = self.cfg['ratings'].get('rated_speed_rpm', 3800.0) * 2 * np.pi / 60
        self.T_cht = np.full(self.N_cyl, 363.15 + 50)
        # Governor state. Pitch starts mid-range and the PI loop settles it
        # within seconds; the trim holds that start point so the integrator
        # carries only the correction.
        self.beta_deg = 25.0
        self._beta_trim = self.beta_deg
        self._gov_int = 0.0
        self.T_gb = self.T_cool + 10.0
        # Coolant is a STATE on a liquid-cooled profile. Initialised at the
        # previous fixed value so a fresh model starts where the old one sat.
        self.T_cool_state = self.T_cool

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
        # Sea-level ISA until the first step() supplies the real atmosphere, so
        # get_outputs() is safe to call on a freshly constructed model.
        self.p_amb = 101325.0
        self.T_amb = 288.15
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
        self.mdot_ex_last = 0.0
        self.bypass_frac_last = 0.0
        self.oil_press_bar_val = 3.4

    def prop_rpm_setpoint(self, throttle_frac: float) -> float:
        return float(np.interp(throttle_frac, self._gov_x, self._gov_y))

    def _govern(self, n_prop_rps: float, throttle_frac: float, h: float) -> None:
        """
        PI governor on fractional prop-speed error, acting on blade pitch.

        Overspeed coarsens pitch (more load), underspeed fines it. Pitch is
        rate-limited like a hydraulic pitch-change mechanism and bounded by the
        fine/coarse stops, with the integrator frozen while on a stop so it
        cannot wind up. An engine too weak to reach its setpoint sits on the
        fine stop below it: correct behaviour, not a control failure.
        """
        n_set = self.prop_rpm_setpoint(throttle_frac) / 60.0
        err = (n_prop_rps - n_set) / n_set
        integ = self._gov_int + err * h
        beta_cmd = self._beta_trim + self._gov_kp * err + self._gov_ki * integ
        if self._beta_min < beta_cmd < self._beta_max:
            self._gov_int = integ
        beta_cmd = min(max(beta_cmd, self._beta_min), self._beta_max)
        max_step = self._pitch_rate * h
        self.beta_deg += min(max(beta_cmd - self.beta_deg, -max_step), max_step)

    @property
    def w_tc(self) -> float:
        return float(np.sqrt(max(2.0 * self.E_tc, 0.0) / self.J_tc))

    def _compressor_ellipse(self, w_tc: float, Pi_c: float, p01: float, T01: float,
                            phi_scale: float = 1.0):
        """
        Leufven & Eriksson Ellipse model — see twin/airpath.py, which now owns
        the equations.

        The plant and the PARITY RESIDUAL GENERATOR must evaluate the same
        compressor map: rho1 compares an induction-side estimate against a
        compressor-side one, and if the two sides used separate copies of these
        equations they would drift and rho1 would be measuring the drift rather
        than the engine. Hence one implementation, called from both.
        """
        return compressor_ellipse(self.cfg, float(w_tc), float(Pi_c),
                                  float(p01), float(T01), float(phi_scale))

    def step(self, dt: float, params: dict, atm: dict, throttle_pct: float,
             tas_mps: float | None = None):
        cd_inj = np.array(params.get('cd_inj', np.ones(self.N_cyl)))
        eta_v_scale = params.get('eta_v_scale', 1.0)
        eta_c_scale = params.get('eta_c_scale', 1.0)
        hA_scale = params.get('hA_scale', 1.0)
        f_fric_scale = params.get('f_fric_scale', 1.0)
        oil_pump_scale = params.get('oil_pump_scale', 1.0)
        # Cooling faults are now two physically distinct mechanisms instead of
        # one abstract conductance: a blocked/fouled radiator core, and a
        # degraded coolant pump.
        rad_eff_scale = params.get('rad_eff_scale', 1.0)
        cool_pump_scale = params.get('cool_pump_scale', 1.0)
        fuel_rail_scale = params.get('fuel_rail_scale', 1.0)
        misfire_prob = params.get('misfire_prob', [0.0] * self.N_cyl)
        detonation_sev = params.get('detonation_sev', [0.0] * self.N_cyl)

        p_atm = atm['p']
        T_atm = atm['T']
        # True airspeed is a scenario input (there is no airframe model). Call
        # sites that pass none get the profile's default.
        tas = self._tas if tas_mps is None else float(tas_mps)
        self._tas_last = tas

        # Retained so get_outputs() can report them. These are environment, not
        # engine state, but they ARE separately instrumented on a real
        # installation (the Austro E4 log carries "Ambient Pressure", and OAT is
        # standard), and parity Path 2 needs both: the compressor map is
        # evaluated at INLET conditions, so it cannot be closed from
        # manifold-side channels alone.
        self.p_amb = float(p_atm)
        self.T_amb = float(T_atm)

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

            throttle_frac = throttle_pct / 100.0

            if self._cycle == 'spark_ignition':
                # AIR-LED mixture schedule — correct for a spark-ignition
                # engine, which holds a target lambda and derives fuel from
                # measured air mass. Linear-in-lambda between an idle/lean
                # point and a near-stoichiometric full-power point, the normal
                # shape of a real FADEC/ECU mixture map.
                lambda_target = self._lambda_idle + (self._lambda_full - self._lambda_idle) * throttle_frac
                target_m_f_total = self.m_a / (self.AFR_st * max(lambda_target, 0.8))
            else:
                # FUEL-LED schedule — a real compression-ignition diesel.
                #
                # Was: hold a TARGET LAMBDA from throttle, derive fuel from
                # measured air mass — spark-ignition logic. Physically
                # impossible for a diesel: compression ignition has no
                # stoichiometric setpoint, runs unthrottled and always lean,
                # and sizes fuel to the injector, not to a target air-fuel
                # ratio. See the comment on engine_vrde_180.yaml's fuel block
                # for the full argument and sourcing.
                #
                # Now: the power lever commands an injection quantity
                # directly, linear between an idle floor and the rated
                # design point —
                target_m_f_total = (
                    self._idle_fuel_frac * self._rated_fuel_kgps
                    + (1.0 - self._idle_fuel_frac) * self._rated_fuel_kgps * throttle_frac
                )
                # — capped by the SMOKE LIMITER: however much fuel the power
                # lever asks for, the FADEC will not inject more than the
                # available (measured) air mass can burn at or leaner than the
                # smoke-limit ratio. This is the mechanism that makes boost a
                # CEILING on power rather than a target: above critical
                # altitude, or under a compressor fault, less air means a
                # tighter cap, means less fuel is ALLOWED regardless of what
                # the throttle schedule asked for — the correct physical
                # reason "flat power to 11,000 ft, then falls" holds, now
                # enforced at the fuel-metering point.
                smoke_limit_m_f = self.m_a / (self.AFR_st * self._lambda_smoke_limit)
                target_m_f_total = min(target_m_f_total, smoke_limit_m_f)
            self.fuel_cmd = target_m_f_total / self.N_cyl

            self.fuel_delivered = cd_inj * self.fuel_cmd * fuel_rail_scale
            m_f_total = np.sum(self.fuel_delivered)

            self.lambda_val = self.m_a / (self.AFR_st * max(m_f_total, 1e-6))

            # 3. Crankshaft
            # provenance: calibrated. 0.40 is the low end for a modern
            # FADEC-controlled common-rail turbodiesel — 0.42-0.48 indicated
            # thermal efficiency is a realistic and citable range for this
            # engine class. Raised toward the middle of that range rather
            # than richening the mixture further (lambda was already at
            # ~1.08, near the smoke limit any real diesel is bounded by).
            eta_i = self._eta_i
            T_ind_i = eta_i * self.fuel_delivered * self.Q_LHV / max(self.w, 1.0)
            
            # Apply misfire and detonation (discrete/cycle-level)
            Q_gas_mult = np.ones(self.N_cyl)
            for cyl in range(self.N_cyl):
                if self.rng.random() < misfire_prob[cyl]:
                    self.fuel_delivered[cyl] = 0.0
                    T_ind_i[cyl] = 0.0
                else:
                    k_sev = detonation_sev[cyl]
                    if k_sev > 0.0 and self.rng.random() < k_sev * 0.3:
                        T_ind_i[cyl] *= (1.0 - k_sev * 0.4)
                        Q_gas_mult[cyl] = (1.0 + k_sev * 0.8)

            m_f_total = np.sum(self.fuel_delivered) # Recompute after misfires
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
            J = tas / (n_prop_rps * self.D_prop)
            if self._constant_speed:
                self._govern(n_prop_rps, throttle_frac, h)
                Cp = propeller_cp(self._prop, J, self.beta_deg)
            else:
                Cp = propeller_cp(self._prop, J)
            rho_air = p_atm / (self.R * max(T_atm, 1.0))
            P_prop = Cp * rho_air * n_prop_rps ** 3 * self.D_prop ** 5
            self.P_prop_last = P_prop
            # Crank-side load: propeller power plus the gearbox's own loss.
            T_load = P_prop / (self._eta_gb * max(self.w, 1.0))

            dw_dt = (T_ind - T_fric - T_pump - T_load) / self.J
            self.brake_power_kW = (T_ind - T_fric - T_pump) * self.w / 1000.0

            mean_T = np.mean(T_ind_i)
            imbalance = np.max(np.abs(T_ind_i - mean_T)) / max(mean_T, 1e-6)
            self.ripple = 0.004 + imbalance * 0.62

            # Everything that rejects heat "to coolant" uses the coolant STATE
            # on a liquid-cooled profile, and the old fixed constant otherwise.
            self.T_cool_sink = self.T_cool_state if self._liquid_cooling else self.T_cool

            # Oil system — §1.6. Wear widens the effective clearance; since
            # laminar flow through a clearance goes as 1/d^4, this uses the
            # SAME f_fric_scale that already drives bearing friction torque,
            # so a bearing-wear fault moves rho10 and T_fric together, the
            # way one physical fault (worn bearings) should.
            Q_pump = self._oil_eta_vol * self._oil_D_pump * (N_rpm / 60.0) * oil_pump_scale
            visc_factor = (self._oil_visc_ref_T / max(self.T_oil, 250.0)) ** self._oil_visc_exp
            dp_bearing_bar = self._oil_dp_gain * Q_pump * visc_factor / max(f_fric_scale, 0.3)
            self.oil_press_bar_val = min(dp_bearing_bar, self._oil_relief_bar)

            P_fric_actual = T_fric * self.w
            dT_oil_dt = (P_fric_actual - self._oil_cooler_gain * (self.T_oil - self.T_cool_sink)) \
                / self._oil_thermal_mass_J

            # 4. Cylinder Head Thermal
            Q_gas_i = 0.15 * self.fuel_delivered * self.Q_LHV * Q_gas_mult
            if self._liquid_cooling:
                # Conductance follows coolant flow (pump is speed-driven) as
                # Re^0.8 — Dittus-Boelter. hA_scale remains the UKF's health
                # parameter on this conductance; a degraded PUMP lands here,
                # while a blocked RADIATOR acts on the radiator term below.
                flow_frac = min(max(N_rpm / self._rated_rpm, 0.05), 1.2) * cool_pump_scale
                ua_head = self._cool_UA_head * hA_scale * flow_frac ** self._cool_flow_exp
                q_head_i = ua_head * (self.T_cht - self.T_cool_state)
                dT_cht_dt = (Q_gas_i - q_head_i) / (self.m_cht * self.cp_cht)
            else:
                h_air = self.h_head * hA_scale
                q_head_i = h_air * self.A_fin * (self.T_cht - self.T_cool)
                dT_cht_dt = (Q_gas_i - q_head_i) / (self.m_cht * self.cp_cht)

            Q_ex_i = self.fuel_delivered * self.Q_LHV - T_ind_i * self.w - Q_gas_i
            m_ex_i = (self.m_a / self.N_cyl) + self.fuel_delivered
            self.T_egt = T_atm + Q_ex_i / (m_ex_i * self.cp_ex)

            # 5. Turbocharger — compressor + turbine + shaft energy balance
            eta_c_scale_eff = eta_c_scale
            # FLOW-CAPACITY LOSS, coupled to the efficiency loss.
            #
            # eta_c_scale alone was an incomplete model of compressor
            # degradation. Fouling and erosion move the map DOWN and to the
            # LEFT — efficiency and swallowing capacity fall together — but
            # scaling efficiency alone leaves the flow map untouched, and the
            # flow map is what parity Path 2 reads. Measured consequence: a 25%
            # "compressor fault" shifted rho1 by 0.4 sigma, i.e. the fault was
            # invisible to the residual whose entire job is to catch it, and
            # the shift was the WRONG SIGN. The incidence matrix has always
            # predicted rho1 = -2 (strong negative) for turbo degradation, so
            # the signature table was right and the simulator was wrong.
            #
            # Coupled rather than made an independent parameter: fouling does
            # both at once, and a free extra degree of freedom would need a
            # matching state in the UKF's theta vector to be identifiable.
            flow_ratio = self.cfg['mvem']['compressor'].get(
                'flow_capacity_loss_ratio', 0.0)
            phi_c_scale = 1.0 - flow_ratio * (1.0 - eta_c_scale)
            phi_c_scale = float(np.clip(phi_c_scale, 0.3, 1.0))
            # Compressor outlet pressure = manifold pressure plus the
            # intercooler core's drop (flow-squared). Pi_c is the COMPRESSOR's
            # pressure ratio, so it is taken here and not at the manifold.
            if self._liquid_cooling:
                flow_ratio_ic = max(self.m_a, 0.0) / max(self._mdot_rated, 1e-6)
                self.p_comp_out = self.p_im + self._ic_dp_rated_pa * flow_ratio_ic ** 2
            else:
                self.p_comp_out = self.p_im
            Pi_c = self.p_comp_out / p_atm  # NO clamp to >=1 — §1.3: Pi_c<1 is
                                       # a legitimate restriction/choke state,
                                       # not an error condition.

            for _ in range(TURB_SUBSTEPS):
                w_tc_now = self.w_tc
                mdot_c_raw, eta_c_map = self._compressor_ellipse(
                    w_tc_now, Pi_c, p_atm, T_atm, phi_c_scale)
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
                # Below ~90% throttle, target scales to max_continuous exactly
                # as before (Gate 1 checks MAP at 72% throttle against that
                # calibration and is unaffected). Above it, target ramps on
                # toward the takeoff limit — the short-duration rating a real
                # FADEC allows for max power, which this profile has always
                # specified and nothing was reading.
                cruise_frac = 0.90
                if throttle_frac <= cruise_frac:
                    target_map_bar = throttle_frac * self._map_max_continuous_bar
                else:
                    extra = (throttle_frac - cruise_frac) / (1.0 - cruise_frac)
                    target_map_bar = (
                        self._map_max_continuous_bar
                        + extra * (self._map_takeoff_bar - self._map_max_continuous_bar)
                    )
                p_im_bar_now = self.p_im / 1.0e5
                # Capped below 1.0: a wastegate never seals perfectly, and
                # the assertion below expects P_turb > 0 whenever fuel
                # flows — full bypass would make that structurally false
                # for the wrong reason (valve position, not a model bug).
                wg_err = p_im_bar_now - target_map_bar
                wg_int = self._wg_int + wg_err * h_tc
                bypass_cmd = self._wastegate_gain * wg_err + self._wastegate_ki * wg_int
                bypass_frac = float(np.clip(bypass_cmd, 0.0, 0.95))
                # Conditional integration: hold the integrator while the valve
                # sits on an end stop (e.g. fully shut above critical altitude),
                # so it cannot wind up and overshoot on the way back.
                if 0.0 < bypass_cmd < 0.95:
                    self._wg_int = wg_int

                # Turbine expansion ratio comes from the EXHAUST side, via a
                # flow-driven backpressure model — independent of p_im. This
                # is what stops P_turb collapsing to zero whenever the
                # intake side is sub-atmospheric (§1.3).
                mdot_ex_total = float(np.sum(m_ex_i)) * (1.0 - bypass_frac)
                self.mdot_ex_last = mdot_ex_total
                self.bypass_frac_last = bypass_frac
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

            # 1. Intake manifold, now through the charge-air cooler.
            # Compressor DELIVERY temperature first...
            self.T_comp_out = T_atm + (T_atm / max(self.eta_c_last, 1e-3)) * \
                (max(Pi_c, 1e-3) ** ((self.gamma - 1) / self.gamma) - 1.0)
            if self._liquid_cooling:
                # ...then the intercooler removes `effectiveness` of the
                # available temperature rise above ambient. IAT is therefore
                # genuinely post-intercooler, as telemetry-schema.md has always
                # said it was, and the charge is denser — which is the physical
                # reason this phase gains power rather than a tuned constant.
                self.T_im = self.T_comp_out - self._ic_eff * (self.T_comp_out - T_atm)
            else:
                self.T_im = self.T_comp_out
            dp_im_dt = (self.R * self.T_im / self.V_im) * (self.m_c - self.m_a)

            # Apply integration
            self.w += dw_dt * h
            self.p_im += dp_im_dt * h
            self.T_cht += dT_cht_dt * h
            self.T_oil += dT_oil_dt * h
            self.T_oil = max(self.T_oil, 250.0)
            if self._has_gearbox:
                q_gb = (1.0 / self._eta_gb - 1.0) * max(P_prop, 0.0)
                self.T_gb += (q_gb - self._gb_UA * (self.T_gb - self.T_cool_sink)) \
                    / self._gb_thermal_mass_J * h
            else:
                q_gb = 0.0

            if self._liquid_cooling:
                # THERMOSTAT: proportional opening between its two published
                # stage temperatures. Closed, the radiator is bypassed, which is
                # what stops the (deliberately take-off-sized) core overcooling
                # the engine at altitude.
                self.thermostat_frac = float(np.clip(
                    (self.T_cool_state - self._stat_open)
                    / (self._stat_full - self._stat_open), 0.0, 1.0))
                mdot_ram = (p_atm / (self.R * max(T_atm, 1.0))) * tas \
                    * self._rad_A * self._rad_capture
                q_rad = (self.thermostat_frac * self._rad_eff * rad_eff_scale
                         * mdot_ram * self.cp_air
                         * (self.T_cool_state - T_atm))
                q_in = float(np.sum(q_head_i)) \
                    + self._oil_cooler_gain * (self.T_oil - self.T_cool_state) \
                    + q_gb
                self.T_cool_state += (q_in - max(q_rad, 0.0)) / self._cool_C * h
                self.T_cool_state = min(max(self.T_cool_state, T_atm), 473.15)

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
            'mdot_ex_kgps': float(self.mdot_ex_last),
            'wastegate_bypass_frac': float(self.bypass_frac_last),
            'oil_press_bar': float(self.oil_press_bar_val),
            'oil_temp_C': float(self.T_oil - 273.15),
            'ripple': float(self.ripple),
            'rpm': float(self.w * 60 / (2 * np.pi)),
            # Compressor INLET conditions — parity Path 2's own sensors.
            'p_amb_hPa': float(self.p_amb / 100.0),
            'oat_K': float(self.T_amb),
            # Drivetrain and air data.
            'prop_rpm': float(self.w / self._gear_ratio * 60 / (2 * np.pi)),
            'blade_angle_deg': float(self.beta_deg) if self._constant_speed else None,
            'gearbox_oil_C': float(self.T_gb - 273.15) if self._has_gearbox else None,
            'prop_power_kW': float(self.P_prop_last / 1000.0),
            'tas_mps': float(self._tas_last),
            # Cooling and charge air. None on a profile with no coolant loop —
            # a channel with no sensor reads null rather than a made-up number.
            'coolant_temp_C': (float(self.T_cool_state - 273.15)
                               if self._liquid_cooling else None),
            'thermostat_frac': (float(self.thermostat_frac)
                                if self._liquid_cooling else None),
            'comp_out_T_K': float(self.T_comp_out),
            'comp_out_p_hPa': float(self.p_comp_out / 100.0),
        }
