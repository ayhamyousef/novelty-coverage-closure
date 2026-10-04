#!/usr/bin/env python3
"""
Stage 4: the constrained-random baseline.

Builds candidate pools, simulates each candidate once, then measures how many
tests random selection needs to reach each coverage target. This curve is what
Stages 5 and 6 are measured against, so the aim is a baseline that's as strong
as it reasonably can be, not one that's easy to beat.

What makes it a fair baseline:
  * candidates come from the full constrained-random parameter space, bursts
    and resets included. Stage 2 showed a generator without those can't reach
    the top of the FIFO at all, so leaving them out would rig the comparison.
  * the parameter ranges were fixed in Stage 3, before any curve was measured,
    and haven't been touched since.
  * variance is sampled over both the candidate set and the ordering, not just
    the ordering.

Cost: 5 pools x 400 candidates = 2000 simulations at roughly 350 ms each, so
about 12 minutes the first time. Results are cached by spec and seed, so a
rerun is free and Stages 5 and 6 pay nothing.

Usage:
    .venv/bin/python scripts/04_random_baseline.py
    .venv/bin/python scripts/04_random_baseline.py --pools 2 --pool-size 100
"""

import argparse
import json
import math
import random
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from simlib.fifo_coverage import build_fifo_coverage_model    # noqa: E402
from simlib.pool import build_pool, simulate_pool            # noqa: E402
from simlib.selection import (RandomSelector, run_selection,  # noqa: E402
                              tests_to_target)
from simlib.stimulus import TEST_CYCLES                      # noqa: E402

LGFLEN = 5
DEPTH = 1 << LGFLEN
# Taken from the model rather than written down here. The targets are a
# percentage of this, so a hardcoded copy that drifted from the real model
# would move every target silently.
N_BINS = build_fifo_coverage_model(DEPTH).n_bins
TARGET_PCTS = (90, 95, 98, 99, 100)
HEADLINE_PCT = 99

# Palette, light mode, from the data-viz reference instance.
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_MUTED = "#52514e"
SERIES_1 = "#2a78d6"
GRID = "#e3e2dd"


def target_bins(pct: int) -> int:
    return math.ceil(pct / 100 * N_BINS)


