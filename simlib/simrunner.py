"""One place that knows how to start a simulation.

This exists because I got the same thing wrong twice. Icarus has no default
timescale, so a 10 ns clock isn't representable and the test dies with a
precision error. cocotb's runner wants the timescale on build() and on test(),
and both times I passed it to test() only, copied the call into the next
script, and shipped something whose --sim icarus option had never been run.
Verilator doesn't care either way, so it all looks fine until someone tries
the fallback simulator.

Putting it here means a caller can't forget it. The scripts say what to
compile and what to run, and never touch the timescale at all.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, Mapping, Optional, Sequence

try:  # cocotb >= 2.0
    from cocotb_tools.runner import get_runner
except ImportError:  # cocotb 1.x
    from cocotb.runner import get_runner

TIMESCALE = ("1ns", "1ps")


def build_dut(sim: str, *, sources: Sequence[Path], toplevel: str,
              build_dir: Path, parameters: Optional[Mapping] = None,
              extra_build_args: Iterable[str] = (), waves: bool = False):
    """Compile the DUT. Returns the runner, which run_test() then uses.

    -Wno-fatal is added for Verilator so a lint warning doesn't kill a build
    that Icarus would have accepted.
    """
    args = list(extra_build_args)
    if sim == "verilator" and "-Wno-fatal" not in args:
        args.append("-Wno-fatal")

    runner = get_runner(sim)
    runner.build(
        sources=list(sources),
        hdl_toplevel=toplevel,
        build_dir=build_dir,
        build_args=args,
        parameters=dict(parameters or {}),
        always=True,
        waves=waves,
        timescale=TIMESCALE,
    )
    return runner


def run_test(runner, *, toplevel: str, test_module: str, test_dir: Path,
             build_dir: Path, results_xml: Path, env: Dict[str, str],
             waves: bool = False, plusargs: Iterable[str] = ()):
    """Run one cocotb test against an already built DUT."""
    return runner.test(
        hdl_toplevel=toplevel,
        test_module=test_module,
        test_dir=test_dir,
        build_dir=build_dir,
        results_xml=str(results_xml),
        extra_env=dict(env),
        plusargs=list(plusargs),
        waves=waves,
        timescale=TIMESCALE,
    )
