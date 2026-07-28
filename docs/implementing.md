# Implementing from this repo

Fork tracks and what you can ship from **public artifacts only**. See
[verification.md](verification.md) for evidence tiers (A–E).

## All tracks — minimum bar

1. Read [control spec v1](protocol/control-spec.md) (frozen 2026-07-24).
2. Run `pytest tests/` and `python3 scripts/publish_scrub_check.py --publish`.
3. Cite clause IDs (`P5`, `H3`, `C1`, `S3`, …) in PRs — not RE symbol names.
4. Label tier **D** claims (quiet ladder, BPF tables) if you depend on them.

## Track A — DE-9 codec + fake-head (fork-ready)

**Ship:** Python or port of `g90_codec` + RX-only `g90_control` session.

| Step           | Artifact                                                             |
| -------------- | -------------------------------------------------------------------- |
| Codec profiles | `src/g90_codec/profiles.py`, `TestModes`, `test_classic_gsoc_mishap` |
| CRC            | `src/g90_codec/crc.py`, `TestCrc`                                    |
| Live session   | `LiveHeadSession`, `docs/bench-setup.md`                             |
| Body proof     | `g90-effect-diff`, fixtures `body_181_*.hex`                         |

**Not proven in CI:** filter BPF table indexing (tier **D**), bytes 13–47 semantics
(**O1** open), hardware-in-loop on your bench.

## Track B — CI-V / Hamlib contributor

**Start from:** `src/g90_control/civ.py`, `tests/fixtures/civ_live/`, [civ.md](protocol/civ.md).

| Item                            | Status                                    |
| ------------------------------- | ----------------------------------------- |
| Radio addr `0x88` (also `0x70`) | Fixture-backed                            |
| Opcodes C1–C4                   | `TestCivReadOnly`                         |
| Quirks C-Q1–C-Q4                | `TestCivLiveFixtures`                     |
| `0x1C` PTT/ATU                  | **Hard-blocked** in this library (**S4**) |

**Hamlib PR checklist (out of tree):**

1. Rig model uses DispUnit **User** jack @ **19200** 8N1 — not rear COMM, not DE-9.
2. Map CI-V mode bytes per [civ.md](protocol/civ.md) — they differ from DE-9 wire values.
3. Handle C-Q1 filter read-back, C-Q2 ATT `0x0C`, C-Q4 VFO `which` reply shape.
4. Decide TX policy: this repo blocks `0x1C`; Hamlib may need a separate safety gate.
5. Validate on hardware; C-Q3 set path is synthetic-only in public fixtures.

Reference: [Xiegu G90 CI-V PDF](https://xiegu.eu/sdm_downloads/g90-civ-reference-v1-0/),
[Hamlib rig definitions](https://github.com/Hamlib/Hamlib).

## Track C — GSOC firmware revival (not fork-ready)

**Blockers from public repo alone:**

| Gap                         | Tier                          |
| --------------------------- | ----------------------------- |
| No GSOC hardware validation | —                             |
| `1.81` ≡ `1.79` alias       | **D** + P2/P4 encode identity |
| Body bytes 13–47            | **O1** open                   |
| BPF/filter table proof      | **D**                         |
| Embedded port / BSP         | Not in tree                   |
| TX/PTT paths                | Blocked by design (**S1–S4**) |

**Prerequisite work:** complete Track A on v1.81 body, then port encoder to GSOC
Linux with your own bench matrix. Link [gsoc-contrib](https://github.com/mdubinko/gsoc-contrib)
for hardware context.

## Spec versioning

- **v1 freeze** = `control-spec.md` + green tests.
- New payload semantics → spec **v1.1+** bump + new clause IDs + tests.
- New Xiegu firmware → re-run quiet-ladder checks; add profile if map breaks.

## Legal boundary

- MIT license — see [LICENSE](../LICENSE).
- No vendor `.xgf` / keys / decompilation dumps — `publish_scrub_check.py --publish`.
- Raw bench captures under `captures/` are gitignored; oracles are curated
  `tests/fixtures/*.hex`.
