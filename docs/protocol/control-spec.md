# G90 control library — frozen behavior spec (v1)

**Status:** **FROZEN** 2026-07-24.
**Target:** track-latest / **v1.81** · codec profile **`1.79`** (`1.81` aliases).
**Implement from:** this document + normative tests — not reverse-engineering dumps.

| Artifact                     | Role                                                             |
| ---------------------------- | ---------------------------------------------------------------- |
| `g90_codec`                  | Normative encode/decode (CRC, mode/filter/`fft_scale`, body map) |
| `g90_control`                | Product defaults, live session, CI-V CAT                         |
| `tests/test_control.py`      | Gate: must pass                                                  |
| `tests/test_codec_golden.py` | Codec golden vectors + fixtures                                  |

Narrative protocol notes: [`wire-format.md`](wire-format.md), [`civ.md`](civ.md),
[`compatibility.md`](../compatibility.md), [`firmware-break.md`](firmware-break.md).
Implementations should cite **clauses + tests below**, not narrative RE notes alone.

---

## 1. Scope / non-goals

### In freeze (v1)

| Area                         | Covered                                                            |
| ---------------------------- | ------------------------------------------------------------------ |
| Dual-profile head↔body codec | `1.78` + `1.79` (+ `1.81` alias)                                   |
| Head TX encode               | Mode, filter Hz, `fft_scale`, VFO freq, RX-only ctrl defaults, CRC |
| Body RX decode               | CRC, meters/status map below, FFT slice @48 (raw + fingerprint)    |
| Control API defaults         | `HeadControl` / `build_head_frame` → product `1.81`                |
| CI-V CAT surface             | Opcodes in §6; `0x1C` hard-blocked                                 |
| Lab safety                   | RX-only head frames; live Po≠0 abort policy                        |

### Out of freeze / open

| ID       | Item                                 | Notes                                                   |
| -------- | ------------------------------------ | ------------------------------------------------------- |
| **O1**   | Body header bytes **13–47**          | Partial static RE; not normative for v1 semantic decode |
| **O2**   | Absolute FFT **dBm**                 | Stride settled (160×u16); `fft_fingerprint` only        |
| **H1b**  | Stock-head TX/UI field match (Y-tap) | Optional confirmation                                   |
| TX / PTT | RF keying, ATU tune, VOX arm         | No antenna policy → not in v1 API                       |

---

## 2. Profiles & aliases — **P**

| Clause | Behavior                                                                          | Normative test                         |
| ------ | --------------------------------------------------------------------------------- | -------------------------------------- |
| **P1** | Profiles: `1.78` (GSOC / ≤1.78) and `1.79` (post-break ≥1.79b02)                  | golden `TestModes`, `TestProfileAlias` |
| **P2** | `Profile.parse("1.81")` ≡ `Profile.V179` (`"1.79"`)                               | `test_181_aliases_179`; golden alias   |
| **P3** | Product default `TARGET_PROFILE = "1.81"` → codec `1.79`                          | `TestAliasAndTarget`                   |
| **P4** | Same controls → identical wire bytes for `1.81` and `1.79`                        | `test_181_and_179_encode_identical…`   |
| **P5** | Classic mishap: encode USB under `1.78`, decode as `1.79` → mode **L-D** (CRC OK) | `test_classic_gsoc_mishap`             |

Mode wire maps (head link — **not** CI-V mode bytes):

| Wire | `1.78` | `1.79` / `1.81` |
| ---- | ------ | --------------- |
| 0    | LSB    | LSB             |
| 1    | USB    | **L-D**         |
| 2    | CW     | **USB**         |
| 3    | CWR    | **U-D**         |
| 4    | AM     | CW              |
| 5    | NFM    | CWR             |
| 6    | —      | AM              |
| 7    | —      | NFM             |

---

## 3. Transport — **T**

| Clause | Behavior                                                           | Test                       |
| ------ | ------------------------------------------------------------------ | -------------------------- |
| **T1** | Head→body frame = **96** bytes; body→head = **372** bytes          | golden lengths             |
| **T2** | Head sync prefix `55 AA` (bytes 0–1)                               | golden encode/decode       |
| **T3** | No length field; framing = fixed size + CRC                        | (spec)                     |
| **T4** | CRC-32 = STM32 HW poly over payload excluding trailing 4 CRC bytes | golden `TestCrc`, fixtures |
| **T5** | Baud (live DE-9): **115200** 8N1 · CI-V User jack: **19200** 8N1   | live tools (optional)      |

