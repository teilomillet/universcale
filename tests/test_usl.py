"""USL fit: coefficient recovery, retrograde detection, overfit guard."""

from __future__ import annotations

import math

from universcale import fit_usl, usl_throughput


def _synth(gamma: float, sigma: float, kappa: float, levels):
    return [(n, usl_throughput(n, gamma=gamma, sigma=sigma, kappa=kappa)) for n in levels]


def test_recovers_known_coefficients():
    # Generate a perfect USL curve, then recover its coefficients.
    obs = _synth(1.0, 0.1, 0.01, [1, 2, 4, 8, 16, 32])
    fit = fit_usl(obs)
    assert fit is not None
    assert math.isclose(fit.gamma, 1.0, rel_tol=1e-6)
    assert math.isclose(fit.sigma, 0.1, rel_tol=1e-6)
    assert math.isclose(fit.kappa, 0.01, rel_tol=1e-6)
    assert fit.r2 > 0.999


def test_retrograde_peak():
    # kappa > 0 => a real throughput peak at N_max = sqrt((1-sigma)/kappa).
    sigma, kappa = 0.05, 0.01
    fit = fit_usl(_synth(1.0, sigma, kappa, [1, 2, 4, 8, 16, 32, 64]))
    assert fit.shape == "retrograde"
    assert math.isclose(fit.n_max, math.sqrt((1 - sigma) / kappa), rel_tol=1e-4)
    # Throughput at the peak is the max anywhere.
    assert fit.x_max >= fit.throughput(fit.n_max * 0.5)
    assert fit.x_max >= fit.throughput(fit.n_max * 2.0)


def test_no_coherency_means_no_peak():
    fit = fit_usl(_synth(1.0, 0.3, 0.0, [1, 2, 4, 8, 16]))
    assert fit.kappa <= 1e-9
    assert fit.n_max == math.inf


def test_overfit_guard():
    # Exactly 3 points = 3 params => exact fit, must be flagged unreliable.
    fit = fit_usl(_synth(1.0, 0.2, 0.005, [1, 2, 4]))
    assert fit.dof == 0
    assert fit.exact_fit is True


def test_too_few_points_returns_none():
    assert fit_usl([(1, 0.7), (2, 0.8)]) is None


def test_anef_like_plateau_curve():
    # Shape of the real ANEF H100 passport sweep: rises then flat (mild retrograde).
    obs = [(1, 0.720), (2, 0.797), (4, 0.781), (8, 0.804), (16, 0.797)]
    fit = fit_usl(obs)
    assert fit is not None
    assert 0.5 < fit.sigma < 1.2          # contention-dominated
    assert fit.r2 > 0.5
    # A plateau/retrograde curve tops out at a modest concurrency, not thousands.
    assert fit.n_max < 50
