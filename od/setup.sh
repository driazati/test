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

# sudo ln -s $(which gcc) /usr/bin/cc
# sudo ln -s $(which g++) /usr/bin/c++

# mkdir ccache
# export CCACHE_PATH=$(which ccache)
# ln -s "$CCACHE_PATH" ~/ccache/cc
# ln -s "$CCACHE_PATH" ~/ccache/c++
# ln -s "$CCACHE_PATH" ~/ccache/gcc
# ln -s "$CCACHE_PATH" ~/ccache/g++
# ln -s "$CCACHE_PATH" ~/ccache/nvcc

# export PATH=$HOME/ccache:$PATH
# echo 'export PATH=$HOME/ccache/:$PATH' >> ~/.bashrc

pip install cmake ninja
python --version
git clone https://github.com/pytorch/pytorch.git
cd pytorch
pip install -r requirements.txt
printenv | sort
which -a cc
which -a c++
python setup.py develop


# sleep 10 && echo wow i slept
# echo wow i didnt sleep
# echo 'set -eux\n./miniconda3/bin/conda activate\npip install cmake\ngit clone https://github.coytorch/pytorch.git\ncd pytorch\npip install -r requirements.txt\n' > go.sh
# echo running go
# cat go.sh
# bash go.sh