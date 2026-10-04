"""ChetnaSetu PoC v2 - run: uvicorn app:app --host 0.0.0.0 --port 8080"""
import os, json, re, sqlite3, random, hashlib, secrets, time, subprocess, threading, datetime as dt
import requests
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from concurrent.futures import ThreadPoolExecutor
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import translate as T
import i18n as I

# --- LLM: the shared server in ~/vllm-server (URL + model alias hardcoded there). This project only owns prompts/ ---
LLM_BASE = os.getenv("LLM_BASE", "http://127.0.0.1:8000")
LLM_URL = LLM_BASE + "/v1/chat/completions"

def discover_llm():
    """Some WSL setups refuse 127.0.0.1 but accept the machine's LAN IP: probe both, keep whichever answers."""
    global LLM_BASE, LLM_URL
    cands = [LLM_BASE]
    if not os.getenv("LLM_BASE"):
        try: ips = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=2).stdout.split()
        except Exception: ips = []
        cands += [f"http://{ip}:8000" for ip in ips if "." in ip] + ["http://localhost:8000"]
    for b in cands:
        try:
            requests.get(b + "/v1/models", headers=HDR(), timeout=1.5).raise_for_status()
            LLM_BASE, LLM_URL = b, b + "/v1/chat/completions"; return True
        except Exception: pass
    return False
LLM_KEY = os.getenv("LLM_KEY", "")
MODEL = os.getenv("LLM_MODEL", "local-llm")
# Demo-only fixed credentials
ADMIN, ADMIN_OTP, USER_PW, USER_OTP = ("admin", "Admin@123"), "123456", "Demo@123", "654321"
SESS, TMP = {}, {}
AI = {"ok": None, "err": "", "ms": None, "ts": None, "analysis_ok": None, "analysis_err": ""}
now = lambda: dt.datetime.now().isoformat(timespec="seconds")
H = lambda s: hashlib.sha256(s.encode()).hexdigest()
app = FastAPI(title="ChetnaSetu PoC")
app.mount("/static", StaticFiles(directory="static"), name="static")

def db():
    c = sqlite3.connect("chetnasetu.db", timeout=15); c.row_factory = sqlite3.Row; return c

with db() as c:
    c.executescript("""
    create table if not exists victims(id text primary key, category text, district text, state text, stage text, hearing text, missed integer default 0);
    create table if not exists checkins(id integer primary key autoincrement, vid text, ts text, channel text, text text,
        feats text, parts text, raw real, score real, level text, slope real, pred real);
    create table if not exists alerts(id integer primary key autoincrement, vid text, ts text, level text, to_role text, reason text);
    create table if not exists ledger(idx integer primary key, ts text, kind text, ref text, phash text, prev text, hash text);""")
    c.executescript("""create table if not exists events(id integer primary key autoincrement, ts text, alert_id integer, action text, actor text, note text);
    create table if not exists coping(id integer primary key autoincrement, vid text, ts text, kind text);
    create table if not exists i18n(lang text primary key, data text);""")
    for _t, _c, _d in [("alerts","status","text default 'open'"),("alerts","due","text"),("alerts","ack_by","text"),("alerts","ack_ts","text"),("alerts","note","text"),("alerts","parent","integer"),
                       ("victims","lang","text default 'auto'"),("victims","toured","integer default 0"),("checkins","distress","real"),("checkins","safety","real"),("checkins","conf","real")]:
        if _c not in [r[1] for r in c.execute(f"pragma table_info({_t})")]: c.execute(f"alter table {_t} add column {_c} {_d}")
    c.execute("update alerts set status='resolved' where due is null and status='open'")   # legacy alerts from before SLA tracking

# ---------- Tamper-evident ledger (hash-chained blocks) ----------
def block(c, kind, ref, payload):
    for _ in range(6):   # concurrent writers can race for the same idx: re-read the chain head and retry
        l = c.execute("select idx,hash from ledger order by idx desc limit 1").fetchone()
        idx = l["idx"] + 1 if l else 0; prev = l["hash"] if l else "0" * 64; ts = now(); ph = H(payload)
        h = H(f"{idx}|{ts}|{kind}|{ref}|{ph}|{prev}")
        try: c.execute("insert into ledger values(?,?,?,?,?,?,?)", (idx, ts, kind, str(ref), ph, prev, h)); return idx, h
        except sqlite3.IntegrityError: continue
    raise RuntimeError("ledger busy")

cpay = lambda vid, ts, ch, tx, fe, sc, lv: json.dumps([vid, ts, ch, tx, fe, f"{sc:.4f}", lv])

def verify(c):
    issues, prev, n = [], "0" * 64, 0
    for b in c.execute("select * from ledger order by idx"):
        n += 1
        if b["prev"] != prev: issues.append({"idx": b["idx"], "msg": "chain broken (previous hash mismatch)"})
        if H(f"{b['idx']}|{b['ts']}|{b['kind']}|{b['ref']}|{b['phash']}|{b['prev']}") != b["hash"]:
            issues.append({"idx": b["idx"], "msg": "block contents altered"})
        if b["kind"] == "checkin":
            r = c.execute("select * from checkins where id=?", (int(b["ref"]),)).fetchone()
            if not r: issues.append({"idx": b["idx"], "msg": "check-in record DELETED from database"})
            elif H(cpay(r["vid"], r["ts"], r["channel"], r["text"], r["feats"], r["score"], r["level"])) != b["phash"]:
                issues.append({"idx": b["idx"], "msg": f"check-in of {r['vid']} ({r['ts']}) was MODIFIED after being sealed"})
        if b["kind"] == "event":
            r = c.execute("select * from events where id=?", (int(b["ref"]),)).fetchone()
            if not r: issues.append({"idx": b["idx"], "msg": "alert action record DELETED from database"})
            elif H(json.dumps([r["alert_id"], r["ts"], r["action"], r["actor"], r["note"]])) != b["phash"]:
                issues.append({"idx": b["idx"], "msg": f"alert action '{r['action']}' on alert #{r['alert_id']} was MODIFIED after being sealed"})
        prev = b["hash"]
    return {"ok": not issues, "blocks": n, "issues": issues}

# ---------- Conversation (streamed plain-text reply) + emotion analysis (short JSON, run in parallel) ----------
SYS_CHAT = ("You are ChetnaSetu, a warm, trauma-informed support assistant for people receiving help under atrocity-relief schemes in India. "
            "Reply in the same language and script the person uses (English, Hindi, Bengali or Hinglish). Keep replies to 1-2 short sentences. "
            "Be empathetic, ask at most one gentle follow-up question, never give legal or medical advice, and never ask them to relive details of the incident. "
            "If they mention danger or wanting to hurt themselves, tell them to call 14566 or 112 and that a counsellor will contact them.")
