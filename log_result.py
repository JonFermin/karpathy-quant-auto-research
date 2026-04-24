"""
log_result.py — sole gatekeeper that appends rows to results.tsv.

The agent runs:

    uv run log_result.py "thesis: 12-1 momentum with skew filter"

Everything else is computed by the harness from state on disk:

  1. Hash the stripped-AST of strategy.py at HEAD and look it up in the
     per-universe shared cache at
     ~/.cache/karpathy-quant-auto-research/trial_cache_<UNIVERSE_TAG>.tsv.
     If any prior trial on any branch of this universe has the same AST
     hash, reject (exit 3). Comment/whitespace/docstring-only changes
     hash identically and are also rejected.
  2. Count existing rows in results.tsv against TRIAL_CAP (exit 4).
  3. Look up the current commit's OOS truth in oos_results.tsv. No row →
     the run never reached print_summary → crash (exit 5, crash row
     written).
  4. Apply the baseline-relative, deflation-aware, walk-forward-gated
     keep rule; compute status; write the row; append to the shared
     cache; exit 0.

The deflation term sigma_n and trial-count N are pooled from the shared
cache — so the multiple-hypothesis hurdle grows with the universe's
cumulative exploration budget, not just this branch's. Running a fresh
branch does not reset the budget.

The agent never chooses the status. Mis-grading is structurally impossible
under this CLI — which is the point of the rewrite. `run.log` may mask
OOS numbers under SHOW_OOS=0; oos_results.tsv and the shared cache are
both harness-side-only and are the sole sources of truth for grading.

Exit codes:
  0 — row logged (status computed)
  2 — description invalid (tab/newline, or missing "thesis: " prefix on a non-crash row)
  3 — AST duplicate of a prior trial on this universe (any branch)
  4 — trial cap reached — stop the loop and review
  5 — crash row logged (the run never reached print_summary)
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import math
import os
import platform
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from prepare import OOS_RESULTS_TSV, REPO_ROOT
from stats import expected_max_sharpe_null, jobson_korkie_memmel

if platform.system() == "Windows":
    import msvcrt
else:
    import fcntl

RESULTS_TSV = REPO_ROOT / "results.tsv"
HEADER = ["commit", "oos_sharpe", "max_dd", "turnover", "status", "description"]

# Shared per-universe trial cache. Lives outside the repo and outside any
# worktree so it survives worktree cleanup and is visible across concurrent
# branches on the same UNIVERSE_TAG. Format: tab-separated, header on first
# write. The cache is the single source of truth for (a) AST-duplicate
# rejection and (b) the pooled sigma_n / N that feed the deflation hurdle.
_CACHE_DIR = Path.home() / ".cache" / "karpathy-quant-auto-research"
CACHE_HEADER = [
    "ast_sha256",
    "branch_tag",
    "commit",
    "oos_sharpe",
    "status",
    "hurdle_version",
    "written_at",
]
# Bump if the hurdle-rule semantics change, so cohort-aware re-evaluation is
# possible. Current rule (v2): baseline + BASELINE_HURDLE_SHARPE + deflation,
# with sigma/N pooled per-universe across the shared cache; ci_lo gate
# replaced by Jobson-Korkie/Memmel one-sided paired Sharpe-difference test;
# fold gate split into IS-only (hard) and OOS-only (soft); fold Sharpes are
# computed with a 21d embargo; optional vol-scaled cost slope and
# time-varying RF are read from prepare's env-configured constants.
HURDLE_VERSION = 2

# One-sided Jobson-Korkie/Memmel p-value threshold: the new strategy's
# OOS Sharpe must beat the baseline's by a margin significant at this level.
JK_MEMMEL_P_MAX = 0.05

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
# Shared-cache dedup + deflation (Phase 1b)
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


def _strategy_ast_hash() -> str | None:
    """SHA-256 of the stripped-AST dump of strategy.py at HEAD.

    Returns None on git failure or syntax error; callers skip the dedup
    check in that case and let the real failure surface elsewhere.
    """
    src = _git_show("HEAD", "strategy.py")
    if src is None:
        return None
    try:
        tree = _strip_docstrings(ast.parse(src))
    except SyntaxError:
        return None
    dump = ast.dump(tree, annotate_fields=False, include_attributes=False)
    return hashlib.sha256(dump.encode("utf-8")).hexdigest()


def _current_branch_tag() -> str:
    """Return the portion of the branch name after 'quant-research/'.

    Falls back to 'unknown' on detached HEAD or unexpected branch names.
    Only used for audit / debug output in the cache; dedup is keyed on
    AST hash, not on branch.
    """
    try:
        out = subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        return "unknown"
    branch = out.decode().strip()
    prefix = "quant-research/"
    return branch[len(prefix):] if branch.startswith(prefix) else (branch or "unknown")


def _cache_path() -> Path:
    """Resolve the per-universe cache path from UNIVERSE_TAG at call time.

    Matches prepare.py's default of sp100_2024 when the env var is unset.
    """
    universe = os.environ.get("UNIVERSE_TAG", "sp100_2024")
    return _CACHE_DIR / f"trial_cache_{universe}.tsv"


@contextmanager
def _cache_lock(path: Path, *, exclusive: bool):
    """Cross-platform file lock context manager.

    Yields an opened file handle. On POSIX uses fcntl.flock; on Windows
    uses msvcrt.locking on a single byte range. msvcrt offers no shared
    lock, so Windows readers serialize against writers too — fine given
    append-only semantics and small file size.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a+" if exclusive else "r"
    f = open(path, mode, encoding="utf-8", newline="")
    try:
        if platform.system() == "Windows":
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield f
    finally:
        try:
            if platform.system() == "Windows":
                try:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        finally:
            f.close()


