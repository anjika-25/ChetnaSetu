"""Pre-translate the interface for every language without a hand-written dictionary (the LLM server must be running).
Run once before a demo:  python warm_i18n.py        (results are cached in chetnasetu.db)"""
import app, translate as T, i18n as I
for code in T.NAME:
    if code == "en" or code in I.CURATED: continue
    s, cached = app.i18n_strings(code)
    done = sum(1 for k in s if s[k] != I.EN[k])
    print(f"{code:4} {T.NAME[code]:18} {done}/{len(I.EN)} keys translated", "(cached)" if cached else "(NOT available - LLM down or output unusable)")
