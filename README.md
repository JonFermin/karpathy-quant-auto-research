# karpathy-quant-auto-research

A direct port of Andrej Karpathy's [`karpathy/autoresearch`](https://github.com/karpathy/autoresearch) pattern — single agent-editable file, immutable harness, IS/OOS gate, git-native experiment trail — applied to daily US equity strategy search instead of LLM pretraining tweaks.

Karpathy's repo works because nanoGPT loss is a clean, low-noise objective: a 1% val-loss improvement is real and reproduces. **OOS Sharpe on a 5-year window is not that.** Porting the loop unchanged would produce a beautiful audit trail of an agent successfully p-hacking itself into a corner. Most of the work in this repo is the machinery to keep that from happening — and an honest accounting of where it still does.

If the original is "let an agent iterate on a clean signal," this fork is "let an agent iterate on a signal so noisy that the loop itself is the main hazard."

## The premise, and why it's dangerous

The setup is straightforward: an AI agent edits one file (`strategy.py`), runs a backtest against a frozen IS/OOS split, checks if OOS Sharpe improved subject to hard constraints, keeps or discards, repeats. You wake up to ~100 logged experiments and a current-best branch.

The danger is also straightforward and worth stating up front:

**A loop that runs hundreds of trials against a fixed out-of-sample slice is a multiple-testing engine.** With OOS Sharpe noise of roughly ±0.5 (90% CI) on this 5-year window, the expected best-of-200 looks impressive even if every underlying strategy is pure coin-flipping. If you take the headline number from a green row at face value, the agent has fooled you. The interesting question this repo tries to answer is not "can an agent find alpha" — it's "can the loop be instrumented to make most of its own false positives visible?"

Spoiler: **partially**. There is no clever trick that makes a fixed-OOS search statistically clean. The repo trades in honest mitigations and an explicit list of where leakage remains.

This is research process, not a product. There is no live deployment, no broker connection, no paper-trading link.

## How it works

Three files matter:

- **`prepare.py`** — fixed constants, one-time data prep (downloads adjusted closes via yfinance), and the backtest engine (`run_backtest`, `print_summary`, `TimeBudget`). The T+1 execution shift lives inside `run_backtest` so the agent cannot accidentally introduce look-ahead. **Not modified.**
- **`strategy.py`** — the single file the agent edits. Contains `generate_weights(prices) → weights` and a driver that calls `run_backtest`. Everything inside `generate_weights` is fair game: signals, sizing, regime filters, rebalancing cadence, neutralization.
- **`program.md`** — agent instructions. Edited and iterated on by the human between runs.

The metric is **`oos_sharpe`** on the 2020–2024 slice, subject to `max_drawdown ≤ 0.35` and `turnover_annual ≤ 50.0`. Constraint violations are force-discarded regardless of headline Sharpe.

## The baseline

`strategy.py` ships with **12-1 cross-sectional momentum**, the hurdle every experiment has to clear. Five lines of real logic:

```python
mom = prices.pct_change(252).shift(21)      # 12-month return, skip last month
ranks = mom.rank(axis=1, pct=True)          # rank across the universe each day
w = (ranks >= 0.9).astype(float)            # long the top 10% of names
w = w.div(w.sum(axis=1).replace(0, 1), axis=0)  # equal-weight, gross = 1
w = w.resample("ME").last().reindex(prices.index, method="ffill").fillna(0.0)  # hold monthly
```

Plain English: **every month, buy the 10 S&P 100 names that went up the most over the last 12 months — ignoring the most recent month — and hold them.** On this harness it lands at OOS Sharpe ≈ 0.92, max drawdown ≈ 0.32, turnover ~6/yr.

### Why this is the baseline

12-1 momentum is probably the single most-studied anomaly in equities. Jegadeesh & Titman (1993) documented it in US stocks. Asness, Moskowitz & Pedersen (2013) found it in 8 asset classes across 40 countries. Fama & French (2012) added it as a fourth factor. It has survived ~30 years of post-publication live performance, which is rare for a quant signal. Two competing explanations: behavioral underreaction (information diffuses slowly) and risk premium (it crashes hard — ~73% drawdown for US long-short in 2009 — and investors demand compensation).

Skipping the last month matters because short-horizon returns (1 day to 1 month) show the **opposite** effect — recent winners pull back, recent losers bounce (Lehmann 1990, Jegadeesh 1990). The "12-1" convention separates the two.

### Why it's hard to beat here

- **It's the right answer for this universe.** Momentum is a real, persistent effect. Most "improvements" an agent invents are either fitting IS noise or rediscovering a component of the same signal under a different name.
- **SP100 is a narrow, rigged universe.** 100 survivorship-selected large-caps frozen at 2024: anything you bought in 2010 made it to 2024. The baseline already eats the survivorship premium for free.
- **The harness is honest about noise.** Bootstrap CIs on OOS Sharpe are wide. A 0.03–0.10 "improvement" lives inside the band. The keep rule kills most of it.

