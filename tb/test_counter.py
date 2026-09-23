"""
Smoke-test bench for rtl/counter.v. Throwaway DUT; the point is to prove the
simulator, cocotb and the coverage flow actually work together.

Everything goes through CounterEnv.step() on purpose. See the note on it.
"""

import json
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

from simlib.covmodel import CoverageModel, CoverPoint

WIDTH = 8
MAXV = (1 << WIDTH) - 1


def build_counter_coverage_model() -> CoverageModel:
    """Stage 1 coverage model. Plain English version is in the README."""
    return CoverageModel([
        CoverPoint(
            "count_val", ["zero", "low", "mid", "high", "max"],
            "Which band of its range the counter has been observed holding.",
        ),
        CoverPoint(
            "wrapped", ["seen"],
            "The counter was at its maximum value with counting enabled, i.e. "
            "the very next clock edge rolls it over to zero.",
        ),
        CoverPoint(
            "load", ["load_zero", "load_max", "load_mid", "load_while_en"],
            "Synchronous load exercised with boundary and mid-range values, and "
            "with the count enable simultaneously asserted (load must win).",
        ),
        CoverPoint(
            "reset", ["during_nonzero_count"],
            "Asynchronous reset asserted while the counter held a non-zero value.",
        ),
        CoverPoint(
            "transition", ["hold_to_count", "count_to_hold", "max_to_zero", "load_to_count"],
            "Interesting cycle-to-cycle changes of the counter's operating mode.",
        ),
    ])


def _band(v: int) -> str:
    if v == 0:
        return "zero"
    if v == MAXV:
        return "max"
    if v < MAXV // 3:
        return "low"
    if v < (2 * MAXV) // 3:
        return "mid"
    return "high"


class CounterEnv:
    """Driver, scoreboard and coverage monitor.

    Scoreboard = an independent Python reimplementation of the RTL, compared
    against the DUT every cycle. Monitor = passive, only records coverage.

    All stimulus goes through step(). An earlier version drove signals directly
    between test phases and let one clock edge go by unobserved with en still
    asserted; the reference model missed that increment and sat one behind the
    DUT for the rest of the run (446 false mismatches). Funnelling every cycle
    through one place means there's nowhere for an unobserved edge to hide.
    """

    def __init__(self, dut, model: CoverageModel):
        self.dut = dut
        self.cov = model
        self.ref = 0
        self.errors = 0
        self.cycle = 0
        self._prev_mode = None
        self._prev_count = None

    async def step(self, en: int = 0, load: int = 0, load_val: int = 0, rst_n: int = 1):
        """One clock cycle.

        Drive on the falling edge so inputs have half a period of setup and
        don't race the active edge. ReadOnly waits for the timestep to settle
        before anything is read back.
        """
        d = self.dut
        await FallingEdge(d.clk)
        d.rst_n.value = rst_n
        d.en.value = en
        d.load.value = load
        d.load_val.value = load_val

        await RisingEdge(d.clk)
        await ReadOnly()

        # Mirrors rtl/counter.v. Priority: reset > load > enable.
        if rst_n == 0:
            self.ref = 0
        elif load:
            self.ref = load_val & MAXV
        elif en:
            self.ref = (self.ref + 1) & MAXV

        got = int(d.count.value)
        if got != self.ref:
            self.errors += 1
            d._log.error("cycle %d MISMATCH dut=%d ref=%d (en=%d load=%d lv=%d rst_n=%d)",
                         self.cycle, got, self.ref, en, load, load_val, rst_n)

        self._sample(en=en, load=load, load_val=load_val, rst_n=rst_n, count=got)
        self.cycle += 1

    def _sample(self, en, load, load_val, rst_n, count):
        cov = self.cov
        cov.hit("count_val", _band(count))

        if int(self.dut.wrapped.value):
            cov.hit("wrapped", "seen")

        if rst_n == 0 and self._prev_count not in (None, 0):
            cov.hit("reset", "during_nonzero_count")

        if load and rst_n:
            lv = load_val & MAXV
            if lv == 0:
                cov.hit("load", "load_zero")
            elif lv == MAXV:
                cov.hit("load", "load_max")
            else:
                cov.hit("load", "load_mid")
            if en:
                cov.hit("load", "load_while_en")

        mode = "reset" if rst_n == 0 else ("load" if load else ("count" if en else "hold"))
        pair = (self._prev_mode, mode)
        if pair == ("hold", "count"):
            cov.hit("transition", "hold_to_count")
        elif pair == ("count", "hold"):
            cov.hit("transition", "count_to_hold")
        elif pair == ("load", "count"):
            cov.hit("transition", "load_to_count")
        if self._prev_count == MAXV and count == 0 and self._prev_mode == "count":
            cov.hit("transition", "max_to_zero")

        self._prev_mode = mode
        self._prev_count = count


@cocotb.test()
async def test_counter_smoke(dut):
    """Directed corners then constrained random, checked against the reference model."""
    seed = int(os.environ.get("TB_SEED", "1"))
    n_cycles = int(os.environ.get("TB_CYCLES", "600"))
    rng = random.Random(seed)

    model = build_counter_coverage_model()
    env = CounterEnv(dut, model)

    cocotb.start_soon(Clock(dut.clk, 10, units="ns").start())

    # Power-on reset.
    for _ in range(2):
        await env.step(rst_n=0)

    # --- directed corners --------------------------------------------------
    # count==255 needs ~255 consecutive enables, which random won't produce in
    # 600 cycles. So drive it deliberately.
    await env.step(load=1, load_val=MAXV - 2)
    for _ in range(5):
        await env.step(en=1)

    # Reset asserted while the count is non-zero.
    await env.step(en=1)
    await env.step(en=1)
    await env.step(rst_n=0)

    # --- constrained-random body ------------------------------------------
    for _ in range(n_cycles):
        do_load = rng.random() < 0.05
        await env.step(
            en=1 if rng.random() < 0.75 else 0,
            load=1 if do_load else 0,
            load_val=rng.choice([0, MAXV, MAXV - 1, rng.randrange(0, MAXV)]) if do_load else 0,
        )

    # --- results -----------------------------------------------------------
    out_dir = os.environ.get("TB_OUT_DIR", ".")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"functional_coverage_seed{seed}.json")
    payload = model.to_dict()
    payload.update(seed=seed, cycles=env.cycle, scoreboard_errors=env.errors)
    with open(out_path, "w") as fh:
        json.dump(payload, fh, indent=2)

    dut._log.info("Cycles simulated: %d", env.cycle)
    dut._log.info("Functional coverage: %d/%d bins (%.1f%%)",
                  model.n_covered(), model.n_bins, 100.0 * model.fraction())
    if model.uncovered():
        dut._log.info("Uncovered bins: %s", ", ".join(model.uncovered()))
    dut._log.info("Scoreboard mismatches: %d", env.errors)
    dut._log.info("Wrote %s", out_path)

    assert env.errors == 0, \
        f"{env.errors} scoreboard mismatches against the Python reference model"
