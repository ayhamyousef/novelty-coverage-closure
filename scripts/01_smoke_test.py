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

sys.path.insert(0, str(REPO))

# The timescale lives in simlib.simrunner, not here. There's no `timescale in
# the RTL so the same file compiles under both simulators, and Icarus needs one
# passed in or it can't represent a 10ns clock.
from simlib.simrunner import build_dut, run_test  # noqa: E402


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

    # --coverage gets line and toggle coverage into coverage.dat
    build_args = ["--coverage"] if args.sim == "verilator" else []

    print(f"[stage1] building with {args.sim} ...", flush=True)
    runner = build_dut(
        args.sim,
        sources=[REPO / "rtl" / "counter.v"],
        toplevel="counter",
        build_dir=build_dir,
        extra_build_args=build_args,
        waves=args.waves,
    )

    env = {
        "PYTHONPATH": os.pathsep.join([str(REPO), str(REPO / "tb")]),
        "TB_SEED": str(args.seed),
        "TB_CYCLES": str(args.cycles),
        "TB_OUT_DIR": str(out_dir),
    }

    # Verilator 5.020 has no runtime arg to choose where coverage goes. Checked
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
    results_xml = run_test(
        runner,
        toplevel="counter",
        test_module="test_counter",
        test_dir=REPO / "tb",
        build_dir=build_dir,
        results_xml=out_dir / f"results_seed{args.seed}.xml",
        plusargs=plusargs,
        env=env,
        waves=args.waves,
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

    # ---- the waveform, if one was dumped ---------------------------------
    # cocotb's Verilator main writes dump.vcd into the simulation's working
    # directory, which is test_dir and not the build directory. Move it in with
    # the rest of the results so it is findable.
    if args.waves:
        produced = REPO / "tb" / "dump.vcd"
        if produced.exists():
            dest = out_dir / f"waves_seed{args.seed}.vcd"
            shutil.move(str(produced), str(dest))
            rel = dest.relative_to(REPO)
            print(f"[stage1] waveform: {rel} ({dest.stat().st_size} bytes), "
                  f"open with: gtkwave {rel}")
        else:
            print("[stage1] NOTE: --waves was asked for but no dump.vcd appeared.")

    # ---- what landed on disk ---------------------------------------------
    print("\n[stage1] artifacts on disk:")
    for p in sorted(out_dir.rglob("*")):
        if p.is_file():
            print(f"    {p.relative_to(REPO)}  ({p.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
