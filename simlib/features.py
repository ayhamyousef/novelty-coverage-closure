"""Two ways of representing a candidate test, both derived from the stimulus only.

Neither of these touches a coverage result, and that's the point. Novelty-based
selection scores a candidate by how unlike the already-simulated stimuli it is.
If the score used coverage instead, the project would be doing coverage-directed
selection, which is a real technique but an easier claim. See simlib/selection.py
for how the interface keeps that honest.

Both representations are computable for a candidate that has never been
simulated, because a stimulus spec plus a seed fully determines the waveform
that would be driven.

    feature_vector(spec)   the hand-built one, 7 numbers off the spec
    sequence(spec, seed)   the temporal one, cycles x 3 of driven pin values
"""

from __future__ import annotations

from typing import List, Sequence

from simlib.stimulus import (BURST_LEN_CHOICES, P_RESET_CHOICES, StimulusSpec,
                             generate)

MAX_BURST = float(max(BURST_LEN_CHOICES))
MAX_P_RESET = float(max(P_RESET_CHOICES))

FEATURE_NAMES = ("p_wr", "p_rd", "p_burst", "wr_burst", "rd_burst", "p_reset",
                 "net_drift")


def feature_vector(spec: StimulusSpec) -> List[float]:
    """The simplest thing that could work: the knobs, scaled to roughly 0 to 1.

    net_drift is the only derived one. It's p_wr minus p_rd, which is what
    decides whether the FIFO tends to fill or drain, and Stage 2 showed that's
    the single thing that determines whether the interesting states are
    reachable at all. Everything else is just the knob value.
    """
    return [
        spec.p_wr,
        spec.p_rd,
        spec.p_burst / max(0.10, 1e-9),
        spec.wr_burst / MAX_BURST,
        spec.rd_burst / MAX_BURST,
        spec.p_reset / MAX_P_RESET if MAX_P_RESET else 0.0,
        (spec.p_wr - spec.p_rd + 1.0) / 2.0,
    ]


def sequence(spec: StimulusSpec, seed: int) -> List[List[float]]:
    """The temporal one: what actually gets driven, cycle by cycle.

    Three channels, write / read / reset, as the testbench would assert them.
    This is the representation the GRU autoencoder reads, and it's the one that
    can tell apart two specs with identical knobs whose seeds send them down
    different paths.
    """
    return [[1.0 if c["wr"] else 0.0,
             1.0 if c["rd"] else 0.0,
             1.0 if c["reset"] else 0.0]
            for c in generate(spec, seed)]


def standardise(vectors: Sequence[Sequence[float]]) -> List[List[float]]:
    """Centre and scale each column, so no one knob dominates the distance.

    Without this, p_burst at 0.10 and wr_burst at 40 would contribute wildly
    different amounts to a Euclidean distance for no principled reason.
    """
    if not vectors:
        return []
    n_cols = len(vectors[0])
    means, scales = [], []
    for j in range(n_cols):
        col = [v[j] for v in vectors]
        m = sum(col) / len(col)
        var = sum((x - m) ** 2 for x in col) / len(col)
        means.append(m)
        scales.append(var ** 0.5 or 1.0)
    return [[(v[j] - means[j]) / scales[j] for j in range(n_cols)] for v in vectors]