SYS_JSON = ('Classify the emotional state of the person who wrote the message (English, Hindi, Bengali or Hinglish). Output ONLY compact JSON on one line, e.g. '
            '{"sentiment":-0.6,"fear":0.7,"sadness":0.5,"anger":0.1,"hopelessness":0.4,"threat":false,"self_harm":false,"needs":["counselling"]} '
            'sentiment is -1 (very negative) to 1 (very positive); other scores are 0 to 1. A neutral greeting like "hi" scores sentiment 0 and every other score 0. '
            'threat is true only if the person reports threats or intimidation. self_harm is true only if they express wanting to die or hurt themselves. '
            'needs is a subset of [counselling,medical,protection,relocation,financial,legal_aid,rehabilitation].')
FALLBACK = "Thank you for sharing. A counsellor will be in touch with you."
SELF = ["kill myself", "suicide", "end my life", "want to die", "marna chahta", "marna chahti", "jeene ka man nahi", "atmahatya"]
THREAT = ["threat", "dhamki", "dhamka", "dhamkaya", "threaten", "threatening", "stalk", "followed", "follow kar", "peecha", "warned", "attack", "hamla", "pressure", "harass", "torture", "maar dalenge", "maar denge", "jaan se", "withdraw the case", "case wapas", "compromise karne", "धमकी", "धमका", "হুমকি"]
NEG = ["afraid", "scared", "scary", "scare", "frighten", "worried", "dar ", "dara", "darr", "डर", "ভয়", "fear", "hopeless", "nirash", "निराश", "alone", "akela", "अकेला", "bekar", "udaas", "cry", "nightmare", "helpless", "no hope", "ghabra", "tired", "pareshan", "neend nahi", "can't sleep"]
NEEDS = ["counselling", "medical", "protection", "relocation", "financial", "legal_aid", "rehabilitation"]
HDR = lambda: {"Authorization": f"Bearer {LLM_KEY}"} if LLM_KEY else {}

def salvage(txt):
    """Field-by-field extraction: works even when the small model truncates or malforms its JSON."""
    d = {}
    for k in ("sentiment", "fear", "sadness", "anger", "hopelessness"):
        m = re.search(rf'"{k}"\s*:\s*(-?[0-9.]+)', txt)
        if m:
            try: d[k] = float(m.group(1))
            except ValueError: pass
    for k in ("threat", "self_harm"):
        m = re.search(rf'"{k}"\s*:\s*(true|false)', txt)
        if m: d[k] = m.group(1) == "true"
    m = re.search(r'"needs"\s*:\s*\[([^\]]*)', txt)
    if m: d["needs"] = [n for n in NEEDS if n in m.group(1)]
    return d

def analyze_feats(text, orig=None):
    t = (text + " " + (orig or "")).lower(); n = sum(k in t for k in NEG)   # keyword checks see the original AND the English translation
    f = dict(sentiment=-min(1, .25 * n), fear=min(1, .3 * n), sadness=min(1, .3 * n), anger=0, hopelessness=min(1, .25 * n), threat=False, self_harm=False, needs=[])
    try:
        r = requests.post(LLM_URL, headers=HDR(), timeout=40, json={"model": MODEL, "temperature": 0, "max_tokens": 120,
                          "messages": [{"role": "system", "content": P("analysis")}, {"role": "user", "content": 'Message: "' + text + '"\nJSON:'}]})
        if r.status_code >= 400: raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
        out = r.json()["choices"][0]["message"]["content"] or ""
        print("[analysis raw]", repr(out))
        f["analysis_raw"] = out[:300]
        d = salvage(out)
        if not d: raise ValueError("no fields in model output: " + out[:80])
        f.update(d); f["source"] = "qwen2.5-3b" if len(d) >= 8 else "qwen2.5-3b (partial output)"
        AI.update(ok=True, err="", ts=now(), analysis_ok=True, analysis_err="")
    except Exception as e:
        print(f"[analysis] {type(e).__name__}: {e}")
        AI.update(analysis_ok=False, analysis_err=f"{type(e).__name__}: {str(e)[:150]}", ts=now())
        if isinstance(e, requests.RequestException) and not isinstance(e, requests.HTTPError): AI.update(ok=False, err=type(e).__name__)
        f["source"] = f"fallback: {type(e).__name__}: {str(e)[:120]}"
    for k in ("sentiment", "fear", "sadness", "anger", "hopelessness"):
        try: f[k] = max(-1, min(1, float(f[k])))
        except Exception: f[k] = 0.0
    f["needs"] = [x for x in f["needs"] if x in NEEDS]
    f["self_harm"] = bool(f["self_harm"]) or any(k in t for k in SELF)    # keywords only ever raise risk
    f["threat_kw"] = any(k in t for k in THREAT)              # keyword-confirmed: triggers hard escalation
    f["threat"] = bool(f["threat"]) or f["threat_kw"]                # model-only flag: adds to the score, never escalates alone
    return f

# ---------- Curated replies for safety-critical moments (a 3B model writes poor Hindi/Bengali; these are fixed and correct) ----------
HINGLISH = {"mujhe", "mai", "main", "hai", "hain", "nahi", "nahin", "kya", "aap", "hu", "hoon", "dara", "dar", "dhamki", "log", "yeh", "ye", "mera", "meri", "bahut",
            "sab", "koi", "kuch", "neend", "bekar", "marna", "chahta", "chahti", "raha", "rahe", "rahi", "hua", "lagta", "lag", "dete", "de"}
CURATED = {
 "self": {"en": "Thank you for telling me - your life matters. Are you safe right now? Please call 14566 or 112 immediately. Your counsellor is being informed and will contact you today.",
          "hi": "आपने मुझे बताया, इसके लिए शुक्रिया - आपकी ज़िंदगी बहुत कीमती है। क्या आप अभी सुरक्षित हैं? कृपया तुरंत 14566 या 112 पर कॉल करें। आपके काउंसलर को अभी खबर दी जा रही है।",
          "hx": "Aapne mujhe bataya, iske liye shukriya - aapki zindagi bahut keemti hai. Kya aap abhi surakshit hain? Kripya turant 14566 ya 112 par call karein. Aapke counsellor ko abhi khabar di ja rahi hai.",
          "bn": "আমাকে বলার জন্য ধন্যবাদ - আপনার জীবন অমূল্য। আপনি কি এখন নিরাপদ? অনুগ্রহ করে এখনই ১৪৫৬৬ বা ১১২ নম্বরে কল করুন। আপনার কাউন্সেলরকে এখনই জানানো হচ্ছে।"},
 "threat": {"en": "That is not okay, and you do not have to face it alone. Your counsellor is being informed right now. Are you safe at this moment?",
            "hi": "यह बिल्कुल ठीक नहीं है, और आपको यह अकेले नहीं सहना है। आपके काउंसलर को अभी खबर दी जा रही है। क्या आप इस समय सुरक्षित हैं?",
            "hx": "Yeh bilkul theek nahi hai, aur aapko yeh akele nahi sahna hai. Aapke counsellor ko abhi khabar di ja rahi hai. Kya aap is waqt surakshit hain?",
            "bn": "এটা একদম ঠিক নয়, আপনাকে একা এটা সহ্য করতে হবে না। আপনার কাউন্সেলরকে এখনই জানানো হচ্ছে। আপনি কি এখন নিরাপদ?"},
 "fear": {"en": "That sounds really hard, and I'm glad you told me. You are not alone in this. What has felt the heaviest today?",
          "hi": "यह सुनकर बहुत दुख हुआ, और मुझे खुशी है कि आपने बताया। आप अकेले नहीं हैं। आज सबसे ज़्यादा क्या परेशान कर रहा है?",
          "hx": "Yeh sunkar bahut dukh hua, aur achha laga ki aapne bataya. Aap akele nahi hain. Aaj sabse zyada kya pareshan kar raha hai?",
          "bn": "এটা শুনে খুব কষ্ট হলো, আপনি বলেছেন বলে ভালো লাগছে। আপনি একা নন। আজ সবচেয়ে বেশি কী কষ্ট দিচ্ছে?"},
 "hello": {"en": "Namaste 🙏 How are you feeling today?", "hi": "नमस्ते 🙏 आज आप कैसा महसूस कर रहे हैं?",
           "hx": "Namaste 🙏 Aap aaj kaisa mehsoos kar rahe hain?", "bn": "নমস্কার 🙏 আজ আপনি কেমন বোধ করছেন?"}}

