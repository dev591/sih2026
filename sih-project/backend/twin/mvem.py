import numpy as np

class MVEM:
    def __init__(self, config: dict):
        self.cfg = config
        
        # MVEM parameters
        mvem_cfg = self.cfg.get('mvem', {})
        self.R = mvem_cfg.get('R_air_J_per_kgK', 287.05)
        self.gamma = mvem_cfg.get('gamma_air', 1.4)
        self.cp_air = mvem_cfg.get('cp_air_J_per_kgK', 1005.0)
        self.cp_ex = mvem_cfg.get('cp_exhaust_J_per_kgK', 1150.0)
        
        self.V_im = mvem_cfg.get('intake_manifold_volume_m3', 4.0e-3)
        self.V_d = self.cfg['geometry']['displacement_m3']
        self.N_cyl = self.cfg['geometry']['cylinders']
        
        self.J = mvem_cfg['crankshaft']['inertia_kgm2']
        self.J_tc = mvem_cfg['compressor']['inertia_kgm2']
        
        self.m_cht = mvem_cfg['thermal']['head_mass_kg']
        self.cp_cht = mvem_cfg['thermal']['head_cp_J_per_kgK']
        self.A_fin = mvem_cfg['thermal']['fin_area_m2']
        self.T_cool = mvem_cfg['thermal']['coolant_temp_K']
        
        self.Q_LHV = self.cfg['fuel']['Q_LHV_J_per_kg']
        self.AFR_st = self.cfg['fuel']['AFR_stoich']
        
        self.eta_v_nom = mvem_cfg['volumetric_efficiency']['nominal']
        self.eta_c_nom = mvem_cfg['compressor']['efficiency_nominal']
        self.eta_m_tc = mvem_cfg['compressor']['mech_efficiency']
        
        # States
        self.p_im = 101325.0
        self.w = 3800.0 * 2 * np.pi / 60
        self.T_cht = np.full(self.N_cyl, 363.15 + 50)
        self.w_tc = 118000.0 * 2 * np.pi / 60
        
        # Derived values for output
        self.T_im = 288.15
        self.m_a = 0.0
        self.m_c = 0.0
        self.fuel_cmd = 0.0
        self.fuel_delivered = np.zeros(self.N_cyl)
        self.T_egt = np.full(self.N_cyl, 288.15)
        self.lambda_val = 1.0
        self.ripple = 0.0
        self.brake_power_kW = 0.0

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
        
        for _ in range(sub_steps):
            eta_v = self.eta_v_nom * eta_v_scale
            N_rpm = max(self.w * 60 / (2 * np.pi), 100.0)
            
            # 2. Cylinder Induction
            self.m_a = eta_v * self.p_im * self.V_d * N_rpm / (self.R * self.T_im * 120.0)
            
            target_m_f_total = (throttle_pct / 100.0) * self.m_a / (self.AFR_st * 1.42)
            self.fuel_cmd = target_m_f_total / self.N_cyl
            
            self.fuel_delivered = cd_inj * self.fuel_cmd
            m_f_total = np.sum(self.fuel_delivered)
            
            self.lambda_val = self.m_a / (self.AFR_st * max(m_f_total, 1e-6))
            
            # 3. Crankshaft
            eta_i = 0.40
            T_ind_i = eta_i * self.fuel_delivered * self.Q_LHV / max(self.w, 1.0)
            T_ind = np.sum(T_ind_i)
            
            T_fric = 6.2 * f_fric_scale * self.w / 100.0
            T_pump = 0.0
            T_load = (throttle_pct / 100.0) * 100.0 * (self.w / 380.0)**2
            
            dw_dt = (T_ind - T_fric - T_pump - T_load) / self.J
            self.brake_power_kW = (T_ind - T_fric - T_pump) * self.w / 1000.0
            
            mean_T = np.mean(T_ind_i)
            imbalance = np.max(np.abs(T_ind_i - mean_T)) / max(mean_T, 1e-6)
            self.ripple = 0.004 + imbalance * 0.62
            
            # 4. Cylinder Head Thermal
            Q_gas_i = 0.15 * self.fuel_delivered * self.Q_LHV
            h_air = 50.0 * hA_scale
            dT_cht_dt = (Q_gas_i - h_air * self.A_fin * (self.T_cht - self.T_cool)) / (self.m_cht * self.cp_cht)
            
            Q_ex_i = self.fuel_delivered * self.Q_LHV - T_ind_i * self.w - Q_gas_i
            m_ex_i = (self.m_a / self.N_cyl) + self.fuel_delivered
            self.T_egt = T_atm + Q_ex_i / (m_ex_i * self.cp_ex)
            
            # 5. Turbocharger Shaft
            eta_c = self.eta_c_nom * eta_c_scale
            p_ratio = max(self.p_im / p_atm, 1.0)
            P_comp = (self.m_a * self.cp_air * T_atm / eta_c) * (p_ratio ** ((self.gamma - 1) / self.gamma) - 1.0)
            
            eta_t = 0.7
            T_egt_mean = np.mean(self.T_egt)
            p_ratio_t = max(self.p_im / p_atm, 1.0)
            P_turb = eta_t * np.sum(m_ex_i) * self.cp_ex * T_egt_mean * (1.0 - (1.0 / p_ratio_t) ** ((self.gamma - 1) / self.gamma))
            
            dw_tc_dt = (self.eta_m_tc * P_turb - P_comp) / (self.J_tc * max(self.w_tc, 1.0))
            
            self.m_c = self.m_a * (self.w_tc / (118000 * 2 * np.pi / 60))
            
            # 1. Intake Manifold
            self.T_im = T_atm + (T_atm / eta_c) * (p_ratio ** ((self.gamma - 1) / self.gamma) - 1.0)
            dp_im_dt = (self.R * self.T_im / self.V_im) * (self.m_c - self.m_a)
            
            # Apply integration
            self.w += dw_dt * h
            self.w_tc += dw_tc_dt * h
            self.p_im += dp_im_dt * h
            self.T_cht += dT_cht_dt * h
            
            self.w = max(self.w, 10.0)
            self.w_tc = max(self.w_tc, 10.0)
            self.p_im = max(self.p_im, p_atm * 0.4)

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
            'oil_press_bar': 3.42,
            'oil_temp_C': 96.3,
            'ripple': float(self.ripple),
            'rpm': float(self.w * 60 / (2 * np.pi))
        }
