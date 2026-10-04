"""Test selection strategies, and the harness that runs them.

The leakage boundary lives here, so it's worth being explicit about.

Stage 7 asks whether the novelty score is getting information from coverage
results, directly or indirectly. If it is, then the project did coverage-directed
selection, which is a real technique but an easier claim than novelty-based
selection. Rather than answer that by reading the code afterwards, the
interface makes it structural:

  * A selector is handed Candidate objects, which carry a stimulus spec and a
    seed and no coverage at all.
  * It's handed the coverage of tests already simulated, since that's what
    the real setting gives you for free.
  * It is never handed, and has no route to, the coverage of a candidate it
    has not picked yet.

run_selection() is the only thing holding the full vector table. A selector
that stays inside this interface can't see the future, whatever it does
internally. A selector that needs more than this is doing something else, and
the README has to say so.
"""

from __future__ import annotations

import random
from typing import Dict, List, Sequence, Tuple

from simlib.pool import Candidate

History = List[Tuple[int, List[int]]]   # (candidate index, its coverage vector)


class Selector:
    """Base class. Subclasses override select()."""

    name = "base"

    def reset(self, candidates: Sequence[Candidate], rng: random.Random) -> None:
        """Called once before a run. Specs only, no coverage."""

    def select(self, candidates: Sequence[Candidate], remaining: List[int],
               history: History) -> int:
        raise NotImplementedError


class RandomSelector(Selector):
    """Pick uniformly at random from what's left.

    This is the baseline the whole project is measured against, so it's worth
    saying what it isn't. It isn't weak random: the candidates it draws from
    come from the full constrained-random parameter space, bursts and resets
    included. Stage 2 showed that a generator without those knobs can't reach
    the top of the FIFO at all, and beating that version would prove nothing.
    """

    name = "random"

    def reset(self, candidates, rng):
        self._rng = rng

    def select(self, candidates, remaining, history):
        return self._rng.choice(remaining)


def run_selection(selector: Selector, candidates: Sequence[Candidate],
                  vectors: Dict[int, List[int]], n_tests: int,
                  rng: random.Random) -> List[int]:
    """Run one selection episode. Returns the cumulative coverage curve.

    curve[i] is how many bins were covered after i+1 tests had been simulated.
    `vectors` never leaves this function.
    """
    n_bins = len(next(iter(vectors.values())))
    covered = [0] * n_bins
    remaining = list(range(len(candidates)))
    history: History = []
    curve: List[int] = []

    selector.reset(candidates, rng)
    for _ in range(min(n_tests, len(remaining))):
        pick = selector.select(candidates, remaining, history)
        remaining.remove(pick)
        vec = vectors[pick]
        for j, b in enumerate(vec):
            if b:
                covered[j] = 1
        history.append((pick, vec))
        curve.append(sum(covered))
    return curve


def tests_to_target(curve: Sequence[int], target_bins: int) -> int | None:
    """How many tests until the curve first reaches target_bins. None if never."""
    for i, c in enumerate(curve, 1):
        if c >= target_bins:
            return i
    return None
