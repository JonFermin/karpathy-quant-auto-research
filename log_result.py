"""
log_result.py — sole gatekeeper that appends rows to results.tsv.

The agent runs:

    uv run log_result.py "thesis: 12-1 momentum with skew filter"

Everything else is computed by the harness from state on disk:

  1. AST-compare strategy.py at HEAD vs HEAD~1 — comment/whitespace/
     docstring-only commits are rejected as no-ops (exit 3).
  2. Count existing rows in results.tsv against TRIAL_CAP (exit 4).
  3. Look up the current commit's OOS truth in oos_results.tsv. No row →
     the run never reached print_summary → crash (exit 5, crash row
     written).
  4. Apply the baseline-relative, deflation-aware, walk-forward-gated
     keep rule; compute status; write the row; exit 0.

The agent never chooses the status. Mis-grading is structurally impossible
under this CLI — which is the point of the rewrite. `run.log` may mask
OOS numbers under SHOW_OOS=0; oos_results.tsv is always complete and is
the sole source of truth for grading.

Exit codes:
  0 — row logged (status computed)
  2 — description invalid (tab/newline, or missing "thesis: " prefix on a non-crash row)
  3 — no code change in strategy.py since HEAD~1
  4 — trial cap reached — stop the loop and review
  5 — crash row logged (the run never reached print_summary)
"""

from __future__ import annotations

import argparse
import ast
import math
import os
import subprocess
import sys

import numpy as np
import pandas as pd

from prepare import OOS_RESULTS_TSV, REPO_ROOT
from stats import expected_max_sharpe_null

RESULTS_TSV = REPO_ROOT / "results.tsv"
HEADER = ["commit", "oos_sharpe", "max_dd", "turnover", "status", "description"]

# How much better than baseline the point estimate must be before the
# deflation term kicks in. Tunable by the reviewer; a smaller hurdle
# trades precision for recall, a larger one makes the gate unreachable
# on this survivorship-biased universe.
BASELINE_HURDLE_SHARPE = 0.15

# The walk-forward median Sharpe must clear the baseline's by at least
# this delta. Guards against a strategy whose 2020–2024 edge evaporates
# outside the headline window.
MIN_FOLD_MEDIAN_DELTA = 0.10

MAX_DD_HARD = 0.35
MAX_TURNOVER = 50.0
MIN_TRADES = 50

TRIAL_CAP = int(os.environ.get("AUTORESEARCH_TRIAL_CAP", "20"))


# --------------------------------------------------------------------------- #
# Git helpers
# --------------------------------------------------------------------------- #

def _short_commit() -> str:
    out = subprocess.check_output(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "--short=7", "HEAD"],
        stderr=subprocess.DEVNULL,
    )
    return out.decode().strip()


def _git_show(ref: str, relpath: str) -> str | None:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "show", f"{ref}:{relpath}"],
            stderr=subprocess.DEVNULL,
        )
        return out.decode("utf-8", errors="replace")
    except subprocess.CalledProcessError:
        return None


# --------------------------------------------------------------------------- #
# No-op detector (Phase 1b)
# --------------------------------------------------------------------------- #

def _strip_docstrings(tree: ast.AST) -> ast.AST:
    """In-place strip module/class/function-level docstrings so a pure
    docstring rewrite compares equal. Comments and whitespace are already
    dropped by `ast.parse`.
    """
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None)
            if not body:
                continue
            first = body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                node.body = body[1:] or [ast.Pass()]
    return tree


def _strategy_is_noop() -> bool | None:
    """AST-compare strategy.py at HEAD vs HEAD~1.

    Returns:
      True  — semantically identical (comment/whitespace/docstring only)
      False — genuine code change
      None  — cannot compare (first commit on branch, git error, syntax error)
    """
    head_src = _git_show("HEAD", "strategy.py")
    prev_src = _git_show("HEAD~1", "strategy.py")
    if head_src is None or prev_src is None:
        return None
    try:
        head_tree = _strip_docstrings(ast.parse(head_src))
        prev_tree = _strip_docstrings(ast.parse(prev_src))
    except SyntaxError:
        return None  # don't reject on syntax errors — the real failure surfaces elsewhere
    a = ast.dump(head_tree, annotate_fields=False, include_attributes=False)
    b = ast.dump(prev_tree, annotate_fields=False, include_attributes=False)
    return a == b


# --------------------------------------------------------------------------- #
# TSV I/O
# --------------------------------------------------------------------------- #

def _read_results_tsv() -> pd.DataFrame:
    if not RESULTS_TSV.exists() or RESULTS_TSV.stat().st_size == 0:
        return pd.DataFrame(columns=HEADER)
    try:
        df = pd.read_csv(RESULTS_TSV, sep="\t")
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=HEADER)
    if "status" in df.columns:
        df["status"] = df["status"].astype(str).str.strip().str.lower()
    return df


