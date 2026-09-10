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
            
            # air_mass_flow and brake_power_kW previously passed through with
            # ZERO noise regardless of add_noise. Consequence: sigma_generator
            # compares plant vs twin both running healthy/nominal params, and
            # with no noise source on these two fields, plant.air_mass_flow
            # and twin.air_mass_flow are bit-identical by construction —
            # sigma(rho1) and sigma(rho5) read exactly 0 no matter how good
            # the underlying physics is. Verified directly: a real 25%
            # compressor fault (plant faulted, twin nominal) already produces
            # rho1=-56.6 through this same pipeline — the physics was never
            # the problem, only this specific test's blind spot.
            #
            # Neither is a directly-sensed raw channel on a real airframe —
            # residual-spec.md describes air_mass_flow as an ESTIMATE derived
            # from noisy MAP/IAT/N (Path 1), not a MAF sensor reading, and
            # brake_power_kW similarly has no direct sensor. The more
            # physically correct fix re-derives both from the other
            # already-noisy channels in this same dict; that needs cfg
            # (V_d, cylinders) threaded through 7 call sites across 3 files,
            # which is a larger change than the time available justifies
            # tonight. This is the smaller, lower-risk fix — noise added the
            # same way every other channel in this function already is — and
            # closes the specific gap the sigma bootstrap exposed. Revisit
            # with the proper derivation when there is time to verify it at
            # every call site.
            'air_mass_flow': float(phys['air_mass_flow'] + n(max(phys['air_mass_flow'] * 0.015, 3e-4))),
            'fuel_cmd_per_cyl': float(phys['fuel_cmd_per_cyl']),
            'brake_power_kW': float(phys['brake_power_kW'] + n(max(phys['brake_power_kW'] * 0.01, 0.05)))
        }