---

## 4. Head TX encode — **H**

Normative packing lives in `g90_codec.encode_head_frame` / `HeadControl.encode`.

| Clause | Behavior                                                 | Test                                   |
| ------ | -------------------------------------------------------- | -------------------------------------- |
| **H1** | Mode name ↔ wire via profile map (§2)                    | golden `TestModes`                     |
| **H2** | **Filter `1.78`:** high=`(Hz−125)/25`, low=`(Hz−100)/25` | golden `TestFilter`                    |
| **H3** | **Filter `1.79+`:** both edges `(Hz−50)/50`              | golden `TestFilter`                    |
| **H4** | **`fft_scale` `1.78`:** 1=AUTO, 2..10 manual             | golden `TestFftScale`                  |
| **H5** | **`fft_scale` `1.79+`:** only `1` (AUTO)                 | golden `test_179_rejects_manual_fft`   |
| **H6** | Encode → decode round-trip                               | `TestControlRoundTrip`; golden         |
| **H7** | Default encode applies **RX-only guard** (§7)            | golden `test_rx_only_guard_clears_ptt` |

Default product filter edges: high **2700** Hz, low **200** Hz, `fft_scale=1`.

---

## 5. Body RX decode — **B**

| Clause | Behavior                                                  | Test                     |
| ------ | --------------------------------------------------------- | ------------------------ |
| **B1** | Decode 372 B → CRC + `status` + `fft` @48                 | golden `TestBodyMinimal` |
| **B2** | FFT = **160 × uint16** LE; fingerprint only (**O2** open) | golden                   |
| **B3** | Mapped `BodyStatus` fields normative for v1               | golden + live fixtures   |
| **B4** | Bytes **13–47**: raw exposure only (**O1** open)          | —                        |

See source `BodyStatus` docstrings for the full offset map.

---

## 6. CI-V surface — **C**

Stock **DispUnit User** jack @ **19200 8N1**. Defaults: radio **`0x88`**, controller **`0xE0`**.

| Clause | Behavior                                             | Normative test                               |
| ------ | ---------------------------------------------------- | -------------------------------------------- |
| **C1** | Opcode **`0x1C` hard-blocked** (`CivTxBlockedError`) | `test_block_0x1c_ptt`                        |
| **C2** | Frequency BCD packing (LSB-first, 5 bytes)           | `test_freq_bcd_roundtrip_icom_packing`       |
| **C3** | Meter BCD2 + read/set frame builders                 | `test_meter_bcd2`, `test_encode_read_frames` |
| **C4** | `parse_frames` + freq/mode/meter reply decode        | `test_parse_and_decode_replies`              |

Also supported (builders tested in `TestCivReadOnly`): `0x05`/`0x06` set freq/mode,
`0x0F` split, `0x11` ATT, `0x14` levels, `0x16` extensions, `0x1D 0x19` radio ID,
`0x25`/`0x26` VFO freq/mode.

Live quirks **C-Q1** … **C-Q4** are documented in [`civ.md`](civ.md).

---

## 7. Safety constraints — **S**

| Clause | Behavior                                                     |
| ------ | ------------------------------------------------------------ |
| **S1** | Default head encode clears PTT/tune/VOX; sets operate/active |
| **S2** | `head_frame_requests_tx` detects armed TX                    |
| **S3** | `LiveHeadSession` refuses TX-requesting frames               |
| **S4** | `CivClient` never sends `0x1C`                               |
| **S5** | `g90-effect-diff` aborts if body **Po ≠ 0**                  | `test_s5_po_abort_when_po_nonzero` |

---

## 8. Normative tests

| Gate         | Command                             | Covers                    |
| ------------ | ----------------------------------- | ------------------------- |
| **Required** | `pytest tests/test_control.py`      | P*, H*, C1–C4, S1–S5      |
| **Required** | `pytest tests/test_codec_golden.py` | T*, H*, B*, CRC, fixtures |
| Optional     | `g90-live-head` / `g90-effect-diff` | Live DE-9 proof           |
| Optional     | `g90-live-civ`                      | Live CAT + quirks         |

Fixtures: `tests/fixtures/head_*.hex`, `body_181_*.hex`.

---

## 9. Revision policy

- **v1 freeze** = this file + green tests as of **2026-07-24**.
- New payload semantics require a **v1.1+** clause bump and tests.
- New Xiegu firmware cuts: re-check the quiet ladder; add a new profile if the map breaks.
