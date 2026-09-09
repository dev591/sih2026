import random

class MeasurementModel:
    def __init__(self, seed: int = 42):
        self.rng = random.Random(seed)

    def noise(self, sigma: float) -> float:
        return self.rng.gauss(0, sigma)

    def measure(self, phys: dict, sensor_biases: dict = None, add_noise: bool = True) -> dict:
        """
        Applies sensor biases and noise to the true physical state.
        This provides the required Sensor vs Engine fault asymmetry.
        """
        if sensor_biases is None:
            sensor_biases = {}
            
        def n(sig):
            return float(self.noise(sig)) if add_noise else 0.0

        cht_bias = sensor_biases.get('cht_C', [0.0] * len(phys['cht_C']))
        egt_bias = sensor_biases.get('egt_C', [0.0] * len(phys['egt_C']))

        return {
            'rpm': float(phys['rpm'] + n(3.0)),
            'map_hPa': float(phys['map_hPa'] + sensor_biases.get('map_hPa', 0.0) + n(2.0)),
            'iat_K': float(phys['iat_K'] + n(0.4)),
            'cht_C': [float(c + cht_bias[i] + n(0.6)) for i, c in enumerate(phys['cht_C'])],
            'egt_C': [float(e + egt_bias[i] + n(2.2)) for i, e in enumerate(phys['egt_C'])],
            'oil_press_bar': float(phys['oil_press_bar'] + n(0.02)),
            'oil_temp_C': float(phys['oil_temp_C'] + n(0.3)),
            'fuel_flow_kgps': float(phys['fuel_flow_total'] + n(2e-6)),
            'lambda_val': float(phys['lambda_val'] + sensor_biases.get('lambda', 0.0) + n(0.006)),
            'turbo_rpm': float(phys['turbo_rpm'] + n(220.0)),
            'ripple': float(phys['ripple'] + n(0.002)),
            
            # Pass-through clean physics values used by downstream schema mapping
            'air_mass_flow': float(phys['air_mass_flow']),
            'fuel_cmd_per_cyl': float(phys['fuel_cmd_per_cyl']),
            'brake_power_kW': float(phys['brake_power_kW'])
        }
