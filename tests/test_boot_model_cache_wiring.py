"""umrs-front boot model cache and health window (issue #635, 2026-10-30).

A cold Hugging Face cache plus a stalled transfer held umrs-server-rccs's boot
provisioning for 9 minutes; the compose healthcheck (start_period 30 s) gave up
first and ``start.sh --server`` failed. The fix has three parts, and these
pins keep the compose half agreeing with the code half:

* every umrs-server keeps its HF cache (``HF_HOME``) on one named volume, so a
  model downloaded once survives container recreates;
* the model load is bounded (``umrs.shared.bounded_load``), and a stalled
  transfer raises inside it: plain HTTP with a read timeout
  (``HF_HUB_DISABLE_XET``, ``HF_HUB_DOWNLOAD_TIMEOUT`` below one attempt's
  deadline), so the wedged load frees its blob lock;
* umrs-server-rccs's healthcheck ``start_period`` covers that bound for two
  cold models (the siblings wait on its health).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from umrs.shared.bounded_load import LoadPolicy

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE = REPO_ROOT / "umrs/deploy/umrs-front/docker-compose.yaml"
CLUSTERS = ("rccs", "ai4s", "bdrt", "octopus")
CACHE_VOLUME = "umrs_hf_cache"


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text())


def _seconds(value: str) -> float:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(s|m)", str(value))
    assert match, value
    return float(match[1]) * (60 if match[2] == "m" else 1)


def test_every_server_keeps_its_hf_cache_on_the_named_volume() -> None:
    compose = _compose()
    assert CACHE_VOLUME in compose["volumes"]
    assert not (compose["volumes"][CACHE_VOLUME] or {}).get("external")
    for cluster in CLUSTERS:
        service = compose["services"][f"umrs-server-{cluster}"]
        hf_home = service["environment"]["HF_HOME"]
        assert f"{CACHE_VOLUME}:{hf_home}" in service["volumes"], cluster


def test_rccs_health_window_covers_two_bounded_cold_model_loads() -> None:
    health = _compose()["services"]["umrs-server-rccs"]["healthcheck"]
    worst = LoadPolicy().worst_case_s()
    assert _seconds(health["start_period"]) >= 2 * worst
    # The 2026-10-27 stall (9 min) would also have fit the new window.
    window = _seconds(health["start_period"]) + health["retries"] * _seconds(health["interval"])
    assert window >= 9 * 60


def test_every_server_lets_a_stalled_hf_transfer_raise_within_one_attempt() -> None:
    compose = _compose()
    for cluster in CLUSTERS:
        env = compose["services"][f"umrs-server-{cluster}"]["environment"]
        assert env.get("HF_HUB_DISABLE_XET") == "1", cluster
        assert 0 < float(env["HF_HUB_DOWNLOAD_TIMEOUT"]) < LoadPolicy().timeout_s, cluster
