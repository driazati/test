#!/bin/bash
set -eux

export PATH=$HOME/miniconda3/bin:$PATH

# Build PyTorch
cd pytorch
python setup.py develop
