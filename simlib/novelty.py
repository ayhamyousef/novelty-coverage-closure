"""Novelty-based test selectors.

Three of them, in increasing order of how much machinery they need:

  FeatureDistanceSelector  distance in a hand-built feature space
  GRUReconstructionSelector    GRU autoencoder, novelty = reconstruction error
  GRUKMeansSelector            GRU autoencoder, novelty = distance to the
                               nearest k-means centroid in the latent space

The simple one stays in the comparison on purpose. If the neural versions don't
beat it, that's the result and it goes in the README.

All three score a candidate purely on its stimulus. They read the *indices* of
tests already simulated, so they know what's been seen, and they never touch the
coverage those tests produced. scripts/check_invariants.py enforces that by
running each selector twice, once with real coverage in the history and once
with the coverage replaced by nonsense, and checking the picks come out
identical.

How a run goes: the first `seed_batch` tests are drawn at random, because there
is nothing to be novel relative to yet. After that the selector refits on the
stimuli simulated so far, scores everything left, and queues the top
`batch_size` picks. Then it refits again.
"""

from __future__ import annotations

import math
import random
from typing import List, Sequence

from simlib.features import feature_vector, sequence, standardise
from simlib.selection import History, Selector

_TORCH_SEEDED = False


class BatchNoveltySelector(Selector):
    """Shared batching. Subclasses only implement score()."""

    name = "batch-novelty"

    def __init__(self, batch_size: int = 5, seed_batch: int = 10):
        self.batch_size = batch_size
        self.seed_batch = seed_batch

    # -- subclass hook -----------------------------------------------------
    def score(self, candidates, simulated: List[int],
              remaining: List[int]) -> List[float]:
        """Novelty of each index in `remaining`. Higher means more novel."""
        raise NotImplementedError

    def prepare(self, candidates, rng) -> None:
        """Precompute anything derivable from the stimuli alone."""

    # -- Selector ----------------------------------------------------------
    def reset(self, candidates, rng):
        self._rng = rng
        self._queue: List[int] = []
        self.prepare(candidates, rng)

    def select(self, candidates, remaining, history: History):
        self._queue = [i for i in self._queue if i in remaining]
        if not self._queue:
            simulated = [idx for idx, _ in history]   # indices only, never the vectors
            if len(simulated) < self.seed_batch:
                take = min(self.batch_size, len(remaining))
                self._queue = self._rng.sample(remaining, take)
            else:
                scores = self.score(candidates, simulated, remaining)
                order = sorted(range(len(remaining)),
                               key=lambda k: (-scores[k], remaining[k]))
                self._queue = [remaining[k]
                               for k in order[:min(self.batch_size, len(remaining))]]
        return self._queue.pop(0)


class FeatureDistanceSelector(BatchNoveltySelector):
    """Novelty = distance to the closest stimulus already simulated.

    The simplest thing that could work. Seven numbers off the spec,
    standardised so no single knob dominates, then nearest-neighbour distance.
    No training, no tensors.
    """

    name = "feature-distance"

    def prepare(self, candidates, rng):
        raw = [feature_vector(c.spec) for c in candidates]
        self._feat = standardise(raw)

    def score(self, candidates, simulated, remaining):
        seen = [self._feat[i] for i in simulated]
        out = []
        for i in remaining:
            v = self._feat[i]
            out.append(min(math.dist(v, s) for s in seen))
        return out


class FeatureKNNSelector(FeatureDistanceSelector):
    """Novelty = mean distance to the k nearest simulated stimuli.

    Added after the first run, and worth being upfront about why. Scoring by
    distance to the single closest neighbour is farthest-point sampling, which
    deliberately chases the extremes of the space. That turned out to pick
    systematically weak tests here: the corners of this parameter box are
    things like a near-idle FIFO, which are unlike anything already run and
    also cover very little.

    Averaging over k neighbours is the usual novelty-search formulation and is
    much less extreme-seeking, so it's the fair version of the same idea rather
    than a different method. Both stay in the comparison.
    """

    name = "feature-knn"

    def __init__(self, batch_size: int = 5, seed_batch: int = 10, k: int = 5):
        super().__init__(batch_size, seed_batch)
        self.k = k

    def score(self, candidates, simulated, remaining):
        seen = [self._feat[i] for i in simulated]
        out = []
        for i in remaining:
            v = self._feat[i]
            d = sorted(math.dist(v, s) for s in seen)
            kk = max(1, min(self.k, len(d)))
            out.append(sum(d[:kk]) / kk)
        return out


