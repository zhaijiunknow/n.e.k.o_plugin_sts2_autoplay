# sts2_autoplay

Describe what this plugin does and how to configure it.

## Development

This repository is meant to live at:

```text
N.E.K.O/plugin/plugins/sts2_autoplay
```

When publishing to the plugin market, use this GitHub repository name:

```text
n.e.k.o_plugin_sts2_autoplay
```

From this plugin repository root:

```bash
uvx ruff==0.12.4 check --ignore-noqa --config ruff.toml .
```

From the N.E.K.O repository root:

```bash
uv run --with pip python -m plugin.neko_plugin_cli.cli sync sts2_autoplay --clean
uv run python -m plugin.neko_plugin_cli.cli check sts2_autoplay
uv run python -m plugin.neko_plugin_cli.cli check -r sts2_autoplay
```

Python runtime dependencies are declared in `pyproject.toml` and synced into
`vendor/` for packaging. The generated `vendor/` directory is not committed;
local builds and CI recreate it before release checks.

## Market release

Push a tag matching `plugin.toml` version to create a GitHub Release asset:

```bash
git tag v0.1.0
git push origin v0.1.0
```

The generated `.github/workflows/release.yml` uploads `sts2_autoplay.neko-plugin`.
Use that GitHub Release URL when publishing a version in the plugin market.

## Entry

```toml
entry = "plugin.plugins.sts2_autoplay:Sts2AutoplayPlugin"
```
