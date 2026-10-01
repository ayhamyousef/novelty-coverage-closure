"""
Functional coverage model for rtl/sfifo.v.

The plain-English version of this lives in the README and was written first.
If the two ever disagree, the README is the specification and this file is the
bug.

Bin order is fixed by construction order and must not change, because Stages 4
to 6 compare coverage vectors produced by separate simulator processes.

One thing worth repeating from the README, since it's easy to get backwards:
the op_x_state bins count what the testbench *attempted*, because writes to a
full FIFO and reads from an empty one are silently dropped by this design and
the drop path is worth covering. The burst bins count what was *accepted*,
because a run of writes only means something if data actually moved.
"""

from __future__ import annotations

from typing import List

from simlib.covmodel import CoverageModel, CoverPoint

OPS = ["idle", "wr", "rd", "rdwr"]

RDWR_RUN_LEN = 4  # consecutive accepted read+write cycles

_TRANSITIONS = {
    ("empty", "one"): "empty_to_one",
    ("one", "empty"): "one_to_empty",
    ("mid", "almost_full"): "mid_to_almost_full",
    ("almost_full", "mid"): "almost_full_to_mid",
    ("almost_full", "full"): "almost_full_to_full",
    ("full", "almost_full"): "full_to_almost_full",
    ("full", "full"): "held_full",
    ("empty", "empty"): "held_empty",
}


def build_fifo_coverage_model(depth: int) -> CoverageModel:
    """The 52-bin model for a FIFO of the given depth."""
    return CoverageModel([
        CoverPoint(
            "op_x_fill",
            [f"{op}_at_{n}" for n in range(depth + 1) for op in OPS],
            "Every operation the testbench can attempt, crossed with every single "
            "fill level from empty to full. This is the SystemVerilog idiom of "
            "binning a counter per value and crossing it with the operation, and "
            "it's where nearly all the difficulty lives: being at exactly one "
            "fill level while attempting one specific operation.",
        ),
        CoverPoint(
            "transition",
            ["empty_to_one", "one_to_empty",
             "mid_to_almost_full", "almost_full_to_mid",
             "almost_full_to_full", "full_to_almost_full",
             "held_full", "held_empty"],
            "Cycle-to-cycle fill changes. Both directions across each boundary, "
            "since a FIFO that fills correctly can still drain wrongly.",
        ),
        CoverPoint(
            "wrap",
            ["wr_pointer_wrap", "rd_pointer_wrap",
             "wr_wrap_into_full", "rd_wrap_to_empty"],
            "The circular buffer's addresses running off the end of memory and "
            "back to zero, on its own and combined with a boundary.",
        ),
        CoverPoint(
            "reset",
            ["while_empty", "while_partial", "while_full",
             "with_write_pending", "with_read_pending"],
            "Reset asserted in each state, and arriving on a cycle where an "
            "operation was being attempted.",
        ),
        CoverPoint(
            "burst",
            ["wr_run_quarter_depth", "wr_run_half_depth", "wr_run_to_full",
             "rd_run_quarter_depth", "rd_run_half_depth", "rd_run_to_empty",
             f"rdwr_run_{RDWR_RUN_LEN}"],
            "Sustained one-sided traffic, and sustained simultaneous traffic. "
            "Run thresholds are a quarter and a half of the depth.",
        ),
    ])