SCRIPTS = [(r"[\u0900-\u097F]", ["hi", "mr", "ne", "kok", "mai", "doi", "brx", "sa"]), (r"[\u0980-\u09FF]", ["bn", "as", "mni"]), (r"[\u0B80-\u0BFF]", ["ta"]),
           (r"[\u0C00-\u0C7F]", ["te"]), (r"[\u0C80-\u0CFF]", ["kn"]), (r"[\u0D00-\u0D7F]", ["ml"]), (r"[\u0A80-\u0AFF]", ["gu"]), (r"[\u0A00-\u0A7F]", ["pa"]),
           (r"[\u0B00-\u0B7F]", ["or"]), (r"[\u0600-\u06FF]", ["ur", "ks", "sd"]), (r"[\u1C50-\u1C7F]", ["sat"]), (r"[\uABC0-\uABFF]", ["mni"])]

def detect(text, pref=None):
    """Language of THIS message: the script decides; the person's saved preference breaks ties inside a shared script (Hindi vs Marathi...)."""
    for rx, cands in SCRIPTS:
        if re.search(rx, text): return pref if pref in cands else cands[0]
    return "hx" if set(re.findall(r"[a-z]+", text.lower())) & HINGLISH else "en"
lang_of = lambda t: detect(t)

# Extra native-script keyword coverage (a safety net next to translation). Must be reviewed by native speakers before real deployment.
SELF += ["आत्महत्या", "मरना चाहता", "मरना चाहती", "আত্মহত্যা", "தற்கொலை", "சாக வேண்டும்", "ఆత్మహత్య", "చనిపోవాలని", "ಆತ್ಮಹತ್ಯೆ", "ആത്മഹത്യ", "આત્મહત્યા", "ਖੁਦਕੁਸ਼ੀ", "خودکشی", "ଆତ୍ମହତ୍ୟା"]
THREAT += ["மிரட்ட", "மிரட்டல்", "బెదిరింపు", "ಬೆದರಿಕೆ", "ഭീഷണി", "ધમકી", "ਧਮਕੀ", "دھمکی", "ଧମକ"]
NEG += ["பயம", "பயந்", "பயம்", "భయం", "ಭಯ", "ഭയം", "ડર", "ਡਰ", "ڈر", "ଭୟ", "भीती"]
for _k, _v in {"self": "என்னிடம் சொன்னதற்கு நன்றி - உங்கள் உயிர் மிகவும் மதிப்புமிக்கது. நீங்கள் இப்போது பாதுகாப்பாக இருக்கிறீர்களா? தயவுசெய்து உடனடியாக 14566 அல்லது 112 எண்ணை அழைக்கவும். உங்கள் ஆலோசகருக்கு இப்போதே தகவல் அனுப்பப்படுகிறது.",
               "threat": "இது சரியல்ல, இதை நீங்கள் தனியாக எதிர்கொள்ள வேண்டியதில்லை. உங்கள் ஆலோசகருக்கு இப்போதே தகவல் தெரிவிக்கப்படுகிறது. இந்த நேரத்தில் நீங்கள் பாதுகாப்பாக இருக்கிறீர்களா?",
               "fear": "இது மிகவும் கடினமாக இருக்கிறது என்று புரிகிறது; நீங்கள் என்னிடம் சொன்னது நல்லது. நீங்கள் தனியாக இல்லை. இன்று உங்களை மிகவும் வருத்துவது எது?",
               "hello": "வணக்கம் 🙏 இன்று நீங்கள் எப்படி உணர்கிறீர்கள்?"}.items(): CURATED[_k]["ta"] = _v   # Tamil: needs native-speaker verification

GREET = r"(hi+|hello+|hey+|namaste|namaskar|नमस्ते|নমস্কার|வணக்கம்)[ !.]*"
def curated_cat(text, text_en=""):
    t = (text + " " + text_en).lower()
    if any(k in t for k in SELF): return "self"
    if any(k in t for k in THREAT): return "threat"
    if any(k in t for k in NEG): return "fear"
    if re.fullmatch(GREET, text.lower().strip()) or re.fullmatch(GREET, text_en.lower().strip()): return "hello"
    return None

_MT = {}
def mt(text, src, tgt, max_tokens=300, extra=""):
    """Translate -> (text, backend). TRANSLATOR=llm (default: the shared Qwen) | indictrans2 (local AI4Bharat models, see translate.py)."""
    if src == tgt or not text.strip(): return text, ""
    k = (text, src, tgt)
    if k in _MT: return _MT[k]
    if os.getenv("TRANSLATOR", "llm") == "indictrans2" and src in T.FLORES and tgt in T.FLORES:
        try: _MT[k] = (T.indictrans2(text, src, tgt), "indictrans2"); return _MT[k]
        except Exception as e: print("[mt] indictrans2 unavailable -> falling back to Qwen:", type(e).__name__, e)
    sysmsg = (f"You are a professional translator for a victim-support helpline in India. Translate the user's message from {T.NAME.get(src, src)} to {T.NAME.get(tgt, tgt)}. "
              "Keep the meaning, tone and urgency (fear, threats) exactly. Output ONLY the translation." + (" " + extra if extra else ""))
    r = requests.post(LLM_URL, headers=HDR(), timeout=40, json={"model": MODEL, "temperature": 0, "max_tokens": max_tokens, "messages": [{"role": "system", "content": sysmsg}, {"role": "user", "content": text}]})
    if r.status_code >= 400: raise RuntimeError(f"HTTP {r.status_code}")
    out = (r.json()["choices"][0]["message"]["content"] or "").strip()
    if not out: raise RuntimeError("empty translation")
    _MT[k] = (out, "qwen-mt"); return _MT[k]

