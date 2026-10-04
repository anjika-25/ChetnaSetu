#!/bin/bash
# One-time setup: creates ~/vllm-server/venv and installs vLLM (multi-GB download, takes a while).
set -e
cd "$(dirname "$0")"
command -v nvidia-smi >/dev/null || echo "WARNING: nvidia-smi not found - is the GPU visible inside WSL?"
python3 -m venv venv || { echo "Install venv support first:  sudo apt install python3-venv"; exit 1; }
source venv/bin/activate
pip install --upgrade pip
pip install vllm
echo; echo "Done. Start the server with:  bash ~/vllm-server/serve.sh"
