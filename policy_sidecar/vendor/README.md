# AlphaDefect inference subset

Copied from https://github.com/hmty-CHEN/AlphaDefect-Public at commit `f77831b6ea77b8ece32daf1912154ae081da875c`,
`game-v0.114.0/python/` (MIT; see LICENSE). This adapts existing checkpoints to v0.114.0;
it does not introduce newly trained weights.

Local changes: `REPO` in gamever.py/catalog.py reads `STS2_POLICY_ROOT`,
`game_version()` honors `STS2_POLICY_GAME_VERSION`, and package initializers omit
headless/training imports (not distributed here). No inference math is changed.

Reproduce with `python game_mod/scripts/vendor-alphadefect-policy.py --upstream <checkout> --apply`
(or `--archive <official-commit.zip>`), paired with `vendor-alphadefect.py` using the same source.
Do not edit these generated files by hand.
