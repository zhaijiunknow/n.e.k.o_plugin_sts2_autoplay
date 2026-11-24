# AlphaDefect inference subset

Copied from https://github.com/hmty-CHEN/AlphaDefect-Public at commit `068953d3f62278ab36cac62850b437f33a5ad3b3` (MIT, see LICENSE):
the frame parser (`sts2env/protocol.py`), the batch builder (`sts2rl/features.py`), the network
(`sts2rl/model.py`) and the content tables they read. Nothing else of upstream is included.

Two local changes, both about where files are found (no change to what is computed):
- `REPO` in `sts2env/gamever.py` and `sts2rl/catalog.py` is the downloaded runtime directory
  (`STS2_POLICY_ROOT`, holding `engine/catalog/`), not a source checkout.
- `game_version()` returns `STS2_POLICY_GAME_VERSION` when set.

Do not edit by hand; the copy is reproduced from upstream's commit with these replacements.
