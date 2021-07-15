#!/bin/bash

set -eux
lsb_release -a

# Wait for background stuff to finish before launching apt
/usr/bin/cloud-init status --wait
sudo apt update
sudo apt install -y build-essential clang git ccache unzip zip ncdu fish silversearcher-ag ripgrep jq

# Install clangd
curl -LO https://github.com/clangd/clangd/releases/download/11.0.0/clangd-linux-11.0.0.zip
unzip clangd-linux-11.0.0.zip
sudo cp ./clangd_11.0.0/bin/clangd /usr/bin/clangd
rm clangd-linux-11.0.0.zip

# Install conda
curl -L -o conda.sh https://repo.anaconda.com/miniconda/Miniconda3-py39_4.9.2-Linux-x86_64.sh
bash conda.sh -b -p
./miniconda3/bin/conda init bash
rm conda.sh

# Setup VSCode Server
curl -o vscode.tar.gz -L https://update.code.visualstudio.com/commit:2aeda6b18e13c4f4f9edf6667158a6b8d408874b/server-linux-x64/stable
mkdir -p .vscode-server/bin
pushd .vscode-server/bin
tar -xvzf ~/vscode.tar.gz --strip-components 1
popd

# Set up VSCode extensions
mkdir -p .vscode-server/extensions

install_extension() {
    mkdir -p workspace
    pushd workspace
    curl -o vsix.zip.gz -L "$1"
    gunzip vsix.zip.gz
    unzip vsix.zip
    PUBLISHER=$(grep Identity extension.vsixmanifest | sed -r 's/.*Publisher="([A-Z0-9a-z\.-]+)".*/\1/g')
    ID=$(grep Identity extension.vsixmanifest | sed -r 's/.*Id="([A-Z0-9a-z\.-]+)".*/\1/g')
    VERSION=$(grep Identity extension.vsixmanifest | sed -r 's/.*Version="([A-Z0-9a-z\.-]+)".*/\1/g')
    DEST="$PUBLISHER.$ID-$VERSION"
    mv extension ~/.vscode-server/extensions/"$DEST"
    popd
    rm -rf workspace
}

# https://marketplace.visualstudio.com/items?itemName=llvm-vs-code-extensions.vscode-clangd
install_extension "https://marketplace.visualstudio.com/_apis/public/gallery/publishers/llvm-vs-code-extensions/vsextensions/vscode-clangd/0.1.11/vspackage"

# https://marketplace.visualstudio.com/items?itemName=ms-python.vscode-pylance
install_extension "https://marketplace.visualstudio.com/_apis/public/gallery/publishers/ms-python/vsextensions/vscode-pylance/2021.7.4/vspackage"

# https://marketplace.visualstudio.com/items?itemName=ms-python.python
install_extension "https://marketplace.visualstudio.com/_apis/public/gallery/publishers/ms-python/vsextensions/python/2021.6.944021595/vspackage"

# https://marketplace.visualstudio.com/items?itemName=eamodio.gitlens
install_extension "https://marketplace.visualstudio.com/_apis/public/gallery/publishers/eamodio/vsextensions/gitlens/11.6.0/vspackage"

# https://marketplace.visualstudio.com/items?itemName=twxs.cmake
install_extension "https://marketplace.visualstudio.com/_apis/public/gallery/publishers/twxs/vsextensions/cmake/0.0.17/vspackage"

# https://marketplace.visualstudio.com/items?itemName=ms-vscode.cmake-tools
install_extension "https://marketplace.visualstudio.com/_apis/public/gallery/publishers/ms-vscode/vsextensions/cmake-tools/1.7.3/vspackage"

# https://marketplace.visualstudio.com/items?itemName=timonwong.shellcheck
install_extension "https://marketplace.visualstudio.com/_apis/public/gallery/publishers/timonwong/vsextensions/shellcheck/0.14.4/vspackage"

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