The baseline is not a strawman. Beating it with a genuinely new idea is the point of the loop. Most of the time, the correct finding is that you didn't.

## P-hacking: prevention and remaining leaks

This is the section that drove the rewrite. Read it before getting excited about any number this repo prints.

### What the harness does to push back

- **Strict-honesty mode (`SHOW_OOS=0`).** During the loop, every OOS-derived line in `run.log` is masked. The agent sees `is_sharpe`, hard-constraint flags, and a `status_hint` derived from IS — it does **not** see `oos_sharpe`, the per-year decomposition, or the bootstrap CI. The full OOS audit trail goes to a side-channel `oos_results.tsv` only the human reads. The agent literally cannot gradient-descend on OOS, because it cannot observe it.
- **Bootstrap CI with a tightened keep rule.** Each run reports a stationary block bootstrap (200 resamples, 20-day blocks). Keep requires `ci_lo > running_best - 0.1`, not just `oos_sharpe > running_best`. A 0.03 "win" inside the noise band is rejected.
- **Per-year OOS decomposition.** Every run emits `oos_sharpe_2020..2024`. A strategy scoring 1.4 by riding 2020 vol and being mediocre 2021–2024 is visible at a glance.
- **Walk-forward (`walkforward.py`).** Five non-overlapping 2-year folds, 2014–2023. Strategies that look great on the headline OOS but inconsistent across folds get caught here in morning review.
- **Deflated Sharpe in `analysis.ipynb`.** Bailey & López de Prado's deflated Sharpe explicitly adjusts for the number of trials and the variance of trial Sharpes. It's the right statistic for "is this best-of-N actually significant," and it's the one to consult before getting excited about a green row.
- **Hard constraints up front.** Max-DD ≤ 0.35 and turnover ≤ 50/yr eliminate the worst classes of degenerate fits before they enter the keep pool.
- **Single-file scope + git audit trail.** Every trial is a commit; `results.tsv` logs the description. Reviewing the *full* set of attempts (not just survivors) is the protection against survivorship bias inside the experiment log itself.

### Where it still leaks

- **The OOS slice is fixed and reused.** Strict-honesty stops the agent, but the *human* sees OOS between sessions, edits `program.md` based on what's working, and reruns. That is a slow but real OOS-contamination loop with no cryptographic fix short of escrowing a slice neither agent nor human ever inspects.
- **Strict-honesty is honor-system.** A clever-enough agent could in principle infer OOS performance from IS/OOS divergence patterns or from `status_hint`. Mitigation is "the agent isn't trying to" — not a security property.
- **Keep rule still uses `running_best`.** Bootstrap-CI tightening reduces but does not eliminate multiple-testing inflation. Deflated Sharpe is reported in the notebook but **not enforced by the keep gate**. A passing run has not passed deflated Sharpe.
- **Survivorship bias compounds with multiple testing.** SP100/SP500 are frozen 2024 snapshots. Strategies that implicitly bet on "winners keep winning" inherit a free tailwind, and the agent gets ~200 chances to find one.
- **Universe, costs, and data are all best-case.** yfinance adjusted closes (no microstructure, no failed fills, vendor-specific corporate actions), 5bps flat per side, 200bps borrow, no impact, no bid/ask, no capacity. Strategies that need tighter spreads or bigger size silently look better than they are.
- **One regime.** 2010–2024 is one bull market with two interruptions (2018Q4, 2020). Walk-forward across 2-year folds helps, but the whole dataset shares a low-rate, high-multiple, US-large-cap-dominant macro backdrop.
- **Cherry-picked branches.** Each `/autoresearch` invocation makes a fresh branch and pushes it. Running the loop ten times and only keeping the best-looking branch is another selection layer on top of the in-loop selection. Mitigation: read *all* the pushed branches in `analysis.ipynb`, not just the winner.

### What "passing" actually means

A `keep` row in `results.tsv` means: this strategy beat the running best on a single fixed OOS window, with a bootstrap-CI lower bound above `running_best - 0.1`, satisfying the hard constraints. **It does not mean the strategy has alpha.** It means it survived one bar in one specific synthetic environment. Honest interpretation of a green row is "worth a closer look in the morning notebook," not "deploy this."

## Quick start

