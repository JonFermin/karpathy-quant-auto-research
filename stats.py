"""
stats.py — shared statistical primitives used by the grader and the notebook.

Keeps the repo scipy-free: Acklam's rational approximation for the normal
inverse CDF is accurate to ~1e-9 in the tails we care about. The
deflated-Sharpe machinery (Bailey & Lopez de Prado 2014) lives here so
`log_result.py` and `analysis.ipynb` apply the exact same deflation.
"""

from __future__ import annotations

import math
from typing import Iterable

import numpy as np

EULER_MASCHERONI = 0.5772156649
T_OOS_DEFAULT = 252 * 5  # OOS window length in trading days (Jan 2020 – Dec 2024)


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_ppf(p: float) -> float:
    """Inverse standard-normal CDF (Acklam 2003, ~1e-9 absolute error).

    Inlined to keep the repo scipy-free; only used on tail quantiles in
    roughly [0.6, 0.9999], where the approximation is plenty.
    """
    if not (0.0 < p < 1.0):
        return float("nan")
    a = [-3.969683028665376e+01, 2.209460984245205e+02,
         -2.759285104469687e+02, 1.383577518672690e+02,
         -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02,
         -1.556989798598866e+02, 6.680131188771972e+01,
         -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01,
         -2.400758277161838e+00, -2.549732539343734e+00,
         4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01,
         2.445134137142996e+00, 3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
               ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1.0)
    if p > phigh:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        return -(((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
                ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1.0)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r + a[1])*r + a[2])*r + a[3])*r + a[4])*r + a[5]) * q / \
           (((((b[0]*r + b[1])*r + b[2])*r + b[3])*r + b[4])*r + 1.0)


def expected_max_sharpe_null(sigma_sharpe: float, n_trials: int) -> float:
    """Bailey & Lopez de Prado (2014) eq. 6: expected max Sharpe under the
    null of zero edge, given N independent trials and observed cross-trial
    Sharpe dispersion `sigma_sharpe`.

    With N=20 trials and sigma=0.2 this comes in around 0.33 — meaning a
    winner needs to clear baseline by *that much* before selection alone
    explains the result.
    """
    if n_trials < 2 or sigma_sharpe <= 0 or not math.isfinite(sigma_sharpe):
        return float("nan")
    z_n = norm_ppf(1.0 - 1.0 / n_trials)
    z_ne = norm_ppf(1.0 - 1.0 / (n_trials * math.e))
    return sigma_sharpe * ((1.0 - EULER_MASCHERONI) * z_n + EULER_MASCHERONI * z_ne)


def deflated_sharpe(
    sr: float,
    all_sharpes: Iterable[float],
    t_days: int = T_OOS_DEFAULT,
) -> float:
    """Probability that `sr` is the best of N trials is real under a Gaussian
    null (PSR evaluated at the expected max under no-edge).

    `all_sharpes` — the full vector of observed Sharpes across trials (used
    to estimate cross-trial dispersion). `t_days` — OOS observation count.
    """
    arr = np.asarray([s for s in all_sharpes if np.isfinite(s)], dtype=float)
    n = int(arr.size)
    if n < 2:
        return float("nan")
    sigma_sr = float(np.std(arr, ddof=1))
    sr0 = expected_max_sharpe_null(sigma_sr, n)
    if not math.isfinite(sr0):
        return float("nan")
    numer = (sr - sr0) * math.sqrt(max(t_days - 1, 1))
    denom = math.sqrt(1.0 + 0.5 * sr * sr)
    return float(norm_cdf(numer / denom))
