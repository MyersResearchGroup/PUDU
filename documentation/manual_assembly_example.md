# Golden Gate Manual Assembly Protocol

## Overview
This document provides human-readable Golden Gate assembly instructions for constructs defined in SBOL-like JSON input.
Each construct is treated as a separate reaction tube with explicit reagent additions and calculated water volumes.

## Inputs
- **composite_1** from `https://SBOL2Build.org/composite_1/1`
- **composite_2** from `https://SBOL2Build.org/composite_2/1`

## Default reagent assumptions
- Total reaction volume: **20 µL**
- Per DNA component volume (backbone and each part): **2 µL**
- Restriction enzyme volume: **2 µL**
- T4 DNA ligase volume: **4 µL**
- T4 DNA ligase buffer volume: **2 µL**

Calculated water volume per reaction:
- **composite_1**: 2 µL
- **composite_2**: 2 µL

## Reaction summary
| Product | Backbone | Parts | Restriction Enzyme | Number of DNA components | Water volume (µL) | Total volume (µL) |
|---|---|---|---|---:|---:|---:|
| composite_1 | pSB1C3 | J23101, B0034, GFP, B0015 | BsaI | 5 | 2 | 20 |
| composite_2 | pSB1C3 | J23106, B0034, RFP, B0015 | BsaI | 5 | 2 | 20 |

## Per-reaction instructions

### Product: composite_1
URI: https://SBOL2Build.org/composite_1/1

1. Label one tube as `composite_1`.
2. Add 2 µL nuclease-free water.
3. Add 2 µL 10X T4 DNA Ligase Buffer.
4. Add 4 µL T4 DNA Ligase.
5. Add 2 µL BsaI (URI: https://SBOL2Build.org/BsaI/1).
6. Add 2 µL backbone `pSB1C3` (URI: https://sbolcanvas.org/pSB1C3/1).
7. Add 2 µL part `J23101` (URI: https://sbolcanvas.org/J23101/1).
8. Add 2 µL part `B0034` (URI: https://sbolcanvas.org/B0034/1).
9. Add 2 µL part `GFP` (URI: https://sbolcanvas.org/GFP/1).
10. Add 2 µL part `B0015` (URI: https://sbolcanvas.org/B0015/1).
11. Mix gently by pipetting. Do not vortex unless explicitly intended.
12. Briefly spin down if appropriate.

### Product: composite_2
URI: https://SBOL2Build.org/composite_2/1

1. Label one tube as `composite_2`.
2. Add 2 µL nuclease-free water.
3. Add 2 µL 10X T4 DNA Ligase Buffer.
4. Add 4 µL T4 DNA Ligase.
5. Add 2 µL BsaI (URI: https://SBOL2Build.org/BsaI/1).
6. Add 2 µL backbone `pSB1C3` (URI: https://sbolcanvas.org/pSB1C3/1).
7. Add 2 µL part `J23106` (URI: https://sbolcanvas.org/J23106/1).
8. Add 2 µL part `B0034` (URI: https://sbolcanvas.org/B0034/1).
9. Add 2 µL part `RFP` (URI: https://sbolcanvas.org/RFP/1).
10. Add 2 µL part `B0015` (URI: https://sbolcanvas.org/B0015/1).
11. Mix gently by pipetting. Do not vortex unless explicitly intended.
12. Briefly spin down if appropriate.

## Thermocycling
1. Cycle 25–30 times between 37°C (digestion) and 16°C (ligation), typically 1–5 minutes at each temperature.
2. Incubate at 50°C for 5 minutes to favor completion of assembled products.
3. Heat inactivate at 80°C for 10 minutes, then hold at 4°C or place on ice.

## Notes
- If constructs are designed correctly, the final assembled product should no longer contain the Type IIS recognition sites used for assembly.
- This generated document is an instruction sheet for manual execution and is not an automated OT-2 script.
- Volumes for ligase, ligase buffer, enzyme, and DNA parts are taken from PUDU assembly defaults unless explicitly overridden.