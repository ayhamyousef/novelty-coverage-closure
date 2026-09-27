# Novelty-based test selection for coverage closure

This is me trying to reproduce a result from the hardware verification
literature: that if you choose simulation stimuli by how *novel* they are, you
hit a functional coverage target in fewer simulations than if you choose them
at random.

The testbench is Python, using cocotb, and everything runs locally on free
tools. Every number below comes out of a script in `scripts/`, and where
something hasn't been measured I've tried to say so.

**Status: Stage 2 of 7.** No claim about novelty selection yet. That starts at
Stage 5.

## What's working so far

- A Python testbench driving both Verilator and Icarus, writing functional
  coverage to disk after every test as a fixed-length binary vector (Stage 1).
- 15/15 functional coverage bins and 14/14 Verilator code coverage points on
  the Stage 1 smoke-test design, with 0 mismatches against a Python reference
  model checked every clock cycle.
- The design under test chosen, and its coverage headroom measured before
  writing any coverage model: 50 simulations, 0 mismatches, showing that
  unbiased random stimulus never pushes the FIFO past about 36 entries no
  matter how deep the FIFO actually is (Stage 2).

## What this project isn't

Worth saying early, since anyone who does verification for a living would
notice anyway.

- **No SystemVerilog, no UVM.** The testbench is Python with cocotb. That was a
  constraint I set myself, but it does mean this repo doesn't show the
  SystemVerilog and UVM skills most verification job ads ask for.
- **Open source simulators only.** Verilator and Icarus, not Xcelium or VCS.
  The coverage model in `simlib/covmodel.py` is a hand-rolled stand-in for what
  would normally be a SystemVerilog covergroup.
- **Small design.** A 486-line FIFO. The papers I'm working from used a
  commercial DSP unit and a commercial bus bridge, so anything I get here is at
  a much smaller scale than what they report.
- **No assertions, no formal.** The FIFO I vendored actually ships with formal
  properties and I'm not using them.

The ML method is the part I'd defend. The verification side I'm picking up as I
go, so some of it is probably done in ways a real verification engineer
wouldn't choose.

## Papers I'm working from

