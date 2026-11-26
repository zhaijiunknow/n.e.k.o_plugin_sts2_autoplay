# AlphaDefect sidecar safety checks


## Content-feature alignment (distributed runtime)

The sidecar's local `validated_content_names` guard runs before vendored `load_state`.
Legacy Silent/Defect/Regent checkpoints omit feature names: `policy_manifest.json` pins their
`content_catalog` to v0.111.0 and distributes that catalog alongside the current v0.113.1 catalog.
Never remove it merely because the active game/catalog version is newer. Unknown feature order
must fail visibly instead of using upstream's positional-padding fallback.

The plugin checks `content_alignment_version` in character health replies to reject a still-running
pre-fix sidecar. After deploying this change, open the policy download page to repair the small
catalog download and restart the plugin-owned sidecar. No checkpoint or torch re-download is needed.

For release checks, use the downloaded interpreter and a complete runtime (or an isolated copy of
its engine data plus a diagnostic manifest pointing at the original models):

```
<runtime>/python/python.exe -I -X utf8 tests/policy_runtime_smoke.py --root <runtime>
```

On an old runtime missing the legacy catalog, add `--expect-missing-legacy` to verify that the three
legacy models are rejected. The smoke test does not download, modify the runtime, or contact a game.
Do not substitute a test against upstream's full checkout: it includes legacy catalogs that a broken
release manifest can omit.
