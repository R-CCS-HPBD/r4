"""Tests for the resource monitor."""

from __future__ import annotations

from r4.companion.resource_monitor import ResourceMonitor


def test_get_resources_returns_dict():
    rm = ResourceMonitor()
    result = rm.get_resources()
    assert isinstance(result, dict)


def test_get_resources_has_expected_keys():
    rm = ResourceMonitor()
    result = rm.get_resources()
    assert "gpu" in result
    assert "cpu" in result
    assert "memory" in result
    assert "slurm_jobs" in result
    assert "hostname" in result


def test_gpu_usage_returns_list():
    rm = ResourceMonitor()
    result = rm._get_gpu_usage()
    assert isinstance(result, list)


def test_cpu_usage_returns_dict_or_none():
    rm = ResourceMonitor()
    result = rm._get_cpu_usage()
    if result is not None:
        assert isinstance(result, dict)
        assert "percent" in result
        assert "count" in result


def test_memory_usage_returns_dict_or_none():
    rm = ResourceMonitor()
    result = rm._get_memory_usage()
    if result is not None:
        assert isinstance(result, dict)
        assert "total" in result
        assert "used" in result
        assert "percent" in result


def test_slurm_jobs_returns_list():
    rm = ResourceMonitor()
    result = rm._get_slurm_jobs()
    assert isinstance(result, list)


def test_get_resources_does_not_raise():
    rm = ResourceMonitor()
    rm.get_resources()
    rm.get_resources()
    rm.get_resources()
