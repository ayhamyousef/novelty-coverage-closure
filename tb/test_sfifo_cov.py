"""
Stage 3 testbench for rtl/sfifo.v: drives one stimulus spec and writes out the
coverage vector it produced.

One run of this is one "test" in the sense the rest of the project uses. It
emits a 52-element binary vector saying which coverage bins it hit, and that
vector is what Stages 4 to 6 accumulate and compare.

The reference FIFO runs in lockstep and is checked every cycle. Coverage is
only meaningful if the DUT is actually behaving, so a scoreboard mismatch
fails the test rather than being recorded alongside the coverage.
"""

import json
import os
from collections import deque

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

from simlib.fifo_coverage import FifoCoverage
from simlib.stimulus import StimulusSpec, generate


class FifoEnv:
    """Driver, reference model and coverage monitor for one test.

    Every clock edge goes through step(), for the reason in the README: the
    Stage 1 scoreboard bug came from driving outside the one place that
    advances the reference model.
    """

    def __init__(self, dut, depth: int, bw: int = 8):
        self.dut = dut
        self.depth = depth
        self.mask = (1 << bw) - 1
        self.ref: deque = deque()
        self.cov = FifoCoverage(depth)
        self.errors = 0
        self.cycle = 0

    async def power_on_reset(self):
        d = self.dut
        d.i_reset.value = 1
        d.i_wr.value = 0
        d.i_rd.value = 0
        d.i_data.value = 0
        for _ in range(2):
            await RisingEdge(d.i_clk)
        await FallingEdge(d.i_clk)
        d.i_reset.value = 0
        self.ref.clear()

    async def step(self, wr: bool, rd: bool, reset: bool, data: int):
        d = self.dut

        await FallingEdge(d.i_clk)
        d.i_wr.value = 1 if wr else 0
        d.i_rd.value = 1 if rd else 0
        d.i_reset.value = 1 if reset else 0
        d.i_data.value = data & self.mask

        await ReadOnly()
        o_full = int(d.o_full.value)
        o_empty = int(d.o_empty.value)
        fill = int(d.o_fill.value)

        # scoreboard, checked before the edge against the state the edge acts on
        if fill != len(self.ref):
            self.errors += 1
            d._log.error("cycle %d: o_fill=%d, reference holds %d",
                         self.cycle, fill, len(self.ref))

        # o_data is only meaningful when the FIFO has something in it. Icarus
        # leaves the memory at X until it's first written, and Verilator zeroes
        # it, so reading it unconditionally works on one simulator and throws on
        # the other. Resolving it only when the FIFO says it's non-empty keeps
        # the two in step, and an X there is a real failure rather than a crash.
        if not o_empty and self.ref:
            dv = d.o_data.value
            if not dv.is_resolvable:
                self.errors += 1
                d._log.error("cycle %d: o_data is X or Z while the FIFO holds %d "
                             "entries", self.cycle, len(self.ref))
            elif int(dv) != self.ref[0]:
                self.errors += 1
                d._log.error("cycle %d: o_data=%d, reference head is %d",
                             self.cycle, int(dv), self.ref[0])

        # same gating the RTL applies (sfifo.v lines 71 to 72)
        acc_wr = wr and not o_full and not reset
        acc_rd = rd and not o_empty and not reset

        self.cov.sample(fill=fill, i_wr=wr, i_rd=rd, i_reset=reset,
                        acc_wr=acc_wr, acc_rd=acc_rd)

        if reset:
            self.ref.clear()
        else:
            if acc_rd:
                self.ref.popleft()
            if acc_wr:
                self.ref.append(data & self.mask)

        await RisingEdge(d.i_clk)
        self.cycle += 1


@cocotb.test()
async def run_one_test(dut):
    seed = int(os.environ.get("TB_SEED", "1"))
    depth = 1 << int(os.environ.get("TB_LGFLEN", "5"))
    spec = StimulusSpec.from_dict(json.loads(os.environ.get("TB_STIM", "{}")))

    env = FifoEnv(dut, depth)
    cocotb.start_soon(Clock(dut.i_clk, 10, units="ns").start())
    await env.power_on_reset()

    for c in generate(spec, seed):
        await env.step(c["wr"], c["rd"], c["reset"], c["data"])

    rec = env.cov.to_dict()
    rec.update({
        "seed": seed,
        "depth": depth,
        "spec": spec.to_dict(),
        "cycles": env.cycle,
        "scoreboard_errors": env.errors,
    })

    out = os.environ.get("TB_OUT")
    if out:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w") as fh:
            json.dump(rec, fh, indent=2)

    dut._log.info("seed=%d covered %d/%d bins (%.1f%%), %d errors",
                  seed, rec["n_covered"], rec["n_bins"],
                  100.0 * rec["fraction"], env.errors)

    assert env.errors == 0, f"{env.errors} mismatches against the reference FIFO"
