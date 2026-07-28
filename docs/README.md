# Documentation

Protocol notes, bench guides, and the frozen v1 control spec for the G90
display↔body DE-9 link. Start with **Getting started** if you want commands;
read **Compatibility** if you are tracing the GSOC firmware cliff.

## Start here

| Document                              | Description                            |
| ------------------------------------- | -------------------------------------- |
| [Getting started](getting-started.md) | Install, run tests, first commands     |
| [Bench setup](bench-setup.md)         | DE-9 wiring, fake-head, safety         |
| [Compatibility](compatibility.md)     | Why the GSOC breaks and what we target |
| [Glossary](glossary.md)               | fake-head, profiles, tiers, Po, Y-tap  |
| [Implementing](implementing.md)       | Fork tracks: DE-9, CI-V/Hamlib, GSOC   |

## Protocol

| Document                                               | Description                                 |
| ------------------------------------------------------ | ------------------------------------------- |
| [Wire format](protocol/wire-format.md)                 | Frame sizes, CRC, DE-9 overview             |
| [Firmware break 1.78→1.79](protocol/firmware-break.md) | The GSOC-breaking payload change            |
| [Control spec (frozen v1)](protocol/control-spec.md)   | Normative behavior for implementers         |
| [CI-V (CAT)](protocol/civ.md)                          | DispUnit User jack usage and quirks         |
| [Verification matrix](verification.md)                 | What CI, fixtures, bench, and RE each prove |
