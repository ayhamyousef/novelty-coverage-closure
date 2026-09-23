#!/usr/bin/env bash
# STAGE 1 -- system packages. Needs sudo; run this once, by hand.
set -euo pipefail
sudo apt-get update
sudo apt-get install -y \
    verilator \
    iverilog \
    gtkwave \
    python3-pip \
    python3-venv \
    python3-dev \
    build-essential \
    git
echo
echo "Installed versions:"
verilator --version
iverilog -V 2>&1 | head -1
