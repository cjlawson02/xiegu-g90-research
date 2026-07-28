# Getting started

Install the package, run the test gate, then try offline encode/decode before
you touch the DE-9 on a live radio. Bench wiring is in [Bench setup](bench-setup.md).

## Requirements

- Python **3.10+**
- For live bench tools: USB serial adapter (**3.3 V** logic), [`pyserial`](https://pypi.org/project/pyserial/)
- For spectrum UI: optional `pip install -e ".[viz]"` (PyQt6 + pyqtgraph)

## Install

```sh
git clone https://github.com/cjlawson02/xiegu-g90-research.git
cd xiegu-g90-research
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev,serial]"
```

Editable install exposes console commands and lets you import `g90_codec` /
`g90_control` from any script.

## Verify

```sh
pytest tests/ -v
```

Both test modules must pass:

- `tests/test_codec_golden.py` — dual-profile codec + fixtures
- `tests/test_control.py` — v1.81 defaults, CI-V vectors, RX-only guards

## Decode a frame (offline)

```sh
# Encode a v1.81 USB head frame
g90-decode --profile 1.81 --encode --mode USB --freq 14225000

# Decode a fixture
g90-decode --profile 1.78 \
  --hex "$(cat tests/fixtures/head_178_usb.hex)"
```

## Python API

```python
from g90_codec import Profile, encode_head_frame, decode_head_frame
from g90_control import HeadControl, build_head_frame

raw = build_head_frame(mode="USB", freq_hz=14_225_000)
dec = decode_head_frame("1.81", raw)
print(dec.summary())

ctrl = HeadControl()  # defaults to profile 1.81
assert ctrl.decode(ctrl.encode(mode="CW")).vfo_a.mode_name == "CW"
```

## Serial ports

Run `g90-live-head --list-ports` (or any live tool with `--list-ports`) and
pass the device your OS exposes:

| OS      | Typical path                                  | Example                           |
| ------- | --------------------------------------------- | --------------------------------- |
| macOS   | `/dev/cu.*` (prefer `cu` over `tty` for FTDI) | `--port /dev/cu.usbserial-FTXXXX` |
| Linux   | `/dev/ttyUSB0`, `/dev/ttyACM0`                | `--port /dev/ttyUSB0`             |
| Windows | `COM3`, `COM4`, …                             | `--port COM3`                     |

Wiring and troubleshooting: [Bench setup](bench-setup.md).

## Live bench (radio required)

See [Bench setup](bench-setup.md). Minimal fake-head loop:

```sh
g90-live-head --list-ports
g90-live-head --profile 1.81 --port /dev/cu.usbserial-XXX \
  --mode USB --freq 14225000 --count 15
```

On success you should see lines like `[1] crc_ok …` and a final summary
`done: tx=15 body_crc_ok=15 body_miss=0`. Any `body_miss` → see
[Bench setup — Troubleshooting](bench-setup.md#troubleshooting).

Prove the body actually follows your encodes:

```sh
g90-effect-diff --port /dev/cu.usbserial-XXX
```

A default **PASS** ends with a verdict block similar to:

```
=== verdict ===
  any_header_delta=True any_fft_delta=True
  mode_moved=True freq_fft_moved=True ...
  max_body_Po=0 (must stay 0)
  PASS (wire): mode+freq steps moved body header/FFT — RX-only held (Po=0)
```

Checked-in manifest from bench **2026-07-27**:
`tests/fixtures/effect_diff_manifest_20260727.jsonl` (8 steps, all `Po=0`).

## CI-V (stock head on, CAT cable)

```sh
g90-live-civ --port /dev/cu.usbserial-XXX --controls --levels
```

Details: [CI-V notes](protocol/civ.md). See also [Compatibility](compatibility.md),
[Control spec v1](protocol/control-spec.md), [Implementing](implementing.md).
