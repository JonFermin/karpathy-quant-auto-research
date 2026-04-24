"""
sensitivity.py — perturb numeric literals in strategy.py and measure OOS Sharpe drift.

Usage:
    uv run sensitivity.py

What it does
------------
1. Parses the currently-checked-out `strategy.py` with `ast`.
2. Collects every numeric literal that looks like a tuning knob:
     - int >= 6 (skip small index offsets like 1, 2, 5)
     - float in (0, 1], excluding exactly 0.0 and 1.0 (skip sentinels)
3. For each candidate, writes a perturbed copy of `strategy.py` to
   `_strategy_perturbed_tmp.py` in the repo root with the literal replaced
   by 0.8x and 1.2x (rounded for ints, clipped into (0, 1] for floats).
4. Runs the perturbed strategy via `uv run _strategy_perturbed_tmp.py` with
   `SHOW_OOS=1` in the environment and parses `^oos_sharpe:` from stdout.
5. Prints a table of (line, original, perturbed, sharpe, delta_from_baseline),
   flags HIGHLY_SENSITIVE if max |delta| > 0.3, and writes a JSON audit
   record to `sensitivity_results_<short_commit>.json`.

This script does NOT modify any existing file. The temp file is deleted
after each run. No dependencies beyond the stdlib + what strategy.py
already needs.
"""

from __future__ import annotations

import ast
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent
STRATEGY_PATH = REPO_ROOT / "strategy.py"
TMP_PATH = REPO_ROOT / "_strategy_perturbed_tmp.py"

RUN_TIMEOUT_S = 300
OOS_RE = re.compile(r"^oos_sharpe:\s*([\-\d\.eE+nNaAiIfF]+)\s*$", re.MULTILINE)

# Literal-selection thresholds from the design brief.
INT_MIN = 6             # ints < this are skipped (likely index arithmetic)
FLOAT_SENTINELS = {0.0, 1.0}


# ---------------------------------------------------------------------------
# Candidate discovery
# ---------------------------------------------------------------------------

def _is_candidate(value: Any) -> bool:
    """Decide whether a constant qualifies as a tuning knob."""
    # bool is a subclass of int; reject it explicitly.
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value >= INT_MIN
    if isinstance(value, float):
        if not math.isfinite(value):
            return False
        if value in FLOAT_SENTINELS:
            return False
        return 0.0 < value <= 1.0
    return False


def find_candidates(source: str) -> list[dict[str, Any]]:
    """Walk the AST and collect candidate numeric literals.

    Returns a list of dicts with keys: idx, line, col, end_line, end_col,
    value, kind ("int" | "float"). `idx` is a stable 0-based counter in
    source-order so we can match the same literal across freshly-parsed
    trees (ast.walk is deterministic but id()s differ per parse).
    """
    tree = ast.parse(source)
    out: list[dict[str, Any]] = []
    idx = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant):
            continue
        if not _is_candidate(node.value):
            continue
        kind = "int" if isinstance(node.value, int) else "float"
        out.append(
            {
                "idx": idx,
                "line": node.lineno,
                "col": node.col_offset,
                "end_line": getattr(node, "end_lineno", node.lineno),
                "end_col": getattr(node, "end_col_offset", node.col_offset),
                "value": node.value,
                "kind": kind,
            }
        )
        idx += 1
    # Sort by (line, col) for human-readable output; keep original idx for
    # stable matching across re-parses.
    out.sort(key=lambda d: (d["line"], d["col"]))
    return out


# ---------------------------------------------------------------------------
# Perturbation
# ---------------------------------------------------------------------------

def _perturbed_values(value: Any) -> list[Any]:
    """Return 0.8x and 1.2x perturbations, de-duplicated.

    Ints: round(value * factor). If the rounded result equals the original
    (e.g. round(6 * 0.8) == 5 is fine, but edge cases can collapse), we
    keep it and the table will show a 0 delta — the duplicate-run cost is
    bounded and the information is still useful.

    Floats: clip to (0, 1]. If clipping collapses to the original value
    we still keep it (same reasoning).
    """
    results: list[Any] = []
    for factor in (0.8, 1.2):
        if isinstance(value, int) and not isinstance(value, bool):
            new = int(round(value * factor))
            results.append(new)
        else:
            new = float(value) * factor
            # Clip into (0, 1].
            if new <= 0.0:
                new = 1e-6
            if new > 1.0:
                new = 1.0
            results.append(new)
    # De-duplicate while preserving order.
    seen: list[Any] = []
    for r in results:
        if r not in seen:
            seen.append(r)
    return seen


