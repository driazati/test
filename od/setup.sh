#!/bin/bash

set -eux
lsb_release -a

# Wait for background stuff to finish before launching apt
/usr/bin/cloud-init status --wait
sudo apt update
sudo apt install -y build-essential clang git ccache unzip

# Install clangd
curl -LO https://github.com/clangd/clangd/releases/download/12.0.0/clangd-linux-12.0.0.zip
unzip clangd-linux-12.0.0.zip
sudo cp ./clangd_12.0.0/bin/clangd /usr/bin/clangd
rm clangd-linux-12.0.0.zip

# Install conda
curl -L -o conda.sh https://repo.anaconda.com/miniconda/Miniconda3-py39_4.9.2-Linux-x86_64.sh
bash conda.sh -b -p
./miniconda3/bin/conda init bash
rm conda.sh

# Manually 'activate' conda to avoid starting a new shell
export PATH=$HOME/miniconda3/bin:$PATH

# Install gh cli
conda install --yes gh --channel conda-forge

# Silence MOTD
touch "$HOME/.hushlogin"

# Install Python + PyTorch deps
pip install cmake ninja ghstack
python --version
git clone https://github.com/pytorch/pytorch.git
cd pytorch
pip install -r requirements.txt
pip install -r requirements-flake8.txt
pip install hypothesis flake8 requests py-spy
make setup_lint

# gh config set -h github.com git_protocol https

# Build PyTorch
python setup.py develop

