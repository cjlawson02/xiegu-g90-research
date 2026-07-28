# xiegu-g90-research

If you run a **GSOC** on a G90 and upgraded the radio past firmware **v1.78**, you
probably saw modes drift, filters sound wrong, or the head stop matching what the
body is doing. Xiegu never updated GSOC firmware after **v1.3** (2021), but kept
changing what the bytes on the front-panel **DE-9** cable *mean*. The frames are
still 96 bytes at 115200 baud with the same CRC — so the link can look fine while
USB on the head is **L-D** on the body.

This repo documents that break, implements the **v1.81** byte map in Python, and
includes tools to drive a modern G90 body from a laptop when you unplug the head
and wire in an FT232. Bench-checked on a v1.81 MainUnit **2026-07-27**; GSOC
hardware itself is not tested here.

[![CI](https://github.com/cjlawson02/xiegu-g90-research/actions/workflows/ci.yml/badge.svg)](https://github.com/cjlawson02/xiegu-g90-research/actions/workflows/ci.yml)

**Status:** Codec and RX-only safety guards pass `pytest` on every push. Fake-head
control on a **v1.81** body was checked on the bench **2026-07-27** (hex
fixtures + live tools; no radio in CI). See [docs/verification.md](docs/verification.md)
for what each tier actually proves.

## What works today

| Capability                            | Command / module           |
| ------------------------------------- | -------------------------- |
| Dual-profile codec (1.78 + 1.79/1.81) | `g90_codec` · `g90-decode` |
| Head control API (v1.81 defaults)     | `g90_control`              |
| Live fake-head (DE-9)                 | `g90-live-head`            |
| Body-effect proof ladder              | `g90-effect-diff`          |
| CI-V on DispUnit User jack            | `g90-live-civ`             |
| Remote spectrum / waterfall           | `g90-live-fft`             |

No vendor firmware, keys, or decompilation dumps — enforced by
`python3 scripts/publish_scrub_check.py --publish` (CI on every push).

## Quick start

```sh
git clone https://github.com/cjlawson02/xiegu-g90-research.git
cd xiegu-g90-research
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,serial]"
pytest tests/ -v
```

```sh
# Offline: encode a v1.81 USB frame
g90-decode --profile 1.81 --encode --mode USB --freq 14225000

# Live (stock head unplugged, FT232 on DE-9 — see docs)
g90-live-head --list-ports
g90-live-head --profile 1.81 --port /dev/cu.usbserial-XXX --count 15
```

Full guide: [docs/getting-started.md](docs/getting-started.md).

## Why the GSOC breaks

GSOC firmware stopped at **v1.3** (Jan 2021) while G90 main-unit firmware
kept moving. At **v1.79** Xiegu inserted L-D/U-D into the mode table and
switched filter packing from ÷25 to ÷50. Frame size, sync word, and CRC stayed
the same, so the link can look healthy while every mode above LSB is wrong.

A GSOC still sending USB as wire byte **1** (correct on ≤1.78) lands the body
in **L-D** on v1.81. This repo implements the post-break map once (codec
profile `1.79`, aliased as `1.81`) and checked it with a laptop pretending to be
the head.

[Compatibility story](docs/compatibility.md) · [1.78→1.79 byte tables](docs/protocol/firmware-break.md)

## Documentation

| Doc                                                       |                                |
| --------------------------------------------------------- | ------------------------------ |
| [Getting started](docs/getting-started.md)                | Install, API, commands         |
| [Bench setup](docs/bench-setup.md)                        | DE-9 wiring and safety         |
| [Control spec v1 (frozen)](docs/protocol/control-spec.md) | Normative behavior             |
| [Wire format](docs/protocol/wire-format.md)               | Frames, CRC, profiles          |
| [CI-V](docs/protocol/civ.md)                              | CAT on DispUnit User jack      |
| [Implementing](docs/implementing.md)                      | Fork tracks (DE-9, CI-V, GSOC) |

Index: [docs/README.md](docs/README.md) · [Glossary](docs/glossary.md).

## Project layout

```
src/g90_codec/       frame encode/decode, profiles, CRC
src/g90_control/     v1.81 defaults, live session, CI-V client
src/*/cli/           console entry points (g90-decode, g90-live-head, …)
tests/               golden vectors + control freeze gate
docs/                protocol notes and bench guides
scripts/             publish scrub helper
```

## Implementing from this repo

See [docs/implementing.md](docs/implementing.md) for fork tracks (DE-9 codec,
CI-V/Hamlib, GSOC hardware). Minimum bar:

1. Read [control spec v1](docs/protocol/control-spec.md)
2. Make `pytest tests/` pass
3. Cite **clause IDs** (e.g. `H3`, `C1`, `S3`) in code reviews — not RE addresses

## Related community work

- [gsoc-contrib](https://github.com/mdubinko/gsoc-contrib) — GSOC hardware teardown
- [zeroping headprotocol](https://github.com/zeroping/xiegu-g90-headprotocol) — early wire notes
- [G90Tools](https://github.com/OpenHamradioFirmware/G90Tools) — community G90 utilities

## License

MIT — see [LICENSE](LICENSE). Vendor firmware is not redistributed.

## Disclaimer

Not affiliated with Xiegu or Radioddity. Use at your own risk; follow amateur
radio regulations and RF safety practices.
