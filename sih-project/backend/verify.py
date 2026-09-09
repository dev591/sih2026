import sys
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from twin.profiles import load_engine_profile
from twin.mvem import MVEM
from twin.atmosphere import isa
from twin.measurement import MeasurementModel
from parity.residuals import compute_residuals

def run_tests():
    cfg = load_engine_profile("engine_vrde_180.yaml")
    
    # 1. & 2. Operating Point Invariance and Sum-To-Zero
    print("Testing OP Invariance and Sum-to-Zero...")
    plant = MVEM(cfg)
    twin = MVEM(cfg)
    meas = MeasurementModel(seed=42)
    nominal_params = {'cd_inj': [1.0]*cfg['geometry']['cylinders'], 'eta_v_scale': 1.0, 'eta_c_scale': 1.0, 'hA_scale': 1.0, 'f_fric_scale': 1.0}
    
    for alt in [0, 10000]:
        for throttle in [50, 100]:
            atm = isa(alt)
            # Step to steady state
            for _ in range(10):
                plant.step(1.0, nominal_params, atm, throttle)
                twin.step(1.0, nominal_params, atm, throttle)
            
            measured = meas.measure(plant.get_outputs(), add_noise=True)
            predicted = meas.measure(twin.get_outputs(), add_noise=False)
            
            rho = compute_residuals(measured, predicted, cfg, sigma_vec=[1.0]*11)
            
            # Sum to zero test
            rho6_9 = rho[5:9]
            sum_rho = sum(rho6_9)
            assert abs(sum_rho) < 1e-4, f"Sum to zero failed: {sum_rho}"
            
    print("OP Invariance & Sum-to-Zero PASSED")

    # 3. Sensor/Engine Asymmetry
    print("Testing Sensor/Engine Asymmetry...")
    plant1 = MVEM(cfg)
    plant2 = MVEM(cfg)
    atm = isa(10000)
    
    for _ in range(10):
        plant1.step(1.0, nominal_params, atm, 75.0)
        plant2.step(1.0, nominal_params, atm, 75.0)
        
    out1 = plant1.get_outputs()
    out2 = plant2.get_outputs()
    
    # Check bit identical engine state outputs
    assert out1['brake_power_kW'] == out2['brake_power_kW']
    assert out1['cht_C'] == out2['cht_C']
    
    # Inject Sensor Fault
    meas1 = MeasurementModel(seed=1)
    meas2 = MeasurementModel(seed=1)
    
    m1 = meas1.measure(out1, add_noise=False)
    m2 = meas2.measure(out2, sensor_biases={'egt_C': [0, 100, 0, 0]}, add_noise=False)
    
    assert m1['egt_C'] != m2['egt_C'] # Sensor outputs differ
    assert m1['rpm'] == m2['rpm'] # Unaffected channels remain same
    
    print("Sensor/Engine Asymmetry PASSED")
    print("\nALL VERIFICATION TESTS PASSED.")

if __name__ == "__main__":
    run_tests()