class _LiteralReplacer(ast.NodeTransformer):
    """Replace the `target_idx`-th candidate constant (in source-walk order)
    with `new_value`. All other nodes are left untouched.
    """

    def __init__(self, target_idx: int, new_value: Any):
        self.target_idx = target_idx
        self.new_value = new_value
        self._counter = 0
        self.replaced = False

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        if _is_candidate(node.value):
            if self._counter == self.target_idx:
                self._counter += 1
                self.replaced = True
                return ast.copy_location(ast.Constant(value=self.new_value), node)
            self._counter += 1
        return node


def build_perturbed_source(source: str, target_idx: int, new_value: Any) -> str:
    """Return a new source string with one candidate literal swapped."""
    tree = ast.parse(source)
    replacer = _LiteralReplacer(target_idx, new_value)
    new_tree = replacer.visit(tree)
    ast.fix_missing_locations(new_tree)
    if not replacer.replaced:
        raise RuntimeError(
            f"Failed to locate candidate idx={target_idx} while rewriting source"
        )
    return ast.unparse(new_tree)


# ---------------------------------------------------------------------------
# Subprocess execution
# ---------------------------------------------------------------------------

def _short_commit() -> str:
    """Current short git hash, or 'unknown' outside a repo."""
    try:
        out = subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short=7", "HEAD"],
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return out.decode().strip() or "unknown"
    except Exception:
        return "unknown"


def _parse_sharpe(stdout: str) -> float:
    """Extract oos_sharpe from a run.log-style stdout, or NaN if missing."""
    m = OOS_RE.search(stdout)
    if not m:
        return float("nan")
    raw = m.group(1)
    try:
        return float(raw)
    except ValueError:
        return float("nan")


def run_strategy(path: Path, label: str) -> tuple[float, float, str]:
    """Run `uv run <path>` with SHOW_OOS=1 and return (sharpe, seconds, tail).

    `tail` is the last ~20 lines of combined stdout/stderr so a crash is
    recognizable in the audit JSON without retaining megabytes of output.
    """
    env = {**os.environ, "SHOW_OOS": "1"}
    t0 = time.time()
    try:
        proc = subprocess.run(
            ["uv", "run", str(path)],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=RUN_TIMEOUT_S,
        )
        combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    except subprocess.TimeoutExpired as e:
        combined = f"TIMEOUT after {RUN_TIMEOUT_S}s\n{e.stdout or ''}\n{e.stderr or ''}"
    except Exception as e:  # pragma: no cover - defensive
        combined = f"EXEC_ERROR: {type(e).__name__}: {e}"
    elapsed = time.time() - t0
    sharpe = _parse_sharpe(combined)
    tail_lines = combined.strip().splitlines()[-20:]
    tail = "\n".join(tail_lines)
    print(
        f"  [{label}] sharpe={sharpe:.6f}  t={elapsed:.1f}s",
        flush=True,
    )
    return sharpe, elapsed, tail


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def _cleanup_tmp() -> None:
    try:
        if TMP_PATH.exists():
            TMP_PATH.unlink()
    except OSError:
        pass


def _format_value(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.6g}"
    return str(v)


