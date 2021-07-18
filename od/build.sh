#!/bin/bash
set -eux

export PATH=$HOME/miniconda3/bin:$PATH

# Build PyTorch
python setup.py develop
