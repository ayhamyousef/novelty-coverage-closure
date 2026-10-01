"""
Stage 2 headroom probe for rtl/sfifo.v.

Not the Stage 3 coverage model. This measures raw DUT behaviour, so that the
coverage model can be built against something measured rather than guessed.

The question is how hard it is for plain random stimulus to push this FIFO into
its interesting states. If one random test fills it every time then there is no
headroom for a smarter selector and nothing worth measuring.

DUT semantics, read off the RTL rather than assumed (rtl/sfifo.v lines 71-72,
109, 161):

    w_wr    = i_wr && !o_full          // a write only happens if not full
    w_rd    = i_rd && !o_empty         // a read only happens if not empty
    o_full  = (i_rd && OPT_WRITE_ON_FULL) ? 0 : (o_fill == 2**LGFLEN)
    o_empty = (OPT_READ_ON_EMPTY && i_wr) ? 0 : r_empty

This probe runs the default parameters: OPT_ASYNC_READ=1, OPT_WRITE_ON_FULL=0,
OPT_READ_ON_EMPTY=0. So o_full/o_empty are plain fill-level flags and o_data is
combinational.
"""

import json
import os
import random
from collections import deque

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge


class FifoEnv:
    """Drives the FIFO and keeps a Python reference model in lockstep.

    Every clock edge goes through step(). The Stage 1 scoreboard bug, 446 false
    mismatches, came from driving signals outside the one place that advances
    the reference model, so an edge went by unobserved. Single path this time.
    """

    def __init__(self, dut, lgflen, bw=8):
        self.dut = dut
        self.lgflen = lgflen
        self.depth = 1 << lgflen
        self.mask = (1 << bw) - 1
        self.ref = deque()
        self.errors = 0
        self.cycle = 0

        # --- statistics -------------------------------------------------
        self.fill_hist = [0] * (self.depth + 1)
        self.cycles_full = 0
        self.cycles_empty = 0
        self.max_fill = 0
        self.first_full_cycle = None      # None => never reached full
        self.accepted_writes = 0
        self.accepted_reads = 0
        self.rw_while_full = 0            # i_wr & i_rd asserted while full
        self.rw_while_empty = 0           # i_wr & i_rd asserted while empty
        self.write_attempt_on_full = 0    # i_wr asserted but dropped
        self.read_attempt_on_empty = 0    # i_rd asserted but dropped

    async def reset(self):
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

    async def step(self, do_wr: bool, do_rd: bool, data: int):
        d = self.dut

        # 1. drive on the falling edge, well away from the active edge
        await FallingEdge(d.i_clk)
        d.i_wr.value = 1 if do_wr else 0
        d.i_rd.value = 1 if do_rd else 0
        d.i_data.value = data & self.mask

        # 2. settle, then sample the flags the coming edge will actually act on
        await ReadOnly()
        o_full = int(d.o_full.value)
        o_empty = int(d.o_empty.value)
        fill = int(d.o_fill.value)

        # 3. check the reference model against the DUT *before* the edge
        if fill != len(self.ref):
            self.errors += 1
            d._log.error("cycle %d: o_fill=%d but reference holds %d",
                         self.cycle, fill, len(self.ref))
        # o_data only means anything when the FIFO is non-empty. Icarus leaves
        # the memory at X until first written and Verilator zeroes it, so
        # resolving it unconditionally throws on Icarus only.
        if not o_empty and self.ref:
            dv = d.o_data.value
            if not dv.is_resolvable:
                self.errors += 1
                d._log.error("cycle %d: o_data is X or Z while non-empty", self.cycle)
            elif int(dv) != self.ref[0]:
                self.errors += 1
                d._log.error("cycle %d: o_data=%d but reference head is %d",
                             self.cycle, int(dv), self.ref[0])

        # 4. statistics on the state the DUT is sitting in this cycle
        self.fill_hist[fill] += 1
        self.max_fill = max(self.max_fill, fill)
        if o_full:
            self.cycles_full += 1
            if self.first_full_cycle is None:
                self.first_full_cycle = self.cycle
            if do_wr and do_rd:
                self.rw_while_full += 1
            if do_wr:
                self.write_attempt_on_full += 1
        if o_empty:
            self.cycles_empty += 1
            if do_wr and do_rd:
                self.rw_while_empty += 1
            if do_rd:
                self.read_attempt_on_empty += 1

        # 5. apply the same gating the RTL applies
        w_wr = do_wr and not o_full
        w_rd = do_rd and not o_empty
        if w_rd:
            self.ref.popleft()
            self.accepted_reads += 1
        if w_wr:
            self.ref.append(data & self.mask)
            self.accepted_writes += 1

        # 6. let the edge happen
        await RisingEdge(d.i_clk)
        self.cycle += 1

    def stats(self) -> dict:
        total = max(1, self.cycle)
        return {
            "lgflen": self.lgflen,
            "depth": self.depth,
            "cycles": self.cycle,
            "scoreboard_errors": self.errors,
            "max_fill": self.max_fill,
            "reached_full": self.max_fill == self.depth,
            "first_full_cycle": self.first_full_cycle,
            "cycles_full": self.cycles_full,
            "cycles_empty": self.cycles_empty,
            "frac_cycles_full": self.cycles_full / total,
            "frac_cycles_empty": self.cycles_empty / total,
            "accepted_writes": self.accepted_writes,
            "accepted_reads": self.accepted_reads,
            "rw_while_full": self.rw_while_full,
            "rw_while_empty": self.rw_while_empty,
            "write_attempt_on_full": self.write_attempt_on_full,
            "read_attempt_on_empty": self.read_attempt_on_empty,
            "fill_hist": self.fill_hist,
        }


@cocotb.test()
async def probe_random_stimulus(dut):
    """Drive naive Bernoulli random stimulus and record where the FIFO goes."""
    seed = int(os.environ.get("TB_SEED", "1"))
    cycles = int(os.environ.get("TB_CYCLES", "500"))
    p_wr = float(os.environ.get("TB_P_WR", "0.5"))
    p_rd = float(os.environ.get("TB_P_RD", "0.5"))
    lgflen = int(os.environ.get("TB_LGFLEN", "4"))

    rng = random.Random(seed)
    env = FifoEnv(dut, lgflen)

    cocotb.start_soon(Clock(dut.i_clk, 10, units="ns").start())
    await env.reset()

    for _ in range(cycles):
        await env.step(rng.random() < p_wr, rng.random() < p_rd, rng.randrange(256))

    st = env.stats()
    st.update({"seed": seed, "p_wr": p_wr, "p_rd": p_rd})

    out = os.environ.get("TB_OUT")
    if out:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w") as fh:
            json.dump(st, fh, indent=2)

    dut._log.info(
        "depth=%d p_wr=%.2f p_rd=%.2f seed=%d -> max_fill=%d/%d full=%d cyc empty=%d cyc errors=%d",
        st["depth"], p_wr, p_rd, seed, st["max_fill"], st["depth"],
        st["cycles_full"], st["cycles_empty"], st["scoreboard_errors"],
    )

    assert env.errors == 0, f"{env.errors} mismatches against the Python reference FIFO"
