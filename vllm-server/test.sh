#!/bin/bash
# Which address reaches vLLM from THIS shell?  bash ~/vllm-server/test.sh
echo "== reachability =="; GOOD=""
for h in 127.0.0.1 localhost $(hostname -I); do
  if curl -s -m 2 "http://$h:8000/v1/models" >/dev/null; then echo "  $h : OK"; [ -z "$GOOD" ] && GOOD=$h; else echo "  $h : refused/unreachable"; fi
done
[ -z "$GOOD" ] && { echo "Nothing reachable. Is serve.sh running? Listening on :8000:"; ss -ltn | grep ':8000 ' || echo "   nothing"; exit 1; }
echo; echo "== chat via $GOOD (system prompt honoured? prompt_tokens should be ~30) =="
curl -s -X POST "http://$GOOD:8000/v1/chat/completions" -H "Content-Type: application/json" -w "\nHTTP %{http_code} | total %{time_total}s\n" \
  -d '{"model":"local-llm","max_tokens":40,"messages":[{"role":"system","content":"Reply with exactly one short sentence."},{"role":"user","content":"hi"}]}'
