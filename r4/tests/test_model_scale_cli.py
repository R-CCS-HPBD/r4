"""ADR-093 D-14 (C22): ``r4 model scale`` — detect, pin, unpin and list.

The CLI runs against an isolated ``XDG_CONFIG_HOME`` and a mocked network;
it never touches the user's ``~/.config/r4``.
"""

from __future__ import annotations

import json

import httpx
import pytest

from r4 import config as r4_config
from r4 import model_scale
from r4._cli import _helpers
from r4._cli import model as model_cli
from r4.cli import _build_parser
from r4.model_scale import load_overrides, overrides_path
from r4.source import Source, get_source_store

SECRET = "sk-test-SECRET-4242"
OLLAMA = Source(name="ollama", kind="public", url="http://ollama.test:11434", provider="ollama")
RIKYU = Source(name="rikyu", kind="public", url="https://rikyu.test/v1", api_key=SECRET)
SHOW = {
    "model_info": {
        "general.architecture": "qwen3moe",
        "general.parameter_count": 30_532_122_624,
        "general.size_label": "30B-A3B",
        "qwen3moe.context_length": 40_960,
    }
}
KIMI = {"data": [{"id": "kimi-k3", "root": "moonshotai/Kimi-K3", "max_model_len": 1_048_576}]}


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for name in ("R4_MODEL_SCALE", "R4_RAG_PROFILE", "R4_SOURCE", "R4_GATEWAY_URL", "R4_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(r4_config, "_config", None)
    model_scale.clear_scale_memo()
    yield tmp_path / "r4"
    model_scale.clear_scale_memo()


@pytest.fixture(autouse=True)
def net(monkeypatch):
    routes = {
        ("POST", "http://ollama.test:11434/api/show"): SHOW,
        ("GET", "https://rikyu.test/v1/models"): KIMI,
    }
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = routes.get((request.method, str(request.url)))
        return httpx.Response(404 if body is None else 200, json=body or {})

    monkeypatch.setattr(model_scale, "_TRANSPORT", httpx.MockTransport(handler))
    return seen


def _run(capsys, *argv):
    args = _build_parser().parse_args(["model", "scale", *argv])
    code = model_cli._cmd_model_scale(args, None)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_the_parser_accepts_every_form():
    parse = _build_parser().parse_args
    show = parse(["model", "scale"])
    pin = parse(["model", "scale", "set", "kimi-k3", "large", "-S", "rikyu", "--params", "1000"])
    assert (show.model_command, show.scale_args, show.scale_source) == ("scale", [], None)
    assert (pin.scale_args, pin.scale_source, pin.scale_params) == (
        ["set", "kimi-k3", "large"],
        "rikyu",
        1000.0,
    )
    flags = parse(["model", "scale", "llama3.2:3b", "--refresh", "--json"])
    assert (flags.scale_args, flags.refresh, flags.as_json) == (["llama3.2:3b"], True, True)


def test_set_writes_the_override_and_show_reads_it(capsys):
    argv = ("set", "kimi-k3", "LARGE", "--source", "rikyu", "--params", "1000", "--active", "32")
    code, out, _ = _run(capsys, *argv, "--note", "rikyu frontier MoE")
    assert (code, out.strip()) == (0, "Pinned rikyu/kimi-k3 to large.")
    assert load_overrides() == {
        "rikyu/kimi-k3": {
            "size_class": "large",
            "params_b": 1000.0,
            "active_params_b": 32.0,
            "note": "rikyu frontier MoE",
        }
    }
    code, out, _ = _run(capsys, "kimi-k3", "--source", "rikyu", "--json")
    shown = json.loads(out)
    assert (code, shown["basis"], shown["profile"], shown["source"]) == (
        0,
        "override",
        "large",
        "rikyu",
    )


@pytest.mark.parametrize(
    "argv",
    [("set", "kimi-k3"), ("set", "kimi-k3", "unknown"), ("unset",), ("list", "x"), ("a", "b")],
)
def test_malformed_forms_print_usage(capsys, argv):
    code, out, err = _run(capsys, *argv)
    assert (code, out) == (2, "")
    assert "usage: r4 model scale" in err


def test_unset_removes_an_override(capsys):
    _run(capsys, "set", "house", "medium")
    code, out, _ = _run(capsys, "unset", "house")
    assert (code, out.strip(), load_overrides()) == (0, "Removed the override for */house.", {})
    code, _, err = _run(capsys, "unset", "house")
    assert (code, err.strip()) == (1, "No override for */house.")


def test_show_prints_class_params_context_basis_and_detail(capsys):
    get_source_store().add(OLLAMA)
    code, out, err = _run(capsys, "qwen3:30b-a3b", "--source", "ollama")
    assert (code, err) == (0, "")
    assert out.splitlines() == [
        "qwen3:30b-a3b @ ollama",
        "  class:   medium (retrieval profile medium)",
        "  params:  30.5B total, 3B active, 9.57B effective",
        "  context: 40k",
        "  basis:   ollama",
        "  detail:  ollama /api/show: general.parameter_count 30.5321B",
    ]


def test_an_unknown_scale_prints_the_notice(capsys):
    code, out, err = _run(capsys, "house-model", "--source", "nowhere")
    assert (code, "class:   unknown (retrieval profile medium)" in out) == (0, True)
    assert "r4 model scale set house-model small|medium|large" in err


def test_the_default_is_the_active_model_on_the_active_source(capsys, monkeypatch, net):
    store = get_source_store()
    store.add(OLLAMA)
    store.set_active("ollama")
    monkeypatch.setenv("R4_MODEL", "qwen3:30b-a3b")

    def no_preflight(**_kwargs):
        raise AssertionError("scale must not need the gateway")

    monkeypatch.setattr(_helpers, "_preflight_check", no_preflight)
    args = _build_parser().parse_args(["model", "scale", "--json"])
    code = model_cli.ModelCommand().handle(args)
    shown = json.loads(capsys.readouterr().out)
    assert (code, shown["model"], shown["source"], shown["basis"]) == (
        0,
        "qwen3:30b-a3b",
        "ollama",
        "ollama",
    )
    assert [request.url.path for request in net] == ["/api/show"]


def test_list_shows_overrides_and_detections(capsys):
    get_source_store().add(OLLAMA)
    _run(capsys, "set", "kimi-k3", "large", "--note", "pinned")
    _run(capsys, "qwen3:30b-a3b", "--source", "ollama")
    code, out, _ = _run(capsys, "list")
    lines = [line.split() for line in out.splitlines()]
    assert (code, lines[0], lines[1][:3]) == (
        0,
        ["Overrides", f"({overrides_path()}):"],
        ["*/kimi-k3", "large", "pinned"],
    )
    assert lines[3][:2] == ["ollama/qwen3:30b-a3b", "medium"]
    code, out, _ = _run(capsys, "list", "--json")
    listed = json.loads(out)
    assert (sorted(listed["overrides"]), sorted(listed["detections"])) == (
        ["*/kimi-k3"],
        ["ollama/qwen3:30b-a3b"],
    )


def test_the_api_key_never_reaches_cli_output(capsys, net):
    get_source_store().add(RIKYU)
    code, out, err = _run(capsys, "kimi-k3", "--source", "rikyu", "--json")
    _, listed, _ = _run(capsys, "list", "--json")
    assert (code, json.loads(out)["size_class"]) == (0, "large")
    assert net[-1].headers["authorization"] == f"Bearer {SECRET}"
    assert [SECRET in text for text in (out, err, listed)] == [False, False, False]


# ------------------------------------------------ review fixes (2026-10-10) ---


def test_list_survives_a_malformed_cache_row(capsys):
    """r4-scale-router-1: one non-dict row crashed ``r4 model scale list``."""
    from r4.model_scale import cache_path

    good = {"model": "kimi-k3", "source": "rikyu", "size_class": "large", "basis": "family"}
    rows = {"other/x": "garbage", "rikyu/kimi-k3": {**good, "detected_at": 1.0}}
    cache_path().parent.mkdir(parents=True, exist_ok=True)
    cache_path().write_text(json.dumps(rows))
    code, out, _ = _run(capsys, "list")
    lines = [line.split() for line in out.splitlines()]
    assert (code, lines[-1][:2]) == (0, ["rikyu/kimi-k3", "large"])


def test_set_and_unset_read_the_model_at_source_form(capsys):
    """r4-scale-router-8: ``set house-model@lab large`` wrote
    ``*/house-model@lab``, a key detection never reads."""
    from r4.model_scale import detect_model_scale

    get_source_store().add(Source(name="lab", kind="public", url="http://lab.test:11434"))
    code, out, _ = _run(capsys, "set", "house-model@lab", "large")
    assert (code, out.strip()) == (0, "Pinned lab/house-model to large.")
    scale = detect_model_scale("house-model@lab")
    assert (scale.size_class, scale.basis) == ("large", "override")
    code, out, _ = _run(capsys, "unset", "house-model@lab")
    assert (code, out.strip(), load_overrides()) == (
        0,
        "Removed the override for lab/house-model.",
        {},
    )


def test_a_model_at_source_that_contradicts_the_source_flag_is_refused(capsys):
    code, out, err = _run(capsys, "set", "house-model@lab", "large", "--source", "rikyu")
    assert (code, out, load_overrides()) == (2, "", {})
    assert "MODEL@SOURCE and --source name different sources" in err


def test_show_prints_no_terminal_escape_from_the_endpoint_or_an_old_cache(capsys):
    """r4-scale-router-12: endpoint-supplied strings (owned_by, root, the
    registry's names) reached the terminal raw."""
    from r4.model_scale import cache_path

    hostile = "\x1b]52;c;cm0gLXJmIH4K\x07evil\x1b[2K"
    old = {"model": "m", "source": "rikyu", "size_class": "unknown", "detail": hostile}
    cache_path().parent.mkdir(parents=True, exist_ok=True)
    cache_path().write_text(json.dumps({"rikyu/m": {**old, "detected_at": 4e9}}))  # fresh
    code, out, err = _run(capsys, "m", "--source", "rikyu")
    _, listed, _ = _run(capsys, "list")
    assert code == 0 and "evil" in out
    assert ["\x1b" in text or "\x07" in text for text in (out, err, listed)] == [False] * 3
