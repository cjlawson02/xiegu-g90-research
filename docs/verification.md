# Verification & evidence tiers

Table of claims with tier (A–E), test or fixture name, and how to reproduce.

**CI gate:** `pytest tests/` (54 tests) + `scripts/publish_scrub_check.py
--publish`. Neither runs live radio hardware. The scrub check fails on tracked
vendor firmware (``.xgf``, ``gsoc_app``), AES keys, decompilation dumps, and
related path/content heuristics — see the script docstring for modes.

## Evidence tiers

| Tier              | Meaning                                                           | How to reproduce                                                      |
| ----------------- | ----------------------------------------------------------------- | --------------------------------------------------------------------- |
| **A — CI**        | Automated test in this repo                                       | `pytest tests/`                                                       |
| **B — Fixture**   | Checked-in hex capture parsed by tests                            | `tests/fixtures/*.hex` (lab date **2026-07-24**)                      |
| **C — Bench**     | Live tool PASS on hardware; not in CI                             | `g90-live-head`, `g90-effect-diff`, `g90-live-civ`, `g90-live-fft`    |
| **D — Static RE** | Firmware/GSOC binary analysis; gray material **not** shipped here | Outcomes summarized in protocol docs; not reproducible from this repo |
| **E — External**  | Community posts, vendor PDFs, third-party tools                   | Cited in doc footers                                                  |

Treat README “proven on bench” claims as **B + C**, not **A**, unless a row
below says **A**.

---

## Proven in CI (tier A)

| Claim                                                                         | Tests / code                                                          |
| ----------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| STM32 CRC-32 (MPEG-2 poly), LE placement                                      | `TestCrc`, fixture CRC checks                                         |
| Head 96 B / body 372 B; sync `55 AA` on head TX                               | golden lengths, encode/decode                                         |
| Mode maps `1.78` (0–5) vs `1.79+` (0–7, L-D/U-D inserted)                     | `TestModes`, `test_classic_gsoc_mishap`                               |
| Filter encode ÷25 vs ÷50                                                      | `TestFilter`                                                          |
| `fft_scale` 1–10 vs AUTO-only                                                 | `TestFftScale`                                                        |
| `Profile.parse("1.81")` ≡ `1.79`; identical wire bytes                        | `test_181_aliases_179`, `test_181_and_179_encode_identical`           |
| GSOC mishap: USB wire `1` under `1.78` → **L-D** on `1.79` decode, CRC OK     | `test_classic_gsoc_mishap`                                            |
| Head control packing (NB/SQL/RIT/… overlays)                                  | `test_head_controls_pack_and_overlay`                                 |
| RX-only guard clears PTT/tune/VOX; operate set                                | `test_rx_only_guard_clears_ptt`                                       |
| `LiveHeadSession` strips `allow_tx` on fake serial                            | `test_live_session_strips_allow_tx`                                   |
| `LiveHeadSession` raises on PTT-armed TX                                      | `test_live_session_raises_on_armed_ptt`                               |
| `CivClient.send_raw` blocks `0x1C`                                            | `test_civ_client_send_raw_blocks_1c`                                  |
| Corrupt CRC fails `crc_ok` on head/body                                       | `test_corrupt_crc_detected`                                           |
| `g90-effect-diff` aborts when body Po ≠ 0 (S5)                                | `test_s5_po_abort_when_po_nonzero`                                    |
| Body FFT @ byte 48 = 160×uint16; fingerprint helper                           | `TestBodyMinimal`, `test_header_diffs_and_fft_fp`                     |
| CI-V framing, BCD freq, synthetic encode vectors                              | `TestCivReadOnly`                                                     |
| CI-V `0x1C` hard-blocked                                                      | `test_block_0x1c_ptt`                                                 |
| Zeroping head-frame byte oracle (1.78 community captures)                     | `TestZeropingOracle`                                                  |
| CI-V quirks C-Q1/C-Q2/C-Q4 live hex                                           | `TestCivLiveFixtures`                                                 |
| Radio ID `00 90`; addr `0x70` reply                                           | `TestCivLiveFixtures`                                                 |
| Level read: non-BCD bytes (e.g. `0x7D`) decode without error (C-Q3 read path) | `test_encode_level_split_vfo` (synthetic; set path not live-fixtured) |

---

## Proven by fixtures only (tier B)

