"""
null_test.py — empirical p-value of the current strategy under an iid no-edge null.

Standalone audit tool. Loads real prices, computes the real OOS Sharpe of
`strategy.generate_weights`, then builds K synthetic price panels by
independently shuffling each ticker's daily pct_change series (preserving
each ticker's marginal return distribution while destroying autocorrelation
and cross-sectional correlation). On each synthetic panel, the same
`generate_weights` is evaluated and its OOS Sharpe recorded. The one-sided
empirical p-value is the fraction of null Sharpes >= the real Sharpe.

Usage:
    uv run null_test.py            # K=200 shuffles, seed=BOOTSTRAP_SEED
    uv run null_test.py --k 500    # more resamples (slower, tighter p)
    uv run null_test.py --seed 42  # override RNG seed

Writes:
    null_results_<short_commit>.json — all K null Sharpes + summary stats

No new dependencies (numpy, pandas only). Reads `strategy.generate_weights`
and the prepare.py APIs listed in the design note; does not import any
private symbols that aren't part of the stated stable surface.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from prepare import (
    BOOTSTRAP_SEED,
    _sharpe,
    load_prices,
    strat_returns,
    val_slice,
)
from strategy import generate_weights


REPO_ROOT = Path(__file__).resolve().parent


def _short_commit() -> str:
    """Short git commit of the currently-checked-out strategy.py. 'unknown' if unavailable."""
    try:
        out = subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short=7", "HEAD"],
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return out.decode().strip() or "unknown"
    except Exception:
        return "unknown"


def _real_oos_sharpe(prices: pd.DataFrame) -> float:
    """OOS Sharpe of the strategy on the real price panel."""
    weights = generate_weights(prices)
    ret = strat_returns(weights, prices)
    return _sharpe(val_slice(ret))


def _shuffle_prices(prices: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Build a synthetic price panel by iid-shuffling each ticker's daily
    pct_change independently, then compounding from the real initial price.

    This preserves each ticker's marginal return distribution (same mean,
    std, skew, kurtosis per name) while destroying:
      - temporal autocorrelation (no more momentum / mean-reversion structure)
      - cross-sectional correlation (no more common factors / regime coupling)

    Any strategy producing a positive OOS Sharpe under this null is picking
    up structure that shouldn't exist — hence a plausible overfit signal.

    Alignment: each ticker's NaN mask (leading non-tradable dates) is
    preserved, so `membership_mask` in `run_backtest` still zeroes out the
    correct rows. Numerical floor keeps compounded prices positive.
    """
    synth = pd.DataFrame(
        np.nan,
        index=prices.index,
        columns=prices.columns,
        dtype=float,
    )
    rets = prices.pct_change()
    for col in prices.columns:
        real_col = prices[col]
        first_idx = real_col.first_valid_index()
        if first_idx is None:
            continue
        # Returns observed from the second valid bar onward (first is NaN).
        col_rets = rets.loc[first_idx:, col].to_numpy()
        # First element is NaN (pct_change seed); drop and shuffle the rest.
        if len(col_rets) < 2:
            continue
        tail = col_rets[1:]
        tail = tail[np.isfinite(tail)]
        if len(tail) == 0:
            continue
        shuffled = rng.permutation(tail)
        # Compound from the real initial price.
        start_price = float(real_col.loc[first_idx])
        compounded = start_price * np.cumprod(1.0 + shuffled)
        # Guard against pathological negatives (shouldn't happen for equities
        # with daily returns > -1, but be safe).
        compounded = np.where(compounded > 0, compounded, np.nan)
        # Reconstruct the column: NaN pre-first_idx, start_price on first_idx,
        # compounded path on subsequent valid bars. Length after first_idx is
        # (len(col_rets) - 1) == len(tail) bars, which equals the count of
        # post-first_idx rows in the column's index.
        col_index = real_col.loc[first_idx:].index
        # Number of rows we actually have synthetic data for:
        n_path = min(len(col_index) - 1, len(compounded))
        # Assign first bar:
        synth.loc[first_idx, col] = start_price
        if n_path > 0:
            synth.loc[col_index[1 : 1 + n_path], col] = compounded[:n_path]
    return synth


