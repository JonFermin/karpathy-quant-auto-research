"""
strategy.py — the one file the agent edits.

Define `generate_weights(prices)` that returns a (date × ticker) weight panel.
The __main__ block calls `run_backtest` (which enforces the T+1 shift) and
prints the fixed output block.

Baseline: 12-1 month momentum, monthly rebalance, long top decile equal-weight.
"""

from __future__ import annotations

import pandas as pd

from prepare import (
    TimeBudget,
    load_prices,
    print_summary,
    run_backtest,
)


def generate_weights(prices: pd.DataFrame) -> pd.DataFrame:
    """
    Build a (date × ticker) target-weight panel.

    Contract:
      - Use data up to and including day t to decide target weights for day t.
      - Do NOT apply any shift here. `run_backtest` shifts by one bar to enforce
        T+1 execution — pre-shifting would double-delay your signal.
      - Row sums represent gross leverage; keep it ≤ 1 unless you know what you're doing.
    """
    # 3-1 momentum (short-medium horizon). Thesis: 3-month momentum captures
    # fresher information than 6/12-month — particularly relevant in mega-caps
    # where institutional accumulation creates persistent monthly flow
    # patterns. Distinct horizon from prior 5d/21d/63d/126d/252d trials.
    ret_3_1 = prices.pct_change(63).shift(21)
    ranks = ret_3_1.rank(axis=1, pct=True)
    mask = (ranks >= 0.9).astype(float)

    row_sum = mask.sum(axis=1).replace(0, 1)
    w = mask.div(row_sum, axis=0)
    w = w.resample("ME").last().reindex(prices.index, method="ffill").fillna(0.0)
    return w


if __name__ == "__main__":
    prices = load_prices()
    with TimeBudget() as tb:
        weights = generate_weights(prices)
        results = run_backtest(weights, prices)
    print_summary(results)
