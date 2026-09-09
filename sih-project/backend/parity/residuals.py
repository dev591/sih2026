def compute_residuals(measured: dict, predicted: dict, cfg: dict, sigma_vec: list = None) -> list:
    """
    Computes the 11-dimensional parity residual vector.
    ρ = measured - predicted.
    If sigma_vec is provided, divides by the healthy-data standard deviation to output in sigma units.
    """
    N_cyl = cfg['geometry']['cylinders']
    AFR_st = cfg['fuel']['AFR_stoich']
    
    m_sd = measured['air_mass_flow']
    m_comp = predicted['air_mass_flow']
    
    m_lambda = measured['lambda_val'] * AFR_st * (measured['fuel_cmd_per_cyl'] * N_cyl)
    
    p_m_a = max(predicted['air_mass_flow'], 1e-6)
    rho1 = ((m_sd - m_comp) / p_m_a) * 340.0
    rho2 = ((m_sd - m_lambda) / p_m_a) * 340.0
    
    if cfg.get('parity_paths', {}).get('intake_restriction', False):
        # Path 4 (residual-spec.md §1): ṁ_R = C_d·A·(p_us/√(R·T_us))·Ψ(p_ds/p_us),
        # the compressible-orifice relation through a throttle body or metering
        # restriction — a genuinely independent estimate of air mass flow, not
        # a restatement of Path 1's speed-density number. No engine profile in
        # this repo declares intake_restriction (the VRDE is an unthrottled
        # FADEC diesel with no Path 4 sensor — spec: "do not fabricate rho3"),
        # so this stays unimplemented rather than emit a wrong number.
        raise NotImplementedError(
            "parity_paths.intake_restriction is set but Path 4 (orifice "
            "relation) has no implementation. Do not fabricate rho3 by "
            "reusing another path's air-mass estimate — see residual-spec.md "
            "§1."
        )
    else:
        rho3 = None
        
    energyGap = (measured['fuel_flow_kgps'] - predicted['fuel_flow_kgps']) / max(predicted['fuel_flow_kgps'], 1e-6)
    chtGap = (sum(measured['cht_C']) - sum(predicted['cht_C'])) / N_cyl
    rho4 = -energyGap * 190.0 + chtGap * 0.42
    
    p_pwr = max(predicted['brake_power_kW'], 1e-6)
    rho5 = ((measured['brake_power_kW'] - predicted['brake_power_kW']) / p_pwr) * 45.0
    
    chtMean = sum(measured['cht_C']) / N_cyl
    egtMean = sum(measured['egt_C']) / N_cyl
    rho6_9 = []
    for i in range(N_cyl):
        dCht = (measured['cht_C'][i] - chtMean) / 0.9
        dEgt = (measured['egt_C'][i] - egtMean) / 6.0
        rho6_9.append(0.45 * dCht + 0.55 * dEgt)
        
    rho10 = ((measured['oil_press_bar'] - predicted['oil_press_bar']) / max(predicted['oil_press_bar'], 1e-6)) * 40.0
    
    rho11 = (measured['ripple'] - 0.004) / 0.0125
    
    raw_rho = [
        float(rho1), 
        float(rho2), 
        float(rho3) if rho3 is not None else None, 
        float(rho4), 
        float(rho5), 
        *[float(x) for x in rho6_9], 
        float(rho10), 
        float(rho11)
    ]
    
    if sigma_vec is not None:
        for i in range(len(raw_rho)):
            if raw_rho[i] is not None and i < len(sigma_vec):
                raw_rho[i] = float(raw_rho[i] / max(sigma_vec[i], 1e-6))
                
    return raw_rho
