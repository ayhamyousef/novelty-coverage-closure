#!/usr/bin/env python3
"""
Stage 2 coverage headroom probe for rtl/sfifo.v.

Novelty-based selection can only beat random selection if random selection
actually struggles. If plain random stimulus drives the FIFO into every
interesting state inside one short test then there is no gap to measure, and
the honest answer is that there is no difference.

So this measures where random stimulus actually takes the design, across a
range of depths, before I write the coverage model. Then Stage 3 can set its
bins from something measured.

The sweep quoted in the README is 5 depths x 2 stimulus biases x 5 seeds, so
50 simulations plus one Verilator build per depth. That takes 38 s on my
machine, and reruns byte-identical, so it is cheap to redo rather than trust.

Usage:
    .venv/bin/python scripts/02_headroom_probe.py
    .venv/bin/python scripts/02_headroom_probe.py --depths 4 5 6 7 --seeds 10
"""

import argparse
import json
import os
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from simlib.simrunner import build_dut, run_test  # noqa: E402

# (label, p_wr, p_rd). Unbiased is the case I care about, since that is what a
# constrained-random generator produces if nobody biases it.
BIASES = [
    ("unbiased", 0.50, 0.50),
    ("write-heavy", 0.70, 0.30),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", default=os.environ.get("SIM", "verilator"),
                    choices=["verilator", "icarus"])
    ap.add_argument("--depths", type=int, nargs="+", default=[4, 5, 6],
                    help="LGFLEN values, i.e. log2 of FIFO depth")
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--cycles", type=int, default=500,
                    help="clock cycles per test; one test = one stimulus sequence")
    args = ap.parse_args()

    out_dir = REPO / "results" / "stage2"
    out_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for lgflen in args.depths:
        build_dir = REPO / "sim_build" / f"stage2_{args.sim}_lg{lgflen}"
        print(f"\n[stage2] building sfifo with LGFLEN={lgflen} (depth {1 << lgflen}) ...",
              flush=True)
        runner = build_dut(
            args.sim,
            sources=[REPO / "rtl" / "sfifo.v"],
            toplevel="sfifo",
            build_dir=build_dir,
            parameters={"LGFLEN": lgflen},
        )

        for label, p_wr, p_rd in BIASES:
            for seed in range(1, args.seeds + 1):
                tag = f"lg{lgflen}_{label}_seed{seed}"
                out_json = out_dir / f"probe_{tag}.json"
                run_test(
                    runner,
                    toplevel="sfifo",
                    test_module="test_sfifo_probe",
                    test_dir=REPO / "tb",
                    build_dir=build_dir,
                    results_xml=build_dir / f"results_{tag}.xml",
                    env={
                        "PYTHONPATH": os.pathsep.join([str(REPO), str(REPO / "tb")]),
                        "TB_SEED": str(seed),
                        "TB_CYCLES": str(args.cycles),
                        "TB_P_WR": str(p_wr),
                        "TB_P_RD": str(p_rd),
                        "TB_LGFLEN": str(lgflen),
                        "TB_OUT": str(out_json),
                    },
                )
                with open(out_json) as fh:
                    rec = json.load(fh)
                rec["bias"] = label
                records.append(rec)

    # Record what produced this file. Rerunning with different arguments
    # overwrites it, and without this there is no way to tell from the file
    # which run you are looking at.
    payload = {
        "config": {"sim": args.sim, "depths": args.depths, "seeds": args.seeds,
                   "cycles": args.cycles, "biases": [b[0] for b in BIASES]},
        "records": records,
    }
    with open(out_dir / "probe_summary.json", "w") as fh:
        json.dump(payload, fh, indent=2)

    # ---- aggregate -------------------------------------------------------
    print(f"\n{'depth':>6} {'bias':>12} {'reached full':>13} {'max fill':>16} "
          f"{'% cycles full':>14} {'% cycles empty':>15} {'R+W at full':>12}")
    print("-" * 92)
    lines = []
    for lgflen in args.depths:
        for label, _, _ in BIASES:
            rs = [r for r in records if r["lgflen"] == lgflen and r["bias"] == label]
            if not rs:
                continue
            depth = rs[0]["depth"]
            n_full = sum(1 for r in rs if r["reached_full"])
            maxf = [r["max_fill"] for r in rs]
            pf = 100.0 * statistics.fmean(r["frac_cycles_full"] for r in rs)
            pe = 100.0 * statistics.fmean(r["frac_cycles_empty"] for r in rs)
            rwf = sum(r["rw_while_full"] for r in rs)
            line = (f"{depth:>6} {label:>12} {n_full:>8}/{len(rs):<4} "
                    f"{statistics.fmean(maxf):>7.1f} (max {max(maxf):>3}) "
                    f"{pf:>13.2f}% {pe:>14.2f}% {rwf:>12}")
            print(line)
            lines.append(line)

    errs = sum(r["scoreboard_errors"] for r in records)
    print("-" * 92)
    print(f"{len(records)} simulations, {errs} scoreboard mismatches against the reference FIFO")
    print(f"raw data: {(out_dir / 'probe_summary.json').relative_to(REPO)}")
    return 0 if errs == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
