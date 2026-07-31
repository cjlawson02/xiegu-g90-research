# FFT bench (optional)

Extended procedure for **Hz/bin**, **ATT relative level**, relative signal
ranking, and optional absolute dBm (generator). **Fake-head only** — set freq,
mode, ATT, and SQL via head-frame bytes; CAT is not required.

**Prerequisites:** [Bench setup](bench-setup.md) (DE-9 wiring, fake-head link,
`Po=0`).

**Scope:** validates wire FFT behavior for **O2** (absolute dBm remains open per
[control spec](protocol/control-spec.md)). Ship **display dB proxy + relative
bins** (`g90-live-fft --dbm`). Absolute calibration is **optional**
([Appendix A](#appendix-a-absolute-dbm-generator-only)).

**Out of scope:** GSOC local IQ `log10` path; stock-head Y-tap re-proof; header
bytes 13–47 semantics (**O1**).

---

## Setup

Complete [Bench setup](bench-setup.md#fake-head-control-path) first. Quick check:

```sh
g90-live-head --profile 1.81 --port /dev/cu.usbserial-XXX \
  --mode USB --freq 14225000 --sql-level 0 --count 5
```

Expect `body_crc_ok` == count.

---

## Part A — Hz/bin (~10 min)

**No generator required.** Uses **VFO offset** to measure frequency spacing.

### Method: VFO offset ±10 kHz

1. Find a **strong on-air signal** (S ≥ 5, stable).
2. Tune VFO to center the peak near bin **80** (`g90-live-fft --dbm` optional).
3. Record VFO frequency (e.g. **14.074 MHz** for FT8).
4. Capture baseline, +10 kHz, −10 kHz:

   ```sh
   FREQ=14074000
   g90-live-head --profile 1.81 --port /dev/cu.usbserial-XXX \
     --mode USB --freq ${FREQ} --count 10 --save captures/fft-vfo

   g90-live-head --profile 1.81 --port /dev/cu.usbserial-XXX \
     --mode USB --freq $((FREQ + 10000)) --count 10 --save captures/fft-plus10k

   g90-live-head --profile 1.81 --port /dev/cu.usbserial-XXX \
     --mode USB --freq $((FREQ - 10000)) --count 10 --save captures/fft-minus10k
   ```

### Parse

```sh
g90-fft-parse --hex-file captures/fft-vfo/body_000.hex --label baseline
g90-fft-parse --hex-file captures/fft-plus10k/body_000.hex \
  --expect-offset-hz 10000 --vfo-bin 80 --label +10k
g90-fft-parse --hex-file captures/fft-minus10k/body_000.hex \
  --expect-offset-hz -10000 --vfo-bin 80 --label -10k
```

`Hz_per_bin = |offset_hz| / |peak_bin − vfo_bin|`

### Pass criteria

- Baseline peak bin ≈ **80** (±5 bins).
- +10 kHz moves peak **left**; −10 kHz moves peak **right**.
- `Hz_per_bin` ≈ **300** (±20%); consistent between +10k and −10k (±10%).
- **FAIL:** Po≠0; unstable peak; VFO offset didn't move FFT peak.

Bench **2026-07-31** (FT8 7.073 MHz): median **~298 Hz/bin** — fixtures in
`tests/fixtures/fft_bench/`.

---

## Part B — Relative ranking (~15 min)

Uses **band noise** and on-air signals. Does **not** close **O2** (absolute dBm).

```sh
g90-band-scan --port /dev/cu.usbserial-XXX --preset lean --mode CW --top 12
```

- **dB~** column = display affine (`−110 + 1.2×i16`) — **relative** only.
- **PASS:** scan completes; ≥3 hits above noise; strong signals rank higher.

---

## Part C — Live FFT sanity (~5 min)

```sh
g90-live-fft --port /dev/cu.usbserial-XXX --mode USB --freq 14074000 --dbm
```

**PASS:** peak near bin 80 on-signal; peak moves with VFO; Po=0 throughout.

---

## Part D — ATT relative level (~5 min)

Toggle `att_mode` in the head frame (byte 4). CAT not required.

```sh
g90-live-head --profile 1.81 --port /dev/cu.usbserial-XXX \
  --mode USB --freq 7073000 --sql-level 0 \
  --count 35 --interval-ms 400 --save captures/fft-att-off
# att_mode=2: g90-effect-diff --extended ladder, or sess.exchange(att_mode=2)
```

**PASS:** ATT on lowers peak u16 vs off (direction only — not calibrated dB).

Bench **2026-07-31:** peak u16 **42 → 29** (`att_mode` 0 vs 2).

---

## Summary

| Item | Status | Tool |
|------|--------|------|
| Hz/bin | ✅ Bench closed | `g90-fft-parse --expect-offset-hz` |
| ATT relative | ✅ Bench closed | `g90-live-head` `att_mode` 0 vs 2 |
| Relative ranking | ✅ Bench closed | `g90-band-scan` |
| Live FFT | ✅ Bench closed | `g90-live-fft --dbm` |
| Absolute dBm (**O2**) | ⏸ Open | Appendix A (generator) |

Record results in [verification.md](verification.md) tier B/C if you add fixtures.

---

## Appendix A — Absolute dBm (generator only)

**OPTIONAL** — closes **O2** only with a calibrated RF injector.

1. Mode **CW** or **USB**. ATT off for first ladder. Clear freq (e.g. 14.225 MHz).
2. Inject ≥3 known levels (e.g. −93, −73, −53 dBm).
3. Capture 20 frames per level; parse with `g90-fft-parse --dbm-true`.

```sh
g90-live-head --profile 1.81 --port /dev/cu.usbserial-XXX \
  --mode CW --freq 14225000 --count 20 --save captures/fft-m73
g90-fft-parse --hex-file captures/fft-m73/body_000.hex --dbm-true -73 --label m73
```

Fit `dBm_true` vs `peak_u16` offline. **PASS:** ≥3 levels; R² > 0.9; residuals
< ±3 dB. Record coefficients in lab notes — not required for default product scope.

---

## Cheat-sheet

```sh
g90-fft-parse --hex-file captures/fft-vfo/body_000.hex --label baseline
g90-fft-parse --hex-file captures/fft-plus10k/body_000.hex \
  --expect-offset-hz 10000 --vfo-bin 80
g90-band-scan --port … --preset lean --mode CW --top 12
g90-live-fft --port … --freq 14074000 --mode USB --dbm
```
