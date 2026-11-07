"""Acceptance tests — every implementation must pass all of them.

pytest test_eikonal.py                                   # the slow reference
EIKONAL_IMPL=my_fast pytest test_eikonal.py              # your module my_fast.py
EIKONAL_IMPL=answer_key.eikonal_numpy_diag pytest test_eikonal.py
"""

from __future__ import annotations

import importlib
import os

import eikonal_slow
import models
import numpy as np
import pytest

IMPL = importlib.import_module(os.environ.get("EIKONAL_IMPL", "eikonal_slow"))
SHAPE = (21, 31, 31)
H = 0.5
SRC = (5, 15, 10)


def _relative_error(T, exact):
    far = models.distance_from(SHAPE, H, SRC) > 4 * H  # first-order scheme: skip the source cells
    return np.abs(T - exact)[far] / exact[far]


def test_source_is_zero_and_times_positive():
    T = IMPL.solve(models.constant(SHAPE), H, SRC)
    assert T[SRC] == 0.0
    assert np.all(np.isfinite(T)) and np.all(T >= 0.0)


def test_constant_velocity_matches_r_over_v():
    T = IMPL.solve(models.constant(SHAPE), H, SRC)
    err = _relative_error(T, models.exact_constant(SHAPE, H, SRC))
    assert err.mean() < 0.05 and err.max() < 0.10


def test_linear_gradient_matches_analytic_solution():
    T = IMPL.solve(models.gradient(SHAPE, H, g=0.5), H, SRC)
    err = _relative_error(T, models.exact_gradient(SHAPE, H, SRC, g=0.5))
    assert err.mean() < 0.05 and err.max() < 0.10


def test_symmetric_model_gives_symmetric_times():
    src = (5, 15, 15)
    T = IMPL.solve(models.gradient(SHAPE, H, g=0.5), H, src)
    np.testing.assert_allclose(T, T[:, :, ::-1], atol=1e-9)
    np.testing.assert_allclose(T, T[:, ::-1, :], atol=1e-9)


@pytest.mark.skipif(IMPL is eikonal_slow, reason="the reference agrees with itself")
def test_agrees_with_the_slow_reference_on_the_basin_model():
    v = models.basin(SHAPE, H)
    np.testing.assert_allclose(IMPL.solve(v, H, SRC), eikonal_slow.solve(v, H, SRC), atol=1e-6)