def _read_cache() -> pd.DataFrame:
    """Return the shared cache as a DataFrame, empty if the file is missing."""
    path = _cache_path()
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=CACHE_HEADER)
    try:
        with _cache_lock(path, exclusive=False) as f:
            f.seek(0)
            df = pd.read_csv(
                f, sep="\t",
                dtype={
                    "ast_sha256": str, "branch_tag": str,
                    "commit": str, "status": str,
                    "hurdle_version": str, "written_at": str,
                },
            )
    except (pd.errors.EmptyDataError, OSError):
        return pd.DataFrame(columns=CACHE_HEADER)
    except pd.errors.ParserError as e:
        print(
            f"WARNING: trial cache at {path} is malformed ({e}); "
            f"dedup disabled for this run. Inspect manually.",
            file=sys.stderr,
        )
        return pd.DataFrame(columns=CACHE_HEADER)
    if "oos_sharpe" in df.columns:
        df["oos_sharpe"] = pd.to_numeric(df["oos_sharpe"], errors="coerce")
    for col in ("ast_sha256", "branch_tag", "commit", "status"):
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()
    return df


def _append_cache(
    ast_hash: str,
    branch_tag: str,
    commit: str,
    oos_sharpe: float,
    status: str,
) -> None:
    """Append a row to the shared cache under an exclusive lock."""
    path = _cache_path()
    written_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    sharpe_str = (
        f"{float(oos_sharpe):.6f}"
        if oos_sharpe is not None and math.isfinite(float(oos_sharpe))
        else "nan"
    )
    row = [
        ast_hash,
        branch_tag,
        commit,
        sharpe_str,
        status,
        str(HURDLE_VERSION),
        written_at,
    ]
    with _cache_lock(path, exclusive=True) as f:
        f.seek(0, os.SEEK_END)
        if f.tell() == 0:
            f.write("\t".join(CACHE_HEADER) + "\n")
        f.write("\t".join(row) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _find_cache_duplicate(
    cache_df: pd.DataFrame, ast_hash: str, current_commit: str
) -> tuple[str, str] | None:
    """Return (branch_tag, commit) of the earliest prior trial whose AST
    hash matches, or None. Excludes rows for the current commit so a
    re-invocation of log_result.py on the same commit doesn't self-hit.
    """
    if cache_df.empty or "ast_sha256" not in cache_df.columns:
        return None
    hits = cache_df[cache_df["ast_sha256"] == ast_hash]
    if "commit" in hits.columns:
        hits = hits[hits["commit"] != current_commit]
    if hits.empty:
        return None
    row = hits.iloc[0]
    return (str(row.get("branch_tag", "unknown")), str(row.get("commit", "")))


# --------------------------------------------------------------------------- #
# TSV I/O
# --------------------------------------------------------------------------- #

def _read_results_tsv() -> pd.DataFrame:
    if not RESULTS_TSV.exists() or RESULTS_TSV.stat().st_size == 0:
        return pd.DataFrame(columns=HEADER)
    try:
        # Force commit column to string: 7-char hex commits like "6987e31"
        # parse as 6.987e+34 under default type inference.
        df = pd.read_csv(
            RESULTS_TSV, sep="\t",
            dtype={"commit": str, "status": str, "description": str},
        )
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=HEADER)
    if "status" in df.columns:
        df["status"] = df["status"].astype(str).str.strip().str.lower()
    if "commit" in df.columns:
        df["commit"] = df["commit"].astype(str).str.strip()
    return df


