# Glossary

Short definitions for terms used across the bench guides and protocol docs.

| Term                        | Meaning                                                                                                                         |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| **Body / main unit**        | The RF half of the G90 (the box you bench). Sends 372-byte replies on DE-9 pin 3.                                               |
| **Head / display unit**     | The touch UI, or a GSOC in its place. Normally sends 96-byte commands on DE-9 pin 2.                                            |
| **Fake-head**               | Laptop + USB-serial adapter replacing the head. Stock display **or GSOC unplugged** from the body DE-9; same wiring either way. |
| **Profile `1.78`**          | Codec map for ≤1.78 firmware (6 modes, ÷25 filter packing). What frozen GSOC v1.3 still speaks.                                 |
| **Profile `1.79` / `1.81`** | Post-break map (8 modes with L-D/U-D, ÷50 filters). `1.81` aliases to `1.79` in code.                                           |
| **Po**                      | Body forward-power meter byte in the 372-byte frame. Non-zero usually means RF is active; `g90-effect-diff` aborts if Po ≠ 0.   |
| **Y-tap**                   | Optional passive sniff of head TX while the stock display stays connected. Not required for fake-head control.                  |
| **Tier A**                  | Proven by `pytest` in CI — no radio needed.                                                                                     |
| **Tier B**                  | Proven by checked-in hex captures parsed in tests.                                                                              |
| **Tier C**                  | Proven by live bench runs (`g90-live-head`, etc.) — not automated in CI.                                                        |
| **Tier D**                  | Static firmware RE; summaries in docs, binaries not shipped.                                                                    |
| **Tier E**                  | Community or vendor reports cited for context, not re-verified here.                                                            |

Full evidence matrix: [verification.md](verification.md).
