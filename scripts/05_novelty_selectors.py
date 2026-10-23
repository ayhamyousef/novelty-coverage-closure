#!/usr/bin/env python3
"""
Stage 5: the novelty selectors, and a first look at whether they do anything.

Three selectors, built in increasing order of machinery, all scoring candidates
on stimulus alone:

    feature-distance     distance in a hand-built 7-number feature space
    gru-reconstruction   GRU autoencoder, novelty = reconstruction error
    gru-kmeans           GRU autoencoder, novelty = distance to the nearest
                         k-means centroid in the latent space

random-batch is the same uniform random selection Stage 4 measured, run through
the identical batching protocol so every arm opens with the same seed batch and
only diverges once scoring starts.

No simulation happens here. Stage 4 already simulated every candidate and cached
the vectors, so this replays selection orders over that cache. The only cost is
fitting the autoencoder, which happens once per batch.

Usage:
    .venv/bin/python scripts/05_novelty_selectors.py              # 25 episodes each
    .venv/bin/python scripts/05_novelty_selectors.py --orders 2   # quicker
"""

import argparse
import json
import math
import statistics as st
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import random  # noqa: E402

from simlib.fifo_coverage import build_fifo_coverage_model  # noqa: E402
from simlib.novelty import (FeatureDistanceSelector,         # noqa: E402
                            FeatureKNNSelector,
                            GRUKMeansSelector,
                            GRUReconstructionSelector,
                            RandomBatchSelector)
from simlib.pool import build_pool                           # noqa: E402
from simlib.selection import run_selection, tests_to_target  # noqa: E402
from simlib.stimulus import TEST_CYCLES                      # noqa: E402

LGFLEN = 5
N_BINS = build_fifo_coverage_model(1 << LGFLEN).n_bins
TARGET_PCTS = (90, 95, 98, 99, 100)
HEADLINE_PCT = 99
POOL_SEEDS = (1000, 1001, 1002, 1003, 1004)

ARMS = {
    "random-batch": lambda: RandomBatchSelector(),
    "feature-distance": lambda: FeatureDistanceSelector(),
    "feature-knn": lambda: FeatureKNNSelector(),
    "gru-reconstruction": lambda: GRUReconstructionSelector(epochs=30),
    "gru-kmeans": lambda: GRUKMeansSelector(epochs=30),
}


def target_bins(pct: int) -> int:
    return math.ceil(pct / 100 * N_BINS)


def load_pool(gen_seed: int, size: int):
    cache_path = REPO / "results" / "stage4" / f"pool_{gen_seed}_cache.json"
    if not cache_path.exists():
        raise SystemExit(f"missing {cache_path}. Run scripts/04_random_baseline.py first.")
    cache = json.load(open(cache_path))["vectors"]
    pool = build_pool(gen_seed=gen_seed, size=size, cycles=TEST_CYCLES)
    vectors = {}
    for c in pool:
        key = json.dumps({"spec": c.spec.to_dict(), "seed": c.seed}, sort_keys=True)
        if key not in cache:
            raise SystemExit(f"pool {gen_seed} candidate {c.index} not in cache")
        vectors[c.index] = cache[key]
    return pool, vectors


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool-size", type=int, default=400)
    ap.add_argument("--orders", type=int, default=5)
    ap.add_argument("--max-tests", type=int, default=200)
    args = ap.parse_args()

    out_dir = REPO / "results" / "stage5"
    out_dir.mkdir(parents=True, exist_ok=True)

    results = {name: [] for name in ARMS}
    for gen_seed in POOL_SEEDS:
        pool, vectors = load_pool(gen_seed, args.pool_size)
        for order_i in range(args.orders):
            seed = gen_seed * 1000 + order_i
            for name, make in ARMS.items():
                t0 = time.time()
                curve = run_selection(make(), pool, vectors, args.max_tests,
                                      random.Random(seed))
                results[name].append({
                    "pool": gen_seed, "order": order_i, "seed": seed,
                    "curve": curve, "secs": round(time.time() - t0, 2),
                    "tests_to": {str(p): tests_to_target(curve, target_bins(p))
                                 for p in TARGET_PCTS},
                })
        print(f"[stage5] pool {gen_seed} done "
              f"({args.orders} orderings x {len(ARMS)} arms)", flush=True)

    # ---- report ----------------------------------------------------------
    base = "random-batch"
    n = len(results[base])
    print(f"\n{n} episodes per arm, paired on pool and ordering seed\n")
    print(f"{'target':>7}  " + "  ".join(f"{a:>19}" for a in ARMS))
    print("-" * (9 + 21 * len(ARMS)))
    summary = {}
    for pct in TARGET_PCTS:
        cells, row = [], {}
        for name in ARMS:
            v = [r["tests_to"][str(pct)] for r in results[name]]
            if any(x is None for x in v):
                cells.append(f"{'not always reached':>19}")
                row[name] = None
                continue
            cells.append(f"{st.fmean(v):>10.1f} +/-{st.stdev(v):>6.1f}")
            row[name] = {"mean": st.fmean(v), "stdev": st.stdev(v),
                         "median": st.median(v), "values": v}
        print(f"{str(pct) + '%':>7}  " + "  ".join(cells))
        summary[str(pct)] = row

    # paired differences against the random baseline
    print(f"\npaired against {base}, at the {HEADLINE_PCT}% headline target")
    print(f"{'arm':>20} {'mean diff':>10} {'sd of diff':>11} "
          f"{'95% CI of mean':>20} {'wins':>8}")
    print("-" * 75)
    bv = [r["tests_to"][str(HEADLINE_PCT)] for r in results[base]]
    paired = {}
    for name in ARMS:
        if name == base:
            continue
        av = [r["tests_to"][str(HEADLINE_PCT)] for r in results[name]]
        if any(x is None for x in av) or any(x is None for x in bv):
            print(f"{name:>20} {'target not always reached':>50}")
            continue
        d = [a - b for a, b in zip(av, bv)]           # negative = fewer tests
        m, sd = st.fmean(d), st.stdev(d)
        sem = sd / len(d) ** 0.5
        lo, hi = m - 1.96 * sem, m + 1.96 * sem
        wins = sum(1 for x in d if x < 0)
        pct_change = 100 * m / st.fmean(bv)
        print(f"{name:>20} {m:>10.1f} {sd:>11.1f} "
              f"{f'[{lo:+.1f}, {hi:+.1f}]':>20} {f'{wins}/{len(d)}':>8}"
              f"   ({pct_change:+.1f}%)")
        paired[name] = {"mean_diff": m, "sd_diff": sd, "ci95": [lo, hi],
                        "wins": wins, "n": len(d), "pct_change": pct_change,
                        "diffs": d}

    secs = {n_: round(sum(r["secs"] for r in rs), 1) for n_, rs in results.items()}
    print(f"\ncompute per arm, seconds: {secs}")

    with open(out_dir / "selectors.json", "w") as fh:
        json.dump({"config": {"pool_size": args.pool_size, "orders": args.orders,
                              "max_tests": args.max_tests, "pools": list(POOL_SEEDS),
                              "n_bins": N_BINS, "episodes_per_arm": n},
                   "targets": {str(p): target_bins(p) for p in TARGET_PCTS},
                   "summary": summary, "paired_vs_random": paired,
                   "runs": results}, fh)
    print(f"raw data: {(out_dir / 'selectors.json').relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
