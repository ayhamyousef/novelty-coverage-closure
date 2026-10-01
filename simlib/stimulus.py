"""
Stimulus description for the FIFO.

A "test" in this project is one of these specs plus a seed. That matters later:
Stage 5's selectors choose between candidate specs, so this is the space the
whole experiment searches.

Stage 2 showed why it needs burst knobs rather than just Bernoulli rates.
Flipping a p=0.5 coin each cycle makes the fill level do a random walk that
never travels far, so a Bernoulli-only generator can't reach the top of a
32-deep FIFO at all, and a baseline built from one would be trivial to beat.

Stage 4 is where the generator gets its proper treatment. This is the minimum
needed to exercise the coverage model and check the bins are reachable.
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass
from typing import Dict, Iterator


@dataclass(frozen=True)
class StimulusSpec:
    cycles: int = 600
    p_wr: float = 0.5          # per-cycle chance of asserting i_wr outside a burst
    p_rd: float = 0.5
    p_burst: float = 0.0       # per-cycle chance of starting a burst
    wr_burst: int = 0          # length of a write burst, 0 disables
    rd_burst: int = 0
    p_reset: float = 0.0       # per-cycle chance of asserting i_reset
    data_width: int = 8

    def to_dict(self) -> Dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict) -> "StimulusSpec":
        known = {f for f in StimulusSpec.__dataclass_fields__}
        return StimulusSpec(**{k: v for k, v in d.items() if k in known})


def generate(spec: StimulusSpec, seed: int) -> Iterator[Dict]:
    """Yield one dict of pin values per cycle.

    Bursts take priority over the Bernoulli rates: once a burst starts it runs
    to completion, driving one side continuously and holding the other low.
    That's the only way to get sustained one-sided traffic, which is what fills
    or drains the FIFO.
    """
    rng = random.Random(seed)
    mask = (1 << spec.data_width) - 1
    burst_left = 0
    burst_is_write = True

    for _ in range(spec.cycles):
        if rng.random() < spec.p_reset:
            yield {"wr": rng.random() < spec.p_wr,
                   "rd": rng.random() < spec.p_rd,
                   "reset": True,
                   "data": rng.randrange(mask + 1)}
            burst_left = 0
            continue

        if burst_left == 0 and spec.p_burst > 0 and rng.random() < spec.p_burst:
            if spec.wr_burst and spec.rd_burst:
                burst_is_write = rng.random() < 0.5
            elif spec.wr_burst:
                burst_is_write = True
            elif spec.rd_burst:
                burst_is_write = False
            else:
                burst_is_write = True
            burst_left = spec.wr_burst if burst_is_write else spec.rd_burst

        if burst_left > 0:
            burst_left -= 1
            do_wr, do_rd = (True, False) if burst_is_write else (False, True)
        else:
            do_wr = rng.random() < spec.p_wr
            do_rd = rng.random() < spec.p_rd

        yield {"wr": do_wr, "rd": do_rd, "reset": False,
               "data": rng.randrange(mask + 1)}
