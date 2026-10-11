"""Tests for Chromium-presence detection in browser_tool.

Regression guard for the "browser tool advertised but Chromium missing"
class of bug — where ``agent-browser`` CLI is discoverable but no
Chromium build is on disk, causing every browser_* tool call to hang
for the full command timeout before surfacing a useless error.
"""

import os
import shutil

import pytest

from tools import browser_tool as bt
from tools import browser_tool_install as bt_install
from tools import browser_tool_cloud as bt_cloud


@pytest.fixture(autouse=True)
def _reset_chromium_cache():
    bt._cached_chromium_installed = None
    yield
    bt._cached_chromium_installed = None


class TestChromiumSearchRoots:
    def test_respects_playwright_browsers_path_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
        roots = bt_install._chromium_search_roots()
        assert str(tmp_path) == roots[0]


    def test_always_includes_default_ms_playwright_cache(self, monkeypatch):
        monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
        roots = bt_install._chromium_search_roots()
        home = os.path.expanduser("~")
        assert any(r == os.path.join(home, ".cache", "ms-playwright") for r in roots)


class TestChromiumInstalled:
    def test_true_when_plain_chromium_on_path(self, monkeypatch):
        monkeypatch.delenv("AGENT_BROWSER_EXECUTABLE_PATH", raising=False)
        monkeypatch.setattr(
            shutil,
            "which",
            lambda name, path=None: "/usr/bin/chromium" if name == "chromium" else None,
        )

        assert bt_install._chromium_installed() is True


    def test_result_cached(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
        (tmp_path / "chromium-1208").mkdir()
        assert bt_install._chromium_installed() is True
        # Delete after first call — cached True should still return True.
        (tmp_path / "chromium-1208").rmdir()
        assert bt_install._chromium_installed() is True


class TestCheckBrowserRequirementsChromium:

    def test_local_mode_with_chromium_returns_true(self, monkeypatch, tmp_path):
        monkeypatch.setattr(bt, "_is_camofox_mode", lambda: False)
        monkeypatch.setattr(bt_install, "_find_agent_browser", lambda **_kw: "/usr/local/bin/agent-browser")
        monkeypatch.setattr("tools.browser_tool_install._requires_real_termux_browser_install", lambda _: False)
        monkeypatch.setattr(bt_cloud, "_get_cloud_provider", lambda: None)
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
        (tmp_path / "chromium-1208").mkdir()

        assert bt_install.check_browser_requirements() is True


    def test_camofox_mode_does_not_require_chromium(self, monkeypatch, tmp_path):
        monkeypatch.setattr(bt, "_is_camofox_mode", lambda: True)
        # Even with no chromium on disk, camofox drives its own backend.
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
        monkeypatch.setattr("os.path.expanduser", lambda p: str(tmp_path / "fakehome"))

        assert bt_install.check_browser_requirements() is True


class TestRunBrowserCommandChromiumGuard:
    """Verify _run_browser_command fails fast (no timeout hang) when
    Chromium is missing in local mode.
    """




@pytest.mark.parametrize("cache_kind", ["empty", "unrelated", "file"])
def test_missing_chromium_checks_every_install_source(monkeypatch, tmp_path, cache_kind):
    """An empty Playwright cache is missing Chromium only when PATH/override are absent."""
    monkeypatch.delenv("AGENT_BROWSER_EXECUTABLE_PATH", raising=False)
    monkeypatch.setattr(shutil, "which", lambda name, path=None: None)
    cache = tmp_path / "browsers"
    if cache_kind == "file":
        cache.write_text("")
    else:
        cache.mkdir()
        if cache_kind == "unrelated":
            (cache / "firefox-1234").mkdir()
    monkeypatch.setattr(bt_install, "_chromium_search_roots", lambda: [str(cache)])

    assert bt_install._chromium_installed() is False


@pytest.mark.parametrize("in_docker, hint", [(True, "docker pull"), (False, "agent-browser install")])
def test_missing_chromium_fails_before_spawning_with_recovery_hint(monkeypatch, tmp_path, in_docker, hint):
    """Port the copied fail-fast contract to the extracted browser modules and lazy-install gate."""
    from tools import browser_tool_session

    monkeypatch.delenv("AGENT_BROWSER_EXECUTABLE_PATH", raising=False)
    monkeypatch.setattr(shutil, "which", lambda name, path=None: None)
    monkeypatch.setattr(bt_install, "_chromium_search_roots", lambda: [str(tmp_path)])
    monkeypatch.setattr(bt_install, "_find_agent_browser", lambda **kw: "/test/agent-browser")
    monkeypatch.setattr(bt_install, "_requires_real_termux_browser_install", lambda _: False)
    monkeypatch.setattr(bt_install, "_running_in_docker", lambda: in_docker)
    monkeypatch.setattr(bt_install, "_maybe_autoinstall_chromium", lambda: False)
    monkeypatch.setattr(bt_cloud, "_is_local_mode", lambda: True)
    monkeypatch.setattr(bt_cloud, "_get_browser_engine", lambda: "chromium")
    monkeypatch.setattr(bt_cloud, "_get_cloud_provider", lambda: None)
    monkeypatch.setattr(bt, "_is_browser_use_cli_mode", lambda: False)
    monkeypatch.setattr(bt, "_is_camofox_mode", lambda: False)
    monkeypatch.setattr("tools.browser_tool_cdp._get_cdp_override_raw", lambda: None)

    def unexpected_spawn(*args, **kwargs):
        pytest.fail("Missing Chromium must fail before starting a browser subprocess")

    monkeypatch.setattr("subprocess.Popen", unexpected_spawn)
    assert bt_install.check_browser_requirements() is False
    result = browser_tool_session._run_browser_command("task-1", "open", ["https://example.com"])
    assert result["success"] is False
    assert "Chromium" in result["error"]
    assert hint in result["error"]
