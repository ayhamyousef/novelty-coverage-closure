# Toolchain versions

Ubuntu 24.04.4 on WSL2 (kernel 6.18.33.2), recorded 2026-09-22.
All free, all local.

| Component | Version | Source |
|---|---|---|
| Verilator | 5.020 2024-01-01 rev (Debian 5.020-1) | apt (`verilator`) |
| Icarus Verilog | 12.0 (stable) () | apt (`iverilog`) |
| GTKWave | v3.3.116 (w)1999-2023 BSI | apt (`gtkwave`) |
| Python | 3.12.3 | system (Ubuntu 24.04) |
| cocotb | 1.9.2 | pip, in `.venv` |
| numpy | 2.5.3 | pip, in `.venv` |
| find-libpython | 0.5.1 | pip dependency of cocotb |
| GCC | 13.3.0 | apt (`build-essential`) |
| GNU Make | 4.3 | apt |
| git | 2.43.0 | apt |

## Not installed yet

Skipped these for now so Stage 1 wasn't waiting on a ~900 MB download:

- `torch` (Stage 5, the GRU autoencoder)
- `scikit-learn` (Stage 5, k-means over the latent space)
- `matplotlib` (Stage 4, the coverage curve)

## cocotb 1.9.2, not 2.x

cocotb 2.0 moved the runner from `cocotb.runner` to `cocotb_tools.runner` and
changed some API details along with it. I went with 1.9.2 because it's the
stable line, most of the Verilator examples I found were written against it,
and it installed on Python 3.12 without complaining.

`scripts/01_smoke_test.py` imports through a try/except that accepts either
module path, so if I do need to move to 2.x later it shouldn't be painful.
