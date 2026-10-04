# ChetnaSetu: what to change in the SIH PDF

## Everywhere
Rename **Sahayak -> ChetnaSetu** (title, logo text, slide headers, architecture labels, tagline). Beneficiary IDs are now CS-xxxx. Update the GitHub link/repo name; re-record the demo video.

## Slide 2: Overview
- Language box -> "Whole interface + chat in 22 Indian languages (+ English, Hinglish); hand-written Hindi, Bengali, Punjabi, Tamil".
- 01 Beneficiary portal -> add: "First-login language chooser + guided tour; daily wellness check-in; calm tools (breathing, grounding)".
- 03 Distress score -> "Two scores: psychological distress + physical-safety risk, with confidence %".
- 04 Admin console -> add: "Alerts with SLA clocks, acknowledgement, justified overrides, auto-escalation (counsellor -> district -> state)".
- Why different -> add "Audit trail: every AI reply and alert action sealed on the ledger".

## Slide 3: Technical approach
- Workflow: add "Detect language + translate" before AI processing; add "Alert SLA + escalation" after Risk level.
- Architecture: add Translation layer, Dual scoring + confidence, SLA engine, i18n service; vLLM runs as a separate shared service (settings held in vllm-server, the app holds only prompts).
- Tech stack: AI logic -> add "curated multilingual safety replies, translation pivot (IndicTrans2 optional)"; Frontend -> add "client-side i18n (22 languages, RTL support)"; Database -> add events, coping, i18n cache tables.
- Implementation table: add rows "Alerts and SLA", "Multilingual layer + whole-UI localisation", "Wellness and calm tools", "Onboarding tour".

## Slide 4: Feasibility
- Add challenge "Translation errors" -> verified safety wording, keyword checks on original + English text, lower confidence, translations logged.
- Add challenge "Missed alerts" -> SLA auto-escalation with sealed audit trail.
- "Over-reliance on automation" -> add justified overrides and low-confidence review flag.
- "Low digital literacy" -> add first-login tour and full-interface language choice.

## Slide 6: References
Add IndicTrans2 (AI4Bharat, 2023: check the arXiv link), DPDP Act 2023.

## Claims to tone down
Voice analysis = simulated signal; IVRS/SMS = simulated interfaces; only English, Hinglish, Hindi, Bengali, Tamil have verified safety text and only Hindi, Bengali, Punjabi, Tamil have hand-written UI; encryption, role-based access, consent capture still planned.