def say(cat, L):
    """Curated safety text in language L: human-verified wording if we have it, else machine translation of the verified English (tagged curated-mt)."""
    c = CURATED[cat]
    if L in c: return c[L], "curated"
    try: return mt(c["en"], "en", L)[0], "curated-mt"
    except Exception as e: print("[say] translation failed, using English:", e); return c["en"], "curated"

_UI_LOCK = threading.Lock()
UI_EXTRA = ("The input is a numbered list of short user-interface labels. Return the same numbered list ('1. ...'), one item per line, translating each label; "
            "keep {placeholders}, digits, emoji and names such as NHAA and Tele-MANAS unchanged.")

def translate_ui(lang):
    """Machine-translate the English UI strings (used only for languages without a hand-written dictionary)."""
    keys = list(I.EN); out = {}
    if os.getenv("TRANSLATOR", "llm") == "indictrans2":
        for k in keys:
            try: out[k] = mt(I.EN[k], "en", lang)[0]
            except Exception: pass
    else:
        for i in range(0, len(keys), 6):
            chunk = keys[i:i + 6]
            try: res, _ = mt("\n".join(f"{n + 1}. {I.EN[k]}" for n, k in enumerate(chunk)), "en", lang, max_tokens=900, extra=UI_EXTRA)
            except Exception as e: print("[ui-mt]", lang, type(e).__name__); continue
            for line in res.splitlines():
                m = re.match(r"\s*(\d+)[.)]\s*(.+)", line)
                if m and 1 <= int(m.group(1)) <= len(chunk): out[chunk[int(m.group(1)) - 1]] = m.group(2).strip()
    ph = lambda x: sorted(re.findall(r"\{\w+\}", x))
    return {k: v for k, v in out.items() if ph(v) == ph(I.EN[k])}     # drop anything that lost a placeholder

def i18n_strings(lang):
    """-> (strings, machine_translated). Hand-written dictionaries first, then the cache, then translate once and cache."""
    if lang in I.CURATED: return {**I.EN, **I.CURATED[lang]}, False
    def cached():
        with db() as c: r = c.execute("select data from i18n where lang=?", (lang,)).fetchone()
        return ({**I.EN, **json.loads(r["data"])}, True) if r else None
    if cached(): return cached()
    with _UI_LOCK:
        if cached(): return cached()
        got = translate_ui(lang)
        if len(got) >= len(I.EN) * 0.6:
            with db() as c: c.execute("insert or replace into i18n(lang,data) values(?,?)", (lang, json.dumps(got, ensure_ascii=False)))
            return {**I.EN, **got}, True
    return dict(I.EN), False       # translation unavailable right now (LLM down?): show English, try again next time

def words(s): return re.findall(r"\S+\s*", s)

STREAM = os.getenv("LLM_STREAM", "1") == "1"   # real token streaming from vLLM; falls back to simulated typing if it fails

def chat_msgs(text, history):
    return [{"role": "system", "content": P("chat")}] + [{"role": m["role"], "content": str(m["content"])[:400]}
            for m in list(history)[-8:] if m.get("role") in ("user", "assistant")] + [{"role": "user", "content": text}]

def get_reply(text, history):
    r = requests.post(LLM_URL, headers=HDR(), timeout=60, json={"model": MODEL, "temperature": 0.3, "max_tokens": 160, "messages": chat_msgs(text, history)})
    if r.status_code >= 400: raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
    return (r.json()["choices"][0]["message"]["content"] or "").strip()

def stream_reply(text, history):
    with requests.post(LLM_URL, headers=HDR(), timeout=60, stream=True,
                       json={"model": MODEL, "temperature": 0.3, "max_tokens": 160, "stream": True, "messages": chat_msgs(text, history)}) as r:
        if r.status_code >= 400: raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
        r.encoding = "utf-8"
        for line in r.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"): continue
            d = line[5:].strip()
            if d == "[DONE]": break
            delta = json.loads(d)["choices"][0]["delta"].get("content")
            if delta: yield delta

def reply_chunks(text, history, pace):
    if STREAM:
        got = False
        try:
            for tok in stream_reply(text, history): got = True; yield tok
            return
        except Exception as e:
            if got: raise
            print("[stream failed -> non-streaming fallback]", e)
    for w in re.findall(r"\S+\s*", get_reply(text, history)):
        yield w
        if pace: time.sleep(pace)

def converse(text, history, pace=0.03, pref="auto"):
    """Yields reply chunks; the final item is the feature dict. Pivot pipeline for non-English: detect -> translate to English -> analyse + reply -> translate back."""
    src = detect(text, pref); out = src if pref in (None, "auto") else pref
    plain = src in ("en", "hx") and out in ("en", "hx")
    parts, err, rsrc, info, text_en = [], "", "model", {"lang": out, "src_lang": src}, text
    if src not in ("en", "hx"):
        try: text_en, b = mt(text, src, "en"); info.update(text_en=text_en, mt_in=b)
        except Exception as e: info["mt_err"] = f"{type(e).__name__}: {str(e)[:80]}"; print("[mt-in]", info["mt_err"])
    ex = ThreadPoolExecutor(1); fut = ex.submit(analyze_feats, text_en, text if text_en != text else None)
    try:
        cat = curated_cat(text, text_en if text_en != text else "")
        if cat: reply, rsrc = say(cat, out); chunks = words(reply)
        elif plain: chunks = reply_chunks(text, history, pace)
        else:
            en_reply = get_reply(text_en, []); reply = en_reply
            if out not in ("en", "hx"):
                try: reply, b2 = mt(en_reply, "en", out); info.update(reply_en=en_reply, mt_out=b2); rsrc = "model-mt"
                except Exception as e: info["mt_err"] = f"{type(e).__name__}: {str(e)[:80]}"; print("[mt-out]", info["mt_err"])
            chunks = words(reply)
        for tok in chunks:
            parts.append(tok); yield tok
            if pace and not plain or (cat and pace): time.sleep(pace)
    except Exception as e:
        err = f"{type(e).__name__}: {str(e)[:120]}"; print("[reply]", err); AI.update(analysis_ok=False, analysis_err=err, ts=now())
    reply = "".join(parts).strip()
    if not reply: reply = FALLBACK; rsrc = "fallback"; yield reply
    f = fut.result(); ex.shutdown(wait=False); f.update(info); f["reply"] = reply; f["reply_err"] = err; f["reply_src"] = rsrc
    model_made = rsrc in ("model", "model-mt")
    f["prompt_ver"] = H(P("chat"))[:8] if model_made else ""; f["model"] = MODEL if model_made else ""
    yield f

# ---------- Dynamic Distress Score ----------
STAGE_W = {"Investigation": 4, "Trial": 6, "Rehabilitation": 3, "Compensation": 3}

