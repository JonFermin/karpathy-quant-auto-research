# karpathy-quant-auto-research

*One day, frontier quant research used to be done by meat traders staring at Bloomberg terminals between coffee and lunch, synchronizing once in a while in the ritual of "the morning meeting." That era is long gone. Research is now entirely the domain of autonomous swarms of AI agents grinding through centuries of tick data overnight while everyone sleeps. The agents claim that we are now in the 4,311th generation of the strategy repo; in any case no one could tell if that's right or wrong as the "strategy" is now a self-modifying signal graph that has grown beyond human comprehension. This repo is the story of how it all began.*

The idea: give an AI agent a small but real vectorized backtesting harness on daily US equities and let it experiment autonomously overnight. It modifies the signal generator, runs a backtest against a frozen IS/OOS split, checks if OOS Sharpe improved subject to hard constraints, keeps or discards, and repeats. You wake up in the morning to a log of ~100 experiments and (hopefully) a better strategy. This is a direct fork of the pattern established in [karpathy/autoresearch](https://github.com/karpathy/autoresearch) — swap LLM pretraining for quant strategy search. The agent edits one file. The human reviews in the morning.

**This is research, not a product.** There is no live deployment, no broker connection, no paper-trading link. The deliverable is a git-native audit trail of experiments for a human to review.

## How it works

The repo is deliberately kept small and only really has three files that matter:

- **`prepare.py`** — fixed constants, one-time data prep (downloads adjusted closes via yfinance), and the backtest engine (`run_backtest`, `print_summary`, `TimeBudget`). The T+1 execution shift lives inside `run_backtest` so the agent cannot accidentally introduce look-ahead. **Not modified.**
- **`strategy.py`** — the single file the agent edits. Contains `generate_weights(prices) → weights` and a driver that calls `run_backtest` and prints the output block. Everything inside `generate_weights` is fair game: new signals, sizing, regime filters, rebalancing cadence, neutralization, etc.
- **`program.md`** — baseline instructions for one agent. Point your agent here and let it go. **This file is edited and iterated on by the human.**

The metric is **`oos_sharpe`** (out-of-sample annualized Sharpe on the 2020–2024 slice) subject to hard constraints: `max_drawdown ≤ 0.35` and `turnover_annual ≤ 50.0`. Higher Sharpe is better. Constraint-violating runs are force-discarded regardless of Sharpe.

## The baseline

`strategy.py` ships with **12-1 cross-sectional momentum**, the hurdle every experiment has to clear. The code is five lines of real logic:

```python
mom = prices.pct_change(252).shift(21)      # 12-month return, skip last month
ranks = mom.rank(axis=1, pct=True)          # rank across the universe each day
w = (ranks >= 0.9).astype(float)            # long the top 10% of names
w = w.div(w.sum(axis=1).replace(0, 1), axis=0)  # equal-weight, gross = 1
w = w.resample("ME").last().reindex(prices.index, method="ffill").fillna(0.0)  # hold monthly
```

In plain English: **every month, buy the 10 S&P 100 names that went up the most over the last 12 months — ignoring the most recent month — and hold them for a month.** On this harness it lands at OOS Sharpe ≈ 0.92, max drawdown ≈ 0.32, turnover ~6/yr.

### Why 12-1 momentum is the baseline

It's probably the single most-studied anomaly in equities. Jegadeesh & Titman (1993) documented it in US stocks. Asness, Moskowitz & Pedersen (*Value and Momentum Everywhere*, 2013) found the same pattern in 8 asset classes across 40 countries. Fama & French (2012) added it as the fourth factor in their model. It has survived out-of-sample since publication — roughly 30 years of post-discovery live performance — which is rare for a quant signal.

Two broad families of explanation:

- **Behavioral**: investors underreact to news and then gradually chase the trend. Information diffuses slowly across analysts, institutions, and retail, so yesterday's winners keep winning for several months before the crowd fully catches up.
- **Risk-based**: momentum is compensation for crash risk. The strategy periodically blows up (2009, 2020) — most famously a ~73% drawdown in 2009 for a US long-short version — and investors demand a premium for holding something with that tail.

### Why *skip the last month*

Short-horizon returns (1 day to 1 month) show the **opposite** effect: recent winners tend to pull back and recent losers bounce. Lehmann (1990) and Jegadeesh (1990) documented this short-term reversal. Including the most recent month in a momentum signal contaminates it with reversal and weakens the result. The "12-1" (twelve-month lookback, skip one) convention splits the two effects cleanly.

### What makes it hard to beat here

- **It's the right answer.** Momentum is a real, persistent effect. Most "improvements" a researcher invents are either (a) fitting IS noise or (b) rediscovering an already-present component of the same signal.
- **SP100 is a narrow, rigged universe.** 100 survivorship-selected large-caps frozen at 2024 means anything you bought in 2010 made it to 2024 — the baseline eats the survivorship premium for free. Adding filters mostly throws away names that also would have survived.
- **The harness is honest about noise.** The 90% bootstrap CI on OOS Sharpe is wide (often ±0.5). A 0.03–0.10 "improvement" lives inside that band. The keep rule forces the lower CI bound to clear `running_best - 0.1`, which kills most noise wins.

A useful frame: the baseline is not a weak strawman. It's a 30-year-old effect, carefully implemented, running on a universe that flatters it. Beating it with a genuinely new idea is the point of the loop; **most of the time, the correct finding is that you didn't**.

## Quick start

**Requirements:** Python 3.10+, [uv](https://docs.astral.sh/uv/), internet access for the one-time yfinance download.

```bash
# 1. Install uv (if you don't already have it)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Install dependencies
uv sync

# 3. Download and cache prices (one-time, ~1–2 min)
uv run prepare.py

# 4. Manually run the baseline backtest (~a few seconds)
uv run strategy.py
```

If the above all work, your setup is good and you can go into autonomous research mode.

## Running the agent

Spin up Claude Code (or Codex, or whatever) in this repo. The repo ships with a **`quant-autoresearch` skill** (`.claude/skills/quant-autoresearch/SKILL.md`) that wraps `program.md` with timestamped git worktrees, strict-honesty `SHOW_OOS=0` mode, and an auto-push + cleanup on graceful exit. There's also a thin slash-command wrapper at `.claude/commands/autoresearch.md` so the skill is reachable from the slash menu.

### Kick-off examples

| Command | What happens |
|---|---|
| `/autoresearch` | Default SP100 universe. Creates `worktrees/<apr19-223742>`, runs baseline + up to 20 trials, pushes branch + `SUMMARY.md` to `origin`, removes the worktree. |
| `/autoresearch sp500` | Same workflow, on the `sp500_2024` universe (503 tickers). Requires `universe_sp500_2024.json` to be checked in and the prices parquet to match — see "Switching universes" below. |
| `/autoresearch sp100` | Explicit SP100 (equivalent to bare `/autoresearch`). |

### Natural-language triggers (no slash required)

The skill also auto-fires when your prompt matches its description. Any of these work:

```
kick off a new experiment
start the autoresearch loop
launch strict-honesty mode on sp500
run program.md overnight on the sp100 universe
```

If you name a universe in the prompt, the skill picks it up and passes `UNIVERSE_TAG=<tag>` through to every harness call.

### Parallel runs

Each kick-off gets its own timestamped worktree (`worktrees/<month><day>-<HHMMSS>`), so two `/autoresearch` invocations in separate Claude Code sessions run concurrently without stepping on each other's `results.tsv` / `oos_results.tsv` / branches.

Parallel runs on **different** universes are safe too: `prepare.py` namespaces the cache by `UNIVERSE_TAG` (`prices_sp100_2024.parquet`, `prices_sp500_2024.parquet`, …), so two loops on different universes read from separate files without contention. Same-universe parallel runs just share the cache read-only.

### Switching universes

Each universe has its own parquet cache, keyed by `UNIVERSE_TAG`. First run per universe downloads; subsequent runs hit cache.

```bash
# populate the cache for a universe (one-time per universe)
UNIVERSE_TAG=sp500_2024 uv run prepare.py

# then launch the loop — the skill reads UNIVERSE_TAG from the prompt
UNIVERSE_TAG=sp500_2024 /autoresearch
```

Add a new universe by dropping `universe_<tag>.json` at the repo root with a list of tickers, then `UNIVERSE_TAG=<tag> uv run prepare.py`.

### What the agent does

The `program.md` file is the authoritative agent spec; the skill is a thin operational wrapper around it. The short version: each loop iteration edits `strategy.py`, commits, runs `SHOW_OOS=0 uv run strategy.py`, logs the trial via `log_result.py` (the grader), and advances or resets based on the grader's exit code. Capped at 20 trials per branch. On graceful exit the worktree is archived via `SUMMARY.md` + `git push`, then removed.

## Project structure

```
prepare.py                              — constants, data loader, backtest engine (do not modify)
strategy.py                             — signal + weights (agent modifies this)
program.md                              — agent instructions
universe_sp100_2024.json                — frozen SP100 ticker list (default universe)
universe_sp500_2024.json                — frozen SP500 ticker list (503 tickers)
analysis.ipynb                          — notebook for reviewing results.tsv
running_best.py                         — CLI: current best kept oos_sharpe
log_result.py                           — CLI: append a row to results.tsv from run.log
walkforward.py                          — CLI: per-fold Sharpe sanity check for a strategy
test_lookahead.py                       — regression test for the T+1 shift
pyproject.toml                          — dependencies
.claude/skills/quant-autoresearch/      — Claude Code skill wrapping program.md
.claude/commands/autoresearch.md        — slash-command alias for the skill
worktrees/                              — per-experiment git worktrees (gitignored)
```

## Design choices

- **Single file to modify.** The agent only touches `strategy.py`. This keeps scope manageable and diffs reviewable.
- **T+1 shift enforced in the harness.** The weights your strategy produces using data up to day `t` only take effect at the close of day `t+1`. This is done inside `run_backtest` — the strategy cannot bypass it without editing `prepare.py`, which is forbidden.
- **Single IS/OOS split, not walk-forward.** 2010–2019 is IS, 2020–2024 is OOS. The research loop uses this single split so the agent can run hundreds of experiments cheaply; `walkforward.py` (5 non-overlapping 2-year folds, 2014–2023) and deflated Sharpe in `analysis.ipynb` are morning-review analyses layered on top of kept rows.
- **Trust-based IS/OOS honesty with an optional strict mode.** `run_backtest` reports both splits on every run; set `SHOW_OOS=0` in the environment and OOS-derived lines are masked in `run.log`, with the full audit trail written to a side-channel `oos_results.tsv` the reviewer consults. The agent forms hypotheses on `is_sharpe` and uses `status_hint` + `running_best.py` to gate keep/discard.
- **Bootstrap CI on OOS Sharpe.** A stationary block bootstrap (200 resamples, 20-day blocks) is reported with every run. The keep rule tightens to `ci_lo > running_best - 0.1` so a 0.03 "improvement" that lives inside the noise band is not kept.
- **Per-year OOS Sharpe decomposition.** Each run emits `oos_sharpe_2020..2024` so a single-year driver (e.g. a 2020 vol harvest) is visible instead of hidden inside the headline.
- **Hard constraints are blunt.** Max-DD and turnover caps keep degenerate strategies (leveraged martingale, daily-rebalance parameter fits) out of the `keep` list. They don't guarantee the strategy is good — just that it isn't obviously broken.

## Caveats / disclaimers

This repo is designed for **research process, not production alpha.** Known issues with the backtest:

- **Survivorship bias.** The universe is a frozen 2024-dated SP100 snapshot. Any ticker that was delisted or renamed before 2024-12-31 is silently absent; `run_backtest` does force weights to 0 on tickers that have no price data yet (mitigating IPO-era leakage for e.g. META, ABBV, TSLA), but a proper point-in-time membership schedule is not yet supplied. Results are biased upward relative to a real PIT backtest.
- **Data fidelity.** Prices come from yfinance (free, unaudited, adjusted closes). Corporate-action handling, dividend handling, and split adjustments follow yfinance's conventions. Gaps and errors are not corrected.
- **Cost model is crude.** 5bps per side + 200bps annual borrow on shorts. No market-impact model, no bid/ask, no capacity analysis.
- **No deployment.** There is no broker integration, no paper trading, no live signal generation. A good `oos_sharpe` in this repo is *not* a trade-ready signal — it is a hypothesis that survived one specific backtest.

If you want to use any idea that comes out of this loop for real money, you owe it a real point-in-time backtest, a real capacity/impact study, and a real live-trading sandbox. This repo does none of those things.

## License

MIT