- Zheng, Eder & Blackmore, [Using Neural Networks for Novelty-based Test Selection](https://arxiv.org/abs/2207.00445).
  They report up to 49.37% fewer simulations to reach 99.5% coverage on a
  commercial DSP unit.
- Zheng, Blackmore, Buckingham & Eder, [Detecting Stimuli with Novel Temporal Patterns](https://arxiv.org/abs/2407.02510).
  26.9% fewer tests to 98.5% on a bus bridge. This one scores novelty on the
  temporal pattern rather than a static feature vector, which is closer to the
  GRU autoencoder I have built before.
- Bennett & Eder, [Review of Machine Learning for Micro-Electronic Design Verification](https://arxiv.org/abs/2503.11687),
  for background on why most of these techniques haven't reached mainstream
  industry use.

## Setup

```bash
bash scripts/00_install.sh        # apt packages, needs sudo, run once
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/01_smoke_test.py --sim verilator
```

Versions are in [docs/VERSIONS.md](docs/VERSIONS.md). Short version: Verilator
5.020, Icarus Verilog 12.0 as a fallback, cocotb 1.9.2, Python 3.12.3.

Add `--waves` to dump a waveform and open it in GTKWave:

```bash
.venv/bin/python scripts/01_smoke_test.py --sim verilator --seed 1 --waves
gtkwave results/stage1/waves_seed1.vcd
```

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
with coverage landing on disk, because I had been warned this is where projects
like this stall. That warning was correct. It took most of a weekend and almost
none of it was interesting.

The design here is throwaway. [`rtl/counter.v`](rtl/counter.v) is an 8-bit
counter with a count enable, synchronous load, asynchronous active-low reset
and a wrap flag. I wrote it in Verilog-2001 rather than SystemVerilog so both
simulators would take it without extra flags.

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
everything after that point looked like a failure. The fix was not local: every
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
representable and the test dies with a precision error. Verilator doesn't
care. Passing the timescale from the runner instead of putting it in the RTL
keeps the design simulator-neutral.

## Stage 2: picking the design under test

Three candidates, all open source and small enough to read in an evening.

| | Sync FIFO | Round-robin arbiter | AXI-Lite to WB bridge |
|---|---|---|---|
| Source | ZipCPU `sfifo.v` | alexforencich `arbiter.v` | ZipCPU `axlite2wbsp.v` |
| Size | 486 lines, 1 file | ~245 lines, 2 files | ~450 lines, 5 to 6 files |
| License | public domain | MIT | Apache-2.0 |
| Stimulus difficulty | low, 2 control bits | low to medium | high, 5-channel protocol |
| Hard states are | temporal | mostly combinational | temporal plus protocol |

I went with the FIFO. Its hard states need a pattern rather than one lucky
cycle, the stimulus is two bits wide so I can't drive it illegally, and
`LGFLEN` gives me a difficulty dial. Provenance, the exact RTL semantics, and
why I turned down the other two are in [docs/DUT.md](docs/DUT.md). The arbiter
is being kept for Stage 7, where the question is whether any gain survives a
change of design.

### Measuring the headroom first

Before writing a coverage model it seemed worth knowing whether random stimulus
struggles at all. If one random test reaches every interesting state then there
is no gap for a smarter selector to close, and the honest answer would be "no
difference". So I measured it rather than assuming.

```bash
.venv/bin/python scripts/02_headroom_probe.py --depths 4 5 6 7 8 --seeds 5 --cycles 500
```

50 simulations, 500 cycles each, two stimulus biases, five seeds, 0 scoreboard
mismatches against a Python reference FIFO. Raw data is in
`results/stage2/probe_summary.json`. The whole sweep takes 38 seconds and comes
back byte-identical on a rerun, so it's cheap to redo rather than take my word
for it.

| Depth | Stimulus | Seeds reaching full | Mean max fill | % cycles full |
|---|---|---|---|---|
| 16 | unbiased, p=0.5/0.5 | 3/5 | 15.2 | 2.64% |
| 16 | write-heavy, 0.7/0.3 | 5/5 | 16.0 | 51.88% |
| 32 | unbiased | 2/5 | 22.4 | 0.48% |
| 32 | write-heavy | 5/5 | 32.0 | 47.32% |
| 64 | unbiased | 0/5 | 23.2 | 0.00% |
| 64 | write-heavy | 5/5 | 64.0 | 38.32% |
| 128 | unbiased | 0/5 | 23.2 | 0.00% |
| 128 | write-heavy | 5/5 | 128.0 | 19.88% |
| 256 | unbiased | 0/5 | 23.2 | 0.00% |
| 256 | write-heavy | 0/5 | 199.2 | 0.00% |

Occupancy distribution, pooled over seeds:

| Depth | Stimulus | median fill | p90 | p99 | max seen |
|---|---|---|---|---|---|
| 32 | unbiased | 8 | 25 | 31 | 32 |
| 32 | write-heavy | 31 | 32 | 32 | 32 |
| 64 | unbiased | 8 | 26 | 34 | 36 |
| 64 | write-heavy | 63 | 64 | 64 | 64 |

### What that actually means

Unbiased stimulus is a plus-or-minus-one random walk on the fill level. It sits
around a median of 8 and never gets past 36 regardless of how deep the FIFO is.
Depths 64, 128 and 256 all give the same 23.2 mean max fill, because past about
36 the extra depth isn't reachable, so it stops mattering.

So whether the FIFO ever fills is decided by the stimulus, not by the FIFO. At
depth 64 the interesting states are unreachable under unbiased stimulus and
trivial under write-heavy stimulus. That's the structure I need. It also means
the difficulty isn't in the RTL parameters, it's in the space of stimulus
sequences the generator can produce.

Two things I'm carrying into the next stages:

- Stage 3 should bin the occupancy so that there are real bins above fill 36,
  where unbiased stimulus doesn't go.
- Stage 4 has to sample the stimulus parameter space, not just flip p=0.5
  coins. A baseline that only ever produces unbiased traffic would be easy to
  beat, and the improvement would be an artefact of a weak baseline rather than
  a real result.

## Plan for the rest

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
   from coverage results, directly or indirectly. If it is, then what I built is
   coverage-*directed* selection, which is a real technique but a different and
   easier claim, and this README has to say which one it was. Also check whether
   any gain survives a different coverage target and a different design. If
   novelty doesn't beat random, that goes here as the result.

## Layout

```
rtl/       designs under test
tb/        cocotb testbenches
simlib/    shared code (coverage model, later the selectors)
scripts/   numbered entry points
results/   measured output
docs/      versions, DUT provenance, notes
```

## License and third-party code

My code is MIT, see [LICENSE](LICENSE).

`rtl/sfifo.v` isn't mine. It's from [ZipCPU/wb2axip](https://github.com/ZipCPU/wb2axip),
written by Dan Gisselquist, released to the public domain, and vendored here
unmodified. The upstream commit and a checksum are recorded in
[docs/DUT.md](docs/DUT.md). `rtl/counter.v` is mine, written as a throwaway for
Stage 1.
