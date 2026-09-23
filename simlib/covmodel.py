"""
Functional coverage: a fixed set of named bins, marked as they're observed.

Rolled by hand instead of using cocotb-coverage because the experiment needs
every test to emit a fixed-length binary vector whose bin order is stable across
separate processes. Stages 4-6 union and compare vectors from different runs, so
that ordering is a hard requirement and not something to leave to a library.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from typing import Iterable, List, Sequence


class CoverPoint:
    """A named group of bins over some sampled value."""

    def __init__(self, name: str, bins: Sequence[str], description: str = ""):
        self.name = name
        self.bins = list(bins)
        self.description = description


class CoverageModel:
    """Fixed, ordered set of bins. Order is set at construction; don't reorder it."""

    def __init__(self, coverpoints: Iterable[CoverPoint]):
        self.coverpoints: List[CoverPoint] = list(coverpoints)
        self._index: "OrderedDict[str, int]" = OrderedDict()
        for cp in self.coverpoints:
            for b in cp.bins:
                key = f"{cp.name}.{b}"
                if key in self._index:
                    raise ValueError(f"duplicate coverage bin: {key}")
                self._index[key] = len(self._index)
        self._hits = [0] * len(self._index)

    # -- shape -------------------------------------------------------------
    @property
    def n_bins(self) -> int:
        return len(self._index)

    @property
    def bin_names(self) -> List[str]:
        return list(self._index.keys())

    # -- sampling ----------------------------------------------------------
    def hit(self, coverpoint: str, bin_name: str) -> None:
        key = f"{coverpoint}.{bin_name}"
        idx = self._index.get(key)
        if idx is None:
            raise KeyError(f"unknown coverage bin: {key}")
        self._hits[idx] += 1

    def hit_if_known(self, coverpoint: str, bin_name: str) -> bool:
        """Like hit(), but ignores bins outside the model instead of raising."""
        key = f"{coverpoint}.{bin_name}"
        idx = self._index.get(key)
        if idx is None:
            return False
        self._hits[idx] += 1
        return True

    # -- results -----------------------------------------------------------
    def vector(self) -> List[int]:
        """Binary hit/no-hit per bin, fixed order. This is what the selectors consume."""
        return [1 if h else 0 for h in self._hits]

    def counts(self) -> List[int]:
        """Hit counts per bin, same order as vector()."""
        return list(self._hits)

    def n_covered(self) -> int:
        return sum(1 for h in self._hits if h)

    def fraction(self) -> float:
        return self.n_covered() / self.n_bins if self.n_bins else 0.0

    def uncovered(self) -> List[str]:
        return [n for n, i in self._index.items() if not self._hits[i]]

    def reset(self) -> None:
        self._hits = [0] * len(self._index)

    def to_dict(self) -> dict:
        return {
            "n_bins": self.n_bins,
            "n_covered": self.n_covered(),
            "fraction": self.fraction(),
            "bin_names": self.bin_names,
            "vector": self.vector(),
            "counts": self.counts(),
        }

    def write_json(self, path: str) -> None:
        with open(path, "w") as fh:
            json.dump(self.to_dict(), fh, indent=2)
