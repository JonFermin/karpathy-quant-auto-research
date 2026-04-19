"""
running_best.py — small CLI for the agent to probe loop state.

Three modes:

    uv run running_best.py               # best kept oos_sharpe (to stdout)
    uv run running_best.py --trials      # rows logged this branch
    uv run running_best.py --baseline    # baseline (seed) oos_sharpe
    uv run running_best.py --verbose     # annotate --best with the winning commit

Under SHOW_OOS=0 the agent wrote 0.000000 for oos_sharpe in results.tsv;
this script joins to the harness-owned oos_results.tsv on commit so the
numbers reflect reality. Exit codes are 0 on success and 1 when the
requested quantity is not yet defined (empty file, no kept rows, etc.).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from prepare import OOS_RESULTS_TSV, REPO_ROOT

DEFAULT_PATH = REPO_ROOT / "results.tsv"


def _load_results(path: Path | str) -> pd.DataFrame | None:
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return None
    try:
        df = pd.read_csv(p, sep="\t")
    except pd.errors.EmptyDataError:
        return None
    if "status" not in df.columns or "commit" not in df.columns:
        return None
    df["status"] = df["status"].astype(str).str.strip().str.lower()
    df["commit"] = df["commit"].astype(str).str.strip()
    if "oos_sharpe" in df.columns:
        df["oos_sharpe"] = pd.to_numeric(df["oos_sharpe"], errors="coerce")
    return df


def _side_channel() -> pd.DataFrame | None:
    if not OOS_RESULTS_TSV.exists() or OOS_RESULTS_TSV.stat().st_size == 0:
        return None
    try:
        side = pd.read_csv(OOS_RESULTS_TSV, sep="\t")
    except pd.errors.EmptyDataError:
        return None
    if "commit" not in side.columns or "oos_sharpe" not in side.columns:
        return None
    side["commit"] = side["commit"].astype(str).str.strip()
    side["oos_sharpe"] = pd.to_numeric(side["oos_sharpe"], errors="coerce")
    return side


def _with_true_sharpe(df: pd.DataFrame) -> pd.DataFrame:
    """Return df with oos_sharpe taken from the side-channel when the
    TSV's own value is the SHOW_OOS=0 placeholder (0.0) or missing.
    """
    side = _side_channel()
    if side is None:
        return df
    # Keep only the most recent side-channel row per commit.
    side = side.sort_index().drop_duplicates(subset=["commit"], keep="last")
    merged = df.merge(side[["commit", "oos_sharpe"]], on="commit", how="left", suffixes=("_log", "_true"))
    merged["oos_sharpe"] = merged["oos_sharpe_true"].where(
        merged["oos_sharpe_true"].notna(), merged["oos_sharpe_log"]
    )
    # If the log had a non-zero value and side channel is missing, keep log.
    mask_zero_or_nan = merged["oos_sharpe"].fillna(0.0) == 0.0
    merged.loc[mask_zero_or_nan, "oos_sharpe"] = merged.loc[mask_zero_or_nan, "oos_sharpe_log"]
    merged = merged.drop(columns=["oos_sharpe_log", "oos_sharpe_true"])
    return merged


def running_best(path: Path | str = DEFAULT_PATH) -> tuple[float, str] | None:
    df = _load_results(path)
    if df is None:
        return None
    kept = df[df["status"] == "keep"].copy()
    if kept.empty:
        return None
    kept = _with_true_sharpe(kept).dropna(subset=["oos_sharpe"])
    if kept.empty:
        return None
    idx = kept["oos_sharpe"].idxmax()
    return float(kept.loc[idx, "oos_sharpe"]), str(kept.loc[idx, "commit"])


def trials_count(path: Path | str = DEFAULT_PATH) -> int | None:
    df = _load_results(path)
    if df is None:
        return None
    return int(len(df))


def baseline(path: Path | str = DEFAULT_PATH) -> tuple[float, str] | None:
    df = _load_results(path)
    if df is None:
        return None
    non_crash = df[df["status"] != "crash"]
    if non_crash.empty:
        return None
    seed = _with_true_sharpe(non_crash).iloc[0]
    val = seed.get("oos_sharpe")
    if pd.isna(val):
        return None
    return float(val), str(seed.get("commit", ""))


def main() -> int:
    parser = argparse.ArgumentParser(description="Report loop state from results.tsv")
    parser.add_argument("--path", default=str(DEFAULT_PATH), help="results.tsv path")
    parser.add_argument("--verbose", action="store_true", help="annotate the numeric result with the winning commit")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--trials", action="store_true", help="print the number of rows logged on this branch")
    mode.add_argument("--baseline", action="store_true", help="print the baseline (seed) oos_sharpe")
    args = parser.parse_args()

    if args.trials:
        n = trials_count(args.path)
        if n is None:
            print("no results.tsv yet", file=sys.stderr)
            return 1
        print(n)
        return 0

    if args.baseline:
        result = baseline(args.path)
        if result is None:
            print("no baseline yet", file=sys.stderr)
            return 1
        val, commit = result
        if args.verbose:
            print(f"baseline: {val:.6f}  (commit {commit})")
        else:
            print(f"{val:.6f}")
        return 0

    result = running_best(args.path)
    if result is None:
        print("no kept rows yet", file=sys.stderr)
        return 1
    best, commit = result
    if args.verbose:
        print(f"running_best: {best:.6f}  (commit {commit})")
    else:
        print(f"{best:.6f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