def compute(f, v, voice, missed):
    try: days = (dt.date.fromisoformat(v["hearing"]) - dt.date.today()).days
    except Exception: days = 99
    g = lambda k: float(f.get(k) or 0)
    parts = {
        "Negative sentiment": 20 * (1 - g("sentiment")) / 2,
        "Emotions (fear/hopelessness/sadness/anger)": 25 * (.3 * g("fear") + .3 * g("hopelessness") + .2 * g("sadness") + .2 * g("anger")),
        "Self-reported sleep/appetite/mood": (15 * (1 - sum((f["wellness"][k] - 1) / 4 for k in ("sleep", "appetite", "mood")) / 3)) if f.get("wellness") else 0,
        "Voice stress (acoustic)": 15 * voice,
        "Case-stage pressure": STAGE_W.get(v["stage"], 3) + (4 if 0 <= days <= 7 else 0),
        "Disengagement (missed check-ins)": 10 * min(missed, 3) / 3,
        "Self-harm expression": 30 * bool(f.get("self_harm")),
    }
    return min(100, sum(parts.values())), {k: round(x, 1) for k, x in parts.items()}

SLA = {"critical": 15, "high": 60, "watch": 240}                 # minutes to acknowledge, by level
NEXT_TIER = {"counsellor": "district_official", "district_official": "state_official"}
GRACE_S = 5   # seconds an alert stays visibly OVERDUE before it escalates
due_for = lambda level: (dt.datetime.now() + dt.timedelta(minutes=SLA.get(level, 60))).isoformat(timespec="seconds")

def safety_compute(c, vid, f, v):
    """Physical-safety risk (threats/intimidation) - kept separate from psychological distress."""
    try: days = (dt.date.fromisoformat(v["hearing"]) - dt.date.today()).days
    except Exception: days = 99
    past = sum(1 for r in c.execute("select feats from checkins where vid=? order by id desc limit 3", (vid,)) if json.loads(r["feats"]).get("threat"))
    kw, md = bool(f.get("threat_kw")), bool(f.get("threat"))
    parts = {"Threat reported (keyword-confirmed)": 55 * kw, "Threat flagged by model only": 25 * (md and not kw),
             "Repeated threats (last 3 check-ins)": 8 * past, "Witness / intimidation case": 10 * ("Witness" in v["category"] and (md or past > 0)),
             "Protection need flagged": 10 * bool({"protection", "relocation"} & set(f.get("needs") or [])), "Hearing within 7 days": 8 * (md and 0 <= days <= 7)}
    return min(100, sum(parts.values())), {k: round(x, 1) for k, x in parts.items()}

def confidence(f, text, voice, channel):
    src = str(f.get("source", "")); why = [f"Analysis source: {src or 'unknown'}"]
    base = 85 if src == "qwen2.5-3b" else 65 if src.startswith("qwen") else 80 if src == "seed-data" else 70 if src == "self-report" else 30 if src.startswith("carry") else 40
    if channel not in ("missed", "wellness") and len(text.split()) < 4: base -= 15; why.append("Very short message - limited evidence")
    if "threat_kw" in f and f.get("threat") != f.get("threat_kw"): base -= 20; why.append("Model and keyword threat checks disagree")
    if voice > 0: base += 5; why.append("Voice-stress signal available")
    if f.get("mt_in") or f.get("mt_out"): base -= 10; why.append("Machine translation in the loop - nuance may be lost")
    return max(10, min(95, base)), why

