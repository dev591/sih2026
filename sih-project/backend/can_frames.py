"""
CAN-frame encoding (PS: "may use a CAN bus / ECU interface" — optional).

What this is: the live telemetry, packed into CANaerospace-style frames — 11-bit arbitration ID +
an 8-byte data payload, each signal fixed-point scaled into 16-bit fields, exactly the shape a real
avionics CAN bus carries. It proves the encoding is ready.

What this is NOT: a live SocketCAN bus. `vcan0` is a Linux kernel feature; this backend runs on the
Windows recording PC, so there is no virtual or physical CAN interface actually running underneath
it. Wiring this encoder to a real `python-can` bus on a Linux host (or a USB-CAN adapter) is a
transport swap, not a rewrite — encode_frame() below is already transport-agnostic; only whatever
calls it needs to change from "return JSON" to "socket.send(frame)". Be honest about this distinction
if asked: this is the frame format, not a running bus.

    GET /can/latest   -> the most recent live frame, CAN-encoded (read-only; does not affect the
                          live telemetry loop at all — main.py's _send loop never calls this module)
"""
from __future__ import annotations

import struct

# arbitration id -> (label, signals). Each signal: (key path into the frame, scale, offset, signed).
# Two bytes per signal, big-endian, value = raw * scale + offset — a standard CAN fixed-point encoding.
FRAME_DEFS: list[tuple[int, str, list[tuple[str, float, float, bool]]]] = [
    (0x100, "engine_speed_map",  [("slow.rpm", 0.125, 0.0, False), ("slow.map_hPa", 0.5, 0.0, False),
                                  ("slow.throttle_pct", 0.01, 0.0, False), ("slow.turbo_rpm", 5.0, 0.0, False)]),
    (0x110, "cht_bank",          [("slow.cht_C.0", 0.1, -40.0, True), ("slow.cht_C.1", 0.1, -40.0, True),
                                  ("slow.cht_C.2", 0.1, -40.0, True), ("slow.cht_C.3", 0.1, -40.0, True)]),
    (0x111, "egt_bank",          [("slow.egt_C.0", 0.25, 0.0, False), ("slow.egt_C.1", 0.25, 0.0, False),
                                  ("slow.egt_C.2", 0.25, 0.0, False), ("slow.egt_C.3", 0.25, 0.0, False)]),
    (0x120, "oil_fuel",          [("slow.oil_press_bar", 0.001, 0.0, False), ("slow.oil_temp_C", 0.1, -40.0, True),
                                  ("slow.fuel_flow_kgps", 1e-6, 0.0, False), ("slow.altitude_ft", 1.0, 0.0, True)]),
    (0x130, "vib_inj",           [("slow.vib_rms_g", 0.0001, 0.0, False), ("slow.inj_timing_deg", 0.01, 0.0, True)]),
    (0x1F0, "diagnosis",         [("health.anomaly.score", 0.0001, 0.0, False),
                                  ("health.diagnosis.top.0.p", 0.0001, 0.0, False)]),
]


def _get(frame: dict, path: str):
    node = frame
    for part in path.split("."):
        if node is None:
            return None
        node = node[int(part)] if part.isdigit() else node.get(part)
    return node


def encode_frame(frame: dict) -> list[dict]:
    """One CAN frame per FRAME_DEFS entry. A signal with no live value (unmodelled, or this frame
    predates a field) encodes as 0x7FFF — CAN's usual 'signal not available' sentinel, never a guess."""
    out = []
    for arb_id, name, signals in FRAME_DEFS:
        payload = b""
        for path, scale, offset, signed in signals[:4]:
            v = _get(frame, path)
            if v is None:
                raw = 0x7FFF
            else:
                raw = int(round((float(v) - offset) / scale))
                lo, hi = (-32768, 32767) if signed else (0, 65535)
                raw = max(lo, min(hi, raw))
            payload += struct.pack(">h" if signed else ">H", raw)
        out.append({"id": f"0x{arb_id:03X}", "name": name, "dlc": len(payload),
                    "data": " ".join(f"{b:02X}" for b in payload)})
    return out


def decode_frame(arb_id: int, data: bytes) -> dict:
    """Round-trip check: decode one payload back to engineering units, given its frame definition."""
    defn = next((d for d in FRAME_DEFS if d[0] == arb_id), None)
    if defn is None:
        raise KeyError(f"no frame definition for id 0x{arb_id:03X}")
    _, name, signals = defn
    out = {}
    for i, (path, scale, offset, signed) in enumerate(signals[:4]):
        raw = struct.unpack_from(">h" if signed else ">H", data, i * 2)[0]
        out[path] = None if raw == 0x7FFF else round(raw * scale + offset, 4)
    return {"id": f"0x{arb_id:03X}", "name": name, "values": out}


if __name__ == "__main__":
    # self-test: encode a synthetic frame, decode it, confirm the round trip.
    sample = {"slow": {"rpm": 3588.0, "map_hPa": 1368.0, "throttle_pct": 72.0, "turbo_rpm": 142000.0,
                       "cht_C": [159.0, 158.0, 160.0, 157.0], "egt_C": [865.0, 863.0, 866.0, 861.0],
                       "oil_press_bar": 3.52, "oil_temp_C": 91.6, "fuel_flow_kgps": 0.00534,
                       "altitude_ft": 11000.0, "vib_rms_g": 0.18, "inj_timing_deg": 14.2},
              "health": {"anomaly": {"score": 0.09977}, "diagnosis": {"top": [{"p": 0.4}]}}}
    frames = encode_frame(sample)
    for f in frames:
        print(f["id"], f["name"], f["dlc"], "bytes:", f["data"])
    dec = decode_frame(0x100, bytes.fromhex(frames[0]["data"].replace(" ", "")))
    print("round-trip decode of 0x100:", dec)
    assert abs(dec["values"]["slow.rpm"] - sample["slow"]["rpm"]) < 0.2
    print("round-trip OK")
