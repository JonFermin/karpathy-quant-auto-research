---
description: Kick off an autonomous quant autoresearch loop in a timestamped git worktree (strict-honesty SHOW_OOS=0)
argument-hint: [universe-tag] — e.g. sp500, sp100, or blank for default sp100_2024
---

Kick off a new quant autoresearch experiment via the `quant-autoresearch` skill.

Arguments passed to this command: $ARGUMENTS

Interpretation:
- If `$ARGUMENTS` names a universe (e.g. `sp500`, `sp100`, or a full `UNIVERSE_TAG=...` expression), use that universe — verify `universe_<tag>.json` exists and that the parquet cache matches per the skill's "Universe selection" section.
- If `$ARGUMENTS` is empty, use the default `UNIVERSE_TAG=sp100_2024`.
- Any additional words are hints (e.g. "overnight", "strict"); strict-honesty `SHOW_OOS=0` is always on regardless.

Then: follow the `quant-autoresearch` skill end-to-end — create a timestamped worktree at `worktrees/MMDD-HHMMSS` (e.g. `worktrees/0419-223742` — numeric, locale-independent), run the full loop, and on graceful exit build `SUMMARY.md`, commit, push to `origin`, and remove the worktree. Do not stop until the grader exits 4 or the human interrupts.
