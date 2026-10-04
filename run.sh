#!/bin/bash
# Starts ChetnaSetu (port 8080). The LLM is a separate shared service:  bash ~/vllm-server/serve.sh
cd "$(dirname "$0")"
[ -d venv ] || python3 -m venv venv || { echo "sudo apt install python3-venv"; exit 1; }
source venv/bin/activate
pip install -q -r requirements.txt
if [ -z "$LLM_BASE" ]; then
  OK=""; for h in 127.0.0.1 $(hostname -I); do curl -s -m 2 "http://$h:8000/v1/models" >/dev/null && { OK=$h; break; }; done
  if [ -n "$OK" ]; then echo "✓ LLM reachable at http://$OK:8000"
  else echo "⚠ LLM not reachable yet. Listening on :8000 in this shell:"; ss -ltn | grep ':8000 ' || echo "   nothing"
       echo "  Start it:  bash ~/vllm-server/serve.sh   (ChetnaSetu runs in fallback mode and auto-connects once it is up)"; fi
fi
uvicorn app:app --host 0.0.0.0 --port 8080
