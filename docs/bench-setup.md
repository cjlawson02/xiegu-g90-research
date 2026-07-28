# Bench setup

Wire a 3.3 V USB-serial adapter to the body DE-9, unplug the stock head, and
have the laptop send 96-byte frames like the display would. Tested against
**G90 MainUnit v1.81**; post-1.79 bodies should match the `1.79` codec profile.
Follow your license and RF safety rules if you enable transmit paths (this
repo defaults to RX-only).

## DE-9 pinout

Connector on the **G90 body front** (standard DE-9, pins toward you):

```
  1  2  3  4  5
   6  7  8  9
```

| Pin | Signal             | Notes                                |
| --- | ------------------ | ------------------------------------ |
| 1   | **+8 V**           | **Never connect** adapter power here |
| 2   | Head → body serial | 96-byte frames @ 115200 8N1          |
| 3   | Body → head serial | 372-byte replies                     |
| 4   | Power button       | Leave alone                          |
| 5   | **GND**            | Common ground to USB adapter         |
| 6–9 | Audio / phones     | Leave alone                          |

Community reference: [zeroping headprotocol](https://github.com/zeroping/xiegu-g90-headprotocol).

## Adapter requirements

- **3.3 V logic** FT232-class adapter (jumper or fixed I/O voltage)
- **Do not** connect adapter VCC/3.3V/5V to the radio
- For passive sniff with stock head attached: **RXD + GND only** — leave TXD disconnected

## Fake-head (control path)

**Unplug the head from the body DE-9** — stock Xiegu display or GSOC, same
connector. The laptop pretends to be the head. This repo does not flash or test
GSOC boards; it only speaks the wire protocol a modern body expects.

| FT232 | DE-9  |
| ----- | ----- |
| GND   | pin 5 |
| TXD   | pin 2 |
| RXD   | pin 3 |

1. Power off, wire as above, plug USB adapter
2. Power on body only (no stock head)
3. Run:

```sh
g90-live-head --profile 1.81 --port /dev/cu.usbserial-XXX \
  --mode USB --freq 14225000 --count 15 --save captures/session-01
```

Expect CRC-valid 372-byte body replies. Tools default to **RX-only** (no PTT,
no ATU tune, no VOX).

## RF bench hygiene

- Prefer **no antenna** or a **dummy load** while exercising DE-9 control. The
  default tools do not key the transmitter, but the radio is still a transmitter.
- Default ladder (`g90-effect-diff` without flags) stays RX-only and aborts if
  body **Po ≠ 0** (forward power non-zero).
- **`--armed-controls`** enables ATU tune and shutdown steps that **may key RF**
  or power off the radio. Do not use it without a deliberate TX policy, suitable
  antenna or load, and your license rules.

## Body-effect proof

After fake-head works, run the automated ladder:

```sh
g90-effect-diff --port /dev/cu.usbserial-XXX
g90-effect-diff --port /dev/cu.usbserial-XXX --extended
```

**PASS** requires header and FFT deltas from `g90-effect-diff`, not only
CRC-valid replies.

## Passive sniff (optional)

With stock head **still connected**, RX-only tap on pin 2:

```sh
g90-sniff --profile 1.81 --port /dev/cu.usbserial-XXX --count 5
```

Never drive TXD while the stock head is on the bus.

## CI-V path (different cable)

CAT uses the DispUnit **User** jack (person icon) at **19200 8N1**, not the
rear/base COMM jack and not the DE-9. See [CI-V notes](protocol/civ.md) and
`g90-live-civ`.

## Remote spectrum demo

```sh
# Offline figure from fixtures
g90-live-fft --replay tests/fixtures --headless --save-png /tmp/fft.png --log

# Live (needs .[viz] extra)
g90-live-fft --port /dev/cu.usbserial-XXX --freq 14060000 --mode CW --dbm
```

Y-axis `dbm` applies a display affine — relative bins, not calibrated absolute
dBm. See control spec **O2**.

## What we proved (v1.81 lab)

Bench session **2026-07-27** — see [verification.md](verification.md) for tiers.

- CRC-valid fake-head TX ↔ body RX both directions *(B + C)*
- Body-effect ladder PASS (`g90-effect-diff`) *(manifest: `effect_diff_manifest_20260727.jsonl`)*
- CI-V freq/mode/meters on DispUnit User jack *(B fixtures + C live)*
- Remote FFT/waterfall from body link bins *(B replay + C live)*

Not claimed here: stock-head UI field-for-field match (Y-tap), absolute dBm
calibration, or “GSOC fixed.”

## Troubleshooting

| Symptom                                          | Things to check                                                                                                                           |
| ------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `body_miss` climbs / “no body frame within 3.0s” | Head (or GSOC) **unplugged** from DE-9; GND on pin 5; adapter is **3.3 V** logic (not 5 V); try swapping TXD↔RXD (pins 2 and 3).          |
| CRC errors on replies                            | Wrong `--profile` for your body firmware (post-1.79 bodies need `1.81` / `1.79`; use `g90-decode` on a saved body hex and check `info6`). |
| `g90-live-head` exits 1, `body_crc_ok=0`         | Baud must be **115200**; only pins 2, 3, 5 connected; never tie adapter VCC to pin 1 (+8 V).                                              |
| Body replies but wrong mode on panel             | You are almost certainly on profile `1.78` against a ≥1.79 body — see [Compatibility](compatibility.md).                                  |
| `g90-effect-diff` aborts on Po                   | Something is driving TX (external PTT, armed controls, or RF in the shack). Fix power state before retrying.                              |
| Works on Mac path, fails on Windows/Linux        | Use `g90-live-head --list-ports` and pass the correct device (see [Getting started](getting-started.md#serial-ports)).                    |
