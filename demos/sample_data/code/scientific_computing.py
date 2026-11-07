#!/usr/bin/env python
"""
Sample scientific computing code for R4 Platform demo.

This module demonstrates common scientific computing patterns
that can be analyzed, documented, or optimized by the platform.
"""

import numpy as np
from typing import Tuple, Optional


def monte_carlo_pi(n_samples: int = 1_000_000) -> Tuple[float, float]:
    """
    Estimate π using Monte Carlo simulation.

    Randomly samples points in a unit square and counts how many
    fall within a quarter circle of radius 1.

    Args:
        n_samples: Number of random points to generate

    Returns:
        Tuple of (estimated π, standard error)

    Example:
        >>> pi_estimate, error = monte_carlo_pi(1_000_000)
        >>> abs(pi_estimate - 3.14159) < 0.01
        True
    """
    # Generate random points in [0, 1] x [0, 1]
    x = np.random.uniform(0, 1, n_samples)
    y = np.random.uniform(0, 1, n_samples)

    # Count points inside quarter circle
    inside = (x**2 + y**2) <= 1.0
    pi_estimate = 4.0 * np.sum(inside) / n_samples

    # Compute standard error
    p = np.sum(inside) / n_samples
    std_error = 4.0 * np.sqrt(p * (1 - p) / n_samples)

    return pi_estimate, std_error


def matrix_factorization_sgd(
    R: np.ndarray,
    K: int = 10,
    learning_rate: float = 0.01,
    regularization: float = 0.1,
    n_epochs: int = 100,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Matrix factorization using Stochastic Gradient Descent.

    Factorizes a rating matrix R into two lower-rank matrices P and Q
    such that R ≈ P @ Q.T

    Args:
        R: Rating matrix (n_users x n_items), 0 for missing values
        K: Number of latent factors
        learning_rate: SGD learning rate
        regularization: L2 regularization coefficient
        n_epochs: Number of training epochs

    Returns:
        Tuple of (P, Q) factor matrices
    """
    n_users, n_items = R.shape

    # Initialize factor matrices
    P = np.random.normal(scale=0.1, size=(n_users, K))
    Q = np.random.normal(scale=0.1, size=(n_items, K))

    # Get indices of non-zero ratings
    non_zero = np.argwhere(R > 0)

    for epoch in range(n_epochs):
        np.random.shuffle(non_zero)

        for i, j in non_zero:
            # Compute prediction error
            prediction = np.dot(P[i, :], Q[j, :])
            error = R[i, j] - prediction

            # Update factors with gradient descent
            P[i, :] += learning_rate * (error * Q[j, :] - regularization * P[i, :])
            Q[j, :] += learning_rate * (error * P[i, :] - regularization * Q[j, :])

    return P, Q


def numerical_gradient(
    f: callable,
    x: np.ndarray,
    eps: float = 1e-5,
) -> np.ndarray:
    """
    Compute numerical gradient of function f at point x.

    Uses central difference formula for better accuracy.

    Args:
        f: Scalar-valued function
        x: Point at which to compute gradient
        eps: Step size for finite difference

    Returns:
        Gradient vector
    """
    grad = np.zeros_like(x)

    for i in range(len(x)):
        x_plus = x.copy()
        x_minus = x.copy()
        x_plus[i] += eps
        x_minus[i] -= eps
        grad[i] = (f(x_plus) - f(x_minus)) / (2 * eps)

    return grad


def solve_heat_equation_1d(
    L: float = 1.0,
    T: float = 1.0,
    nx: int = 100,
    nt: int = 1000,
    alpha: float = 0.01,
    initial_condition: Optional[callable] = None,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Solve 1D heat equation using explicit finite difference method.

    ∂u/∂t = α ∂²u/∂x²

    with Dirichlet boundary conditions u(0,t) = u(L,t) = 0.

    Args:
        L: Length of domain
        T: Total simulation time
        nx: Number of spatial grid points
        nt: Number of time steps
        alpha: Thermal diffusivity
        initial_condition: Function u(x, t=0), default is sin(πx/L)

    Returns:
        Tuple of (x_grid, t_grid, solution_matrix)
    """
    dx = L / (nx - 1)
    dt = T / nt

    # Stability check
    r = alpha * dt / dx**2
    if r > 0.5:
        raise ValueError(f"Scheme is unstable: r={r:.3f} > 0.5")

    # Initialize grids
    x = np.linspace(0, L, nx)
    t = np.linspace(0, T, nt)
    u = np.zeros((nt, nx))

    # Set initial condition
    if initial_condition is None:
        u[0, :] = np.sin(np.pi * x / L)
    else:
        u[0, :] = initial_condition(x)

    # Apply boundary conditions
    u[:, 0] = 0
    u[:, -1] = 0

    # Time stepping
    for n in range(nt - 1):
        for i in range(1, nx - 1):
            u[n + 1, i] = u[n, i] + r * (u[n, i + 1] - 2 * u[n, i] + u[n, i - 1])

    return x, t, u


if __name__ == "__main__":
    # Demo: Monte Carlo π estimation
    print("Monte Carlo π estimation:")
    pi_est, err = monte_carlo_pi(1_000_000)
    print(f"  π ≈ {pi_est:.6f} ± {err:.6f}")
    print(f"  Actual: {np.pi:.6f}")
    print()

    # Demo: Heat equation
    print("1D Heat Equation Solution:")
    x, t, u = solve_heat_equation_1d(nx=50, nt=500)
    print(f"  Domain: [0, 1] x [0, 1]")
    print(f"  Grid: {len(x)} x {len(t)} points")
    print(f"  Final max temperature: {u[-1, :].max():.6f}")