def _read_oos_log() -> pd.DataFrame:
    if not OOS_RESULTS_TSV.exists() or OOS_RESULTS_TSV.stat().st_size == 0:
        return pd.DataFrame()
    try:
        # Force text columns to string: 7-char hex commits ("6987e31")
        # otherwise parse as scientific notation and the grader can't
        # match them against _short_commit(). Same for status_hint.
        df = pd.read_csv(
            OOS_RESULTS_TSV, sep="\t",
            dtype={
                "commit": str, "status_hint": str,
                "yearly_sharpe_json": str, "fold_sharpes_json": str,
                "is_fold_sharpes_json": str, "oos_fold_sharpes_json": str,
                "oos_daily_returns_json": str,
            },
        )
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


def _parse_daily_returns(raw: object) -> pd.Series | None:
    """Deserialize a `pd.Series.to_json(orient='split')` blob into a Series,
    or return None if the blob is empty/invalid.
    """
    if raw is None:
        return None
    if isinstance(raw, float) and not math.isfinite(raw):
        return None
    s = str(raw).strip()
    if not s or s.lower() == "nan":
        return None
    try:
        ser = pd.read_json(io.StringIO(s), orient="split", typ="series")
    except (ValueError, TypeError):
        return None
    try:
        ser.index = pd.to_datetime(ser.index)
    except (ValueError, TypeError):
        return None
    return ser.astype(float)


