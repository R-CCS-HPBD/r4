"""The reverse-tunnel unit runs only on the login node it was deployed to (2026-11-14).

``remote-tunnel.yaml`` writes ``umrs-reverse-tunnel-<c>.service`` into
``~/.config/systemd/user``. On bdrt that home is shared across the nodes, so
the unit started on spark-atom-0 as well; that copy held data0's 127.0.0.1:2202
and the login node's copy failed to bind it about 246,000 times over two
weeks. Gate calls through the ``nimbus-0`` alias then landed on spark-atom-0,
where ``sacctmgr`` cannot reach the login node's slurmdbd.

The unit now carries ``ConditionHost=`` with the deploying node's name, so
every other node sharing the home skips it.
"""

from __future__ import annotations

from pathlib import Path

import jinja2
import yaml

ROOT = Path(__file__).resolve().parent.parent
PLAYBOOK = ROOT / "deploy/ansible/playbooks/remote-tunnel.yaml"
TEMPLATE = ROOT / "deploy/ansible/playbooks/templates/reverse-tunnel/reverse-tunnel.service.j2"


def _render(**values: str) -> str:
    env = jinja2.Environment(undefined=jinja2.StrictUndefined, keep_trailing_newline=True)
    return env.from_string(TEMPLATE.read_text()).render(**values)


def test_the_unit_is_conditioned_on_the_login_host() -> None:
    unit = _render(
        _rt_cluster="bdrt",
        _rt_repo_path="/home/u/ai4s-platform",
        _rt_script_path="/home/u/bin/reverse_tunnel_bdrt.sh",
        _rt_login_host="nimbus-0.r-ccs10.riken.jp",
    )
    unit_section = unit.split("[Service]", 1)[0]
    assert "ConditionHost=nimbus-0.r-ccs10.riken.jp\n" in unit_section


def test_the_playbook_passes_the_deploying_nodes_name() -> None:
    plays = yaml.safe_load(PLAYBOOK.read_text())
    tasks = [task for play in plays for task in play.get("tasks", [])]
    (deploy,) = [t for t in tasks if t.get("name") == "Deploy the systemd --user unit"]
    assert deploy["vars"]["_rt_login_host"] == "{{ ansible_nodename }}"
