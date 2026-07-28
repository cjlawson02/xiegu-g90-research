# Contributing

Thanks for contributing. Match the evidence tiers in
[docs/verification.md](docs/verification.md) when adding claims.

## Documentation tone

Docs here should read like lab notes from someone who has the radio on the bench,
not like a generated overview. Prefer:

- Commands, hex fixtures, and test names over adjectives
- Dates and firmware versions when stating what was verified
- Short sentences; one fact per line in tables
- Explicit limits ("not in CI", "tier D only") instead of vague confidence

Avoid filler transitions ("Furthermore", "It is important to note"), significance
inflation ("crucial", "pivotal"), and em-dash chains. The normative
[control spec](docs/protocol/control-spec.md) stays formal; narrative pages
should stay plain.

## Development setup

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,serial,viz]"
pytest tests/ -v
```

## Pull request checklist

- [ ] `pytest tests/` passes
- [ ] `python3 scripts/publish_scrub_check.py --publish` passes
- [ ] Behavior changes update [docs/protocol/control-spec.md](docs/protocol/control-spec.md) **and** tests
- [ ] No vendor binaries, keys, or decrypted firmware added
- [ ] Docs updated when user-facing behavior changes

## Spec changes

The v1 control surface is **frozen**. Expanding semantics (e.g. decoding body
bytes 13–47, absolute dBm) requires a explicit spec version bump and new tests.

## Code style

- Python 3.10+; stdlib-first in `g90_codec`
- `ruff check src tests` (optional locally)
- Match existing naming in `g90_control` / `g90_codec`

## Questions

Open a [GitHub issue](https://github.com/cjlawson02/xiegu-g90-research/issues)
for protocol questions, bench results, or firmware-version reports.