def _baseline_values(
    results: pd.DataFrame, oos_log: pd.DataFrame
) -> dict | None:
    """Return a dict of baseline metrics drawn from the first non-crash row in
    results.tsv, joined to oos_results.tsv. Keys:
        oos_sharpe, median_fold_sharpe, is_fold_median_sharpe,
        oos_fold_median_sharpe, daily_returns (pd.Series or None)
    Returns None when no baseline exists yet (this run IS the baseline).
    """
    if results.empty or "status" not in results.columns:
        return None
    non_crash = results[results["status"] != "crash"]
    if non_crash.empty:
        return None
    seed_commit = str(non_crash.iloc[0].get("commit", "")).strip()

    out: dict = {
        "oos_sharpe": float("nan"),
        "median_fold_sharpe": float("nan"),
        "is_fold_median_sharpe": float("nan"),
        "oos_fold_median_sharpe": float("nan"),
        "daily_returns": None,
    }
    if seed_commit and not oos_log.empty and "commit" in oos_log.columns:
        matches = oos_log[oos_log["commit"] == seed_commit]
        if not matches.empty:
            r = matches.iloc[-1]
            for key in (
                "oos_sharpe", "median_fold_sharpe",
                "is_fold_median_sharpe", "oos_fold_median_sharpe",
            ):
                v = r.get(key)
                if pd.notna(v):
                    out[key] = float(v)
            out["daily_returns"] = _parse_daily_returns(r.get("oos_daily_returns_json"))
    # Fallback to results.tsv's own number if the side-channel doesn't have it.
    if not math.isfinite(out["oos_sharpe"]):
        raw = non_crash.iloc[0].get("oos_sharpe")
        try:
            out["oos_sharpe"] = float(raw)
        except (TypeError, ValueError):
            pass
    return out


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

    # Phase 1b — reject AST duplicates of any prior trial on this universe.
    # The shared cache also feeds the deflation pool below, so read it once.
    ast_hash = _strategy_ast_hash()
    cache_df = _read_cache()
    if ast_hash is not None:
        dup = _find_cache_duplicate(cache_df, ast_hash, commit)
        if dup is not None:
            dup_branch, dup_commit = dup
            universe = os.environ.get("UNIVERSE_TAG", "sp100_2024")
            print(
                f"ERROR: strategy.py AST matches prior trial {dup_commit} "
                f"(branch quant-research/{dup_branch}, universe {universe}). "
                f"Pick a genuinely different hypothesis.",
                file=sys.stderr,
            )
            return 3

    # Phase 1 — fetch OOS truth for the current commit.
    oos_log = _read_oos_log()
    oos_row = _latest_oos_row_for(commit, oos_log)

    if oos_row is None:
        # The run never reached print_summary. Crash rows are allowed free-
        # form descriptions — people often paste the stack-trace gist there.
        _append_results_row(commit, float("nan"), float("nan"), float("nan"), "crash", desc)
        if ast_hash is not None:
            _append_cache(
                ast_hash, _current_branch_tag(), commit, float("nan"), "crash"
            )
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
    is_fold_median = float(oos_row.get("is_fold_median_sharpe", float("nan")))
    oos_fold_median = float(oos_row.get("oos_fold_median_sharpe", float("nan")))
    current_returns = _parse_daily_returns(oos_row.get("oos_daily_returns_json"))

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
        base_oos = baseline["oos_sharpe"]
        base_is_fold_median = baseline["is_fold_median_sharpe"]
        base_returns = baseline["daily_returns"]

        # Pool sigma_n and N across ALL non-crash trials on this universe
        # (any branch). The shared cache is the single source of truth; the
        # multiple-hypothesis hurdle grows with the universe's cumulative
        # exploration budget, not just this branch's. Restrict the pool to
        # the current HURDLE_VERSION so a cost-model bump doesn't mix the
        # old and new regimes in the deflation.
        sigma_n = 0.0
        n_for_sr0 = 2
        if {"oos_sharpe", "status", "hurdle_version"}.issubset(cache_df.columns):
            same_version = cache_df[
                cache_df["hurdle_version"].astype(str) == str(HURDLE_VERSION)
            ]
            pool = same_version.loc[
                same_version["status"] != "crash", "oos_sharpe"
            ].dropna().to_numpy()
            pool = pool[np.isfinite(pool)]
            if pool.size >= 2:
                sigma_n = float(pool.std(ddof=1))
            n_for_sr0 = max(int(pool.size), 2)
        sr0_raw = expected_max_sharpe_null(sigma_n, n_for_sr0) if sigma_n > 0 else 0.0
        sr0 = sr0_raw if (sr0_raw is not None and math.isfinite(sr0_raw)) else 0.0
        if sr0 < 0:
            sr0 = 0.0

        hurdle = base_oos + BASELINE_HURDLE_SHARPE + sr0
        is_fold_hurdle = (
            base_is_fold_median + MIN_FOLD_MEDIAN_DELTA
            if math.isfinite(base_is_fold_median)
            else -math.inf
        )

        # Jobson-Korkie/Memmel paired Sharpe-difference test. Needs the
        # baseline's and current run's OOS daily returns. If either is
        # missing (legacy rows predating the serialized-returns column),
        # fall back to the old ci_lo > baseline rule so the gate still
        # produces a verdict.
        if current_returns is not None and base_returns is not None:
            jk_z, jk_p = jobson_korkie_memmel(current_returns, base_returns)
            jk_pass = math.isfinite(jk_p) and jk_p <= JK_MEMMEL_P_MAX
            jk_label = f"JK-Memmel p {jk_p:.4f} <= {JK_MEMMEL_P_MAX:.2f} (z {jk_z:.3f})"
        else:
            jk_pass = math.isfinite(oos_lo) and oos_lo > base_oos
            jk_label = (
                f"ci_lo {oos_lo:.4f} > baseline {base_oos:.4f} "
                "(fallback — missing OOS return series)"
            )

        checks = {
            f"oos_sharpe {oos_sharpe:.4f} > hurdle {hurdle:.4f}"
            f" (= baseline {base_oos:.4f} + {BASELINE_HURDLE_SHARPE:.2f} + sr0 {sr0:.4f})":
                oos_sharpe > hurdle,
            jk_label: jk_pass,
            f"is_fold_median_sharpe {is_fold_median:.4f} > is_fold_hurdle {is_fold_hurdle:.4f}":
                math.isfinite(is_fold_median) and is_fold_median > is_fold_hurdle,
            f"oos_fold_median_sharpe {oos_fold_median:.4f} > 0":
                math.isfinite(oos_fold_median) and oos_fold_median > 0,
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
    # Under SHOW_OOS=0, suppress the detailed gate-level reason: it leaks
    # OOS numbers (fold medians, JK p-values, Sharpe deltas) through the
    # per-gate strings. The agent sees only pass/fail; the reviewer reads
    # the full reason in the oos_results.tsv audit trail.
    show_oos = os.environ.get("SHOW_OOS", "1").strip().lower() not in {"0", "false", "no", "off"}
    if show_oos:
        print(f"grader: {status} ({reason})", file=sys.stderr)
    else:
        generic = {
            "keep": "cleared all gates",
            "discard": "one or more gates failed",
        }.get(status, "verdict")
        print(f"grader: {status} ({generic})", file=sys.stderr)
    _append_results_row(commit, oos_sharpe, max_dd, turnover, status, desc)
    if ast_hash is not None:
        _append_cache(
            ast_hash, _current_branch_tag(), commit, oos_sharpe, status
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
