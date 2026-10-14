# Wire format — G90 DE-9 head↔body link

Overview of the proprietary binary link on the G90 front-panel DE-9. This is
**not** CI-V (CAT uses a separate jack at 19200 baud).

## Transport

| Direction   | Size          | Rate       | Sync                           |
| ----------- | ------------- | ---------- | ------------------------------ |
| Head → body | **96 bytes**  | 115200 8N1 | `55 AA` @ bytes 0–1            |
| Body → head | **372 bytes** | same       | no fixed preamble (CRC + size) |

Framing is **fixed length + CRC** — there is no length field or escape framing.

## Checksum

STM32 hardware CRC-32 (MPEG-2 polynomial) over all bytes except the trailing
4-byte little-endian CRC word:

- Head frame: CRC over bytes 0–91, stored @ 92–95
- Body frame: CRC over bytes 0–367, stored @ 368–371

## Head frame (96 bytes) — essentials

After the 2-byte sync, each VFO occupies a 16-byte block starting @ byte 4
(VFO-A) and byte 20 (VFO-B):

| Offset (VFO-relative) | Field                                                  |
| --------------------- | ------------------------------------------------------ |
| 0–3                   | Frequency Hz, LE u32                                   |
| 5                     | Mode wire value (profile-dependent map)                |
| 8–9                   | Filter high/low raw bytes (profile-dependent encoding) |
| 15 low nibble         | `fft_scale` (profile-dependent)                        |

Control bytes for PTT, operate/active, ATU tune, and VOX sit in the mid-frame
region (see control spec §7). Default library encodes **RX-only** unless
explicitly overridden.

## Body frame (372 bytes) — essentials

| Region  | Content                                                            |
| ------- | ------------------------------------------------------------------ |
| 0–12    | Meters / status (S-meter, Po, SWR, voltage, ATU, version bytes, …) |
| 13–47   | Raw only (**B4** / **O1**). Body rolls a C string at 20–47. Head use of that window is not a v1 decode |
| 48–367  | **160 × uint16** FFT bins (relative, not calibrated dBm)           |
| 368–371 | CRC                                                                |

## Two payload profiles

Transport is identical across the GSOC-breaking firmware step; **semantics**
are not:

| Profile    | When                       | Mode map         | Filter encode |
| ---------- | -------------------------- | ---------------- | ------------- |
| **`1.78`** | Stock GSOC, DispUnit ≤1.78 | 0–5              | ÷25 scheme    |
| **`1.79`** | ≥1.79b02 through **1.81**  | 0–7 with L-D/U-D | ÷50 scheme    |

`Profile.parse("1.81")` aliases to **`1.79`** — justified by a firmware quiet-ladder
survey (static RE, tier **D**); the alias itself is CI-tested (**P2/P4**).

See [verification.md](../verification.md) for what CI vs bench vs RE actually proves.

## Community corroboration

- [zeroping/xiegu-g90-headprotocol](https://github.com/zeroping/xiegu-g90-headprotocol)
- [OpenHamradioFirmware G90Tools](https://github.com/OpenHamradioFirmware/G90Tools)

## Normative reference

Implementers: [Control spec v1](control-spec.md) + `pytest tests/`.