def plot_curve(curves, out_path: Path, max_tests: int):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    full = min(len(c) for c in curves)
    mean_full = [statistics.fmean(c[i] for c in curves) for i in range(full)]
    # Focus on where the curve is still moving. Most of a 200-test run is a
    # flat plateau and showing all of it just shrinks the part that matters.
    closed = next((i for i, m in enumerate(mean_full, 1) if m >= N_BINS - 0.5), full)
    n = min(full, max(40, int(closed * 1.25)))
    xs = list(range(1, n + 1))
    mean = [statistics.fmean(c[i] for c in curves) for i in range(n)]
    lo = [min(c[i] for c in curves) for i in range(n)]
    hi = [max(c[i] for c in curves) for i in range(n)]

    fig, ax = plt.subplots(figsize=(8, 4.8), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    # Targets are reference lines, not data, so they stay in muted ink rather
    # than taking a series colour. Only two are drawn: 153, 155 and 156 sit
    # within three bins of each other and their labels collide, so the rest of
    # the ladder is reported in the table instead of crowding the chart.
    for pct, weight in ((90, 0.45), (HEADLINE_PCT, 0.8)):
        tb = target_bins(pct)
        ax.axhline(tb, color=INK_MUTED, lw=0.9, ls=(0, (4, 3)), alpha=weight, zorder=1)
        ax.text(n * 0.995, tb + 1.2, f"{pct}% target, {tb} bins",
                ha="right", va="bottom", fontsize=8, color=INK_MUTED)

    ax.fill_between(xs, lo, hi, color=SERIES_1, alpha=0.16, lw=0, zorder=2)
    ax.plot(xs, mean, color=SERIES_1, lw=2.0, zorder=3, solid_capstyle="round")

    # One series, so no legend box. Label the line directly instead, and say
    # what the band is: an unlabelled band could be a standard deviation, a
    # confidence interval or the full range, and they mean different things.
    ax.annotate(f"random selection\nmean of {len(curves)} runs\n"
                f"band is min to max",
                xy=(n * 0.62, mean[int(n * 0.62) - 1]),
                xytext=(n * 0.42, N_BINS * 0.50),
                fontsize=8.5, color=INK,
                arrowprops=dict(arrowstyle="-", color=INK_MUTED, lw=0.8))

    ax.set_xlabel("simulations run", fontsize=9, color=INK_MUTED)
    ax.set_ylabel("coverage bins hit, of 156", fontsize=9, color=INK_MUTED)
    ax.set_title("Functional coverage closure, constrained-random selection",
                 fontsize=11, color=INK, loc="left", pad=10)
    ax.set_xlim(1, n)
    ax.set_ylim(0, N_BINS + 6)
    ax.grid(axis="y", color=GRID, lw=0.7, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_MUTED, labelsize=8.5)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_path, facecolor=SURFACE)
    plt.close(fig)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", default="verilator", choices=["verilator", "icarus"])
    ap.add_argument("--pools", type=int, default=5)
    ap.add_argument("--pool-size", type=int, default=400)
    ap.add_argument("--orders", type=int, default=10)
    ap.add_argument("--max-tests", type=int, default=200,
                    help="how far along the curve to run each episode")
    args = ap.parse_args()

    out_dir = REPO / "results" / "stage4"
    out_dir.mkdir(parents=True, exist_ok=True)

    curves, per_run = [], []
    for pool_i in range(args.pools):
        gen_seed = 1000 + pool_i
        pool = build_pool(gen_seed=gen_seed, size=args.pool_size, cycles=TEST_CYCLES)
        print(f"[stage4] pool {pool_i + 1}/{args.pools} "
              f"(gen_seed={gen_seed}, {args.pool_size} candidates) ...", flush=True)
        vectors = simulate_pool(pool, repo=REPO, sim=args.sim, lgflen=LGFLEN,
                                cache_path=out_dir / f"pool_{gen_seed}_cache.json")
        reachable = [0] * N_BINS
        for v in vectors.values():
            for j, b in enumerate(v):
                if b:
                    reachable[j] = 1
        print(f"           pool can reach {sum(reachable)}/{N_BINS} bins", flush=True)

        for order_i in range(args.orders):
            rng = random.Random(gen_seed * 1000 + order_i)
            curve = run_selection(RandomSelector(), pool, vectors,
                                  args.max_tests, rng)
            curves.append(curve)
            per_run.append({
                "pool_gen_seed": gen_seed, "order_seed": order_i,
                "curve": curve,
                "tests_to": {str(p): tests_to_target(curve, target_bins(p))
                             for p in TARGET_PCTS},
            })

    # ---- aggregate -------------------------------------------------------
    print(f"\n{'target':>8} {'bins':>5} {'reached':>9} {'mean tests':>12} "
          f"{'std':>7} {'min':>5} {'max':>5}")
    print("-" * 56)
    summary = {}
    for pct in TARGET_PCTS:
        vals = [r["tests_to"][str(pct)] for r in per_run]
        hit = [v for v in vals if v is not None]
        if hit:
            mean = statistics.fmean(hit)
            sd = statistics.stdev(hit) if len(hit) > 1 else 0.0
            print(f"{pct:>7}% {target_bins(pct):>5} {len(hit):>5}/{len(vals):<3} "
                  f"{mean:>12.1f} {sd:>7.1f} {min(hit):>5} {max(hit):>5}")
            summary[str(pct)] = {"bins": target_bins(pct), "reached": len(hit),
                                 "of": len(vals), "mean": mean, "stdev": sd,
                                 "min": min(hit), "max": max(hit)}
        else:
            print(f"{pct:>7}% {target_bins(pct):>5} {0:>5}/{len(vals):<3} "
                  f"{'never':>12}")
            summary[str(pct)] = {"bins": target_bins(pct), "reached": 0,
                                 "of": len(vals)}

    plot_path = out_dir / "coverage_curve.png"
    plot_curve(curves, plot_path, args.max_tests)
    print(f"\nplot: {plot_path.relative_to(REPO)}")

    with open(out_dir / "baseline.json", "w") as fh:
        json.dump({
            "config": {"sim": args.sim, "pools": args.pools,
                       "pool_size": args.pool_size, "orders": args.orders,
                       "max_tests": args.max_tests, "cycles": TEST_CYCLES,
                       "lgflen": LGFLEN, "n_bins": N_BINS},
            "targets": {str(p): target_bins(p) for p in TARGET_PCTS},
            "headline_pct": HEADLINE_PCT,
            "summary": summary,
            "runs": per_run,
        }, fh)
    print(f"raw data: {(out_dir / 'baseline.json').relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
