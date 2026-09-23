#!/usr/bin/env python3
"""
Stage 1: build the counter, run the cocotb bench, collect both kinds of coverage.

Writes to results/stage1/:
    functional_coverage_seed<N>.json    our coverage model
    coverage.dat + coverage_annotated/  Verilator line/toggle coverage

    python3 scripts/01_smoke_test.py                 # Verilator
    python3 scripts/01_smoke_test.py --sim icarus    # fallback
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# No `timescale in the RTL, so the same file compiles under both simulators.
# Verilator doesn't need one. Icarus defaults to 1s precision and then can't
# represent a 10ns clock, so pass it in from here.
TIMESCALE = ("1ns", "1ps")
sys.path.insert(0, str(REPO))

try:  # cocotb >= 2.0
    from cocotb_tools.runner import get_runner
except ImportError:  # cocotb 1.x
    from cocotb.runner import get_runner


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", default=os.environ.get("SIM", "verilator"),
                    choices=["verilator", "icarus"])
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--cycles", type=int, default=600)
    ap.add_argument("--waves", action="store_true", help="dump a VCD waveform")
    args = ap.parse_args()

    out_dir = REPO / "results" / "stage1"
    build_dir = REPO / "sim_build" / f"stage1_{args.sim}"
    out_dir.mkdir(parents=True, exist_ok=True)

    runner = get_runner(args.sim)

    build_args = []
    if args.sim == "verilator":
        # --coverage -> line + toggle into coverage.dat
        # -Wno-fatal keeps lint warnings from killing the build
        build_args = ["--coverage", "-Wno-fatal"]
        if args.waves:
            build_args.append("--trace")

    print(f"[stage1] building with {args.sim} ...", flush=True)
    runner.build(
        sources=[REPO / "rtl" / "counter.v"],
        hdl_toplevel="counter",
        build_dir=build_dir,
        build_args=build_args,
        always=True,
        waves=args.waves,
        timescale=TIMESCALE,
    )

    env = {
        "PYTHONPATH": os.pathsep.join([str(REPO), str(REPO / "tb")]),
        "TB_SEED": str(args.seed),
        "TB_CYCLES": str(args.cycles),
        "TB_OUT_DIR": str(out_dir),
    }

    # Verilator 5.020 has no runtime arg to choose where coverage goes -- checked
    # verilated.cpp, it only parses +verilator+{debug,error+limit,prof+*,
    # rand+reset,seed,version}. So VerilatedCov::write() always drops
    # coverage.dat into the sim's cwd, which is test_dir. Clear stale copies
    # first so an old run can't be mistaken for this one.
    test_dir = REPO / "tb"
    cov_src = test_dir / "coverage.dat"
    cov_dat = out_dir / "coverage.dat"
    if args.sim == "verilator":
        for stale in (cov_src, cov_dat):
            if stale.exists():
                stale.unlink()
    plusargs = []

    print(f"[stage1] running test (seed={args.seed}, cycles={args.cycles}) ...", flush=True)
    results_xml = runner.test(
        hdl_toplevel="counter",
        test_module="test_counter",
        test_dir=REPO / "tb",
        build_dir=build_dir,
        results_xml=str(out_dir / f"results_seed{args.seed}.xml"),
        plusargs=plusargs,
        extra_env=env,
        waves=args.waves,
        timescale=TIMESCALE,
    )
    print(f"[stage1] cocotb results XML: {results_xml}")

    # ---- Verilator code coverage -----------------------------------------
    if args.sim == "verilator":
        if cov_src.exists():
            shutil.move(str(cov_src), str(cov_dat))
        if not cov_dat.exists():
            print("[stage1] NOTE: Verilator produced no coverage.dat. "
                  "Functional coverage JSON is unaffected.")
        else:
            n_points = sum(1 for ln in cov_dat.read_text().splitlines()
                           if ln.startswith("C "))
            print(f"[stage1] Verilator code coverage: {cov_dat} "
                  f"({n_points} coverage points, {cov_dat.stat().st_size} bytes)")
            annot = out_dir / "coverage_annotated"
            if annot.exists():
                shutil.rmtree(annot)
            try:
                # --annotate-all matters: plain --annotate only emits files
                # that still have uncovered points, so at 100% you get an empty
                # directory and exit code 0.
                proc = subprocess.run(
                    ["verilator_coverage", "--annotate", str(annot),
                     "--annotate-all", "--annotate-min", "1", str(cov_dat)],
                    check=True, cwd=REPO, capture_output=True, text=True,
                )
                summary = (proc.stdout + proc.stderr).strip()
                if summary:
                    print(f"[stage1] verilator_coverage: {summary}")
                    (out_dir / "code_coverage_summary.txt").write_text(summary + "\n")
                written = sorted(q for q in annot.rglob("*") if q.is_file())
                print(f"[stage1] annotated source: {annot} ({len(written)} file(s))")
            except (subprocess.CalledProcessError, FileNotFoundError) as exc:
                print(f"[stage1] verilator_coverage --annotate failed: {exc}")

    # ---- what landed on disk ---------------------------------------------
    print("\n[stage1] artifacts on disk:")
    for p in sorted(out_dir.rglob("*")):
        if p.is_file():
            print(f"    {p.relative_to(REPO)}  ({p.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