def _read_oos_log() -> pd.DataFrame:
    if not OOS_RESULTS_TSV.exists() or OOS_RESULTS_TSV.stat().st_size == 0:
        return pd.DataFrame()
    try:
        df = pd.read_csv(OOS_RESULTS_TSV, sep="\t")
    except pd.errors.EmptyDataError:
        return pd.DataFrame()
    numeric_cols = [
        "oos_sharpe", "oos_sharpe_lo", "oos_sharpe_hi",
        "is_sharpe", "max_drawdown", "annual_return", "annual_vol",
        "turnover_annual", "calmar",
        "median_fold_sharpe", "min_fold_sharpe",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "num_trades" in df.columns:
        df["num_trades"] = pd.to_numeric(df["num_trades"], errors="coerce").fillna(0).astype(int)
    if "commit" in df.columns:
        df["commit"] = df["commit"].astype(str).str.strip()
    if "status_hint" in df.columns:
        df["status_hint"] = df["status_hint"].astype(str).str.strip().str.lower()
    return df


def _append_results_row(
    commit: str,
    oos_sharpe: float,
    max_dd: float,
    turnover: float,
    status: str,
    desc: str,
) -> None:
    def fmt(x: float, spec: str, fallback: str) -> str:
        if x is None or not math.isfinite(float(x)):
            return fallback
        return format(float(x), spec)

    needs_header = (not RESULTS_TSV.exists()) or RESULTS_TSV.stat().st_size == 0
    row = [
        commit,
        fmt(oos_sharpe, ".6f", "0.000000"),
        fmt(max_dd, ".4f", "0.0000"),
        fmt(turnover, ".2f", "0.00"),
        status,
        desc,
    ]
    with open(RESULTS_TSV, "a", encoding="utf-8", newline="") as f:
        if needs_header:
            f.write("\t".join(HEADER) + "\n")
        f.write("\t".join(row) + "\n")
    print(f"logged: {commit}\t{row[1]}\t{row[2]}\t{row[3]}\t{status}\t{desc}")
    # Machine-parseable trailer so the loop can branch on the outcome without
    # re-reading the TSV. `tail -1 | grep '^status='` picks it up.
    print(f"status={status}")


# --------------------------------------------------------------------------- #
# Lookup helpers
# --------------------------------------------------------------------------- #

def _latest_oos_row_for(commit: str, oos_log: pd.DataFrame) -> pd.Series | None:
    if oos_log.empty or "commit" not in oos_log.columns:
        return None
    matches = oos_log[oos_log["commit"] == commit]
    if matches.empty:
        return None
    return matches.iloc[-1]


def _baseline_values(
    results: pd.DataFrame, oos_log: pd.DataFrame
) -> tuple[float, float] | None:
    """Return (baseline_oos_sharpe, baseline_median_fold_sharpe) drawn from
    the first non-crash row in results.tsv, joined to oos_results.tsv for
    the real numbers. Returns None when no baseline exists yet (this run
    IS the baseline).
    """
    if results.empty or "status" not in results.columns:
        return None
    non_crash = results[results["status"] != "crash"]
    if non_crash.empty:
        return None
    seed_commit = str(non_crash.iloc[0].get("commit", "")).strip()

    base_oos = float("nan")
    base_median_fold = float("nan")
    if seed_commit and not oos_log.empty and "commit" in oos_log.columns:
        matches = oos_log[oos_log["commit"] == seed_commit]
        if not matches.empty:
            r = matches.iloc[-1]
            if pd.notna(r.get("oos_sharpe")):
                base_oos = float(r["oos_sharpe"])
            if pd.notna(r.get("median_fold_sharpe")):
                base_median_fold = float(r["median_fold_sharpe"])
    # Fallback to results.tsv's own number if the side-channel doesn't have it.
    if not math.isfinite(base_oos):
        raw = non_crash.iloc[0].get("oos_sharpe")
        try:
            base_oos = float(raw)
        except (TypeError, ValueError):
            base_oos = float("nan")
    return base_oos, base_median_fold


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Log this run to results.tsv. Status is computed — the agent does not choose it."
    )
    parser.add_argument(
        "description",
        help="One-line rationale. Non-crash rows must start with 'thesis: '.",
    )
    args = parser.parse_args()
    desc = args.description.strip()
    if "\t" in desc or "\n" in desc:
        print("ERROR: description may not contain tabs or newlines", file=sys.stderr)
        return 2

    commit = _short_commit()

    # Phase 2 — trial cap (crashes count: they still consume cognitive budget).
    results = _read_results_tsv()
    n_existing = len(results)
    if n_existing >= TRIAL_CAP:
        print(
            f"ERROR: trial cap {TRIAL_CAP} reached on this branch "
            f"(set AUTORESEARCH_TRIAL_CAP to override, or start a new branch).",
            file=sys.stderr,
        )
        return 4

    # Phase 1b — reject no-op commits.
    noop = _strategy_is_noop()
    if noop is True:
        print("ERROR: no code change in strategy.py since HEAD~1", file=sys.stderr)
        return 3

    # Phase 1 — fetch OOS truth for the current commit.
    oos_log = _read_oos_log()
    oos_row = _latest_oos_row_for(commit, oos_log)

    if oos_row is None:
        # The run never reached print_summary. Crash rows are allowed free-
        # form descriptions — people often paste the stack-trace gist there.
        _append_results_row(commit, float("nan"), float("nan"), float("nan"), "crash", desc)
        return 5

    if not desc.lower().startswith("thesis:"):
        print("ERROR: keep/discard descriptions must start with 'thesis: '", file=sys.stderr)
        return 2

    oos_sharpe = float(oos_row.get("oos_sharpe", float("nan")))
    oos_lo = float(oos_row.get("oos_sharpe_lo", float("nan")))
    max_dd = float(oos_row.get("max_drawdown", float("nan")))
    turnover = float(oos_row.get("turnover_annual", float("nan")))
    num_trades = int(oos_row.get("num_trades", 0) or 0)
    status_hint = str(oos_row.get("status_hint", "")).strip().lower()
    median_fold = float(oos_row.get("median_fold_sharpe", float("nan")))
    min_fold = float(oos_row.get("min_fold_sharpe", float("nan")))

    # Harness-level crash (hard constraint violated / non-finite metrics) →
    # discard. The row is still worth logging because it informs the trial cap.
    harness_crash = status_hint == "crash" or not all(
        math.isfinite(x) for x in (oos_sharpe, max_dd, turnover)
    )

    baseline = _baseline_values(results, oos_log)

    if harness_crash:
        status = "discard"
        reason = f"harness reported crash/non-finite ({status_hint or 'bad metrics'})"
    elif baseline is None:
        status = "keep"
        reason = "seed row (no prior baseline)"
    else:
        base_oos, base_median_fold = baseline

        # sigma_N across all non-crash oos_results.tsv rows on this branch.
        sigma_n = 0.0
        if "oos_sharpe" in oos_log.columns:
            pool = oos_log["oos_sharpe"].dropna().to_numpy()
            pool = pool[np.isfinite(pool)]
            if pool.size >= 2:
                sigma_n = float(pool.std(ddof=1))

        # N: number of trials already on this branch (pre-this-row).
        n_for_sr0 = max(int(n_existing), 2)
        sr0_raw = expected_max_sharpe_null(sigma_n, n_for_sr0) if sigma_n > 0 else 0.0
        sr0 = sr0_raw if (sr0_raw is not None and math.isfinite(sr0_raw)) else 0.0
        if sr0 < 0:
            sr0 = 0.0

        hurdle = base_oos + BASELINE_HURDLE_SHARPE + sr0
        fold_hurdle = (
            base_median_fold + MIN_FOLD_MEDIAN_DELTA
            if math.isfinite(base_median_fold)
            else -math.inf
        )

        checks = {
            f"oos_sharpe {oos_sharpe:.4f} > hurdle {hurdle:.4f}"
            f" (= baseline {base_oos:.4f} + {BASELINE_HURDLE_SHARPE:.2f} + sr0 {sr0:.4f})":
                oos_sharpe > hurdle,
            f"oos_sharpe_ci_lo {oos_lo:.4f} > baseline {base_oos:.4f}":
                math.isfinite(oos_lo) and oos_lo > base_oos,
            f"median_fold_sharpe {median_fold:.4f} > fold_hurdle {fold_hurdle:.4f}":
                math.isfinite(median_fold) and median_fold > fold_hurdle,
            f"min_fold_sharpe {min_fold:.4f} > 0":
                math.isfinite(min_fold) and min_fold > 0,
            f"max_drawdown {max_dd:.4f} <= {MAX_DD_HARD}":
                math.isfinite(max_dd) and max_dd <= MAX_DD_HARD,
            f"turnover_annual {turnover:.2f} <= {MAX_TURNOVER}":
                math.isfinite(turnover) and turnover <= MAX_TURNOVER,
            f"num_trades {num_trades} >= {MIN_TRADES}":
                num_trades >= MIN_TRADES,
        }
        keep = all(checks.values())
        status = "keep" if keep else "discard"
        if keep:
            reason = "cleared all gates"
        else:
            failed = [k for k, v in checks.items() if not v]
            reason = "failed: " + "; ".join(failed)

    # Report the grader's verdict to stderr so the agent sees why it lost.
    print(f"grader: {status} ({reason})", file=sys.stderr)
    _append_results_row(commit, oos_sharpe, max_dd, turnover, status, desc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
