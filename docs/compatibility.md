# G90 firmware compatibility

GSOC firmware still sends **1.78** mode bytes; a **v1.81** body decodes them
under the **1.79+** map. This repo implements the post-break encoder for laptop
DE-9 control — not a GSOC firmware update. GSOC boards were not exercised here;
timeline dates below are community/vendor reports unless a fixture says otherwise.

## Split radio, one cable

The G90 is two boxes: **main unit** (RF, DSP, the part you bench) and
**display unit** (touch UI, or a GSOC in its place). They talk over a
front-panel **DE-9** at 115200 baud with fixed 96-byte commands and 372-byte
replies. Each half has its own flash image.

The GSOC is a third-party head that bit-bangs the same binary protocol as the
stock display. When Xiegu changes what a byte *means* without changing frame
length, an old head can still pass CRC checks and still tune the wrong mode.
That is the GSOC cliff, not a magic version string on the wire.

## Timeline

| Item                  | Version             | Notes                                                       |
| --------------------- | ------------------- | ----------------------------------------------------------- |
| GSOC firmware (last)  | V1.3                | Jan 2021 — community/vendor report, not bench-verified here |
| G90 pair GSOC targets | MainUnit ≤ V1.78b01 | Community floor ~V1.74 — same                               |
| Current G90 firmware  | V1.81               | Aug 2025 — vendor; `info6=0x81` in fixtures only            |

The GSOC received no firmware updates for years while G90 main-unit images
moved through 1.79, 1.80, and 1.81. The practical workaround many operators
used: stay on G90 firmware **≤ V1.78** and accept missing fixes elsewhere.

## The 1.78 → 1.79 break (GSOC cliff)

The **wire container did not change**: 96-byte head→body, 372-byte body→head,
`55 AA` sync, STM32 CRC-32, 115200 baud.

What changed is **payload meaning**:

| Field          | ≤ 1.78 (stock GSOC)                   | ≥ 1.79 (through 1.81)                       |
| -------------- | ------------------------------------- | ------------------------------------------- |
| Mode enum      | 0–5 (LSB…NFM)                         | 0–7 with **L-D/U-D inserted** after LSB/USB |
| Filter packing | `(Hz−125)/25` high, `(Hz−100)/25` low | **Both** edges `(Hz−50)/50`                 |
| Spectrum zoom  | Manual 1–10                           | Library encodes **1** only (**H5**). Body still applies 1–10 |

A frozen GSOC still sends 1.78 mode numbers on a 1.81 body. USB (wire value 1
on 1.78) becomes **L-D** on a 1.79+ body. Every non-LSB mode and filter edge
is wrong.

Details: [Firmware break](protocol/firmware-break.md).

## What this repo targets

**v1.81 bodies today**, encoded with the post-break map:

- Codec profile **`1.79`** (`Profile.parse("1.81")` aliases to it)
- On **v1.81 hardware only** (bench 2026-07-27): no observed mode/filter map
  change vs 1.79; CI proves **P2/P4** wire-byte identity, not a per-firmware
  survey ([verification.md](verification.md) tier **D** for quiet ladder)
- Checked on the wire with fake-head sessions and `g90-effect-diff` (fixtures
  from 2026-07-27; not automated hardware CI)

Profile **`1.78`** stays in the tree so tests can show the classic GSOC mishap
(USB wire `1` decoded as L-D on a modern body).

## Scope

**v0.1 (in tree):** laptop over DE-9 — `g90-live-head`, bench PASS
**2026-07-27**. **GSOC Linux port:** not started; out of scope for current tools.

## References

- [YO3HJV — COMM LOST](https://yo3hjv.blogspot.com/2021/05/xiegu-g90-comm-lost-error.html)
- [zeroping headprotocol](https://github.com/zeroping/xiegu-g90-headprotocol)
- [gsoc-contrib teardown](https://github.com/mdubinko/gsoc-contrib)
- [G90Tools](https://github.com/OpenHamradioFirmware/G90Tools) (community firmware tooling — not shipped here)
