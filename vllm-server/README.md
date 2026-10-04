# vllm-server  (shared by every project in ~)

    bash ~/vllm-server/setup.sh    # once: venv + vLLM
    bash ~/vllm-server/serve.sh    # every session, one WSL window, Ctrl+C stops it
    bash ~/vllm-server/test.sh     # health check

| | |
|---|---|
| URL | `http://localhost:8000/v1` (OpenAI-compatible, no key) |
| Model alias | `local-llm` (also answers to `Qwen/Qwen2.5-3B-Instruct-AWQ`) |
| Settings | hardcoded at the top of `serve.sh` (model, port, max len 2048, GPU 0.75) |

## Rule of the house
Technical settings live here only. A project (`~/chetnasetu`, ...) contains just its own **system prompts**
and calls the URL above, e.g.

    requests.post("http://localhost:8000/v1/chat/completions",
                  json={"model": "local-llm", "messages": [{"role": "system", "content": MY_PROMPT}, {"role": "user", "content": text}]})

Streaming (`"stream": true`) works. Logs: `~/vllm-server/logs/` (auto-deleted after 3 days).

## Known issue (fixed in serve.sh)
`nvcc fatal: Unknown option '--compress-mode=size'` at startup = system CUDA toolkit older than 12.4 breaking FlashInfer's JIT build.
`serve.sh` sets `VLLM_USE_FLASHINFER_SAMPLER=0` to avoid it. (Permanent alternative: install CUDA toolkit >= 12.4.)
