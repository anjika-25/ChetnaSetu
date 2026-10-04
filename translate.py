"""Language table + OPTIONAL local IndicTrans2 backend.
Default translation backend is the shared Qwen (see app.mt). To use AI4Bharat IndicTrans2 instead (better quality for Indian languages):
    pip install torch transformers sentencepiece IndicTransToolkit      # in the chetnasetu venv
    TRANSLATOR=indictrans2 bash run.sh                                   # first call downloads ai4bharat/indictrans2-*-dist-200M
(Not exercised in the development sandbox - verify on your machine; app.mt falls back to Qwen if it fails.)"""
NAME = {"en": "English", "as": "Assamese", "bn": "Bengali", "brx": "Bodo", "doi": "Dogri", "gu": "Gujarati", "hi": "Hindi", "kn": "Kannada", "ks": "Kashmiri",
        "kok": "Konkani", "mai": "Maithili", "ml": "Malayalam", "mni": "Manipuri (Meitei)", "mr": "Marathi", "ne": "Nepali", "or": "Odia", "pa": "Punjabi",
        "sa": "Sanskrit", "sat": "Santali", "sd": "Sindhi", "ta": "Tamil", "te": "Telugu", "ur": "Urdu"}      # 22 scheduled languages + English
FLORES = {"en": "eng_Latn", "as": "asm_Beng", "bn": "ben_Beng", "brx": "brx_Deva", "doi": "doi_Deva", "gu": "guj_Gujr", "hi": "hin_Deva", "kn": "kan_Knda",
          "ks": "kas_Arab", "kok": "gom_Deva", "mai": "mai_Deva", "ml": "mal_Mlym", "mni": "mni_Mtei", "mr": "mar_Deva", "ne": "npi_Deva", "or": "ory_Orya",
          "pa": "pan_Guru", "sa": "san_Deva", "sat": "sat_Olck", "sd": "snd_Arab", "ta": "tam_Taml", "te": "tel_Telu", "ur": "urd_Arab"}
_M = {}

def indictrans2(text, src, tgt):
    import torch
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
    from IndicTransToolkit.processor import IndicProcessor
    d = "indic-en" if tgt == "en" else "en-indic"
    if d not in _M:
        name = f"ai4bharat/indictrans2-{d}-dist-200M"; dev = "cuda" if torch.cuda.is_available() else "cpu"
        tok = AutoTokenizer.from_pretrained(name, trust_remote_code=True)
        mod = AutoModelForSeq2SeqLM.from_pretrained(name, trust_remote_code=True, torch_dtype=torch.float16 if dev == "cuda" else torch.float32).to(dev).eval()
        _M[d] = (tok, mod, IndicProcessor(inference=True), dev)
    tok, mod, ip, dev = _M[d]
    batch = ip.preprocess_batch([text], src_lang=FLORES[src], tgt_lang=FLORES[tgt])
    inp = tok(batch, truncation=True, padding="longest", return_tensors="pt").to(dev)
    with torch.no_grad(): out = mod.generate(**inp, num_beams=4, max_length=256, num_return_sequences=1)
    return ip.postprocess_batch(tok.batch_decode(out, skip_special_tokens=True, clean_up_tokenization_spaces=True), lang=FLORES[tgt])[0]
