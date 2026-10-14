# Firmware break: DispUnit 1.78b01 → 1.79b02

Xiegu's step release where the DE-9 frames stayed the same size but the payload
registers inside them changed. That is the line GSOC v1.3 firmware never crossed.

## What did not change

The head↔body **link container** across this boundary:

- Frame lengths (96 / 372 bytes)
- Sync word `55 AA`
- STM32 CRC-32 placement
- UART **115200** 8N1
- DMA / handshake behavior *(static RE — not exercised in `pytest`)*

The break is **payload register semantics** inside the same containers.

## Mode enum renumbering

≤1.78 wire map (6 modes):

| Wire | Mode |
| ---- | ---- |
| 0    | LSB  |
| 1    | USB  |
| 2    | CW   |
| 3    | CWR  |
| 4    | AM   |
| 5    | NFM  |

≥1.79 wire map (8 modes) — L-D and U-D are **inserted**, not appended:

| Wire | Mode    |
| ---- | ------- |
| 0    | LSB     |
| 1    | **L-D** |
| 2    | **USB** |
| 3    | **U-D** |
| 4    | CW      |
| 5    | CWR     |
| 6    | AM      |
| 7    | NFM     |

**Classic mishap:** a GSOC sending USB as wire value **1** (correct on 1.78)
is read as **L-D** on a 1.79+ body. CRC can still validate. The body is in L-D,
not USB.

## Filter bytes

| Profile | High edge         | Low edge          |
| ------- | ----------------- | ----------------- |
| 1.78    | `(Hz − 125) / 25` | `(Hz − 100) / 25` |
| 1.79+   | `(Hz − 50) / 50`  | `(Hz − 50) / 50`  |

The body indexes BPF coefficient tables by these raw bytes *(static RE — not
correlated in `g90-effect-diff`)*. It does not redo the ÷25 or ÷50 math.
1.78 indexes with `raw % 237`; 1.79+ uses a 256-wide index. Wrong encoding →
wrong filter edges even when frequency and mode look plausible.

## Spectrum zoom (`fft_scale`)

- **1.78:** values 1–10 (1 = AUTO)
- **1.79+:** library encode is wire value **1** only (**H5**). The body still applies 1–10 and clamps 11–15 to 10. The post-clamp curve is internal and is not a new profile.

## Quiet ladder through 1.81

Firmware survey across 1.79b02 → 1.81 found **no further** mode/filter/
`fft_scale` map changes on the DE-9 link. That is why this repo ships one
post-break codec profile (`1.79`) and aliases `1.81` to it.

> **Evidence note:** The survey is **static RE** (private firmware analysis), not
> a `pytest` matrix. The public repo defends the alias via **P2/P4** tests
> (identical wire bytes for `1.81` ≡ `1.79`) and fixture parses from v1.81
> hardware. See [verification.md](../verification.md) tier **D**.

Re-check when Xiegu ships new firmware — add a new profile if the map breaks.

## Evidence tiers

| Finding                                    | Tier                                   | Notes                                         |
| ------------------------------------------ | -------------------------------------- | --------------------------------------------- |
| Mode and filter-packing break at 1.78→1.79 | **A** (codec) + **D** (firmware RE)    | Mishap demo: `test_classic_gsoc_mishap`       |
| `fft_scale` body still applies 1–10 after the break | **D**                         | **H5** is the library encode limit, not a body reject |
| Transport unchanged (sizes, sync, CRC)     | **A** + **D**                          | DMA/handshake: **D** only                     |
| Quiet ladder 1.79→1.81                     | **D** + alias tests **A**              | No per-version capture matrix in public repo  |
| Fake-head on live MainUnit v1.81           | **B** (fixtures) + **C** (bench tools) | Not hardware-in-loop CI                       |
| Not a version gate                         | **D**                                  | Emergent payload drift, not a spoofable check |

Full matrix: [verification.md](../verification.md).

## See also

- [Compatibility](../compatibility.md)
- [Wire format](wire-format.md)
- [Control spec](control-spec.md)
