# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Autonomous quant strategy research loop (daily US equities). An agent repeatedly edits one file, backtests, and commits/discards. The canonical agent instructions live in `program.md` — **read it before starting any experimental loop**. `README.md` covers the design rationale.

`CLAUDE.md`, `results.tsv`, and `run.log` are all gitignored — `CLAUDE.md` is per-session scratch, `results.tsv` is the agent's experiment log (kept out of git by design), `run.log` is transient stdout from each backtest.

## The one rule that matters

- **`strategy.py` is the ONLY file the agent edits.** Everything inside `generate_weights()` is fair game.
- **`prepare.py` is READ-ONLY.** It contains the fixed constants, data loader, cost model, hard constraints, and — critically — the `weights.shift(1)` T+1 execution shift inside `run_backtest`. Do NOT pre-shift weights in `strategy.py`; double-shifting silently cripples the signal.
- Do not add dependencies beyond `pyproject.toml` (numpy, pandas, pyarrow, yfinance, matplotlib, jupyter).
- Do not inspect OOS metrics and tune toward them. Form hypotheses on IS; OOS is a verdict, not a gradient.

## Commands

```bash
uv sync                               # install deps
uv run prepare.py                     # one-time price download → ~/.cache/karpathy-quant-auto-research/prices_<tag>.parquet
uv run prepare.py --refresh           # force re-download
uv run strategy.py > run.log 2>&1     # run one backtest (ALWAYS redirect; do not tee/flood context)
grep "^oos_sharpe:\|^max_drawdown:\|^turnover_annual:\|^num_trades:" run.log
tail -n 50 run.log                    # inspect stack trace if grep is empty (crash)
```

Prices cache lives at `~/.cache/karpathy-quant-auto-research/prices_<UNIVERSE_TAG>.parquet` (outside the repo). Each universe gets its own cache file — set `UNIVERSE_TAG=sp500_2024` (or similar) to switch.

## Experiment loop (from program.md)

1. Each run gets its own branch: `quant-research/<tag>` (e.g. `quant-research/mar5`).
2. Edit `strategy.py` → `git commit` → `uv run strategy.py > run.log 2>&1` → grep metrics.
3. **Keep rule** (all required): `oos_sharpe > running_best` AND `max_drawdown ≤ 0.35` AND `turnover_annual ≤ 50.0` AND `num_trades ≥ 50`.
4. `keep` → advance branch. `discard`/`crash` → `git reset --hard HEAD~1`.
5. Append a row to `results.tsv` (tab-separated, 6 cols: `commit oos_sharpe max_dd turnover status description`). **Do not `git add results.tsv`** — it stays untracked.
6. **NEVER STOP** once the loop has begun — no "should I keep going?" prompts. Run until manually interrupted.

## Backtest contract (what `run_backtest` enforces)

- Universe: `universe_sp100_2024.json` (frozen SP100 snapshot — survivorship-biased by design).
- IS: 2010-01-01 → 2019-12-31. OOS: 2020-01-01 → 2024-12-31.
- `weights` shape: `(date × ticker)`, row sums = gross leverage, negatives = shorts.
- Costs: 5bps per side on `|Δw|`, 200bps annual borrow on short exposure.
- Hard constraints → `force_discard`: `max_drawdown > 0.35` OR `turnover_annual > 50.0`. `num_trades < 50` → `crash`.
- `status_hint` in output is informational; the real keep/discard rule is in program.md step 7 above.
- 5-minute wall-clock cap (`TIME_BUDGET_S = 300`) inside `run_backtest`.

## Output format (what `print_summary` emits)

```
---
oos_sharpe:       1.234567
is_sharpe:        1.456789
max_drawdown:     0.1823
annual_return:    0.1245
annual_vol:       0.0934
turnover_annual:  5.23
calmar:           0.6830
num_trades:       1247
backtest_seconds: 12.4
status_hint:      keep_eligible | force_discard | crash
```

Empty grep output ⇒ the run crashed before reaching `print_summary`.
