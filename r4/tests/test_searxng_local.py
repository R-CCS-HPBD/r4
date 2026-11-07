"""``r4 search-server``: a local SearXNG for ``web_search`` (ADR-102).

Hermetic: the container engine is never called (``_run`` and ``shutil.which``
are stubbed), the health probe is stubbed, and the config dir is ``tmp_path``.
"""

from __future__ import annotations

import stat
from argparse import Namespace

import pytest

from r4.agent import searxng_local, web_search

URL = "http://127.0.0.1:8888"


@pytest.fixture(autouse=True)
def _scratch(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for name in (
        searxng_local.PORT_ENV,
        searxng_local.IMAGE_ENV,
        searxng_local.ENGINE_ENV,
        web_search.SEARXNG_URL_ENV,
    ):
        monkeypatch.delenv(name, raising=False)
    searxng_local._PROBE_CACHE.clear()
    monkeypatch.setattr(searxng_local.time, "sleep", lambda _s: None)


def _answers(monkeypatch: pytest.MonkeyPatch, up: bool) -> None:
    monkeypatch.setattr(searxng_local, "_probe", lambda url: up)


class _Engine:
    """Records every engine command; ``fail`` maps a verb to its error text."""

    def __init__(self, fail: dict[str, str] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.fail = fail or {}

    def __call__(self, command: list[str], timeout: int) -> str | None:
        self.calls.append(command)
        verb = " ".join(command[1:3]) if command[1] == "image" else command[1]
        return self.fail.get(verb)

    def verbs(self) -> list[str]:
        return [" ".join(c[1:3]) if c[1] == "image" else c[1] for c in self.calls]


def _engine(monkeypatch: pytest.MonkeyPatch, fail: dict[str, str] | None = None) -> _Engine:
    engine = _Engine(fail)
    monkeypatch.setattr(searxng_local, "_run", engine)
    monkeypatch.setattr(searxng_local.shutil, "which", lambda name: f"/usr/bin/{name}")
    return engine


def test_the_container_is_loopback_only_read_only_config_no_capabilities(tmp_path) -> None:
    command = searxng_local.run_command("podman", 8888, "img:tag", tmp_path)
    assert command[:2] == ["podman", "run"]
    assert "127.0.0.1:8888:8080" in command
    assert f"{tmp_path}:/etc/searxng:Z,ro" in command
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert "no-new-privileges" in command
    assert command[-1] == "img:tag"


def test_engine_prefers_podman_and_honours_the_override(monkeypatch) -> None:
    installed = {"podman", "docker"}
    monkeypatch.setattr(searxng_local.shutil, "which", lambda n: n if n in installed else None)
    assert searxng_local.container_engine() == "podman"
    installed.discard("podman")
    assert searxng_local.container_engine() == "docker"
    monkeypatch.setenv(searxng_local.ENGINE_ENV, "podman")
    assert searxng_local.container_engine() is None


def test_settings_enable_json_keep_a_private_secret_and_keep_edits() -> None:
    path = searxng_local.write_settings()
    text = path.read_text()
    assert "formats: [html, json]" in text and "limiter: false" in text
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    path.write_text(text + "# mine\n")
    assert searxng_local.write_settings().read_text().endswith("# mine\n")


def test_up_starts_pulls_only_a_missing_image_and_records_the_url(monkeypatch) -> None:
    engine = _engine(monkeypatch, fail={"image inspect": "no such image"})
    probes = iter([False, False, True])
    monkeypatch.setattr(searxng_local, "_probe", lambda url: next(probes))
    result = searxng_local.up()
    assert result["state"] == "started" and result["url"] == URL
    assert engine.verbs() == ["info", "image inspect", "pull", "rm", "run"]
    assert searxng_local.recorded_url() == URL


def test_up_reports_each_step_to_the_progress_callback(monkeypatch) -> None:
    _engine(monkeypatch, fail={"image inspect": "no such image"})
    probes = iter([False, False, True])
    monkeypatch.setattr(searxng_local, "_probe", lambda url: next(probes))
    lines: list[str] = []
    assert searxng_local.up(progress=lines.append)["state"] == "started"
    assert [line.split(" ")[0] for line in lines] == ["downloading", "starting", "waiting"]
    assert "first start only" in lines[0] and URL in lines[2]


def test_up_skips_the_pull_when_the_image_is_present(monkeypatch) -> None:
    engine = _engine(monkeypatch)
    probes = iter([False, True])
    monkeypatch.setattr(searxng_local, "_probe", lambda url: next(probes))
    assert searxng_local.up()["state"] == "started"
    assert "pull" not in engine.verbs()


def test_up_is_a_no_op_while_the_instance_answers(monkeypatch) -> None:
    engine = _engine(monkeypatch)
    _answers(monkeypatch, True)
    assert searxng_local.up() == {"state": "running", "url": URL}
    assert engine.calls == []
    assert searxng_local.recorded_url() == URL


def test_up_failures_say_why(monkeypatch) -> None:
    _answers(monkeypatch, False)
    monkeypatch.setattr(searxng_local.shutil, "which", lambda name: None)
    assert "no container engine" in searxng_local.up()["reason"]
    _engine(monkeypatch, fail={"image inspect": "missing", "pull": "unauthorized"})
    failed = searxng_local.up()
    assert "unauthorized" in failed["reason"] and "logout" in failed["hint"]
    monkeypatch.setattr(searxng_local, "_START_TIMEOUT_SEC", 0.0)
    _engine(monkeypatch)
    assert "healthz" in searxng_local.up()["reason"]
    assert searxng_local.recorded_url() is None


# 2026-11-01, live report: a stopped podman machine. `image inspect` failed on
# the dead connection, read as "image missing"; the console said
# "downloading…" and ended on podman's raw, cut-off error.
_PODMAN_DOWN = (
    "Cannot connect to Podman. Please verify your connection to the Linux system using "
    "`podman system connection list`, or try `podman machine init` and `podman machine "
    "start` to manage a new Linux VM\nError: unable to connect to Podman socket: dial unix …"
)


def test_a_stopped_podman_machine_stops_before_any_download(monkeypatch) -> None:
    engine = _engine(monkeypatch, fail={"info": _PODMAN_DOWN, "image inspect": _PODMAN_DOWN})
    _answers(monkeypatch, False)
    monkeypatch.setattr(searxng_local.sys, "platform", "darwin")
    lines: list[str] = []
    failed = searxng_local.up(progress=lines.append)
    assert engine.verbs() == ["info"]
    assert lines == []
    assert failed["reason"] == "podman cannot reach its machine or service"
    assert "`podman machine start`" in failed["hint"] and "logout" not in failed["hint"]


def test_on_linux_the_podman_hint_names_the_connection(monkeypatch) -> None:
    _engine(monkeypatch, fail={"info": _PODMAN_DOWN})
    _answers(monkeypatch, False)
    monkeypatch.setattr(searxng_local.sys, "platform", "linux")
    assert "podman system connection list" in searxng_local.up()["hint"]


def test_a_stopped_docker_daemon_says_so(monkeypatch) -> None:
    monkeypatch.setenv(searxng_local.ENGINE_ENV, "docker")
    down = (
        "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. "
        "Is the docker daemon running?"
    )
    _engine(monkeypatch, fail={"info": down})
    _answers(monkeypatch, False)
    failed = searxng_local.up()
    assert failed["reason"] == "the Docker daemon is not running"
    assert "Docker Desktop" in failed["hint"]


def test_other_errors_keep_their_first_line_and_get_no_unrelated_hint(monkeypatch) -> None:
    _engine(monkeypatch, fail={"image inspect": "missing", "pull": "network unreachable\ndetail"})
    _answers(monkeypatch, False)
    failed = searxng_local.up()
    assert failed["reason"].endswith(": network unreachable")
    assert "hint" not in failed


def test_down_removes_the_container_and_forgets_the_url(monkeypatch) -> None:
    engine = _engine(monkeypatch)
    _answers(monkeypatch, True)
    searxng_local.up()
    assert searxng_local.down() == {"state": "stopped"}
    assert engine.calls[-1][1:] == ["rm", "-f", searxng_local.CONTAINER_NAME]
    assert searxng_local.status() == {"state": "absent"}


def test_status_reports_a_recorded_instance_that_stopped(monkeypatch) -> None:
    _engine(monkeypatch)
    _answers(monkeypatch, True)
    searxng_local.up()
    _answers(monkeypatch, False)
    assert searxng_local.status() == {"state": "down", "url": URL}


def test_web_search_uses_the_running_local_instance(monkeypatch) -> None:
    _engine(monkeypatch)
    _answers(monkeypatch, True)
    assert web_search.base_url() is None  # nothing recorded yet
    searxng_local.up()
    assert web_search.base_url() == URL
    monkeypatch.setenv(web_search.SEARXNG_URL_ENV, "https://searx.example.org")
    assert web_search.base_url() == "https://searx.example.org"
    monkeypatch.delenv(web_search.SEARXNG_URL_ENV)
    searxng_local._PROBE_CACHE.clear()
    _answers(monkeypatch, False)
    assert web_search.configured() is False


def _cli(action: str | None) -> int:
    from r4._cli.search_server import SearchServerCommand

    return SearchServerCommand().handle(Namespace(search_server_action=action, as_json=False))


def test_cli_exit_codes_follow_the_result(monkeypatch, capsys) -> None:
    monkeypatch.setattr(searxng_local, "up", lambda: {"state": "started", "url": URL})
    monkeypatch.setattr(searxng_local, "down", lambda: {"state": "failed", "reason": "boom"})
    monkeypatch.setattr(searxng_local, "status", lambda: {"state": "absent"})
    assert _cli("up") == 0
    assert "started at http://127.0.0.1:8888" in capsys.readouterr().out
    assert _cli("down") == 1
    assert "reason: boom" in capsys.readouterr().err
    assert _cli(None) == 0
    assert "absent" in capsys.readouterr().out


def test_cli_is_registered() -> None:
    from r4.cli import _DISPATCH_MODULES, _PRE_REGISTER_COMMANDS, _PRE_REGISTER_MODULES

    assert "search_server" in _DISPATCH_MODULES
    assert "search_server" in _PRE_REGISTER_MODULES
    assert "search-server" in _PRE_REGISTER_COMMANDS
