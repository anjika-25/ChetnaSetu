#!/usr/bin/env bash
# Talks straight to vLLM (no gateway).  bash debug_llm.sh   (LLM_BASE=http://127.0.0.1:8000 by default)
URL=${LLM_BASE:-http://127.0.0.1:8000}; M="local-llm"; H=(-H "Content-Type: application/json")
T='\nHTTP %{http_code} | first byte %{time_starttransfer}s | total %{time_total}s\n'
echo "=== 1. models ==="; curl -s "$URL/v1/models" -w "$T" | head -c 400
echo; echo "=== 2. analysis call, real SYSTEM prompt ==="
curl -s -X POST "$URL/v1/chat/completions" "${H[@]}" -w "$T" -d '{"model":"'"$M"'","temperature":0,"max_tokens":120,"messages":[{"role":"system","content":"You are a JSON API, not a chatbot. Output ONLY compact JSON on one line, e.g. {\"sentiment\":-0.6,\"fear\":0.7,\"sadness\":0.5,\"anger\":0.1,\"hopelessness\":0.4,\"threat\":false,\"self_harm\":false,\"needs\":[\"counselling\"]}"},{"role":"user","content":"Message: \"Mujhe bahut dar lag raha hai\"\nJSON:"}]}'
echo; echo "=== 3. prompt_tokens for \"hi\" (should be ~20-30 now, was 680 via the gateway) ==="
curl -s -X POST "$URL/v1/chat/completions" "${H[@]}" -d '{"model":"'"$M"'","max_tokens":5,"messages":[{"role":"user","content":"hi"}]}' | python3 -c "import sys,json;print('prompt_tokens =',json.load(sys.stdin)['usage']['prompt_tokens'])"
echo; echo "=== 4. streaming (should now work) ==="
curl -sN -X POST "$URL/v1/chat/completions" "${H[@]}" -d '{"model":"'"$M"'","stream":true,"max_tokens":30,"messages":[{"role":"user","content":"say hello in Hindi"}]}' | head -6
