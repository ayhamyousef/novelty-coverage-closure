"""Candidate pool: generate stimulus specs, simulate each once, cache the result.

The experiment is about test *selection*, so both arms have to choose from the
same set of candidates. Coverage of a test is fully determined by its spec and
its seed, so each candidate only needs simulating once. After that any
selection order can be replayed from the cache for free, which is what makes
the 5 pools x 10 orderings in Stage 4 and the comparison in Stage 6 cheap.

The cache is also the obvious place to leak information from, so see
simlib/selection.py for how that's kept out of the selectors' reach.
"""

from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from simlib.simrunner import build_dut, run_test
from simlib.stimulus import StimulusSpec, random_spec


@dataclass(frozen=True)
class Candidate:
    """One candidate test. Deliberately carries no coverage result.

    A selector is handed these and nothing else about the tests it has not yet
    chosen, so it can't see what a candidate would cover before picking it.
    """
    index: int
    spec: StimulusSpec
    seed: int


def build_pool(gen_seed: int, size: int, cycles: int) -> List[Candidate]:
    rng = random.Random(gen_seed)
    return [Candidate(index=i, spec=random_spec(rng, cycles=cycles),
                      seed=gen_seed * 100000 + i)
            for i in range(size)]


def simulate_pool(pool: List[Candidate], *, repo: Path, sim: str, lgflen: int,
                  cache_path: Path, progress_every: int = 50) -> Dict[int, List[int]]:
    """Simulate every candidate once. Returns index -> coverage vector.

    Results are cached on disk keyed by the candidate's spec and seed, so a
    rerun costs nothing. The cache is flushed every 25 candidates through a
    temporary file and a rename, so killing an 11 minute run loses at most the
    last 25 simulations rather than all of them.
    """
    cache: Dict[str, List[int]] = {}
    if cache_path.exists():
        with open(cache_path) as fh:
            cache = json.load(fh).get("vectors", {})

    def key(c: Candidate) -> str:
        return json.dumps({"spec": c.spec.to_dict(), "seed": c.seed}, sort_keys=True)

    missing = [c for c in pool if key(c) not in cache]
    if missing:
        build_dir = repo / "sim_build" / f"pool_{sim}_lg{lgflen}"
        runner = build_dut(sim, sources=[repo / "rtl" / "sfifo.v"],
                           toplevel="sfifo", build_dir=build_dir,
                           parameters={"LGFLEN": lgflen})
        out_json = build_dir / "cov.json"
        cache_path.parent.mkdir(parents=True, exist_ok=True)

        def flush():
            tmp = cache_path.with_suffix(".tmp")
            with open(tmp, "w") as fh:
                json.dump({"vectors": cache}, fh)
            tmp.replace(cache_path)

        for n, c in enumerate(missing, 1):
            run_test(runner, toplevel="sfifo", test_module="test_sfifo_cov",
                     test_dir=repo / "tb", build_dir=build_dir,
                     results_xml=build_dir / "r.xml",
                     env={"PYTHONPATH": os.pathsep.join([str(repo), str(repo / "tb")]),
                          "TB_SEED": str(c.seed), "TB_LGFLEN": str(lgflen),
                          "TB_STIM": json.dumps(c.spec.to_dict()),
                          "TB_OUT": str(out_json)})
            with open(out_json) as fh:
                rec = json.load(fh)
            if rec["scoreboard_errors"]:
                raise RuntimeError(f"candidate {c.index} hit "
                                   f"{rec['scoreboard_errors']} scoreboard errors")
            cache[key(c)] = rec["vector"]
            if n % 25 == 0:
                flush()
            if progress_every and n % progress_every == 0:
                print(f"    simulated {n}/{len(missing)}", flush=True)
        flush()

    return {c.index: cache[key(c)] for c in pool}