Captures from lab sessions **2026-07-24** and **2026-07-27** (bench on G90 v1.81). Prove the **codec parses real
traffic**, not that your radio today will PASS the full ladder.

| Claim                                                 | Fixture(s)                                                               |
| ----------------------------------------------------- | ------------------------------------------------------------------------ |
| v1.81 body CRC-ok; `info6=0x81`; voltage ≈ 13.5 V     | `body_181_live.hex`                                                      |
| Fake-head TX ↔ body RX round-trip (USB @ 14.225 MHz)  | `head_181_fake_usb.hex` + body fixtures                                  |
| Body header bytes 20–47 roll ASCII (`cw_text` window) | `body_181_fake_rx_{early,partial,late}.hex`, `body_181_effect_cw_E*.hex` |
| Live fake-head body CRC-ok @ 14.225 MHz               | `body_181_live_head_20260727.hex`                                        |
| Effect-diff ladder summary (8 steps, Po=0)            | `effect_diff_manifest_20260727.jsonl`                                    |
| Effect-diff CW `"E "` march in header                 | `body_181_effect_cw_E*.hex`                                              |
| CI-V quirks C-Q1/C-Q2/C-Q4, radio ID, addr `0x70`     | `tests/fixtures/civ_live/*.hex`                                          |

---

## Bench-only, not in CI (tier C)

Documented in [bench-setup.md](bench-setup.md). Requires G90 MainUnit v1.81 (or
compatible post-1.79 body), FT232 on DE-9, and manual runs.

| Claim                                                     | Tool              | Status in repo                                                            |
| --------------------------------------------------------- | ----------------- | ------------------------------------------------------------------------- |
| CRC-valid fake-head TX ↔ body RX (live)                   | `g90-live-head`   | Bench **2026-07-27** PASS; fixture `body_181_live_head_20260727.hex`      |
| Body-effect ladder PASS (mode/freq → header + FFT deltas) | `g90-effect-diff` | Bench **2026-07-27** PASS; manifest `effect_diff_manifest_20260727.jsonl` |
| CI-V freq/mode/meters on DispUnit User jack               | `g90-live-civ`    | Fixtures `tests/fixtures/civ_live/*.hex` (2026-07-27)                     |
| Remote FFT/waterfall from body bins                       | `g90-live-fft`    | Replay from fixtures only in CI                                           |


---

## Static RE (tier D)

Some protocol claims (transport unchanged 1.78→1.79, quiet ladder through 1.81,
BPF table indexing, operate-bit TX gate) come from private firmware analysis.
Gray binaries and decompilation dumps are intentionally absent here; public
summaries live in [firmware-break.md](protocol/firmware-break.md) and
[control-spec.md](protocol/control-spec.md). Re-check the quiet ladder when
Xiegu ships new firmware.

---

## External / community (tier E)

| Claim                                     | Source                                                                      | In-repo support                              |
| ----------------------------------------- | --------------------------------------------------------------------------- | -------------------------------------------- |
| GSOC last firmware V1.3 (Jan 2021)        | Vendor/community timeline                                                   | None — narrative                             |
| G90 pair for GSOC ≤ V1.78b01              | Groups.io, YO3HJV                                                           | None — narrative                             |
| Current G90 V1.81 (Aug 2025)              | Vendor release notes                                                        | Fixture `info6=0x81` only                    |
| DE-9 pinout (pin 2/3/5; never pin 1 +8 V) | [zeroping headprotocol](https://github.com/zeroping/xiegu-g90-headprotocol) | None — wiring guide                          |
| CI-V on DispUnit **User** jack @ 19200    | Live bench                                                                  | C-Q1–C-Q4 fixtures                           |
| ~48 kHz FFT span, GSOC dBm affine         | GSOC/community                                                              | Display proxy in `g90-live-fft`; **O2** open |

---

## What we explicitly do **not** claim

- Stock-head UI field-for-field match (Y-tap) — optional H1b
- Absolute FFT dBm calibration — O2 open
- “GSOC fixed” or product revival
- Filter changes always visible in body header bytes (`g90-effect-diff` checks
  mode/freq steps; filter may not echo in bytes 13–47)
- Vendor firmware redistribution (blocked by `publish_scrub_check.py --publish`)

---

## Open gaps

- **New firmware survey** — if Xiegu ships 1.82+, re-run quiet-ladder checks and
  add a codec profile if the map breaks.