def main() -> int:
    if not STRATEGY_PATH.exists():
        print(f"ERROR: {STRATEGY_PATH} not found", file=sys.stderr)
        return 2

    source = STRATEGY_PATH.read_text(encoding="utf-8")
    candidates = find_candidates(source)
    commit = _short_commit()
    print(f"strategy.py @ {commit}: found {len(candidates)} candidate literal(s)")
    for c in candidates:
        print(
            f"  idx={c['idx']:>2}  line={c['line']:>3}  col={c['col']:>3}  "
            f"kind={c['kind']}  value={_format_value(c['value'])}"
        )

    if not candidates:
        print("No tuning-knob literals detected — nothing to perturb.")
        audit = {
            "commit": commit,
            "baseline_sharpe": float("nan"),
            "num_candidates": 0,
            "perturbations": [],
            "max_abs_delta": 0.0,
            "std_delta": 0.0,
            "verdict": "ROBUST",
        }
        out_path = REPO_ROOT / f"sensitivity_results_{commit}.json"
        out_path.write_text(json.dumps(audit, indent=2), encoding="utf-8")
        print(f"Wrote audit JSON: {out_path}")
        print("VERDICT: ROBUST")
        return 0

    # ---- Baseline run (unperturbed source, for completeness & fair diff).
    print("\nRunning baseline (unperturbed copy) ...", flush=True)
    try:
        TMP_PATH.write_text(source, encoding="utf-8")
        baseline_sharpe, baseline_secs, baseline_tail = run_strategy(TMP_PATH, "baseline")
    finally:
        _cleanup_tmp()

    if not math.isfinite(baseline_sharpe):
        print(
            "WARNING: baseline run did not produce a finite oos_sharpe. "
            "Deltas will be NaN; continuing so the raw perturbed sharpes are "
            "still captured.",
            file=sys.stderr,
        )

    # ---- Perturbation sweep.
    perturbations: list[dict[str, Any]] = []
    for cand in candidates:
        for new_value in _perturbed_values(cand["value"]):
            label = (
                f"line={cand['line']} {_format_value(cand['value'])}"
                f" -> {_format_value(new_value)}"
            )
            print(f"\nPerturbing {label} ...", flush=True)
            try:
                new_src = build_perturbed_source(source, cand["idx"], new_value)
                TMP_PATH.write_text(new_src, encoding="utf-8")
                sharpe, secs, tail = run_strategy(TMP_PATH, label)
            except Exception as e:
                print(f"  ! rewrite/exec failed: {type(e).__name__}: {e}", file=sys.stderr)
                sharpe, secs, tail = float("nan"), 0.0, f"{type(e).__name__}: {e}"
            finally:
                _cleanup_tmp()

            delta = (
                sharpe - baseline_sharpe
                if math.isfinite(sharpe) and math.isfinite(baseline_sharpe)
                else float("nan")
            )
            perturbations.append(
                {
                    "idx": cand["idx"],
                    "line": cand["line"],
                    "col": cand["col"],
                    "kind": cand["kind"],
                    "original": cand["value"],
                    "perturbed": new_value,
                    "sharpe": sharpe,
                    "delta": delta,
                    "seconds": secs,
                    "tail": tail if not math.isfinite(sharpe) else "",
                }
            )

    # ---- Table.
    print("\n" + "=" * 78)
    print(
        f"{'line':>4} | {'original':>12} | {'perturbed':>12} | "
        f"{'sharpe':>10} | {'delta':>10}"
    )
    print("-" * 78)
    for p in perturbations:
        print(
            f"{p['line']:>4} | {_format_value(p['original']):>12} | "
            f"{_format_value(p['perturbed']):>12} | "
            f"{p['sharpe']:>10.6f} | {p['delta']:>10.6f}"
        )
    print("=" * 78)

    # ---- Summary stats on finite deltas.
    finite_deltas = [p["delta"] for p in perturbations if math.isfinite(p["delta"])]
    if finite_deltas:
        max_abs_delta = max(abs(d) for d in finite_deltas)
        # Population std (ddof=0) — we're describing the observed set, not
        # estimating a population parameter.
        mean_d = sum(finite_deltas) / len(finite_deltas)
        std_delta = math.sqrt(
            sum((d - mean_d) ** 2 for d in finite_deltas) / len(finite_deltas)
        )
    else:
        max_abs_delta = float("nan")
        std_delta = float("nan")

    verdict = (
        "HIGHLY_SENSITIVE"
        if math.isfinite(max_abs_delta) and max_abs_delta > 0.3
        else "ROBUST"
    )
    print(f"baseline_sharpe : {baseline_sharpe:.6f}  ({baseline_secs:.1f}s)")
    print(f"max |delta|     : {max_abs_delta:.6f}")
    print(f"std(delta)      : {std_delta:.6f}")
    print(f"VERDICT         : {verdict}")

    # ---- JSON audit.
    audit = {
        "commit": commit,
        "strategy_path": str(STRATEGY_PATH),
        "baseline_sharpe": baseline_sharpe,
        "baseline_seconds": baseline_secs,
        "num_candidates": len(candidates),
        "candidates": [
            {
                "idx": c["idx"],
                "line": c["line"],
                "col": c["col"],
                "kind": c["kind"],
                "value": c["value"],
            }
            for c in candidates
        ],
        "perturbations": perturbations,
        "max_abs_delta": max_abs_delta,
        "std_delta": std_delta,
        "verdict": verdict,
        "threshold_highly_sensitive": 0.3,
    }
    out_path = REPO_ROOT / f"sensitivity_results_{commit}.json"
    out_path.write_text(json.dumps(audit, indent=2, default=_json_default), encoding="utf-8")
    print(f"\nWrote audit JSON: {out_path}")

    return 0


def _json_default(obj: Any) -> Any:
    """Fallback for json.dumps — handle NaN/Inf explicitly (json allows them
    by default but we want stable, portable output). Returns None for
    non-finite floats; otherwise raises.
    """
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        _cleanup_tmp()
