set -eux
echo Installing Redis
lsb_release -a
pwd
whoami
/usr/bin/cloud-init status --wait
sudo apt update
sudo apt install -y build-essential clang git ccache unzip
curl -LO https://github.com/clangd/clangd/releases/download/12.0.0/clangd-linux-12.0.0.zip
unzip clangd-linux-12.0.0.zip
sudo cp ./clangd_12.0.0/bin/clangd /usr/bin/clangd

curl -L -o conda.sh https://repo.anaconda.com/miniconda/Miniconda3-py39_4.9.2-Linux-x86_64.sh
bash conda.sh -b -p
./miniconda3/bin/conda init bash

export PATH=$HOME/miniconda3/bin:$PATH

pip install cmake ninja
python --version
git clone https://github.com/pytorch/pytorch.git
cd pytorch
pip install -r requirements.txt
pip install -r requirements-flake8.txt
pip install hypothesis flake8 requests py-spy
make setup_lint

python setup.py develop