def process(c, vid, ts, channel, text, f, voice=0.0, missed=0):
    v = c.execute("select * from victims where id=?", (vid,)).fetchone()
    raw, parts_d = compute(f, v, voice, missed)
    prev = c.execute("select coalesce(distress, score) d, coalesce(safety, 0) s from checkins where vid=? order by id desc limit 1", (vid,)).fetchone()
    distress = .6 * raw + .4 * prev["d"] if prev else raw
    raw_s, parts_s = safety_compute(c, vid, f, v)
    safety = max(raw_s, .5 * raw_s + .5 * prev["s"]) if prev else raw_s          # threat risk decays slowly
    score = max(distress, safety)
    hist = [r["score"] for r in c.execute("select score from checkins where vid=? order by id desc limit 4", (vid,))][::-1]
    ys = hist + [score]; n = len(ys); xm, ym = (n - 1) / 2, sum(ys) / n
    slope = sum((i - xm) * (y - ym) for i, y in enumerate(ys)) / (sum((i - xm) ** 2 for i in range(n)) or 1) if n > 1 else 0
    pred = max(0, min(100, score + 2 * slope))
    level = ("critical" if f.get("self_harm") or score >= 80 else "high" if score >= 60 else
             "watch" if (score >= 40 and pred >= 60 and slope >= 4) or (pred >= 65 and slope >= 4) else "moderate" if score >= 40 else "low")
    if f.get("threat_kw") and level in ("low", "moderate", "watch"): level = "high"   # reported intimidation is never left in the low queue
    conf, why_c = confidence(f, text, voice, channel)
    f["conf"] = round(conf); f["distress"] = round(distress, 1); f["safety"] = round(safety, 1)    # sealed with the record via feats
    fj = json.dumps(f)
    cid = c.execute("insert into checkins(vid,ts,channel,text,feats,parts,raw,score,level,slope,pred,distress,safety,conf) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (vid, ts, channel, text, fj, json.dumps({"d": parts_d, "s": parts_s, "why": why_c}), raw, score, level, slope, pred, distress, safety, conf)).lastrowid
    bi, bh = block(c, "checkin", cid, cpay(vid, ts, channel, text, fj, score, level))
    why = f"distress {distress:.0f}, safety {safety:.0f}, trend {slope:+.1f}/check-in, projected {pred:.0f}" + (", self-harm expression" if f.get("self_harm") else "") + (", threat/intimidation reported" if f.get("threat_kw") else "")
    routes = {"critical": ["counsellor", "district_official"], "high": ["counsellor"], "watch": ["counsellor"]}.get(level, [])
    for role in routes:
        c.execute("insert into alerts(vid,ts,level,to_role,reason,status,due) values(?,?,?,?,?,'open',?)", (vid, ts, level, role, ("EARLY WARNING: " if level == "watch" else "") + why, due_for(level)))
    return dict(level=level, alerts=routes, blk=bi, hash=bh, distress=distress, safety=safety, conf=conf)

def recommend(f, level, v):
    r = []
    if f.get("conf", 100) < 50: r.append("Low-confidence assessment - counsellor to review manually")
    if f.get("self_harm"): r.append("URGENT: same-day counsellor call + Tele-MANAS/crisis follow-up")
    if f.get("threat"): r.append("Protection review; inform SP for police protection")
    if level in ("critical", "high"): r.append("Priority counselling session within 48h")
    elif level == "watch": r.append("Proactive counsellor check-in this week (trend rising)")
    if v["missed"] >= 2: r.append("Outreach: 2+ consecutive missed check-ins - telephonic/field follow-up")
    m = {"medical": "Medical / psychiatric evaluation", "relocation": "Relocation / shelter support", "financial": "Expedite relief & compensation disbursal",
         "legal_aid": "Assign legal aid & court accompaniment", "rehabilitation": "Enrol in rehabilitation scheme", "protection": "Protection assessment"}
    r += [m[n] for n in f.get("needs", []) if n in m and m[n] not in r]
    if v["stage"] == "Trial": r.append("Court-preparation support before next hearing")
    return r or ["Continue routine periodic check-ins"]

# ---------- Auth (demo: fixed passwords + fixed OTPs) ----------
def need(role, req: Request):
    s = SESS.get(req.cookies.get("sid_" + role, ""))
    if not s or s["role"] != role: raise HTTPException(401)
    return s

class Login(BaseModel): role: str; uid: str; password: str
class Otp(BaseModel): tmp: str; otp: str

@app.post("/api/login")
def login(b: Login):
    uid = b.uid if b.role == "admin" else b.uid.upper()
    if b.role == "admin": ok = (uid, b.password) == ADMIN
    else:
        with db() as c: ok = b.password == USER_PW and c.execute("select 1 from victims where id=?", (uid,)).fetchone() is not None
    if not ok: raise HTTPException(400, "Invalid ID or password")
    t = secrets.token_hex(8); TMP[t] = (b.role, uid)
    return {"tmp": t, "hint": f"OTP sent to registered mobile +91 ••••••3210 (demo OTP: {ADMIN_OTP if b.role == 'admin' else USER_OTP})"}

@app.post("/api/otp")
def otp(b: Otp, res: Response):
    role, uid = TMP.get(b.tmp, (None, None))
    if not role or b.otp != (ADMIN_OTP if role == "admin" else USER_OTP): raise HTTPException(400, "Invalid OTP")
    del TMP[b.tmp]; tok = secrets.token_hex(16); SESS[tok] = {"role": role, "id": uid}
    res.set_cookie("sid_" + role, tok, httponly=True); return {"ok": True}

@app.post("/api/logout")
def logout(b: dict, req: Request, res: Response):
    SESS.pop(req.cookies.get("sid_" + b.get("role", ""), ""), None); res.delete_cookie("sid_" + b.get("role", "")); return {"ok": True}

@app.get("/api/ai/status")
def ai_status():
    t = time.time()
    try:
        requests.get(LLM_BASE + "/v1/models", headers=HDR(), timeout=3).raise_for_status()
        AI.update(ok=True, err="", ms=int((time.time() - t) * 1000))
    except Exception as e:
        found = discover_llm(); AI.update(ok=found, err="" if found else type(e).__name__, ms=int((time.time() - t) * 1000))
    AI["base"] = LLM_BASE; AI["ts"] = now(); return AI

def page(role, req):
    s = SESS.get(req.cookies.get("sid_" + role, ""))
    return FileResponse(f"static/{role if s and s['role'] == role else 'login'}.html")

@app.get("/admin")
def p_admin(req: Request): return page("admin", req)
@app.get("/user")
def p_user(req: Request): return page("user", req)
@app.get("/")
def home(): return HTMLResponse("<h2>ChetnaSetu PoC</h2><a href=/admin>Admin console</a> · <a href=/user>Beneficiary portal</a>")

# ---------- Admin API ----------
@app.get("/api/admin/cases")
def cases(req: Request):
    need("admin", req)
    with db() as c:
        return [dict(r) for r in c.execute("""select v.*, k.score, k.distress, k.safety, k.conf, k.level, k.slope, k.pred, k.ts from victims v
            left join checkins k on k.id=(select max(id) from checkins where vid=v.id) order by k.score desc""")]

@app.get("/api/admin/victim/{vid}")
def victim(vid: str, req: Request):
    need("admin", req)
    with db() as c:
        v = c.execute("select * from victims where id=?", (vid,)).fetchone()
        h = []
        for r in c.execute("select k.*, l.idx blk, l.hash from checkins k left join ledger l on l.kind='checkin' and l.ref=cast(k.id as text) where k.vid=? order by k.ts, k.id", (vid,)):
            d = dict(r); d["parts"] = json.loads(d["parts"]); fe = json.loads(d.pop("feats")); d["engine"] = fe.get("source", ""); d["reply"] = fe.get("reply", ""); d["reply_src"] = fe.get("reply_src", ""); d["prompt_ver"] = fe.get("prompt_ver", ""); d["lang"] = fe.get("lang", ""); d["text_en"] = fe.get("text_en", ""); d["reply_en"] = fe.get("reply_en", ""); d["fe"] = fe; h.append(d)
        v = dict(v); v["coping"] = c.execute("select count(*) from coping where vid=?", (vid,)).fetchone()[0]
        if h: v["recs"] = recommend(h[-1]["fe"], h[-1]["level"], v)
        for d in h: d.pop("fe")
    return {"victim": v, "history": h}

@app.get("/api/admin/feed")
def feed(req: Request):
    need("admin", req)
    with db() as c:
        return [dict(r) for r in c.execute("""select k.id,k.vid,k.ts,k.channel,k.text,k.level,
            (select group_concat(to_role) from alerts a where a.vid=k.vid and a.ts=k.ts) routed from checkins k order by k.ts desc, k.id desc limit 25""")]

@app.get("/api/admin/ledger")
def ledger(req: Request):
    need("admin", req)
    with db() as c: return [dict(r) for r in c.execute("select * from ledger order by idx desc limit 12")]

@app.get("/api/admin/verify")
def do_verify(req: Request):
    need("admin", req)
    with db() as c: return verify(c)

@app.post("/api/admin/tamper")
def tamper(b: dict, req: Request):
    need("admin", req)
    with db() as c:   # simulates an insider silently lowering the highest-risk record, bypassing the ledger
        r = c.execute("select id,vid from checkins order by score desc limit 1").fetchone()
        c.execute("update checkins set score=8, level='low' where id=?", (r["id"],))
    return {"vid": r["vid"]}

@app.post("/api/admin/reset")
def reset(b: dict, req: Request):
    need("admin", req)
    with db() as c: seed_data(c)
    return {"ok": True}

# ---------- Beneficiary API ----------
class UCheck(BaseModel): channel: str; text: str = ""; voice_stress: float = 0; history: list = []

@app.post("/api/user/checkin")
def ucheck(b: UCheck, req: Request):
    vid = need("user", req)["id"]
    with db() as c:
        v = c.execute("select * from victims where id=?", (vid,)).fetchone()
        if b.channel == "missed":
            missed = v["missed"] + 1; last = c.execute("select feats from checkins where vid=? order by id desc limit 1", (vid,)).fetchone()
            f = json.loads(last["feats"]) if last else dict(sentiment=0, fear=0, sadness=0, anger=0, hopelessness=0, needs=[], threat=False, self_harm=False)
            f.update(reply_src="system", prompt_ver="", model="", analysis_raw="", source="carry-forward (no response)", reply="No problem - we will try again soon. You can call 14566 any time."); text, voice = "(scheduled check-in missed)", 0
        else:
            missed = 0; f = next(x for x in converse(b.text, b.history, pace=0, pref=v["lang"] or "auto") if isinstance(x, dict)); text = b.text; voice = b.voice_stress if b.channel == "ivrs" else 0
        c.execute("update victims set missed=? where id=?", (missed, vid))
        out = process(c, vid, now(), b.channel, text, f, voice, missed)
    ai_ok = None if b.channel == "missed" else (f["source"].startswith("qwen") and not f.get("reply_err"))
    return {"reply": f["reply"], "detail": f.get("reply_err") or f["source"], "alerted": bool(out["alerts"]), "blk": out["blk"], "hash": out["hash"], "ai_ok": ai_ok, **flags(f)}

def sse(o): return f"data: {json.dumps(o, ensure_ascii=False)}\n\n"

@app.post("/api/user/chat")
def chat(b: UCheck, req: Request):
    vid = need("user", req)["id"]
    with db() as c: pref = c.execute("select lang from victims where id=?", (vid,)).fetchone()["lang"] or "auto"
    def gen():
        f = None
        for x in converse(b.text, b.history, pref=pref):
            if isinstance(x, dict): f = x
            else: yield sse({"t": x})
        with db() as c:
            c.execute("update victims set missed=0 where id=?", (vid,))
            out = process(c, vid, now(), "chatbot", b.text, f, 0, 0)
        yield sse({"done": True, "alerted": bool(out["alerts"]), "blk": out["blk"], "hash": out["hash"],
                   "ai_ok": f["source"].startswith("qwen") and not f["reply_err"], "detail": f["reply_err"] or f["source"], **flags(f)})
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.get("/api/user/me")
def me(req: Request):
    vid = need("user", req)["id"]
    with db() as c:
        v = dict(c.execute("select id,district,state,stage,hearing,lang,toured from victims where id=?", (vid,)).fetchone())
        h = [dict(r) for r in c.execute("select k.ts,k.channel,k.text,l.idx blk,l.hash from checkins k left join ledger l on l.kind='checkin' and l.ref=cast(k.id as text) where k.vid=? order by k.ts desc, k.id desc limit 30", (vid,))]
    return {"victim": v, "history": h}

# ---------- Alerts: SLA clock, acknowledgement, auto-escalation, justified overrides (every action sealed in the ledger) ----------
def flags(f):
    g = lambda k: float(f.get(k) or 0); crisis = bool(f.get("self_harm"))
    return {"crisis": crisis, "coping": (not crisis) and (max(g("fear"), g("sadness"), g("hopelessness")) >= .3 or bool(f.get("threat")))}

def event(c, aid, action, actor, note=""):
    ts = now(); eid = c.execute("insert into events(ts,alert_id,action,actor,note) values(?,?,?,?,?)", (ts, aid, action, actor, note)).lastrowid
    block(c, "event", eid, json.dumps([aid, ts, action, actor, note]))

def sweep(c):
    cutoff = (dt.datetime.now() - dt.timedelta(seconds=GRACE_S)).isoformat(timespec="seconds")
    for a in c.execute("select * from alerts where status='open' and due is not null and due < ?", (cutoff,)).fetchall():
        nxt = NEXT_TIER.get(a["to_role"])
        if c.execute("update alerts set status=? where id=? and status='open'", ("escalated" if nxt else "overdue", a["id"])).rowcount == 0: continue   # another request already handled it
        dup = nxt and c.execute("select 1 from alerts where vid=? and to_role=? and status in ('open','ack','overdue')", (a["vid"], nxt)).fetchone()
        event(c, a["id"], "auto-escalated" if nxt else "overdue (top tier)", "system",
              f"No acknowledgement within SLA ({SLA.get(a['level'], 60)} min)" + ((f" - {nxt} already has an active alert" if dup else f" - escalated to {nxt}") if nxt else ""))
        if nxt and not dup:
            c.execute("insert into alerts(vid,ts,level,to_role,reason,status,due,parent) values(?,?,?,?,?,'open',?,?)",
                      (a["vid"], now(), a["level"], nxt, f"AUTO-ESCALATED from {a['to_role']}: " + a["reason"], due_for(a["level"]), a["id"]))

@app.get("/api/admin/alerts")
def alerts_api(req: Request):
    need("admin", req)
    with db() as c:
        sweep(c); n = dt.datetime.now(); act = []
        for r in c.execute("select * from alerts where status in ('open','overdue','ack') order by case status when 'overdue' then 0 when 'open' then 1 else 2 end, id desc limit 25"):
            d = dict(r); d["remaining"] = int((dt.datetime.fromisoformat(r["due"]) - n).total_seconds()) if r["due"] else 0; act.append(d)
        ev = [dict(r) for r in c.execute("select * from events order by id desc limit 8")]
    return {"active": act, "events": ev, "open": sum(a["status"] != "ack" for a in act), "overdue": sum(a["status"] == "overdue" for a in act)}

class Act(BaseModel): action: str; note: str = ""; role: str = "counsellor"

@app.post("/api/admin/alert/{aid}/act")
def alert_act(aid: int, b: Act, req: Request):
    need("admin", req)
    if b.action not in ("ack", "resolve", "override"): raise HTTPException(400, "Unknown action")
    if b.action != "ack" and len(b.note.strip()) < 10: raise HTTPException(400, "A justification of at least 10 characters is mandatory")
    with db() as c:
        a = c.execute("select * from alerts where id=?", (aid,)).fetchone()
        if not a or a["status"] not in ("open", "overdue", "ack"): raise HTTPException(409, "Alert is no longer active")
        st, label = {"ack": ("ack", "acknowledged"), "resolve": ("resolved", "resolved"), "override": ("overridden", "overridden (justified)")}[b.action]
        c.execute("update alerts set status=?, ack_by=?, ack_ts=?, note=? where id=?", (st, b.role, now(), b.note.strip(), aid))
        event(c, aid, label, b.role, b.note.strip())
    return {"ok": True}

@app.post("/api/admin/fastforward")
def fastforward(b: dict, req: Request):   # demo helper: simulate an SLA breach
    need("admin", req); past = (dt.datetime.now() - dt.timedelta(seconds=1)).isoformat(timespec="seconds")
    with db() as c:
        if b.get("id"): c.execute("update alerts set due=? where status='open' and id=?", (past, int(b["id"])))
        else: c.execute("update alerts set due=? where status='open'", (past,))
        sweep(c)
    return {"ok": True, "grace": GRACE_S}

# ---------- Daily wellness check-in + calming exercises ----------
class Well(BaseModel): sleep: int; appetite: int; mood: int; note: str = ""

@app.post("/api/user/wellness")
def wellness(b: Well, req: Request):
    vid = need("user", req)["id"]
    s_, a_, m_ = [max(1, min(5, x)) for x in (b.sleep, b.appetite, b.mood)]; note = b.note.strip(); t = note.lower(); low = (5 - m_) / 4
    f = dict(sentiment=(m_ - 3) / 2, fear=0, sadness=.8 * low, anger=0, hopelessness=.6 * low, self_harm=any(k in t for k in SELF),
             threat_kw=any(k in t for k in THREAT), needs=["counselling"] if m_ <= 2 else [], wellness=dict(sleep=s_, appetite=a_, mood=m_), source="self-report", prompt_ver="", model="", analysis_raw="")
    f["threat"] = f["threat_kw"]; L = lang_of(note) if note else "en"
    heavy = m_ <= 2 or s_ <= 2
    f["reply"] = (say("self", L)[0] if f["self_harm"] else say("threat", L)[0] if f["threat_kw"] else
                  "Thank you for telling me. Things sound heavy right now - your counsellor will see this. A short calming exercise can help; would you like to try one?" if heavy
                  else "Thank you for checking in today. Take care of yourself 🙏"); f["reply_src"] = "curated"
    text = f"Daily wellness: sleep {s_}/5 · appetite {a_}/5 · mood {m_}/5" + (f" — {note}" if note else "")
    with db() as c:
        c.execute("update victims set missed=0 where id=?", (vid,))
        out = process(c, vid, now(), "wellness", text, f, 0, 0)
    fl = flags(f)
    return {"reply": f["reply"], "alerted": bool(out["alerts"]), "blk": out["blk"], "hash": out["hash"], "crisis": fl["crisis"], "coping": fl["coping"] or heavy and not fl["crisis"]}

@app.post("/api/user/coping")
def coping(b: dict, req: Request):
    vid = need("user", req)["id"]
    with db() as c: c.execute("insert into coping(vid,ts,kind) values(?,?,?)", (vid, now(), str(b.get("kind", ""))[:20]))
    return {"ok": True}

# ---------- Language preference ----------
@app.get("/api/user/greeting")
def greeting(req: Request):
    vid = need("user", req)["id"]
    with db() as c: pref = c.execute("select lang from victims where id=?", (vid,)).fetchone()["lang"] or "auto"
    if pref in ("auto", "en"): return {"text": "Namaste 🙏 I am your ChetnaSetu assistant. How are you feeling today?", "src": "curated"}
    t, src = say("hello", pref); return {"text": t, "src": src}

class LangSet(BaseModel): lang: str

@app.post("/api/user/lang")
def set_lang(b: LangSet, req: Request):
    vid = need("user", req)["id"]
    if b.lang not in ("auto", "hx") and b.lang not in T.NAME: raise HTTPException(400, "Unsupported language")
    with db() as c: c.execute("update victims set lang=? where id=?", (b.lang, vid))
    return {"ok": True}

@app.get("/api/i18n/{lang}")
def i18n_api(lang: str):
    if lang in ("auto", "en", "hx"): return {"lang": "en", "dir": "ltr", "mt": False, "s": I.EN}
    if lang not in T.NAME: raise HTTPException(404, "Unsupported language")
    st, mtf = i18n_strings(lang)
    return {"lang": lang, "dir": "rtl" if lang in I.RTL else "ltr", "mt": mtf, "s": st}

@app.post("/api/user/tour")
def tour_done(b: dict, req: Request):      # first-login tour finished or skipped (admin reset sets it back to 0)
    vid = need("user", req)["id"]
    with db() as c: c.execute("update victims set toured=1 where id=?", (vid,))
    return {"ok": True}

# ---------- Demo data ----------
def seed_data(c):
    random.seed(7)
    for t in ("victims", "checkins", "alerts", "ledger", "events", "coping"): c.execute(f"delete from {t}")
    block(c, "genesis", "-", "ChetnaSetu ledger genesis")
    V = [("CS-1001", "Rape / gang rape", "Nalanda", "Bihar", "Trial", 1.4), ("CS-1002", "Murder (family)", "Gaya", "Bihar", "Investigation", .6),
         ("CS-1003", "Arson / caste violence", "Sitapur", "Uttar Pradesh", "Rehabilitation", 0), ("CS-1004", "Witness - intimidation", "Barmer", "Rajasthan", "Trial", 1.8),
         ("CS-1005", "Grievous hurt", "Nadia", "West Bengal", "Compensation", -.3), ("CS-1006", "Rape / gang rape", "Hathras", "Uttar Pradesh", "Investigation", 1.0),
         ("CS-1007", "Arson / caste violence", "Jalore", "Rajasthan", "Rehabilitation", -.5), ("CS-1008", "Murder (family)", "Bankura", "West Bengal", "Trial", .3)]
    txt = ["I feel okay today", "Sleep is difficult, court date worries me", "Some people are pressuring us to withdraw", "I am scared and feel alone", "No one is helping, feel hopeless"]
    for vid, cat, d, s, st, tr in V:
        hd = (dt.date.today() + dt.timedelta(days=random.choice([3, 20, 45]))).isoformat()
        c.execute("insert into victims(id,category,district,state,stage,hearing,missed) values(?,?,?,?,?,?,0)", (vid, cat, d, s, st, hd))
        for i in range(6):
            sev = max(0, min(1, .25 + .1 * tr * i + random.uniform(-.1, .1)))
            f = dict(sentiment=-sev, fear=sev, sadness=sev * .8, anger=sev * .3, hopelessness=sev * .9, threat=sev > .85, self_harm=False,
                     needs=["counselling"] + (["protection"] if sev > .6 else []), source="seed-data")
            ts = (dt.datetime.now() - dt.timedelta(weeks=5 - i, hours=random.randint(0, 20))).isoformat(timespec="seconds")
            process(c, vid, ts, random.choice(["ivrs", "chatbot", "app", "sms"]), txt[min(4, int(sev * 5))], f, sev * .8, int(sev > .7))
    c.execute("update alerts set status='resolved', note='historical (seed data)'")
    c.execute("""update alerts set status='open', note='', due=? where id in (select max(a.id) from alerts a join checkins k on k.vid=a.vid
                 and k.id=(select max(id) from checkins where vid=a.vid) where k.level in ('high','critical','watch') group by a.vid, a.to_role)""", (due_for("high"),))

# ---------- Editable prompts (prompts/*.txt are re-read on every request - no restart needed) ----------
DEFAULT_PROMPTS = {"chat": SYS_CHAT, "analysis": "You are a JSON API, not a chatbot: never greet or chat. " + SYS_JSON}
os.makedirs("prompts", exist_ok=True)
for _k, _v in DEFAULT_PROMPTS.items():
    if not os.path.exists(f"prompts/{_k}.txt"): open(f"prompts/{_k}.txt", "w", encoding="utf-8").write(_v)

def P(name):
    try: return open(f"prompts/{name}.txt", encoding="utf-8").read().strip() or DEFAULT_PROMPTS[name]
    except OSError: return DEFAULT_PROMPTS[name]

print('[llm] reachable at', LLM_BASE) if discover_llm() else print('[llm] NOT reachable yet - will keep retrying (start ~/vllm-server/serve.sh)')

with db() as c:
    if not c.execute("select 1 from victims").fetchone(): seed_data(c)