def _null_oos_sharpe(
    prices: pd.DataFrame, rng: np.random.Generator
) -> float:
    """One draw from the null distribution: shuffle prices, run the strategy,
    return its OOS Sharpe. Any exception during weight generation or returns
    computation is caught and reported as NaN (and counted separately)."""
    synth = _shuffle_prices(prices, rng)
    try:
        weights = generate_weights(synth)
        ret = strat_returns(weights, synth)
        return _sharpe(val_slice(ret))
    except Exception:
        return float("nan")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Empirical p-value of current strategy vs iid no-edge null."
    )
    parser.add_argument(
        "--k",
        type=int,
        default=200,
        help="Number of null resamples (default: 200).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=BOOTSTRAP_SEED,
        help=f"RNG seed (default: BOOTSTRAP_SEED={BOOTSTRAP_SEED}).",
    )
    args = parser.parse_args()

    k = int(args.k)
    seed = int(args.seed)
    if k < 1:
        print("ERROR: --k must be >= 1", file=sys.stderr)
        return 2

    commit = _short_commit()
    print(f"commit:           {commit}")
    print(f"k:                {k}")
    print(f"seed:             {seed}")

    print("Loading real prices...")
    prices = load_prices()
    print(f"Prices: {prices.shape[0]} rows x {prices.shape[1]} tickers")

    print("Computing real OOS Sharpe...")
    sharpe_real = _real_oos_sharpe(prices)
    print(f"sharpe_real:      {sharpe_real:.6f}")

    if not np.isfinite(sharpe_real):
        print("ERROR: real OOS Sharpe is non-finite; cannot compute p-value.",
              file=sys.stderr)
        return 3

    rng = np.random.default_rng(seed)
    null_sharpes: list[float] = []
    n_nan = 0
    print(f"Running {k} null shuffles...")
    for i in range(k):
        s = _null_oos_sharpe(prices, rng)
        if not np.isfinite(s):
            n_nan += 1
        null_sharpes.append(float(s))
        # Lightweight progress every 10%:
        step = max(1, k // 10)
        if (i + 1) % step == 0 or (i + 1) == k:
            print(f"  [{i + 1:>4}/{k}]  latest={s:.4f}")

    arr = np.array(null_sharpes, dtype=float)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        print("ERROR: all null draws produced non-finite Sharpes.",
              file=sys.stderr)
        return 4

    # One-sided p-value: fraction of null draws >= real. Non-finite draws
    # are treated as missing (excluded from both numerator and denominator).
    n_ge = int((finite >= sharpe_real).sum())
    p_value = n_ge / finite.size

    mean = float(finite.mean())
    std = float(finite.std(ddof=1)) if finite.size > 1 else float("nan")
    p05 = float(np.quantile(finite, 0.05))
    p95 = float(np.quantile(finite, 0.95))

    print("---")
    print(f"sharpe_real:      {sharpe_real:.6f}")
    print(f"null_n:           {finite.size}")
    print(f"null_nan:         {n_nan}")
    print(f"null_mean:        {mean:.6f}")
    print(f"null_std:         {std:.6f}")
    print(f"null_p05:         {p05:.6f}")
    print(f"null_p95:         {p95:.6f}")
    print(f"p_value:          {p_value:.3f}")
    status = "REJECT_NULL" if p_value <= 0.05 else "FAIL_TO_REJECT"
    print(f"status:           {status}")

    out_path = REPO_ROOT / f"null_results_{commit}.json"
    record = {
        "commit": commit,
        "k": k,
        "seed": seed,
        "sharpe_real": float(sharpe_real),
        "null_n_total": k,
        "null_n_finite": int(finite.size),
        "null_n_nan": n_nan,
        "null_mean": mean,
        "null_std": std,
        "null_p05": p05,
        "null_p95": p95,
        "p_value": p_value,
        "status": status,
        "null_sharpes": null_sharpes,
    }
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2, allow_nan=True)
        print(f"Wrote: {out_path}")
    except OSError as e:
        print(f"WARNING: failed to write {out_path}: {e}", file=sys.stderr)

    # Print the two contract lines one more time at the very end so callers
    # can grep the last two lines of the log.
    print(f"p_value: {p_value:.3f}")
    print(f"status: {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
