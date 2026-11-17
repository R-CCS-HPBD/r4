"""Deployment wiring of the per-user disk budget (ADR-051 D-3).

umrs ``disk_ledger.disk_bytes_max`` reads ``UMRS_USER_DISK_BYTES_MAX`` (code
default 100 GiB). The converge writes it into the umrs-front ``.env`` from
``umrs_front_user_disk_bytes_max``, and every umrs server loads that file
through ``env_file`` without overriding the key. On 2026-11-13 the maintainer
raised it to 400 GiB so the bdrt Nextcloud staging dataset fits.
"""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE = REPO_ROOT / "umrs/deploy/umrs-front/docker-compose.yaml"
TASKS = REPO_ROOT / "deploy/ansible/roles/front_end/tasks/umrs-front.yaml"
GROUP_VARS = REPO_ROOT / "deploy/ansible/inventory/umrs-front/group_vars/umrs_front.yaml"
FRONTS = ("rccs", "ai4s", "bdrt", "octopus")
KEY = "UMRS_USER_DISK_BYTES_MAX"


def _env_items() -> list[dict]:
    tasks = yaml.safe_load(TASKS.read_text())
    items: list[dict] = []
    for task in tasks:
        lineinfile = task.get("ansible.builtin.lineinfile") if isinstance(task, dict) else None
        if isinstance(lineinfile, dict) and lineinfile.get("path") == "{{ umrs_front_env_file }}":
            items.extend(item for item in (task.get("loop") or []) if isinstance(item, dict))
    return items


def test_the_converge_writes_the_disk_budget_from_the_inventory_variable():
    lines = [item["line"] for item in _env_items() if item.get("regexp") == f"^{KEY}="]
    assert lines == [
        f"{KEY}={{{{ umrs_front_user_disk_bytes_max | default(107374182400) | int }}}}"
    ]


def test_the_inventory_raises_the_budget_to_400_gib():
    assert yaml.safe_load(GROUP_VARS.read_text())["umrs_front_user_disk_bytes_max"] == 400 * (
        1 << 30
    )


def test_every_front_server_loads_the_env_file_without_overriding_the_budget():
    services = yaml.safe_load(COMPOSE.read_text())["services"]
    for front in FRONTS:
        service = services[f"umrs-server-{front}"]
        env_file = service.get("env_file")
        assert env_file == ".env" or ".env" in (env_file or []), front
        assert KEY not in (service.get("environment") or {}), front
