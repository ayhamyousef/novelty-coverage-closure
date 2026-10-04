#!/usr/bin/env python3
"""
Stage 3: print the coverage model and check every bin is reachable.

Two separate questions, and they get answered separately.

1. Is the model well formed? A bin that no stimulus at all can reach is a bug
   in the model, not a hard target, and it would sit uncovered forever and stop
   the coverage curve from ever hitting 100%. Those have to be found now.

2. How much of it does plain unbiased random get for free? Not to tune the
   model, which would be rigging the experiment. Just to know in advance
   roughly how much room Stages 4 to 6 have to work in.

Cost: one Verilator build plus about 40 short simulations, well under a minute.

Usage:
    .venv/bin/python scripts/03_coverage_model.py
    .venv/bin/python scripts/03_coverage_model.py --list   # just print the bins
"""

import argparse
import json
import os
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from simlib.fifo_coverage import build_fifo_coverage_model  # noqa: E402
from simlib.stimulus import StimulusSpec, random_spec  # noqa: E402

from simlib.simrunner import build_dut, run_test  # noqa: E402

LGFLEN = 5
DEPTH = 1 << LGFLEN

# The reachability sets below deliberately use long 600-cycle tests. They are
# answering "can any stimulus reach this bin at all", so being generous is the
# point. The feasibility curve further down uses the real 120-cycle test
# length, because that one is previewing Stage 4.
UNBIASED = StimulusSpec(cycles=600, p_wr=0.5, p_rd=0.5)

# A deliberately varied set, used only to answer "can anything reach this bin".
# These are hand-written to push at the corners, which is exactly what a human
# writing directed tests would do, and exactly what Stage 5 is supposed to
# learn to do without being told.
VARIED = {
    "write_heavy_burst": StimulusSpec(cycles=600, p_wr=0.8, p_rd=0.2,
                                      p_burst=0.05, wr_burst=DEPTH + 4),
    "read_heavy_burst": StimulusSpec(cycles=600, p_wr=0.2, p_rd=0.8,
                                     p_burst=0.05, rd_burst=DEPTH + 4),
    "both_bursts": StimulusSpec(cycles=800, p_wr=0.5, p_rd=0.5, p_burst=0.08,
                                wr_burst=DEPTH + 2, rd_burst=DEPTH + 2),
    "simultaneous": StimulusSpec(cycles=600, p_wr=0.95, p_rd=0.95),
    "with_resets": StimulusSpec(cycles=600, p_wr=0.7, p_rd=0.3, p_burst=0.06,
                                wr_burst=DEPTH + 4, p_reset=0.01),
    "fill_then_drain": StimulusSpec(cycles=900, p_wr=0.5, p_rd=0.5, p_burst=0.10,
                                    wr_burst=DEPTH + 8, rd_burst=DEPTH + 8,
                                    p_reset=0.002),
}


def union(vectors):
    if not vectors:
        return []
    out = [0] * len(vectors[0])
    for v in vectors:
        for i, b in enumerate(v):
            if b:
                out[i] = 1
    return out


