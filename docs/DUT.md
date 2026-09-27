# Design under test

## What I picked: ZipCPU `sfifo.v`, a synchronous FIFO

| | |
|---|---|
| Source | <https://github.com/ZipCPU/wb2axip>, `rtl/sfifo.v` |
| Upstream commit | `8ea6f5ce9adb13de6d617770dac3b649e7e52462` (2025-02-05) |
| Fetched | 2026-09-23 |
| sha256 of my copy | `76a653781f1ecca1c1da92c4e5105ecd5d0f832b13cbfd31300ec88d9947e0c1` |
| License | Public domain, stated in the file header |
| Author | Dan Gisselquist, Gisselquist Technology LLC |
| Size | 486 lines, of which 1 to 240 are RTL and 241 to 481 are formal properties |
| Language | Verilog-2001 |
| Dependencies | None, single file |

Vendored unmodified into [`rtl/sfifo.v`](../rtl/sfifo.v). I'm not going to
edit it. The whole reason for using someone else's RTL is that I don't get to
decide how hard the design is, and editing it would throw that away.

The `ifdef FORMAL` block never compiles, since I never define `FORMAL`, so
lines 241 to 481 are inert in simulation. Verilator `--lint-only -Wall` gives
zero warnings and Icarus takes the file in its default Verilog-2005 mode, so
the fallback simulator stays available.

### Interface

```
i_clk  i_reset
i_wr   i_data[BW-1:0]      ->  o_full
i_rd                       ->  o_empty
                           ->  o_fill[LGFLEN:0]
                           ->  o_data[BW-1:0]

parameters: BW=8, LGFLEN=4, OPT_ASYNC_READ=1,
            OPT_WRITE_ON_FULL=0, OPT_READ_ON_EMPTY=0
```

There are no almost-full or almost-empty pins on this FIFO, which I thought was
a problem at first. It isn't: `o_fill` gives the exact occupancy, so those
thresholds can live in the coverage model instead of in the RTL. That's better
anyway, since the design stays untouched.

### Semantics, read off the RTL rather than assumed

```verilog
w_wr    = i_wr && !o_full                                       // line 71
w_rd    = i_rd && !o_empty                                      // line 72
o_full  = (i_rd && OPT_WRITE_ON_FULL) ? 1'b0 : o_fill[LGFLEN]   // line 109
o_empty = (OPT_READ_ON_EMPTY && i_wr) ? 1'b0 : r_empty          // line 161
```

Writing to a full FIFO and reading from an empty one are silently dropped, not
flagged as errors. So "the testbench asserted `i_wr` while the FIFO was full"
and "the FIFO accepted a write" are two different events, and the coverage
model has to tell them apart. I'd have got this wrong if I'd assumed the
behaviour instead of reading it.

`OPT_WRITE_ON_FULL` and `OPT_READ_ON_EMPTY` change those rules. I'm leaving
both at 0 for now. Turning them on is a cheap way to get a DUT that behaves
differently for the Stage 7 robustness check, without having to switch designs.

## Why this one and not the others

I compared three candidates. The FIFO won on three things:

1. **Its hard states need a pattern, not a lucky cycle.** Filling the FIFO
   takes a sustained run of writes that are not matched by reads. No single
   cycle gets you there. That's the kind of structure a sequence autoencoder
   is for, and it's closer to the DUT in the Zheng et al. temporal-patterns
   paper than a combinational arbiter would be.
2. **I can't drive the stimulus illegally.** Two one-bit controls. Compare the
   AXI-Lite bridge, where getting the protocol wrong gives you a hang and
   plausible-looking but meaningless coverage rather than an error.
3. **Difficulty has a dial.** `LGFLEN` sets the depth, and the headroom probe
   in the README shows depth moves the reachability of the interesting states a
   long way.

### Round-robin arbiter, not chosen yet

alexforencich `arbiter.v` plus `priority_encoder.v`, about 245 lines across two
files, MIT. Good design and a genuinely large coverage space, since
`grant_encoded` crossed with the request mask is PORTS times 2^PORTS. I didn't
pick it first because most of its bins fall to a single well-chosen cycle
rather than to a temporal pattern, which suits a static feature vector more
than it suits the GRU.

I'm keeping it for Stage 7, where the question is whether any gain survives a
change of design. A design whose difficulty is combinational rather than
temporal is a good stress test for exactly that.

### AXI-Lite to Wishbone bridge, rejected

ZipCPU `axlite2wbsp.v`, about 450 lines plus `axilwr2wbsp`, `axilrd2wbsp` and
`wbarbiter`, so 5 or 6 files, Apache-2.0. This is the closest match to the
commercial bus bridge in the temporal-patterns paper, and it would be the most
impressive to talk about.

I turned it down because driving a protocol-legal AXI-Lite master across five
independently handshaken channels is a lot of verification work, and it's
verification work rather than ML work. It also fails badly: a protocol mistake
shows up as a hang rather than an assertion, and I don't yet have the
experience to debug that quickly.

Worth reconsidering only if the FIFO and the arbiter both turn out to have no
coverage headroom.