**Requirements:** Python 3.10+, [uv](https://docs.astral.sh/uv/), internet for the one-time yfinance download.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # install uv
uv sync                                            # install deps
uv run prepare.py                                  # download + cache prices (~1–2 min)
uv run strategy.py                                 # run the baseline backtest
```

If those work, your setup is good and you can go into autonomous research mode.

## Running the agent

Spin up Claude Code (or Codex, or whatever) in this repo. The repo ships with a **`quant-autoresearch` skill** (`.claude/skills/quant-autoresearch/SKILL.md`) that wraps `program.md` with timestamped git worktrees, strict-honesty `SHOW_OOS=0` mode, and an auto-push + cleanup on graceful exit. There's also a slash-command wrapper at `.claude/commands/autoresearch.md`.

### Kick-off examples

| Command | What happens |
|---|---|
| `/autoresearch` | Default SP100. Creates `worktrees/<apr19-223742>`, runs baseline + up to 20 trials, pushes branch + `SUMMARY.md` to `origin`, removes the worktree. |
| `/autoresearch sp500` | Same workflow, on the `sp500_2024` universe (503 tickers). Requires `universe_sp500_2024.json` and the matching prices parquet. |
| `/autoresearch sp100` | Explicit SP100 (equivalent to bare `/autoresearch`). |

### Natural-language triggers

The skill auto-fires when your prompt matches its description:

```
kick off a new experiment
start the autoresearch loop
launch strict-honesty mode on sp500
run program.md overnight on the sp100 universe
```

If you name a universe in the prompt, the skill picks it up and passes `UNIVERSE_TAG=<tag>` through to every harness call.

### Parallel runs

Each kick-off gets its own timestamped worktree, so two `/autoresearch` invocations in separate sessions run concurrently without stepping on each other's `results.tsv` / `oos_results.tsv` / branches. Different universes are also safe: `prepare.py` namespaces the cache by `UNIVERSE_TAG` (`prices_sp100_2024.parquet`, `prices_sp500_2024.parquet`, …). Same-universe parallel runs share the cache read-only.

### Switching universes

```bash
UNIVERSE_TAG=sp500_2024 uv run prepare.py     # one-time per universe
UNIVERSE_TAG=sp500_2024 /autoresearch         # then launch
```

Add a new universe by dropping `universe_<tag>.json` at the repo root with a list of tickers, then `UNIVERSE_TAG=<tag> uv run prepare.py`.

### What the agent does

`program.md` is the authoritative agent spec; the skill is a thin operational wrapper. Each iteration: edit `strategy.py`, commit, run `SHOW_OOS=0 uv run strategy.py`, log the trial via `log_result.py`, advance or reset based on the grader's exit code. Capped at 20 trials per branch. On graceful exit the worktree is archived via `SUMMARY.md` + `git push`, then removed.

## Project structure

```
prepare.py                              — constants, data loader, backtest engine (do not modify)
strategy.py                             — signal + weights (agent modifies this)
program.md                              — agent instructions
universe_sp100_2024.json                — frozen SP100 ticker list (default universe)
universe_sp500_2024.json                — frozen SP500 ticker list (503 tickers)
analysis.ipynb                          — notebook for reviewing results.tsv (deflated Sharpe lives here)
running_best.py                         — CLI: current best kept oos_sharpe
log_result.py                           — CLI: append a row to results.tsv from run.log
walkforward.py                          — CLI: per-fold Sharpe sanity check for a strategy
test_lookahead.py                       — regression test for the T+1 shift
pyproject.toml                          — dependencies
.claude/skills/quant-autoresearch/      — Claude Code skill wrapping program.md
.claude/commands/autoresearch.md        — slash-command alias for the skill
worktrees/                              — per-experiment git worktrees (gitignored)
```

## Other limitations

Beyond the multiple-testing problem above:

- **Survivorship bias.** The universe is a frozen 2024-dated SP100 snapshot. Any ticker delisted or renamed before 2024-12-31 is silently absent. `run_backtest` does force weights to 0 on tickers without price data yet (mitigating IPO-era leakage for META, ABBV, TSLA, etc.), but a proper point-in-time membership schedule is not yet supplied. Results are biased upward relative to a real PIT backtest, often substantially.
- **Data fidelity.** yfinance, free, unaudited, adjusted closes. Corporate actions, dividends, splits all follow yfinance conventions. Gaps and errors are not corrected. Don't assume parity with a paid vendor.
- **Cost model is crude.** 5bps per side + 200bps annual borrow on shorts. No market-impact, no bid/ask, no capacity, no financing curve, no shorting locate cost beyond the flat 200bps.
- **No deployment.** No broker, no paper trading, no live signal. A good `oos_sharpe` here is a hypothesis that survived one synthetic backtest, not a trade-ready signal.
- **The agent is not a quant.** It pattern-matches against textbook signals and combinations. It does not understand microstructure, institutional plumbing, or why a published anomaly might already be arbitraged away. Treat its "discoveries" as starting points for human investigation, not as conclusions.

If you want to use any idea that comes out of this loop for real money, you owe it: a proper point-in-time universe, a real cost/impact/capacity study, an out-of-sample window the loop has *never* touched, and a paper-trading sandbox before any capital. This repo does none of those things and is not a substitute for any of them.

## Credit

This repo is a fork-of-pattern (not a fork-of-code) of Andrej Karpathy's [`karpathy/autoresearch`](https://github.com/karpathy/autoresearch). The loop structure, the single-file-edit constraint, the agent-as-experimenter framing, and the git-native audit trail are all his ideas. Everything that's specifically about defending against multiple-testing leakage in a noisy financial objective — strict-honesty mode, bootstrap CI gates, per-year decomposition, walk-forward, deflated Sharpe in review, and the candid leakage list above — is what this fork adds on top.

## License

MIT