def crosscheck() -> int:
    """Same spec, same seed, both simulators. The vectors have to match.

    Stages 4 to 6 pool coverage vectors produced by separate simulator
    processes, so if the two simulators disagreed about what a test covered
    then every comparison downstream would be built on sand. Cheap to check,
    and it already caught one real bug: the memory inside the FIFO starts at X
    under Icarus and at zero under Verilator, so reading o_data before the
    first write threw on one simulator and not the other.
    """
    spec = StimulusSpec(cycles=400, p_wr=0.7, p_rd=0.4, p_burst=0.08,
                        wr_burst=40, rd_burst=40, p_reset=0.01)
    got = {}
    for sim in ("verilator", "icarus"):
        bd = REPO / "sim_build" / f"stage3_xcheck_{sim}"
        runner = build_dut(sim, sources=[REPO / "rtl" / "sfifo.v"],
                           toplevel="sfifo", build_dir=bd,
                           parameters={"LGFLEN": LGFLEN})
        oj = bd / "cov.json"
        run_test(runner, toplevel="sfifo", test_module="test_sfifo_cov",
                 test_dir=REPO / "tb", build_dir=bd, results_xml=bd / "r.xml",
                 env={"PYTHONPATH": os.pathsep.join([str(REPO), str(REPO / "tb")]),
                      "TB_SEED": "42", "TB_LGFLEN": str(LGFLEN),
                      "TB_STIM": json.dumps(spec.to_dict()), "TB_OUT": str(oj)})
        with open(oj) as fh:
            got[sim] = json.load(fh)

    v, i = got["verilator"], got["icarus"]
    same = (v["bin_names"] == i["bin_names"] and v["vector"] == i["vector"]
            and v["counts"] == i["counts"])
    print(f"\nverilator: {v['n_covered']}/{v['n_bins']} bins, "
          f"{v['scoreboard_errors']} errors")
    print(f"icarus   : {i['n_covered']}/{i['n_bins']} bins, "
          f"{i['scoreboard_errors']} errors")
    print(f"bin names identical : {v['bin_names'] == i['bin_names']}")
    print(f"vectors identical   : {v['vector'] == i['vector']}")
    print(f"hit counts identical: {v['counts'] == i['counts']}")
    if not same:
        bad = [n for n, a, b in zip(v["bin_names"], v["vector"], i["vector"]) if a != b]
        print(f"DIFFERING BINS: {bad}")
    return 0 if same and not (v["scoreboard_errors"] or i["scoreboard_errors"]) else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", default=os.environ.get("SIM", "verilator"),
                    choices=["verilator", "icarus"])
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--cycles", type=int, default=120,
                    help="clock cycles per sampled test. 120 is the shortest "
                         "length at which every bin still closes, measured.")
    ap.add_argument("--feasibility", type=int, default=30,
                    help="randomly sampled specs, to preview Stage 4's headroom")
    ap.add_argument("--crosscheck", action="store_true",
                    help="run one test on both simulators and compare the vectors")
    ap.add_argument("--list", action="store_true", help="print the bins and stop")
    args = ap.parse_args()

    if args.crosscheck:
        return crosscheck()

    model = build_fifo_coverage_model(DEPTH)
    names = model.bin_names

    if args.list:
        for cp in model.coverpoints:
            print(f"\n{cp.name}  ({len(cp.bins)} bins)")
            print(f"  {cp.description}")
            for b in cp.bins:
                print(f"    {cp.name}.{b}")
        print(f"\n{model.n_bins} bins total")
        return 0

    out_dir = REPO / "results" / "stage3"
    out_dir.mkdir(parents=True, exist_ok=True)
    build_dir = REPO / "sim_build" / f"stage3_{args.sim}_lg{LGFLEN}"

    print(f"[stage3] building sfifo with LGFLEN={LGFLEN} (depth {DEPTH}) ...", flush=True)
    runner = build_dut(
        args.sim,
        sources=[REPO / "rtl" / "sfifo.v"],
        toplevel="sfifo",
        build_dir=build_dir,
        parameters={"LGFLEN": LGFLEN},
    )

    def run(tag, spec, seed):
        out_json = out_dir / f"cov_{tag}_seed{seed}.json"
        run_test(
            runner,
            toplevel="sfifo",
            test_module="test_sfifo_cov",
            test_dir=REPO / "tb",
            build_dir=build_dir,
            results_xml=build_dir / f"r_{tag}_{seed}.xml",
            env={
                "PYTHONPATH": os.pathsep.join([str(REPO), str(REPO / "tb")]),
                "TB_SEED": str(seed),
                "TB_LGFLEN": str(LGFLEN),
                "TB_STIM": json.dumps(spec.to_dict()),
                "TB_OUT": str(out_json),
            },
        )
        with open(out_json) as fh:
            return json.load(fh)

    records = []
    print(f"[stage3] unbiased random, {args.seeds} seeds ...", flush=True)
    unbiased_recs = [run("unbiased", UNBIASED, s) for s in range(1, args.seeds + 1)]
    records += unbiased_recs

    varied_recs = []
    for tag, spec in VARIED.items():
        print(f"[stage3] {tag}, {args.seeds} seeds ...", flush=True)
        varied_recs += [run(tag, spec, s) for s in range(1, args.seeds + 1)]
    records += varied_recs

    errs = sum(r["scoreboard_errors"] for r in records)
    u_unbiased = union([r["vector"] for r in unbiased_recs])
    u_all = union([r["vector"] for r in records])

    # ---- report ----------------------------------------------------------
    print(f"\n{'coverpoint':>14} {'bins':>5} {'unbiased reaches':>17} {'anything reaches':>17}")
    print("-" * 58)
    i = 0
    for cp in model.coverpoints:
        n = len(cp.bins)
        ub = sum(u_unbiased[i:i + n])
        al = sum(u_all[i:i + n])
        print(f"{cp.name:>14} {n:>5} {ub:>13}/{n:<3} {al:>13}/{n:<3}")
        i += n
    print("-" * 58)
    print(f"{'TOTAL':>14} {model.n_bins:>5} {sum(u_unbiased):>13}/{model.n_bins:<3} "
          f"{sum(u_all):>13}/{model.n_bins:<3}")

    never = [names[j] for j, b in enumerate(u_all) if not b]
    only_hard = [names[j] for j in range(len(names)) if u_all[j] and not u_unbiased[j]]

    print(f"\n{len(records)} simulations, {errs} scoreboard mismatches")
    print(f"\nReachable but not by unbiased random ({len(only_hard)} bins) "
          f"- this is the room Stages 4 to 6 have to work in:")
    for n in only_hard:
        print(f"    {n}")

    if never:
        print(f"\nNOT REACHED BY ANYTHING ({len(never)} bins). "
              f"These are model bugs and need fixing before Stage 4:")
        for n in never:
            print(f"    {n}")
    else:
        print("\nEvery bin was reached by at least one stimulus, so the model can "
              "close and nothing is permanently stuck.")

    # ---- feasibility: what does sampling the parameter space get? ---------
    print(f"\n[stage3] feasibility check, {args.feasibility} randomly sampled specs ...",
          flush=True)
    rng = random.Random(12345)
    cumulative, curve, feas_recs = [0] * model.n_bins, [], []
    for k in range(args.feasibility):
        # Same generator Stage 4's baseline uses. Keeping one copy means the
        # reachability check here and the baseline there can't drift apart.
        spec = random_spec(rng, cycles=args.cycles)
        r = run(f"sampled{k}", spec, seed=1000 + k)
        feas_recs.append(r)
        for j, b in enumerate(r["vector"]):
            if b:
                cumulative[j] = 1
        curve.append(sum(cumulative))
    errs += sum(r["scoreboard_errors"] for r in feas_recs)

    print("\ncumulative bins covered as sampled tests accumulate:")
    for k in range(0, len(curve), 5):
        chunk = " ".join(f"{curve[j]:>3}" for j in range(k, min(k + 5, len(curve))))
        print(f"  tests {k + 1:>3} to {min(k + 5, len(curve)):>3}:  {chunk}")
    print(f"\n{args.feasibility} sampled tests reached {curve[-1]}/{model.n_bins} bins. "
          f"Still missing: {[names[j] for j, b in enumerate(cumulative) if not b] or 'nothing'}")

    summary = {
        "config": {"sim": args.sim, "seeds": args.seeds,
                   "feasibility": args.feasibility, "cycles": args.cycles},
        "depth": DEPTH,
        "feasibility_curve": curve,
        "feasibility_union": cumulative,
        "n_bins": model.n_bins,
        "bin_names": names,
        "union_unbiased": u_unbiased,
        "union_all": u_all,
        "unreachable": never,
        "reachable_but_not_unbiased": only_hard,
        "n_simulations": len(records),
        "scoreboard_errors": errs,
        "records": records,
    }
    with open(out_dir / "model_check.json", "w") as fh:
        json.dump(summary, fh, indent=2)
    print(f"\nraw data: {(out_dir / 'model_check.json').relative_to(REPO)}")
    return 1 if (errs or never) else 0


if __name__ == "__main__":
    raise SystemExit(main())
