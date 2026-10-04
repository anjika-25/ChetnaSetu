#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Shared local LLM server. Every technical setting is hardcoded HERE on purpose:
# all projects on this machine talk to the same URL with the same settings.
# Projects only own their system prompts.
#
#     URL          http://localhost:8000/v1      (OpenAI-compatible, no API key)
#     Model alias  local-llm                     (use this as "model" in requests)
# ─────────────────────────────────────────────────────────────────────────────
cd "$(dirname "$0")"
source venv/bin/activate 2>/dev/null || { echo "venv missing - run: bash ~/vllm-server/setup.sh"; exit 1; }

MODEL="Qwen/Qwen2.5-3B-Instruct-AWQ"
ALIAS="local-llm"
HOST="0.0.0.0"            # reachable via 127.0.0.1, localhost AND your LAN IP. No API key -> trusted network only
PORT=8000
MAX_LEN=2048
GPU_UTIL=0.75

if curl -s -m 2 "http://localhost:$PORT/v1/models" >/dev/null; then
  echo "vLLM is already running at http://localhost:$PORT/v1 - nothing to do."; exit 0
fi
if ss -ltn | grep -q ":$PORT "; then
  echo "Port $PORT is used by another process (an old gateway?). Stop it first."; exit 1
fi

# Your system nvcc is too old to JIT-compile FlashInfer's sampling kernel
# ("nvcc fatal: Unknown option '--compress-mode=size'"), so use vLLM's built-in PyTorch sampler instead.
export VLLM_USE_FLASHINFER_SAMPLER=0

# Model is already cached: never contact HuggingFace at startup (a changed/absent wifi would otherwise stall it).
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1

mkdir -p logs
find logs/ -type f -name "*.log" -mtime +3 -delete
LOGFILE="logs/server_$(date +%Y-%m-%d_%H-%M-%S).log"
echo "Starting vLLM: $MODEL as '$ALIAS' on http://localhost:$PORT/v1  (log: $LOGFILE)"
echo "Model load takes ~1 minute. Ready when you see 'Application startup complete'."

python -m vllm.entrypoints.openai.api_server \
  --model "$MODEL" \
  --served-model-name "$ALIAS" "$MODEL" \
  --host "$HOST" \
  --port "$PORT" \
  --max-model-len "$MAX_LEN" \
  --gpu-memory-utilization "$GPU_UTIL" \
  2>&1 | tee -a "$LOGFILE"