class FifoCoverage:
    """Watches the FIFO and ticks off bins. Passive, never drives anything.

    Call sample() once per clock edge with the state the edge is about to act
    on. It works out the resulting state itself, so the caller can't get the
    before and after out of step.
    """

    def __init__(self, depth: int):
        self.depth = depth
        self.model = build_fifo_coverage_model(depth)

        # run thresholds as fractions of depth, so other depths still work
        self.wr_run_short = max(2, depth // 4)
        self.wr_run_long = max(3, depth // 2)

        # pointers, mirrored from the reference model rather than read out of
        # the DUT, so this doesn't depend on simulator-specific signal access
        self.wr_addr = 0
        self.rd_addr = 0

        self.wr_run = 0
        self.rd_run = 0
        self.rdwr_run = 0

    # -- classification ----------------------------------------------------
    def state(self, fill: int) -> str:
        if fill == 0:
            return "empty"
        if fill == 1:
            return "one"
        if fill == self.depth:
            return "full"
        if fill == self.depth - 1:
            return "almost_full"
        return "mid"

    # -- sampling ----------------------------------------------------------
    def sample(self, *, fill: int, i_wr: bool, i_rd: bool, i_reset: bool,
               acc_wr: bool, acc_rd: bool) -> None:
        """One clock edge. `fill` is the level the edge is about to act on."""
        cov = self.model
        st = self.state(fill)

        op = "rdwr" if (i_wr and i_rd) else "wr" if i_wr else "rd" if i_rd else "idle"
        cov.hit("op_x_fill", f"{op}_at_{fill}")

        if i_reset:
            if fill == 0:
                cov.hit("reset", "while_empty")
            elif fill == self.depth:
                cov.hit("reset", "while_full")
            else:
                cov.hit("reset", "while_partial")
            if i_wr:
                cov.hit("reset", "with_write_pending")
            if i_rd:
                cov.hit("reset", "with_read_pending")
            fill_after = 0
            acc_wr = acc_rd = False
        else:
            fill_after = fill + (1 if acc_wr else 0) - (1 if acc_rd else 0)

        # pointer wraparound
        if acc_wr:
            wrapping = (self.wr_addr & (self.depth - 1)) == self.depth - 1
            self.wr_addr += 1
            if wrapping:
                cov.hit("wrap", "wr_pointer_wrap")
                if fill_after == self.depth:
                    cov.hit("wrap", "wr_wrap_into_full")
        if acc_rd:
            wrapping = (self.rd_addr & (self.depth - 1)) == self.depth - 1
            self.rd_addr += 1
            if wrapping:
                cov.hit("wrap", "rd_pointer_wrap")
                if fill_after == 0:
                    cov.hit("wrap", "rd_wrap_to_empty")
        if i_reset:
            self.wr_addr = 0
            self.rd_addr = 0

        # sustained traffic
        if acc_wr and not acc_rd:
            self.wr_run += 1
            self.rd_run = self.rdwr_run = 0
            if self.wr_run >= self.wr_run_short:
                cov.hit("burst", "wr_run_quarter_depth")
            if self.wr_run >= self.wr_run_long:
                cov.hit("burst", "wr_run_half_depth")
            if self.wr_run >= 2 and fill_after == self.depth:
                cov.hit("burst", "wr_run_to_full")
        elif acc_rd and not acc_wr:
            self.rd_run += 1
            self.wr_run = self.rdwr_run = 0
            if self.rd_run >= self.wr_run_short:
                cov.hit("burst", "rd_run_quarter_depth")
            if self.rd_run >= self.wr_run_long:
                cov.hit("burst", "rd_run_half_depth")
            if self.rd_run >= 2 and fill_after == 0:
                cov.hit("burst", "rd_run_to_empty")
        elif acc_wr and acc_rd:
            self.rdwr_run += 1
            self.wr_run = self.rd_run = 0
            if self.rdwr_run >= RDWR_RUN_LEN:
                cov.hit("burst", f"rdwr_run_{RDWR_RUN_LEN}")
        else:
            self.wr_run = self.rd_run = self.rdwr_run = 0

        # transitions. The change happens at this edge, so before and after both
        # come from this one call and can't drift apart.
        pair = (st, self.state(fill_after))
        named = _TRANSITIONS.get(pair)
        if named:
            cov.hit("transition", named)

    # -- results -----------------------------------------------------------
    def vector(self) -> List[int]:
        return self.model.vector()

    def to_dict(self) -> dict:
        return self.model.to_dict()
