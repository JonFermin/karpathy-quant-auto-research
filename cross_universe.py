"""
cross_universe.py — read-only reporting CLI that aggregates per-universe
trial caches and applies a family-wise multiple-testing correction across
universes.

For each universe-tagged trial cache at
    ~/.cache/karpathy-quant-auto-research/trial_cache_<UNIVERSE_TAG>.tsv
it computes a deflated-Sharpe probability (PSR) for that universe's best
OOS Sharpe relative to its own cohort of attempts, converts that to a
one-sided p-value (1 - PSR), then applies a Bonferroni correction across
the K universes that produced any non-crash trials.

Pure reporting: never writes to disk, never touches the caches. Missing
or malformed rows are skipped with a warning.
"""

from __future__ import annotations

import glob
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from stats import deflated_sharpe

CACHE_DIR = Path.home() / ".cache" / "karpathy-quant-auto-research"
CACHE_GLOB = "trial_cache_*.tsv"
T_OOS_DAYS = 252 * 5  # matches OOS window length (Jan 2020 – Dec 2024)
ALPHA = 0.05

EXPECTED_COLS = {
    "ast_sha256",
    "branch_tag",
    "commit",
    "oos_sharpe",
    "status",
    "hurdle_version",
    "written_at",
}


def _warn(msg: str) -> None:
    print(f"[warn] {msg}", file=sys.stderr)


def _universe_from_path(path: Path) -> str:
    # trial_cache_<UNIVERSE_TAG>.tsv -> <UNIVERSE_TAG>
    stem = path.stem  # strips .tsv
    prefix = "trial_cache_"
    if stem.startswith(prefix):
        return stem[len(prefix):]
    return stem


def _load_cache(path: Path) -> pd.DataFrame | None:
    try:
        # Force text columns to string so hex commits like "6987e31"
        # don't parse as scientific notation.
        df = pd.read_csv(
            path, sep="\t",
            dtype={
                "ast_sha256": str, "branch_tag": str,
                "commit": str, "status": str,
                "hurdle_version": str, "written_at": str,
            },
        )
    except FileNotFoundError:
        _warn(f"{path.name}: file disappeared, skipping")
        return None
    except pd.errors.EmptyDataError:
        _warn(f"{path.name}: empty file, skipping")
        return None
    except Exception as exc:  # noqa: BLE001 — be graceful per spec
        _warn(f"{path.name}: failed to read ({exc!r}), skipping")
        return None

    missing = EXPECTED_COLS - set(df.columns)
    if missing:
        _warn(f"{path.name}: missing columns {sorted(missing)}, skipping")
        return None

    # Coerce oos_sharpe to float; drop rows that fail the cast or are non-finite.
    df = df.copy()
    df["oos_sharpe"] = pd.to_numeric(df["oos_sharpe"], errors="coerce")
    bad = df["oos_sharpe"].isna() | ~np.isfinite(df["oos_sharpe"])
    if bad.any():
        _warn(f"{path.name}: dropping {int(bad.sum())} rows with non-numeric oos_sharpe")
        df = df[~bad]

    return df


def _analyze_universe(universe: str, df: pd.DataFrame) -> dict | None:
    n_trials_total = len(df)
    # Deflation cohort: finite, non-crash Sharpes only.
    live = df[df["status"].astype(str) != "crash"]
    all_sharpes = live["oos_sharpe"].to_numpy(dtype=float)
    all_sharpes = all_sharpes[np.isfinite(all_sharpes)]
    n_live = int(all_sharpes.size)

    if n_live < 2:
        _warn(f"{universe}: only {n_live} non-crash trial(s), cannot deflate")
        return None

    # Best trial = max Sharpe within the live cohort.
    best_idx = int(np.argmax(all_sharpes))
    best_sharpe = float(all_sharpes[best_idx])
    # Map back to the original row so we can pull the commit.
    live_reset = live.reset_index(drop=True)
    finite_mask = np.isfinite(live_reset["oos_sharpe"].to_numpy(dtype=float))
    live_finite = live_reset[finite_mask].reset_index(drop=True)
    best_row = live_finite.iloc[best_idx]
    best_commit = str(best_row.get("commit", ""))

    # "keeps" in the spec's table = trials the harness accepted.
    n_keeps = int((df["status"].astype(str) == "keep").sum())

    psr = deflated_sharpe(best_sharpe, all_sharpes, t_days=T_OOS_DAYS)
    if not math.isfinite(psr):
        _warn(f"{universe}: PSR non-finite (cohort too degenerate), skipping")
        return None

    p_raw = max(0.0, min(1.0, 1.0 - float(psr)))

    return {
        "universe": universe,
        "n_trials": n_trials_total,
        "n_live": n_live,
        "n_keeps": n_keeps,
        "best_sharpe": best_sharpe,
        "best_commit": best_commit,
        "psr": float(psr),
        "p_raw": p_raw,
    }


def _format_table(rows: list[dict]) -> str:
    headers = [
        "universe",
        "n_trials",
        "n_keeps",
        "best_sharpe",
        "best_commit",
        "p_raw",
        "p_adj",
        "verdict",
    ]
    table = []
    for r in rows:
        table.append([
            r["universe"],
            str(r["n_trials"]),
            str(r["n_keeps"]),
            f"{r['best_sharpe']:+.4f}",
            r["best_commit"][:10] if r["best_commit"] else "-",
            f"{r['p_raw']:.4f}",
            f"{r['p_adj']:.4f}",
            r["verdict"],
        ])

    widths = [len(h) for h in headers]
    for row in table:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt(cells: list[str]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells))

    out = [fmt(headers), fmt(["-" * w for w in widths])]
    for row in table:
        out.append(fmt(row))
    return "\n".join(out)


def main() -> int:
    if not CACHE_DIR.exists():
        print(f"No cache directory at {CACHE_DIR} — nothing to report.")
        return 0

    cache_paths = sorted(Path(p) for p in glob.glob(str(CACHE_DIR / CACHE_GLOB)))
    if not cache_paths:
        print(f"No trial caches found under {CACHE_DIR}/{CACHE_GLOB}.")
        return 0

    results: list[dict] = []
    for path in cache_paths:
        universe = _universe_from_path(path)
        df = _load_cache(path)
        if df is None or df.empty:
            _warn(f"{universe}: no usable rows, skipping")
            continue
        summary = _analyze_universe(universe, df)
        if summary is not None:
            results.append(summary)

    if not results:
        print("No universes produced a usable deflation cohort.")
        return 0

    k = len(results)
    for r in results:
        p_adj = min(1.0, r["p_raw"] * k)
        r["p_adj"] = p_adj
        r["verdict"] = "SURVIVES" if p_adj <= ALPHA else "NOT_SIGNIFICANT"

    # Stable ordering: best (lowest p_adj) first, then by universe name.
    results.sort(key=lambda r: (r["p_adj"], r["universe"]))

    print(f"Cross-universe deflated-Sharpe report (K = {k} universes, "
          f"alpha = {ALPHA:.2f}, T_oos = {T_OOS_DAYS}d)")
    print()
    print(_format_table(results))
    print()

    survivors = [r for r in results if r["verdict"] == "SURVIVES"]
    if survivors:
        names = ", ".join(r["universe"] for r in survivors)
        print(
            f"Summary: {len(survivors)} of {k} universes survive the "
            f"Bonferroni-adjusted {ALPHA:.0%} gate: {names}."
        )
    else:
        print(
            f"Summary: 0 of {k} universes survive the Bonferroni-adjusted "
            f"{ALPHA:.0%} gate."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
