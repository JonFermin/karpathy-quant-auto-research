"""
stats.py — shared statistical primitives used by the grader and the notebook.

Keeps the repo scipy-free: Acklam's rational approximation for the normal
inverse CDF is accurate to ~1e-9 in the tails we care about. The
deflated-Sharpe machinery (Bailey & Lopez de Prado 2014) lives here so
`log_result.py` and `analysis.ipynb` apply the exact same deflation.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence

import numpy as np
import pandas as pd

EULER_MASCHERONI = 0.5772156649
T_OOS_DEFAULT = 252 * 5  # OOS window length in trading days (Jan 2020 – Dec 2024)
TRADING_DAYS_PER_YEAR = 252


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


def jobson_korkie_memmel(
    returns_a: pd.Series,
    returns_b: pd.Series,
) -> tuple[float, float]:
    """Memmel-corrected Jobson-Korkie paired Sharpe-difference test.

    H0: SR_a = SR_b. One-sided alternative: SR_a > SR_b. Returns (z, p).

    Operates on raw (non-annualized) daily return series; the annualization
    constant cancels in the ratio so the test statistic is scale-free.
    Series are aligned on the intersection of their indices; NaNs are dropped
    jointly. Returns (nan, nan) when the aligned sample is too short or
    either series has zero variance.

    Reference: Memmel (2003), "Performance hypothesis testing with the
    Sharpe ratio" — fixes the asymptotic-variance formula in Jobson &
    Korkie (1981).
    """
    df = pd.concat([returns_a, returns_b], axis=1, join="inner").dropna()
    if len(df) < 30:
        return (float("nan"), float("nan"))
    ra = df.iloc[:, 0].to_numpy(dtype=float)
    rb = df.iloc[:, 1].to_numpy(dtype=float)
    t = len(ra)
    mu_a = float(ra.mean())
    mu_b = float(rb.mean())
    sd_a = float(ra.std(ddof=1))
    sd_b = float(rb.std(ddof=1))
    if sd_a <= 0 or sd_b <= 0 or not (math.isfinite(sd_a) and math.isfinite(sd_b)):
        return (float("nan"), float("nan"))
    sr_a = mu_a / sd_a
    sr_b = mu_b / sd_b
    # Sample correlation between the two return series.
    if t < 2:
        return (float("nan"), float("nan"))
    denom = sd_a * sd_b
    if denom <= 0:
        return (float("nan"), float("nan"))
    cov_ab = float(((ra - mu_a) * (rb - mu_b)).sum()) / (t - 1)
    rho = cov_ab / denom
    rho = max(-1.0, min(1.0, rho))
    # Memmel (2003) asymptotic variance of the Sharpe-ratio difference:
    # V = 2(1-ρ) + 0.5(SR_a² + SR_b²) - 0.5·SR_a·SR_b·(1 + ρ²).
    # Degenerates to 0 when the two return series are identical (ρ=1,
    # SR_a=SR_b), which is handled below by returning (nan, nan).
    v = (
        2.0 * (1.0 - rho)
        + 0.5 * (sr_a * sr_a + sr_b * sr_b)
        - 0.5 * sr_a * sr_b * (1.0 + rho * rho)
    )
    if v <= 0 or not math.isfinite(v):
        return (float("nan"), float("nan"))
    z = (sr_a - sr_b) / math.sqrt(v / t)
    if not math.isfinite(z):
        return (float("nan"), float("nan"))
    p = 1.0 - norm_cdf(z)
    return (float(z), float(p))


def effective_n_corr(
    returns_matrix: pd.DataFrame | np.ndarray,
    min_overlap: int = 60,
) -> float:
    """Correlation-adjusted effective number of independent trials.

    `returns_matrix` is (T × N) — columns are per-trial strategy return
    series. Computes the mean off-diagonal correlation ρ̄ and returns
    N_eff = N / (1 + (N - 1) · max(ρ̄, 0)), clipped to [1, N].

    The canonical iid-trial selection-bias formulas (Bailey-Lopez de Prado,
    Bonferroni) assume independent trials. When hypotheses are correlated —
    momentum variants, reversal variants, etc. — the effective N is smaller
    than the raw count and the deflation hurdle should shrink accordingly.
    Pairs with fewer than `min_overlap` common observations are dropped so
    short-lived trials do not dominate the average correlation.
    """
    if isinstance(returns_matrix, pd.DataFrame):
        arr = returns_matrix.to_numpy(dtype=float)
    else:
        arr = np.asarray(returns_matrix, dtype=float)
    if arr.ndim != 2 or arr.shape[1] < 2:
        return float(arr.shape[1]) if arr.ndim == 2 else float("nan")
    n = arr.shape[1]
    corrs: list[float] = []
    for i in range(n):
        for j in range(i + 1, n):
            a = arr[:, i]
            b = arr[:, j]
            mask = np.isfinite(a) & np.isfinite(b)
            if mask.sum() < min_overlap:
                continue
            ai = a[mask]
            bj = b[mask]
            sa = float(ai.std(ddof=1))
            sb = float(bj.std(ddof=1))
            if sa <= 0 or sb <= 0:
                continue
            rho = float(np.corrcoef(ai, bj)[0, 1])
            if math.isfinite(rho):
                corrs.append(rho)
    if not corrs:
        return float(n)
    rho_bar = float(np.mean(corrs))
    rho_bar = max(0.0, rho_bar)  # negative average ρ̄ cannot increase N_eff
    n_eff = n / (1.0 + (n - 1) * rho_bar)
    return max(1.0, min(float(n), n_eff))


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
