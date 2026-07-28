# CI-V (CAT) on the G90 display unit

Notes for `g90-live-civ` and `CivClient`. This is the **Icom CI-V** path on the
stock **display unit (DispUnit)** — separate from the DE-9 head↔body link.

## Connection


| Parameter                  | Value                                                                 |
| -------------------------- | --------------------------------------------------------------------- |
| Jack                       | DispUnit **User** port (person icon on the front panel)               |
| Baud                       | **19200** 8N1, no flow control                                        |
| Default radio address      | `0x88` (also responds as `0x70` — see fixture `addr70_read_mode.hex`) |
| Default controller address | `0xE0`                                                                |


CAT is only on the DispUnit **User** jack above — not the rear/base **COMM**
jack on the main unit. That is a port-labeling issue, not a CI-V protocol quirk.

## Frame format

```
FE FE  <to>  <from>  <cmd>  [payload…]  FD
```



## Supported opcodes (v1 freeze)


| Cmd             | Role                         |
| --------------- | ---------------------------- |
| `0x03` / `0x04` | Read frequency / mode        |
| `0x05` / `0x06` | Set frequency / mode         |
| `0x0F`          | Split read/write             |
| `0x11`          | ATT read/write               |
| `0x14`          | Levels (AF, SQL, …)          |
| `0x15`          | Meters (S, Po, SWR, ALC, …)  |
| `0x16`          | Extensions (PRE, AGC, NB, …) |
| `0x1D 0x19`     | Radio ID → `00 90` (G90)     |
| `0x25` / `0x26` | Selected VFO freq/mode       |


**Blocked:** `0x1C` (PTT / ATU) — hard error in this library.

CI-V mode bytes differ from DE-9 wire mode values:

`LSB=0x00 USB=0x01 AM=0x02 CW=0x03 NFM=0x05 CWR=0x07`

## Live quirks (v1.81)

Bench **2026-07-27** captures in `tests/fixtures/civ_live/`. Tests in
`TestCivLiveFixtures`.


| ID       | Behavior                                                                                               | In-repo test                                                      |
| -------- | ------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------- |
| **C-Q1** | `0x06` filter group may ACK but read-back stays `0x01`                                                 | `test_cq1_set_mode_ack_echo`, `test_cq1_filter_readback_stays_01` |
| **C-Q2** | ATT on reads `0x0C`; set replies may be `0x11 <state>` not `FB`                                        | `test_cq2_att_live_hex`                                           |
| **C-Q3** | `0x14` level read may use non-BCD bytes (e.g. `0x7D`); set ACK vs read-back mismatch not live-fixtured | `test_encode_level_split_vfo` (synthetic read path)               |
| **C-Q4** | `0x25`/`0x26` with `which=0x01`: reply carries `which=0x00` (selected VFO data), not the requested VFO | `test_cq4_unselected_vfo_reply_shape`                             |


Evidence tiers: [verification.md](../verification.md).

## Usage

```sh
g90-live-civ --list-ports
g90-live-civ --port /dev/cu.usbserial-XXX --controls --levels
g90-live-civ --port /dev/cu.usbserial-XXX \
  --set-freq 14225000 --set-mode USB --restore
```

Stock head must remain on the body; CAT cable on the DispUnit **User** jack.
No antenna required for read-only polling; do not send PTT/ATU commands without
RF safety policy.

## References

- [Xiegu G90 CI-V reference (vendor PDF)](https://xiegu.eu/sdm_downloads/g90-civ-reference-v1-0/)
- [Hamlib rig definitions](https://github.com/Hamlib/Hamlib) (addr `0x88`)

