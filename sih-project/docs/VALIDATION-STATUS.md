# Validation status — public evidence only

Last updated: 2026-09-12

## What is directly validated for the target engine

The target is the DRDO/VRDE 180 hp UAV aero-diesel. DRDO publicly states that
it produces 180 hp take-off power constant through 11,000 ft, was tested for
power, thrust and fuel consumption on a dynamometer/thrust cradle, and was
validated at Leh and Changla to 17,664 ft. [DRDO product page](https://drdo.gov.in/drdo/en/offerings/products/180-hp-diesel-engine-uav)

| VRDE public anchor | Current twin result | Status |
|---|---:|---|
| 180 hp at sea level | 156.2 hp | -13.2%; calibration gap |
| 180 hp at 11,000 ft | 143.9 hp | -20.1%; calibration gap |
| Validated at 17,664 ft | 106.9 hp at ISA+20 scenario | Operating point only: DRDO does not publish power there |

These are intentionally not hidden behind a pass/fail gate. The public source
does not include a dyno trace, test uncertainty, ambient conditions, or a
17,664-ft power value, so an acceptance tolerance would be invented.

## Public comparable-engine check

TEI's PD180ST is a public comparator, **not a source of VRDE measurements**:
it is a 2.1 L inline-four, common-rail, single-stage-turbo, FADEC aviation
diesel rated 182 hp maximum / 172 hp continuous. TEI publishes 220 g/kWh
sea-level BSFC and altitude power of 170 hp at 20,000 ft, 115 hp at 30,000 ft,
and 75 hp at 40,000 ft. [TEI PD180ST specifications](https://www.tei.com.tr/en/products/tei-pd180st-turbodiesel-aviation-engine)

This checks whether the model's power-lapse behaviour is in the right broad
engineering regime. It cannot calibrate the VRDE model; only VRDE test-cell or
flight data can do that.

| Altitude | Twin power | TEI power | Twin / TEI | Twin lapse from 20k | TEI lapse from 20k |
|---:|---:|---:|---:|---:|---:|
| 20,000 ft | 109.3 hp | 170 hp | 64.3% | 100.0% | 100.0% |
| 30,000 ft | 77.9 hp | 115 hp | 67.8% | 71.3% | 67.6% |
| 40,000 ft | 49.1 hp | 75 hp | 65.5% | 45.0% | 44.1% |

Interpretation: the absolute power level needs target-engine calibration, but
the normalized high-altitude lapse closely follows this comparable public
turbo-diesel. This is evidence for the model's altitude-response *form*, not
evidence that it has been calibrated to VRDE.

## Reproduce

```bash
cd /Volumes/dev/sih2026/sih-project/backend
source .venv/bin/activate
python validate_vrde.py
python validate_rotax.py
```

## Cross-engine transfer result

The same MVEM, measurement, and parity code is loaded with the Rotax 914
profile—no engine-specific code branch. The sea-level take-off validation is
85.65 kW at 5,673 rpm against the published 84.5 kW / 5,800 rpm rating
(+1.4% power, -2.2% speed). CHT is 124.9 C against the 135 C EASA limit, oil
temperature is 106.5 C against 130 C, and oil pressure is within the published
2–5 bar running range. `validate_rotax.py` also proves the healthy parity
vector remains centred at a cross-engine 10,000-ft / 72% operating point.
That profile records a fixed commissioning baseline for the repeatable healthy
probe/model offsets; it is subtracted before sigma scoring and is not adapted
online, so fault departures remain observable.

For the presentation, say: *“We use DRDO's published VRDE anchors as target
evidence. Where detailed VRDE data is unavailable, a clearly labelled public
comparable engine checks model form—not the target-engine calibration.”*
