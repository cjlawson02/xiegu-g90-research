# FFT bench fixtures

Captured **2026-07-31** on G90 MainUnit **v1.81** with fake-head (FT232 on DE-9;
stock head unplugged). On-air **FT8 @ 7.073 MHz** USB; no RF generator.

Re-capture: [`docs/fft-bench.md`](../../docs/fft-bench.md).

| File | Bench step | Peak (u16@bin) | Notes |
|------|------------|----------------|-------|
| `body_181_fft_vfo_7073mhz_20260731.hex` | Hz/bin baseline | 14@80 | VFO 7.073 MHz |
| `body_181_fft_plus10k_7073mhz_20260731.hex` | +10 kHz offset | 14@47 | Δbin −33 → ~303 Hz/bin |
| `body_181_fft_minus10k_7073mhz_20260731.hex` | −10 kHz offset | 11@114 | Δbin +34 → ~294 Hz/bin |
| `body_181_fft_att_off_20260731.hex` | ATT off (`att_mode=0`) | 42@79 | Same VFO |
| `body_181_fft_att_on_20260731.hex` | ATT on (`att_mode=2`) | 29@80 | Peak drops vs off |

Tests: `TestFftBenchFixtures` in `tests/test_fft_bench.py`.
