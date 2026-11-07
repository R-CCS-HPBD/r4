"""``--gateway-url https://HOST/SITE`` is refused at every parser (2026-11-05).

umrs mint flows print a login hint whose gateway value is the EXAMPLE
placeholder ``https://HOST/SITE`` (umrs ``cli/admin/main.py``
``_GATEWAY_URL_EXAMPLE``), preceded by "replace ... with the site's public
gateway URL". Pasting the whole line anyway used to SAVE the placeholder
verbatim: the profile pointed at a host that resolves nowhere, and every
later failure (connection errors, ``r4 auth login`` talking to nothing)
surfaced far from the real mistake. The umrs-side parsers already refuse the
example (``_gateway_url_arg`` there); the four r4 parsers did not. Now they
share one guard, :func:`r4.config.gateway_url_arg`.
"""

from __future__ import annotations

import argparse

import pytest

EXAMPLE = "https://HOST/SITE"

# argv prefix that reaches a parser owning ``--gateway-url``: the command's
# registration name followed by the subcommand (where any).
SITES = [
    ("auth", ["auth", "login", "--api-key", "k"]),
    ("login", ["login", "--api-key", "k"]),
    ("config", ["config", "init"]),
    ("init", ["init"]),
]


def _command(name: str):
    from r4._cli.auth import AuthCommand
    from r4._cli.config import ConfigCommand
    from r4._cli.init import InitCommand
    from r4._cli.login import LoginCommand

    return {
        "auth": AuthCommand,
        "login": LoginCommand,
        "config": ConfigCommand,
        "init": InitCommand,
    }[name]()


def _parse(name: str, argv: list[str]):
    parser = argparse.ArgumentParser(prog="r4")
    _command(name).register(parser.add_subparsers(dest="command"))
    return parser.parse_args(argv)


@pytest.mark.parametrize("name,prefix", SITES, ids=[s[0] for s in SITES])
def test_the_umrs_example_is_refused(name: str, prefix: list[str], capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        _parse(name, [*prefix, "--gateway-url", EXAMPLE])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "example" in err
    assert "HOST/SITE" in err


@pytest.mark.parametrize("name,prefix", SITES, ids=[s[0] for s in SITES])
def test_the_example_with_a_trailing_slash_is_refused(name: str, prefix: list[str], capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        _parse(name, [*prefix, "--gateway-url", EXAMPLE + "/"])
    assert exc.value.code == 2
    assert "HOST/SITE" in capsys.readouterr().err


@pytest.mark.parametrize("name,prefix", SITES, ids=[s[0] for s in SITES])
def test_real_gateway_urls_pass_through_unchanged(name: str, prefix: list[str]) -> None:
    url = "https://gw.example/rccs"
    args = _parse(name, [*prefix, "--gateway-url", url])
    assert args.gateway_url == url


@pytest.mark.parametrize("name,prefix", SITES, ids=[s[0] for s in SITES])
def test_bare_host_urls_pass_through_unchanged(name: str, prefix: list[str]) -> None:
    """Bare hosts stay valid (``r4 login --gateway-url genai:8080``), the guard
    only kills the one string that is never a real gateway."""
    args = _parse(name, [*prefix, "--gateway-url", "genai:8080"])
    assert args.gateway_url == "genai:8080"


def test_the_guard_matches_the_umrs_contract() -> None:
    from r4.config import GATEWAY_URL_EXAMPLE, gateway_url_arg

    assert GATEWAY_URL_EXAMPLE == "https://HOST/SITE"
    with pytest.raises(argparse.ArgumentTypeError, match="example"):
        gateway_url_arg(GATEWAY_URL_EXAMPLE)
    assert gateway_url_arg("http://localhost:8080") == "http://localhost:8080"
