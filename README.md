# ChetnaSetu PoC
Two WSL windows:

    bash ~/vllm-server/serve.sh     # shared LLM server (see ~/vllm-server/README.md)
    bash ~/chetnasetu/run.sh       # this app

Open http://localhost:8080/admin  (admin / Admin@123 / OTP 123456)  and  /user  (CS-1001..1008 / Demo@123 / OTP 654321)

This project contains NO model/server settings. It only owns its system prompts:
  prompts/chat.txt        - how the chatbot talks
  prompts/analysis.txt    - how emotions are scored (JSON)
Both are re-read on every message, so edit and retry without restarting.
Env overrides (rarely needed): LLM_BASE (default http://localhost:8000), LLM_MODEL (default local-llm), LLM_STREAM=0.
Diagnose: bash debug_llm.sh

## New in this version
- **Alerts & SLA** (admin, right column): every alert has a countdown (critical 15 min, high 60, watch 240 - edit `SLA` in app.py).
  Acknowledge / Resolve / Override. Resolve and Override need a written justification (min 10 chars). Unacknowledged alerts auto-escalate
  counsellor -> district official -> state official -> OVERDUE. Every action is sealed on the ledger. "⏩" simulates an SLA breach for demos.
- **Two scores + confidence**: Psychological distress and Physical-safety risk are scored separately (overall = the higher). Each assessment carries a
  confidence % with reasons; low confidence adds a "counsellor to review" recommendation.
- **Daily wellness** check-in (sleep / appetite / mood) feeds the distress score. **Calm corner**: box breathing + 5-4-3-2-1 grounding, offered
  automatically after distressing messages. Self-harm messages show a crisis card (112 / 14566 / 14416) instead.
After upgrading an existing install, press "Reset demo session" once in the admin console.

## Multilingual layer (22 scheduled languages + English + Hinglish)
- Each beneficiary picks **🌐 My language** (stored in the profile); "Auto-detect" uses the message script. The chat greeting follows the choice.
- Pipeline for non-English: detect language -> translate to English -> emotion analysis + reply (English) -> translate back. Keyword safety checks run on
  BOTH the original text and the English translation.
- Safety replies (greeting / fear / threat / self-harm) use human-verified text where we have it (English, Hinglish, Hindi, Bengali, Tamil*). Other
  languages get a machine translation of the verified English text, tagged `curated-mt` in the audit trail. *Tamil wording needs native-speaker review.
- Audit trail shows 🌐 language, the English translation and the English reply on hover; confidence drops by 10 when machine translation is involved.
- Translation backend: default = the shared Qwen (works now, quality modest). Better: `pip install torch transformers sentencepiece IndicTransToolkit` then
  `TRANSLATOR=indictrans2 bash run.sh` (AI4Bharat IndicTrans2, local; see translate.py - falls back to Qwen if unavailable).
- Not yet localised: interface labels/buttons (English only); multi-turn context is not carried across translated turns.

## SLA fast-forward (demo)
"⏩ Simulate breach" turns the alert OVERDUE immediately; after 5 s (GRACE_S) it escalates. If the next tier already holds an alert, the audit line says so.

## Whole-interface language switching + first-login tour
- 🌐 selector in the header (and on the user login page) switches the **entire interface** (labels, forms, exercises, errors, tour) and the chat language together,
  like government portals. Hand-written dictionaries: Hindi, Bengali, Punjabi, Tamil (native-speaker review needed). Every other scheduled language is
  machine-translated once, cached in the database and flagged with an "auto-translated" banner. Urdu/Kashmiri/Sindhi switch the page to right-to-left.
  First use of an auto-translated language takes ~1-2 min with Qwen: pre-warm with `python warm_i18n.py` (LLM running). Admin console stays English.
- **First login:** a language chooser, then a 6-step guided tour. "Reset demo session" in the admin console sets every account back to unopened, so the next
  login repeats it (the admin case detail shows "🆕 first login pending"). The header "Tour" button replays it any time.
- Beneficiary IDs are now CS-1001 ... CS-1008 (password Demo@123, OTP 654321).
