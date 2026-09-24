"""Runtime workspace selection respects user configuration and platform defaults."""

from pathlib import Path

import pytest

import macloader.config as runtime_config


def test_workspace_override_is_expanded_and_authoritative(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    requested = tmp_path / "configured-workspace"
    monkeypatch.setenv("MACLOADER_WORKSPACE", str(requested))
    assert runtime_config._default_workspace() == requested


@pytest.mark.parametrize(
    ("system", "base_env", "base_value", "expected_suffix"),
    [
        ("win32", "LOCALAPPDATA", "local/appdata", "Libre_Core-MacLoader"),
        ("darwin", None, None, "Library/Caches/Libre_Core-MacLoader"),
        ("linux", "XDG_CACHE_HOME", "xdg/cache", "Libre_Core-MacLoader"),
    ],
)
def test_workspace_defaults_follow_host_platform(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    system: str,
    base_env: str | None,
    base_value: str | None,
    expected_suffix: str,
) -> None:
    monkeypatch.delenv("MACLOADER_WORKSPACE", raising=False)
    monkeypatch.setattr(runtime_config.sys, "platform", system)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    if base_env is not None and base_value is not None:
        base = tmp_path / base_value
        base.mkdir(parents=True)
        monkeypatch.setenv(base_env, str(base))
        expected = base / expected_suffix
    else:
        expected = tmp_path / expected_suffix
    assert runtime_config._default_workspace() == expected


def test_workspace_uses_temp_when_default_base_is_not_writable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("MACLOADER_WORKSPACE", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setattr(runtime_config.sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "unavailable-home")
    monkeypatch.setattr(runtime_config.os, "access", lambda _path, _mode: False)
    monkeypatch.setattr(runtime_config.tempfile, "gettempdir", lambda: str(tmp_path))
    assert runtime_config._default_workspace() == tmp_path / "Libre_Core-MacLoader"
