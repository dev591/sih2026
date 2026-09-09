import asyncio
import time
import json
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from twin.profiles import load_engine_profile
from twin.atmosphere import isa
from twin.mvem import MVEM
from twin.measurement import MeasurementModel
from parity.residuals import compute_residuals

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load config globally to avoid reloading file per connection
cfg = load_engine_profile("engine_vrde_180.yaml")

# Try to load sigma vector
sigma_path = Path(__file__).parent / "config" / "sigma_vector.json"
try:
    with open(sigma_path, "r") as f:
        sigma_vec = json.load(f)
except FileNotFoundError:
    sigma_vec = [1.0] * 11

@app.websocket("/ws/telemetry")
async def telemetry_endpoint(websocket: WebSocket):
    await websocket.accept()
    
    # Initialize simulation state per connection to avoid race conditions
    plantA = MVEM(cfg)
    twinA = MVEM(cfg)
    plantB = MVEM(cfg)

    measureA = MeasurementModel(seed=42)
    measureB = MeasurementModel(seed=100)
    measureTwin = MeasurementModel(seed=999)
    
    start_time = time.time()
    
    try:
        while True:
            t = time.time() - start_time
            
            # Scenario configuration (healthy engine for now)
            altitude_ft = 18000.0
            throttle_pct = 72.0
            atm = isa(altitude_ft, isa_offset_K=0.0) # Same atm for A and B
            
            nominal_params = {
                'cd_inj': [1.0] * cfg['geometry']['cylinders'],
                'eta_v_scale': 1.0,
                'eta_c_scale': 1.0,
                'hA_scale': 1.0,
                'f_fric_scale': 1.0
            }
            
            # Engine faults can be injected here by modifying plantA_params
            plantA_params = nominal_params.copy()
            
            # Sensor faults can be injected here
            sensor_biasesA = {'cht_C': [0.0]*cfg['geometry']['cylinders'], 'egt_C': [0.0]*cfg['geometry']['cylinders'], 'map_hPa': 0.0, 'lambda': 0.0}
            
            # Step Physics Integration (1 second step)
            plantA.step(1.0, plantA_params, atm, throttle_pct)
            twinA.step(1.0, nominal_params, atm, throttle_pct)
            plantB.step(1.0, nominal_params, atm, throttle_pct)
            
            # Measurement Layer
            outputs_plantA = plantA.get_outputs()
            outputs_twinA = twinA.get_outputs()
            outputs_plantB = plantB.get_outputs()
            
            measuredA = measureA.measure(outputs_plantA, sensor_biases=sensor_biasesA, add_noise=True)
            predictedA = measureTwin.measure(outputs_twinA, add_noise=False) # Twin is noise-free
            measuredB = measureB.measure(outputs_plantB, add_noise=True)
            
            # Calculate Parity Residuals
            rho = compute_residuals(measuredA, predictedA, cfg, sigma_vec=sigma_vec)
            
            # Construct schemas
            slow = {
                "schema": "pramana.slow.v1",
                "t": float(t),
                "engine_id": "A",
                "seq": int(t),
                "rpm": measuredA['rpm'],
                "map_hPa": measuredA['map_hPa'],
                "iat_K": measuredA['iat_K'],
                "cht_C": measuredA['cht_C'],
                "egt_C": measuredA['egt_C'],
                "oil_press_bar": measuredA['oil_press_bar'],
                "oil_temp_C": measuredA['oil_temp_C'],
                "fuel_flow_kgps": measuredA['fuel_flow_kgps'],
                "fuel_rail_bar": 1.68,
                "lambda": measuredA['lambda_val'],
                "turbo_rpm": measuredA['turbo_rpm'],
                "comp_out_p_hPa": measuredA.get('comp_out_p_hPa', measuredA['map_hPa'] * 1.05),
                "comp_out_T_K": measuredA['iat_K'],
                "inj_timing_deg": 12.4,
                "bus_voltage_V": 27.8,
                "alternator_A": 14.2,
                "throttle_pct": float(throttle_pct),
                "vib_rms_g": [0.42] * cfg['geometry']['cylinders'],
                "altitude_ft": float(altitude_ft),
                "tas_mps": 61.2,
                "oat_K": atm['T']
            }
            
            predicted = slow.copy()
            predicted['rpm'] = predictedA['rpm']
            predicted['map_hPa'] = predictedA['map_hPa']
            predicted['iat_K'] = predictedA['iat_K']
            predicted['cht_C'] = predictedA['cht_C']
            predicted['egt_C'] = predictedA['egt_C']
            predicted['oil_press_bar'] = predictedA['oil_press_bar']
            predicted['oil_temp_C'] = predictedA['oil_temp_C']
            predicted['fuel_flow_kgps'] = predictedA['fuel_flow_kgps']
            predicted['lambda'] = predictedA['lambda_val']
            predicted['turbo_rpm'] = predictedA['turbo_rpm']
            
            slowB = slow.copy()
            slowB['engine_id'] = "B"
            slowB['rpm'] = measuredB['rpm']
            slowB['map_hPa'] = measuredB['map_hPa']
            slowB['iat_K'] = measuredB['iat_K']
            slowB['cht_C'] = measuredB['cht_C']
            slowB['egt_C'] = measuredB['egt_C']
            slowB['oil_press_bar'] = measuredB['oil_press_bar']
            slowB['oil_temp_C'] = measuredB['oil_temp_C']
            slowB['fuel_flow_kgps'] = measuredB['fuel_flow_kgps']
            slowB['lambda'] = measuredB['lambda_val']
            slowB['turbo_rpm'] = measuredB['turbo_rpm']
            
            fast = {
                "schema": "pramana.fastfeat.v1",
                "t": float(t),
                "engine_id": "A",
                "order_0p5_mag": measuredA['ripple'],
                "order_0p5_phase_deg": 143.7,
                "order_1p0_mag": 0.118,
                "order_2p0_mag": 0.077,
                "knock_intensity": [0.02] * cfg['geometry']['cylinders'],
                "vib_band_rms": {"lo_0_500": 0.31, "mid_500_5k": 0.44, "hi_5k_20k": 0.12}
            }
            
            health = {
                "schema": "pramana.health.v1",
                "t": float(t),
                "engine_id": "A",
                "rho": {
                    "rho1_sd_vs_comp": rho[0],
                    "rho2_sd_vs_lambda": rho[1],
                    "rho3_sd_vs_restr": rho[2],
                    "rho4_energy": rho[3],
                    "rho5_power": rho[4],
                    "rho6_9_cyl_dev": rho[5:9],
                    "rho10_oil": rho[9],
                    "rho11_ripple": rho[10]
                },
                "theta": {
                    "eta_v_scale": {"value": 1.0, "sigma": 0.011},
                    "eta_c_scale": {"value": 1.0, "sigma": 0.019},
                    "hA_scale": {"value": 1.0, "sigma": 0.024},
                    "cd_inj": {
                        "value": [1.0] * cfg['geometry']['cylinders'],
                        "sigma": [0.02] * cfg['geometry']['cylinders']
                    },
                    "f_fric_scale": {"value": 1.0, "sigma": 0.015}
                },
                "virtual": {
                    "peak_cyl_press_bar": [140.0] * cfg['geometry']['cylinders'],
                    "knock_margin_deg": 6.4,
                    "turbo_shaft_rpm_est": predictedA['turbo_rpm'],
                    "comb_efficiency": [0.98] * cfg['geometry']['cylinders'],
                    "air_mass_flow_kgps": predictedA['air_mass_flow'],
                    "brake_power_kW": predictedA['brake_power_kW'],
                    "bsfc_g_per_kWh": 250.0
                },
                "anomaly": {
                    "score": 0.0,
                    "threshold": 0.31,
                    "persistence": {"n": 0, "of": 5, "met": False}
                },
                "diagnosis": {
                    "top": [],
                    "is_sensor_fault": False,
                    "ambiguous": False,
                    "probe": None
                },
                "rul": {
                    "component": "none",
                    "physics_h": 50.0,
                    "network_h": 50.0,
                    "p10_h": 45.0,
                    "p50_h": 50.0,
                    "p90_h": 55.0,
                    "reported_h": 50.0,
                    "heads_disagree": False
                },
                "mission": {
                    "p_complete_continue": 0.99,
                    "p_complete_derate": 0.99,
                    "p_complete_rtb": 0.99,
                    "derate_cost_min_on_station": 40.0,
                    "recommended": "continue",
                    "recommended_power_pct": 100.0,
                    "recommended_boost_hPa": 1187.0,
                    "point_of_no_return_s": 9240.0
                },
                "limits_state": "green"
            }
            
            payload = {
                "slow": slow,
                "fast": fast,
                "predicted": predicted,
                "health": health,
                "slowB": slowB
            }
            
            await websocket.send_json(payload)
            await asyncio.sleep(1)
            
    except WebSocketDisconnect:
        print("Client disconnected")

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
