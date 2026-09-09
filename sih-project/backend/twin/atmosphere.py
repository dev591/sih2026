import math

def isa(altitude_ft: float, isa_offset_K: float = 0.0) -> dict:
    h = altitude_ft * 0.3048
    T0 = 288.15 + isa_offset_K
    p0 = 101325.0
    L = 0.0065
    g = 9.80665
    R = 287.05

    if h < 11000.0:
        T = T0 - L * h
        p = p0 * math.pow(1 - (L * h) / T0, (g / (R * L)))
    else:
        T11 = T0 - L * 11000.0
        p11 = p0 * math.pow(1 - (L * 11000.0) / T0, (g / (R * L)))
        T = T11
        p = p11 * math.exp(-g * (h - 11000.0) / (R * T))

    return {'T': T, 'p': p}
