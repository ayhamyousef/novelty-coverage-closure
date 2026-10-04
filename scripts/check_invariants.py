#!/usr/bin/env python3
"""
Self-checks for the things the later stages quietly depend on.

None of this touches a simulator. It checks the properties that, if they
silently broke, would make the Stage 6 comparison wrong without anything
failing visibly: that the leakage boundary actually holds, that a selection
episode does what it claims, that pools are reproducible, and that the cache
can be reused safely.

    .venv/bin/python scripts/check_invariants.py
"""

import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from simlib.fifo_coverage import build_fifo_coverage_model  # noqa: E402
from simlib.pool import Candidate, build_pool               # noqa: E402
from simlib.selection import (RandomSelector, Selector,      # noqa: E402
                              run_selection, tests_to_target)
from simlib.stimulus import StimulusSpec, generate, random_spec  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAILURES.append(name)


def main() -> int:
    print("\nleakage boundary")
    # A Candidate must carry no coverage, by any name. A selector that only
    # gets these can't see what a test would cover before picking it.
    c = Candidate(index=0, spec=StimulusSpec(), seed=1)
    fields = set(vars(c)) | set(getattr(c, "__dataclass_fields__", {}))
    leaky = {f for f in fields if any(w in f.lower()
                                      for w in ("cov", "vector", "bin", "hit", "result"))}
    check("Candidate exposes no coverage field", not leaky, f"fields={sorted(fields)}")

    # An adversarial selector tries to reach the vector table through the
    # arguments it's handed. It should find nothing.
    peeked = {}

    class Peeker(Selector):
        name = "peeker"

        def reset(self, candidates, rng):
            self._rng = rng

        def select(self, candidates, remaining, history):
            for cand in candidates:
                for attr in dir(cand):
                    if attr.startswith("_"):
                        continue
                    val = getattr(cand, attr)
                    if isinstance(val, list) and val and all(x in (0, 1) for x in val):
                        peeked["found"] = attr
            return self._rng.choice(remaining)

    pool = build_pool(gen_seed=7, size=12, cycles=40)
    rng = random.Random(0)
    vecs = {i: [1 if (i + j) % 5 == 0 else 0 for j in range(20)] for i in range(12)}
    run_selection(Peeker(), pool, vecs, 12, rng)
    check("adversarial selector finds no coverage on candidates", not peeked,
          f"found {peeked}" if peeked else "")

    print("\nselection harness")
    curve = run_selection(RandomSelector(), pool, vecs, 12, random.Random(1))
    check("curve is non-decreasing", all(b >= a for a, b in zip(curve, curve[1:])))
    union = [0] * 20
    for v in vecs.values():
        for j, b in enumerate(v):
            if b:
                union[j] = 1
    check("running the whole pool reaches the pool's union coverage",
          curve[-1] == sum(union), f"{curve[-1]} vs {sum(union)}")
    check("one entry per test", len(curve) == 12, f"len={len(curve)}")
    check("never asks for more tests than the pool has",
          len(run_selection(RandomSelector(), pool, vecs, 999, random.Random(1))) == 12)

    # tests_to_target against a brute-force scan
    tgt = max(1, curve[-1] - 1)
    brute = next((i for i, c_ in enumerate(curve, 1) if c_ >= tgt), None)
    check("tests_to_target matches a brute-force scan",
          tests_to_target(curve, tgt) == brute, f"{tests_to_target(curve, tgt)} vs {brute}")
    check("tests_to_target reports None for an unreachable target",
          tests_to_target(curve, 10 ** 6) is None)

    # No candidate may be simulated twice in one episode.
    picks = []

    class Recorder(RandomSelector):
        def select(self, candidates, remaining, history):
            p = super().select(candidates, remaining, history)
            picks.append(p)
            return p

    run_selection(Recorder(), pool, vecs, 12, random.Random(3))
    check("no candidate is selected twice", len(picks) == len(set(picks)))

    print("\npools and caching")
    a = build_pool(gen_seed=42, size=50, cycles=120)
    b = build_pool(gen_seed=42, size=50, cycles=120)
    check("same generator seed gives the same pool",
          [x.spec for x in a] == [x.spec for x in b])
    big = build_pool(gen_seed=42, size=200, cycles=120)
    check("a bigger pool extends the smaller one rather than reshuffling it",
          [x.spec for x in big[:50]] == [x.spec for x in a]
          and [x.seed for x in big[:50]] == [x.seed for x in a])
    seeds = {x.seed for p in (0, 1, 2, 3, 4) for x in build_pool(1000 + p, 400, 120)}
    check("candidate seeds never collide across pools", len(seeds) == 5 * 400,
          f"{len(seeds)} unique of {5 * 400}")

    print("\nstimulus and model")
    spec = StimulusSpec(cycles=77, p_wr=0.3, p_rd=0.4)
    check("generator emits exactly the requested cycle count",
          len(list(generate(spec, 1))) == 77)
    check("same seed gives the same stimulus",
          list(generate(spec, 5)) == list(generate(spec, 5)))
    check("different seeds give different stimulus",
          list(generate(spec, 5)) != list(generate(spec, 6)))
    m = build_fifo_coverage_model(32)
    check("coverage model is 156 bins", m.n_bins == 156, f"n_bins={m.n_bins}")
    check("bin names are unique", len(set(m.bin_names)) == m.n_bins)
    r1 = [random_spec(random.Random(9)) for _ in range(5)]
    r2 = [random_spec(random.Random(9)) for _ in range(5)]
    check("random_spec is reproducible from its rng", r1 == r2)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        return 1
    print("all invariants hold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
