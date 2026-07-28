# CI-V live fixtures

Captured **2026-07-27** on stock G90 v1.81. CAT cable on the DispUnit **User**
jack (person icon on the front panel) — **not** the rear/base COMM jack.

@ 19200 8N1, radio addr `0x88`, controller `0xE0`.

Re-capture:

```sh
.venv/bin/python scripts/capture_civ_fixtures.py --port /dev/cu.usbserial-XXX
```

| File | Quirk / check |
|------|----------------|
| `radio_id_1d_19_addr88.hex` | Radio ID → `00 90` |
| `cq2_att_off.hex` / `cq2_att_on.hex` | C-Q2 ATT read (`0x0C` when on) |
| `cq1_set_mode_usb_filter02.hex` / `cq1_read_mode_after_filter02.hex` | C-Q1 filter read-back stays `0x01` |
| `cq4_vfo_freq_which01.hex` / `cq4_vfo_mode_which01.hex` | C-Q4: `which=0x01` TX → reply uses `which=0x00` |
| `addr70_read_mode.hex` | Radio also responds at CI-V addr `0x70` |

Tests: `TestCivLiveFixtures` in `tests/test_control.py`.
