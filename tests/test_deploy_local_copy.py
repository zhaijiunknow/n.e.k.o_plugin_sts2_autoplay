"""Execute the real deploy script with fake build tools, never a real game install."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHELL = shutil.which("pwsh") or shutil.which("powershell")
pytestmark = pytest.mark.skipif(os.name != "nt" or not SHELL, reason="Windows deployment script")


def ps_quote(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


@pytest.mark.parametrize("running", [False, True, "during_build"])
def test_deploy_preserves_local_copy_against_workshop_overwrite(tmp_path, running):
    project = tmp_path / "project"
    game = tmp_path / "game"
    steam = tmp_path / "steam"
    upload = tmp_path / "upload"
    data = game / "data_sts2_windows_x86_64"
    data.mkdir(parents=True)
    output = project / "nekospire/bin/Release/net9.0/nekospire.dll"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"new-local-dll")
    manifest = project / "nekospire/mod_id.json"
    manifest.write_text('{"id":"nekospire","version":"0.1.11"}', encoding="utf-8")
    builder = tmp_path / "fake-godot.ps1"
    builder.write_text(
        '[System.IO.File]::WriteAllText($args[-1], "new-local-pck")\n$global:LASTEXITCODE = 0\n',
        encoding="utf-8-sig",
    )
    # Neither the build tool nor process discovery contacts the real installation.
    if running == "during_build":
        processes = '$global:probes++; if ($global:probes -gt 1) { [pscustomobject]@{ Id = 123 } }'
    else:
        processes = "[pscustomobject]@{ Id = 123 }" if running else ""
    runner = tmp_path / "run.ps1"
    runner.write_text(
        '$ErrorActionPreference = "Stop"\n'
        'function dotnet { $global:LASTEXITCODE = 0 }\n'
        f'function Get-Process {{ param($Name, $ErrorAction) {processes} }}\n'
        f'$env:STS2_DATA_DIR = {ps_quote(data)}\n'
        f'& {ps_quote(ROOT / "game_mod/scripts/deploy-workshop.ps1")} '
        f'-ProjectRoot {ps_quote(project)} -GodotExe {ps_quote(builder)} '
        f'-SteamRoot {ps_quote(steam)} -UploadContentDir {ps_quote(upload)}\n',
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [SHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(runner)],
        capture_output=True, timeout=45,
    )
    local = game / "mods/nekospire"
    workshop = steam / "steamapps/workshop/content/2868840/3794941932"
    if running:
        assert result.returncode != 0
        assert not local.exists()
        assert not workshop.exists()
        assert not upload.exists()
        return
    assert result.returncode == 0, (result.stdout, result.stderr)
    for destination in (local, workshop, upload):
        assert (destination / "nekospire.dll").read_bytes() == output.read_bytes()
        assert (destination / "mod_id.json").read_bytes() == manifest.read_bytes()
        assert (destination / "nekospire.pck").read_text() == "new-local-pck"
    # A Workshop refresh must not affect the protected local payload.
    (workshop / "nekospire.dll").write_bytes(b"old-workshop-dll")
    (workshop / "mod_id.json").write_text('{"id":"nekospire","version":"0.1.7"}')
    assert (local / "nekospire.dll").read_bytes() == b"new-local-dll"
    assert (local / "mod_id.json").read_bytes() == manifest.read_bytes()
