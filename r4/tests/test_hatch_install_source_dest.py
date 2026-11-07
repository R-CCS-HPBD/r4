"""``r4 mcp hatch install --from`` must not collide with the global ``--source``.

Regression: ``--from`` used ``dest="source"``, the same attribute as the
top-level ``--source NAME`` option that selects an r4 endpoint source. The
subcommand default ("registry") overwrote it, so every ``hatch install`` tried
to activate an r4 source named "registry" and failed with
"Source 'registry' not found" — on every deployment, not only stand-alone.
"""

from __future__ import annotations

from argparse import Namespace

from r4._cli.mcp import _hatch_install_source
from r4.cli import _build_parser


def test_hatch_install_leaves_global_source_unset() -> None:
    args = _build_parser().parse_args(["mcp", "hatch", "install", "platform-tools"])
    assert args.source is None
    assert args.hatch_from == "registry"


def test_global_source_survives_hatch_install() -> None:
    args = _build_parser().parse_args(
        ["--source", "standalone", "mcp", "hatch", "install", "pkg", "--from", "local"]
    )
    assert args.source == "standalone"
    assert args.hatch_from == "local"


def test_install_source_reads_hatch_from() -> None:
    args = Namespace(package="/tmp/pkgs/my-pkg", hatch_from="local", url=None, registry=None)
    name, source, _url = _hatch_install_source(args)
    assert name == "my-pkg"
    assert source.value == "local"
