# Novelty-based test selection for coverage closure

This is me trying to reproduce a result from the hardware verification
literature: that if you choose simulation stimuli by how *novel* they are, you
hit a functional coverage target in fewer simulations than if you choose them
at random.

My background is ML, not hardware. Learning the verification side is half the
point of doing this, so there are probably things in here a real verification
engineer would do differently.

Papers I'm working from:

- Zheng, Eder & Blackmore, [Using Neural Networks for Novelty-based Test Selection](https://arxiv.org/abs/2207.00445).
  They report up to 49.37% fewer simulations to reach 99.5% coverage on a
  commercial DSP unit.
- Zheng, Blackmore, Buckingham & Eder, [Detecting Stimuli with Novel Temporal Patterns](https://arxiv.org/abs/2407.02510).
  26.9% fewer tests to 98.5% on a bus bridge. This one scores novelty on the
  temporal pattern rather than a static feature vector, which is closer to the
  GRU autoencoder I've built before.

Everything runs locally on free tools. There's no SystemVerilog in the
project. The testbench is Python, driven by cocotb.

**Status: Stage 1 of 7.** I'm not claiming anything yet. Every number below
comes from a script in `scripts/`, and where something isn't measured I've
tried to say so.

## Setup

```bash
bash scripts/00_install.sh        # apt packages, needs sudo, run once
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/01_smoke_test.py --sim verilator
```

Versions are in [docs/VERSIONS.md](docs/VERSIONS.md). Short version: Verilator
5.020, Icarus Verilog 12.0 as a fallback, cocotb 1.9.2, Python 3.12.3.

## Code coverage vs functional coverage

I kept conflating these at the start, so writing it down.

Code coverage (line, branch, toggle) is what the simulator gives you for free.
It tells you which lines of RTL ran and which signal bits flipped both ways. It
doesn't tell you whether the design ever got into an interesting *state*.

Functional coverage is a list someone writes by hand of the situations that
actually matter, like a FIFO going full, or an arbiter handing the grant from
one master to another. The testbench watches for them and ticks them off.

The experiment measures functional coverage. I collect code coverage as well,
but only as a sanity check.

## Stage 1: does the toolchain work

No ML in this stage. The only goal was to get a simulation running from Python
with coverage landing on disk, because I'd been warned this is where projects
like this stall. That warning was correct. It took most of a weekend and
almost none of it was interesting.

The design here is throwaway. [`rtl/counter.v`](rtl/counter.v) is an 8-bit
counter with a count enable, synchronous load, asynchronous active-low reset
and a wrap flag. I wrote it in Verilog-2001 rather than SystemVerilog so both
simulators would take it without extra flags. The real design under test gets
picked in Stage 2.

```bash
.venv/bin/python scripts/01_smoke_test.py --sim verilator --seed 1
.venv/bin/python scripts/01_smoke_test.py --sim icarus    --seed 1
```

That writes into `results/stage1/`:

- `functional_coverage_seed<N>.json`: 15/15 bins hit, per-bin counts, and the
  binary coverage vector for that test
- `coverage.dat`: Verilator code coverage, 26 raw points
- `code_coverage_summary.txt`: `Total coverage (14/14) 100.00%`
- `coverage_annotated/counter.v`: the RTL with per-line execution counts

Both simulators pass with 0 mismatches against a Python reference model that
gets checked every clock cycle.

I also checked that the same seed gives bit-identical coverage vectors and
identical per-bin hit counts on both simulators (611 cycles, 0 errors). That
seemed worth confirming rather than assuming, since later stages compare
vectors that were produced by separate runs.

### Things that broke

Writing these down because most of them failed quietly instead of erroring.

**Scoreboard off by one, 446 false mismatches.** My first version of the
testbench drove signals directly in between phases, and one clock edge slipped
past unobserved while `en` was still high. The reference model missed that
increment and then stayed one behind the DUT for the rest of the run, so
everything after that point looked like a failure. The fix wasn't local: every
cycle now goes through a single `CounterEnv.step()` that drives, advances,
updates the reference model and samples coverage, so there's nowhere for an
edge to hide.

**`verilator_coverage --annotate` wrote nothing and exited 0.** It only writes
out files that still have uncovered points in them. At 100% coverage you get an
empty directory and no error message, which took me a while to work out. Need
`--annotate-all`.

**No `+verilator+coverage+file+` in Verilator 5.020.** cocotb's source has a
comment saying you can point the coverage output somewhere with this plusarg. I
couldn't get it to do anything, and checking `verilated.cpp` the runtime only
parses `+verilator+{debug,error+limit,prof+*,rand+reset,seed,version}`. I think
the comment describes a newer version. So `coverage.dat` has to be collected
from the simulation's working directory instead.

**Icarus needs a timescale.** It has no default, so a 10 ns clock isn't
representable and the test dies with a precision error. Verilator doesn't care.
Passing the timescale from the runner instead of putting it in the RTL keeps
the design simulator-neutral.

## Plan for the rest

2. Pick the real design under test. I'm looking at a synchronous FIFO, a
   round-robin arbiter, or a small bus bridge. The thing I'm most worried about
   is coverage headroom. If constrained-random closes the coverage model
   immediately then there's no gap for novelty to close and the experiment has
   nothing to measure.
3. Write the functional coverage model. Plain English in this README first,
   then implement it. Each test needs to emit a coverage vector I can store.
4. Constrained-random baseline. Coverage against number of tests, averaged over
   at least 5 seeds, up to a target fixed in advance. Everything gets measured
   against this, so the baseline needs to be strong rather than convenient.
5. The novelty selector. Start with distance in a hand-built feature space,
   then try a GRU autoencoder scoring novelty by reconstruction error or by
   distance to k-means centroids in the latent space. The simple version stays
   in the comparison, and if the neural one doesn't beat it I want to know.
6. Compare. Same design, same coverage model, same target, same seeds. Report
   the reduction with its variance across seeds, not as a single number.
7. Honesty pass. Check whether the novelty score is getting any information
   from coverage results, directly or indirectly. If it is, then what I've
   built is coverage-*directed* selection, which is a real technique but a
   different and easier claim, and this README has to say which one it was.
   Also check whether any gain survives a different coverage target and a
   different design. If novelty doesn't beat random, that goes in here as the
   result.

## Layout

```
rtl/       designs under test
tb/        cocotb testbenches
simlib/    shared code (coverage model, later the selectors)
scripts/   numbered entry points
results/   measured output
docs/      versions, notes
```