class _GRUBase(BatchNoveltySelector):
    """Shared GRU sequence autoencoder, refitted on the simulated stimuli."""

    def __init__(self, batch_size: int = 5, seed_batch: int = 10,
                 hidden: int = 24, epochs: int = 60, lr: float = 1e-2,
                 torch_seed: int = 0):
        super().__init__(batch_size, seed_batch)
        self.hidden = hidden
        self.epochs = epochs
        self.lr = lr
        self.torch_seed = torch_seed

    def prepare(self, candidates, rng):
        import torch
        torch.manual_seed(self.torch_seed)
        torch.use_deterministic_algorithms(True, warn_only=True)
        seqs = [sequence(c.spec, c.seed) for c in candidates]
        self._x = torch.tensor(seqs, dtype=torch.float32)   # (N, T, 3)

    def _fit(self, simulated: Sequence[int]):
        import torch
        from torch import nn

        torch.manual_seed(self.torch_seed)
        n_ch = self._x.shape[2]
        hidden = self.hidden

        class AE(nn.Module):
            def __init__(self):
                super().__init__()
                self.enc = nn.GRU(n_ch, hidden, batch_first=True)
                self.dec = nn.GRU(hidden, hidden, batch_first=True)
                self.out = nn.Linear(hidden, n_ch)

            def encode(self, x):
                _, h = self.enc(x)
                return h[-1]

            def forward(self, x):
                z = self.encode(x)
                rep = z.unsqueeze(1).expand(-1, x.shape[1], -1)
                y, _ = self.dec(rep)
                return self.out(y), z

        model = AE()
        xs = self._x[list(simulated)]
        opt = torch.optim.Adam(model.parameters(), lr=self.lr)
        lossf = nn.BCEWithLogitsLoss()
        model.train()
        for _ in range(self.epochs):
            opt.zero_grad()
            recon, _ = model(xs)
            loss = lossf(recon, xs)
            loss.backward()
            opt.step()
        model.eval()
        return model

    def _recon_error(self, model, idx: Sequence[int]):
        import torch
        from torch import nn
        with torch.no_grad():
            xs = self._x[list(idx)]
            recon, z = model(xs)
            per = nn.functional.binary_cross_entropy_with_logits(
                recon, xs, reduction="none").mean(dim=(1, 2))
        return per, z


class GRUReconstructionSelector(_GRUBase):
    """Novelty = how badly the autoencoder reconstructs the candidate.

    The model only ever trains on stimuli that have been simulated, so a
    sequence it reconstructs poorly is one unlike anything tried so far.
    """

    name = "gru-reconstruction"

    def score(self, candidates, simulated, remaining):
        model = self._fit(simulated)
        per, _ = self._recon_error(model, remaining)
        return per.tolist()


class GRUKMeansSelector(_GRUBase):
    """Novelty = distance to the nearest k-means centroid in the latent space.

    Same autoencoder, different read-off. The simulated stimuli are clustered
    in latent space and a candidate scores high when it sits far from every
    cluster, which is the shape of the method in the Zheng et al. papers.
    """

    name = "gru-kmeans"

    def __init__(self, *args, k: int = 8, **kw):
        super().__init__(*args, **kw)
        self.k = k

    def score(self, candidates, simulated, remaining):
        import numpy as np
        import torch
        from sklearn.cluster import KMeans

        model = self._fit(simulated)
        with torch.no_grad():
            z_seen = model.encode(self._x[list(simulated)]).numpy()
            z_rem = model.encode(self._x[list(remaining)]).numpy()
        k = max(1, min(self.k, len(simulated)))
        km = KMeans(n_clusters=k, n_init=10, random_state=self.torch_seed).fit(z_seen)
        d = np.linalg.norm(z_rem[:, None, :] - km.cluster_centers_[None, :, :], axis=2)
        return d.min(axis=1).tolist()


class RandomBatchSelector(BatchNoveltySelector):
    """Random selection, but through the same batching protocol.

    Stage 4's RandomSelector picks one at a time, which gives the same uniform
    random ordering and the same distribution. This version exists so the
    comparison can be paired tightly: every arm consumes its rng the same way,
    so for a given seed they all open with the *identical* seed batch and only
    diverge once novelty scoring starts. Any difference after that is down to
    selection rather than to a luckier opening hand.
    """

    name = "random-batch"

    def score(self, candidates, simulated, remaining):
        return [self._rng.random() for _ in remaining]
