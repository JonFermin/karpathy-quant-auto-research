---
name: quant-autoresearch
description: Use when the user asks to kick off / start / begin a new quant research experiment in this repo, run the autonomous strategy loop, or launch an overnight SHOW_OOS=0 run. Triggers on phrases like "kick off a new experiment", "start the autoresearch loop", "run program.md", "launch strict-honesty mode". Drives the program.md loop: edit strategy.py → commit → backtest → log_result.py → keep/discard, inside a dedicated git worktree on a fresh quant-research/<tag> branch (timestamped so parallel launches don't collide), in strict-honesty SHOW_OOS=0 mode, never stopping until the grader exits 4 or the human interrupts.
---

# quant-autoresearch

Kicks off an autonomous quant-strategy experiment loop in this repo per `program.md`. Strict-honesty `SHOW_OOS=0` mode. Runs inside a **dedicated git worktree** on a fresh `quant-research/<tag>` branch, where `<tag>` is **timestamped** (e.g. `apr19-223742`) so two experiments can run in parallel without stomping on each other. Never stops until the grader returns exit 4 (trial cap) or the human interrupts.

## Non-negotiable ground rules (do not violate)

- `strategy.py` is the ONLY file you edit. Everything inside `generate_weights()` is fair game.
- `prepare.py` is READ-ONLY. It already does `weights.shift(1)` inside `run_backtest` — do NOT pre-shift in `strategy.py`.
- `SHOW_OOS=0` means: do **not** `cat oos_results.tsv`, do **not** grep the OOS lines out of `run.log`, do **not** peek at per-year OOS. Form hypotheses on `is_sharpe` / `status_hint` / `running_best.py`. That is the whole point of strict-honesty mode.
- Never stop to ask "should I keep going?". The human may be asleep. Only three exits: grader returns 4, human interrupts, or you genuinely run out of defensible hypotheses (documented in a one-paragraph summary — not a 19th micro-variant).
- Never `git add results.tsv` or `oos_results.tsv` — both are gitignored by design.
- No new dependencies. No modifications to `prepare.py`, `log_result.py`, or `running_best.py`.

## Step 1 — Setup

You start in the repo root (the main checkout). Run these checks in parallel:

```bash
git status
git rev-parse --abbrev-ref HEAD
ls ~/.cache/karpathy-quant-auto-research/prices.parquet
grep -q '^worktrees/' .gitignore && echo ok || echo "NEEDS worktrees/ in .gitignore"
```

Then:

1. **Pick a timestamped tag**: `<month-abbrev><day>-<HHMMSS>` from the current local time (e.g. `apr19-223742`). The seconds suffix is what lets two concurrent skill launches coexist — do NOT drop it even if you're the only one running. Verify `git branch --list quant-research/<tag>` is empty; if by some fluke it exists (clock skew, replay), append `b`, `c`, … as a collision bump.
2. **Create a dedicated worktree on a fresh branch from master**:
   ```bash
   mkdir -p worktrees
   git worktree add -b quant-research/<tag> worktrees/<tag> master
   cd worktrees/<tag>
   ```
   Every subsequent command in the loop runs from **inside the worktree** (`worktrees/<tag>/`). `results.tsv`, `oos_results.tsv`, `run.log`, and the `strategy.py` edits all live there — so parallel experiments never touch each other's state. If the main working tree has uncommitted changes, that's fine (worktrees are independent) — but check that `worktrees/` is in `.gitignore` (see the grep check above). If it isn't, stop and tell the human to add it before proceeding; otherwise `git status` in the main tree will fill with worktree noise.
3. **Verify prices cache**: if `~/.cache/karpathy-quant-auto-research/prices.parquet` is missing, stop and tell the human to `uv run prepare.py`. Do not try to run it yourself — it re-downloads several MB from yfinance. If the human specified a non-default universe (e.g. "sp500"), also verify the right cache is mounted — see **Universe selection** below.
4. **Seed `results.tsv`** inside the worktree with just the header row (it should be absent in a brand-new worktree):
   ```
   commit	oos_sharpe	max_dd	turnover	status	description
   ```
   (tab-separated, no trailing newline weirdness). Do NOT stage or commit it.
5. **Read context**: `README.md`, `prepare.py`, `strategy.py`. You've already read `program.md` (that's why you're here). Skim `log_result.py` and `running_best.py` only if you need to confirm an exit-code detail — don't re-derive the rules, they're in `program.md`.
6. **Baseline run FIRST**: do not edit `strategy.py` yet. The very first run of the branch is the baseline commit-free run to seed `oos_results.tsv`. Follow the loop below with a trivial identity-commit path — see "First iteration" note at the bottom.

### Universe selection

The experiment loop defaults to `UNIVERSE_TAG=sp100_2024`. If the human names a different universe in the launch prompt (e.g. "on SP500", "with UNIVERSE_TAG=sp500_2024"):

- Verify `universe_<tag>.json` exists at the repo root.
- Verify `~/.cache/karpathy-quant-auto-research/prices.parquet` corresponds to that universe — `prepare.py` does NOT tag the cache file by universe, so the cache on disk might be the previous universe's prices. Check column count roughly matches the universe JSON (e.g. `uv run python -c "import pandas as pd; print(pd.read_parquet('...').shape)"`).
- If the cache is for the wrong universe, stop and tell the human to either `cp ~/.cache/karpathy-quant-auto-research/prices.<tag>.parquet ~/.cache/karpathy-quant-auto-research/prices.parquet` (if they've preserved a tagged backup — see the multi-universe workflow memory) or `UNIVERSE_TAG=<tag> uv run prepare.py --refresh` to re-download. Do NOT re-download autonomously.
- Export `UNIVERSE_TAG=<tag>` on EVERY call to `strategy.py`, `log_result.py`, `running_best.py`, etc. — they all read it at import time.

### Parallel experiments

Two or more worktrees can run concurrently when each has a unique timestamped tag. But:

- **Parallel runs must share the same `UNIVERSE_TAG`.** The price cache is a single file (`~/.cache/karpathy-quant-auto-research/prices.parquet`); if two loops want different universes, they'll fight over it. Same-universe parallel runs just share the cache read-only, which is safe.
- Each worktree has its own `results.tsv` / `oos_results.tsv` / `run.log`, so the grader and `running_best.py` see only that worktree's trials — they do NOT pool across parallel experiments. That's intentional: each run is a clean 20-trial branch.
- Don't reach into a sibling worktree's files from inside your loop. If the human wants to compare across parallel branches, that's a morning-review job.

## Step 2 — The loop

Run every command from **inside the worktree** (`worktrees/<tag>/`). Repeat until the grader exits 4 or the human interrupts:

```bash
# 1. Form a hypothesis (one line, economic intuition, not a knob-twist)
# 2. Edit strategy.py — real code change (comments/whitespace-only is auto-rejected)
git add strategy.py
git commit -m "<short imperative summary of the change>"

# 3. Backtest in strict-honesty mode — ALWAYS redirect, never tee/stream.
#    Include UNIVERSE_TAG if non-default.
SHOW_OOS=0 uv run strategy.py > run.log 2>&1
# (or: SHOW_OOS=0 UNIVERSE_TAG=sp500_2024 uv run strategy.py > run.log 2>&1)

# 4. Extract IS-only headline metrics (OOS lines will be masked <hidden, SHOW_OOS=0>)
grep "^is_sharpe:\|^max_drawdown:\|^turnover_annual:\|^num_trades:\|^status_hint:" run.log
# If empty → crash. tail -n 50 run.log to read the trace.

# 5. Log the row (grader writes status, not you)
uv run log_result.py "thesis: <one-line rationale>"
echo "exit=$?"

# 6. Branch on exit code:
#    0 → parse "status=keep" or "status=discard" from last stdout line
#        keep    → advance (do nothing, next iteration starts from this HEAD)
#        discard → git reset --hard HEAD~1
#    2 → description invalid. Fix the command and rerun log_result.py.
#        Nothing was logged; do NOT reset.
#    3 → no-op commit (AST-equal to HEAD~1). git reset --hard HEAD~1.
#        Do not retry the same non-change.
#    4 → TRIAL CAP. Stop. Summarize results.tsv for morning review.
#    5 → crash row written. git reset --hard HEAD~1. tail -n 50 run.log,
#        learn, then try a different idea (or fix the bug if obvious).
```

### Probing state between iterations (all safe under SHOW_OOS=0)

```bash
uv run running_best.py              # single number: best kept oos_sharpe so far
uv run running_best.py --baseline   # seed row's oos_sharpe
uv run running_best.py --trials     # rows on this branch (cap awareness)
git log --oneline -10               # recent experiment commits
grep '^thesis:' results.tsv         # scan your hypothesis history
grep '^keep' results.tsv            # survivors so far
```

Do NOT run `cat oos_results.tsv` during the loop. Do NOT `grep oos_sharpe_2` or any per-year OOS. If you catch yourself about to peek, stop.

### First iteration (baseline)

`log_result.py` requires a real code change against `HEAD~1`, so the baseline needs a tiny scaffolding commit to anchor it. Make the first iteration a genuine minimal tweak — e.g. a single-line guard, a rename, a clarifying refactor — then run the full loop above. This produces the first `oos_results.tsv` row that becomes the fixed baseline anchor. Subsequent keeps are judged against this anchor, not the running max.

## Hypothesis discipline

- Frame the thesis **before** editing. Write it as the `thesis:` line first; if you can't, skip the idea.
- Prefer changes with economic intuition (why a market would pay for this edge) over parameter sweeps.
- A 5-line change with a thesis beats a 10-hyperparam grid search. Simpler is better. Deleting code that works equally well is a win.
- "Nothing beat baseline" is the most likely correct outcome on a survivorship-biased 100-name universe. Do not pad the count to reach 20 — fewer honest hypotheses beats knob-twist churn.

## Idea seeds (from program.md — pick ones you can defend, don't sweep them)

Momentum variants (3-1 / 6-1 / 12-1; risk-adjusted; residual) · short-horizon reversal (5d / 21d) · z-score ranks vs decile cutoffs · sector / beta neutralization · inverse-vol sizing · regime gate (SPY drawdown or equal-weighted universe drawdown — no VIX in cache) · combination of two previously-kept signals with a complementarity thesis · long-only vs long-short · gross leverage / per-name caps.

## Stop conditions (the only three)

On a **graceful** stop (cases 1 and 3 below), run the **Archive + push + cleanup** sequence (next section) before returning to the human. On a human interrupt (case 2), do nothing — you can't clean up reliably mid-signal.

1. `log_result.py` returns exit 4 → trial cap reached. Print a one-screen summary: the worktree path, branch name, `UNIVERSE_TAG`, count of keep / discard / crash rows, best kept Sharpe from `running_best.py`, baseline from `running_best.py --baseline`, `thesis:` lines grouped by status. Then run **Archive + push + cleanup**.
2. Human interrupts (Ctrl-C or explicit "stop"). Leave the worktree and branch as-is; do not tidy up. The human has taken over.
3. You cannot articulate a defensible non-micro-variant hypothesis. Write a one-paragraph summary to chat (not to a file) explaining what's been tried and why you're stopping. Then run **Archive + push + cleanup**. Do NOT fabricate a filler trial.

## Archive + push + cleanup

`results.tsv` and `oos_results.tsv` are gitignored, so removing the worktree destroys them. The archive step folds their content into a committed `SUMMARY.md` so the branch on origin is self-describing.

```bash
# (from inside worktrees/<tag>)

# 1. Build SUMMARY.md — capture the full audit trail in a committed file.
cat > SUMMARY.md <<EOF
# quant-research/<tag>

- **UNIVERSE_TAG**: <tag value, or "sp100_2024 (default)">
- **Baseline (seed)**: $(uv run running_best.py --baseline --verbose 2>/dev/null || echo "n/a")
- **Running best**:   $(uv run running_best.py --verbose 2>/dev/null || echo "no kept rows")
- **Trials logged**:  $(uv run running_best.py --trials 2>/dev/null || echo "0")
- **Stop reason**:    <trial-cap | no-defensible-hypothesis | …>

## results.tsv

\`\`\`
$(cat results.tsv)
\`\`\`

## Theses by status

### keep
$(awk -F'\t' 'NR>1 && $5=="keep"  {print "- " $6}' results.tsv)

### discard
$(awk -F'\t' 'NR>1 && $5=="discard"{print "- " $6}' results.tsv)

### crash
$(awk -F'\t' 'NR>1 && $5=="crash"  {print "- " $6}' results.tsv)
EOF

# 2. Commit the summary.
git add SUMMARY.md
git commit -m "summary: quant-research/<tag> (<n-keep>/<n-trials>)"

# 3. Push the branch — only if a remote exists. Do not force-push.
if git remote get-url origin >/dev/null 2>&1; then
  git push -u origin quant-research/<tag> || echo "push failed — leaving worktree in place for manual recovery"
else
  echo "no origin remote — skipping push, leaving worktree in place"
fi

# 4. Only remove the worktree if the push succeeded (or no remote was expected).
#    If push failed, STOP and tell the human — do not silently lose the branch state.
cd ../..    # back to repo root
if git branch -r | grep -q "origin/quant-research/<tag>"; then
  git worktree remove worktrees/<tag>
  echo "archived and cleaned up: quant-research/<tag> pushed to origin"
else
  echo "worktree preserved at worktrees/<tag> — manual cleanup required"
fi
```

Rules:
- Never `--force` on push. If origin rejects (somehow the branch exists upstream), STOP and report — do not overwrite.
- Never `git worktree remove --force` if the remote is missing the commits; that loses work.
- Do NOT delete the branch locally (`git branch -D`). The worktree removal already detaches it; the branch stays referenceable. Deleting branches is the human's choice.
- If there is no `origin` remote configured, skip push and preserve the worktree. Tell the human. Do NOT try to add a remote.

## Morning-review hint for the human (do NOT act on this during the loop)

After the human wakes up and the cleanup has run, they review via the pushed branch:

```bash
git fetch origin
git log origin/quant-research/<tag> --oneline        # trial commits + summary
git show origin/quant-research/<tag>:SUMMARY.md      # the full archived summary

# to sanity-check a specific kept commit:
git checkout <commit> && uv run walkforward.py
```

If the loop was Ctrl-C'd (stop condition 2), the worktree is still at `worktrees/<tag>/` and results.tsv / oos_results.tsv are intact there — review locally before deciding whether to archive.

Walk-forward is the human's job, not yours. You do not run `walkforward.py` inside the loop — it is a post-hoc OOS check.
