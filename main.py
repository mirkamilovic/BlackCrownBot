"""
BlackCrownUzBot - bitta fayl: aiogram bot + FastAPI web-game (WebSocket) + owner paneli.
Ishga tushirish:
  pip install "aiogram>=3.13" fastapi "uvicorn[standard]"
  BOT_TOKEN=... OWNER_ID=123,456 WEBAPP_URL=https://domen.up.railway.app DATA_DIR=./data python main.py
Lokal sinov: DEV=1 va brauzerda  /?dev=111&name=Ali  (har xil dev=ID bilan bir necha oyna)
"""
import asyncio, hashlib, hmac, html, json, os, random, sqlite3, time
from pathlib import Path
from urllib.parse import parse_qsl

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, Response
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (InlineKeyboardButton as B, InlineKeyboardMarkup as KB, LabeledPrice,
                           MenuButtonWebApp, WebAppInfo)

BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNERS = {int(x) for x in os.environ.get("OWNER_ID", "").split(",") if x.strip()}
WEBAPP_URL = (os.environ.get("WEBAPP_URL", "").split() or [""])[0].rstrip("/")
DATA = Path(os.environ.get("DATA_DIR") or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or "./data"); (DATA / "assets").mkdir(parents=True, exist_ok=True)
PORT = int(os.environ.get("PORT", "8080")); DEV = os.environ.get("DEV") == "1"
BOT_USERNAME = ""

# ============================ BAZA ============================
DB = sqlite3.connect(DATA / "bc.db", check_same_thread=False); DB.row_factory = sqlite3.Row
DB.execute("PRAGMA journal_mode=WAL"); DB.execute("PRAGMA synchronous=NORMAL")
DB.executescript("""
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,name TEXT,points INTEGER DEFAULT 0,games INTEGER DEFAULT 0,
 wins INTEGER DEFAULT 0,pro_until INTEGER DEFAULT 0,banned INTEGER DEFAULT 0,clan_id INTEGER DEFAULT 0,
 free_voice INTEGER DEFAULT 10,created INTEGER);
CREATE TABLE IF NOT EXISTS assets(key TEXT PRIMARY KEY,path TEXT,ext TEXT,emoji_id TEXT,ts INTEGER);
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY,v TEXT);
CREATE TABLE IF NOT EXISTS clans(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT,tag TEXT,owner_id INTEGER,points INTEGER DEFAULT 0,created INTEGER);
CREATE TABLE IF NOT EXISTS games(id INTEGER PRIMARY KEY AUTOINCREMENT,room TEXT,players INTEGER,winner TEXT,days INTEGER,ts INTEGER);
CREATE TABLE IF NOT EXISTS points_log(uid INTEGER,d INTEGER,ts INTEGER);
CREATE INDEX IF NOT EXISTS i_pl ON points_log(ts);
CREATE TABLE IF NOT EXISTS game_players(uid INTEGER,gid INTEGER,role TEXT,win INTEGER,n INTEGER,days INTEGER,ts INTEGER);
CREATE INDEX IF NOT EXISTS i_gp ON game_players(uid,ts);
CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY AUTOINCREMENT,uid INTEGER,day INTEGER,slot INTEGER,kind TEXT,goal INTEGER,
 prog INTEGER DEFAULT 0,done INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS i_tk ON tasks(uid,day);
CREATE TABLE IF NOT EXISTS friends(uid INTEGER,fid INTEGER,PRIMARY KEY(uid,fid));
""")
for _c, _d in [("streak", "INTEGER DEFAULT 0"), ("best", "INTEGER DEFAULT 0"), ("last_day", "INTEGER DEFAULT 0"),
               ("keys", "INTEGER DEFAULT 0"), ("rr_day", "INTEGER DEFAULT 0"), ("photo", "TEXT DEFAULT ''"),
               ("ref", "INTEGER DEFAULT 0"), ("ref_ok", "INTEGER DEFAULT 0"), ("prefs", "TEXT DEFAULT '{}'"),
               ("survived", "INTEGER DEFAULT 0")]:
    try: DB.execute(f"ALTER TABLE users ADD COLUMN {_c} {_d}")
    except sqlite3.OperationalError: pass
for _c, _d in [("reason", "TEXT DEFAULT ''"), ("alive", "INTEGER DEFAULT 0")]:
    try: DB.execute(f"ALTER TABLE game_players ADD COLUMN {_c} {_d}")
    except sqlite3.OperationalError: pass
try: DB.execute("ALTER TABLE users ADD COLUMN last_n INTEGER DEFAULT 0")
except sqlite3.OperationalError: pass
DB.commit()
def sql(q, a=()): c = DB.execute(q, a); DB.commit(); return c
def one(q, a=()): return DB.execute(q, a).fetchone()
def allr(q, a=()): return DB.execute(q, a).fetchall()

DEF = {"win_pts": 30, "lose_pts": 0, "leave_pen": 25, "kick_pen": 30, "open_mult": 1.5, "intro": 120, "night": 60,
       "vote": 40, "disc": 120, "star_1": 100, "star_3": 280, "star_6": 500, "star_12": 800, "free_voice": 10,
       "min_players": 6, "max_players": 16, "last_word": 20, "afk_max": 2, "popup": 3.5, "chat_gap": 0.8,
       "channel_url": "", "chat_url": "", "brand": "Play Mafia", "ad_secs": 4.0, "banner_ad_url": "", "ad_1_url": "", "ad_2_url": "", "ad_3_url": "", "ad_4_url": "", "ad_5_url": ""}
def S(k):
    r = one("SELECT v FROM settings WHERE k=?", (k,))
    return type(DEF[k])(r["v"]) if r else DEF[k]

# ============================ KONTENT SLOTLARI ============================
ROLE_NAMES = {"tinch": "Tinch aholi", "izquvar": "Izquvar", "doktor": "Doktor", "bezori": "Bezori", "rais": "Rais",
              "qassob": "Qassob", "hamsoya": "Hamsoya", "folbin": "Folbin", "mirshab": "Mirshab", "mafia": "Mafia",
              "mafiaboshi": "Mafiaboshi", "xufya": "Xufya"}
MAFIA_TEAM = {"mafia", "mafiaboshi", "xufya"}
SLOTS = {"logo": ("Logo", "img"), "bg_lobby": ("Lobbi banneri", "img"), "banner_ad": ("Reklama banneri", "img"),
         "banner_pro": ("Pro banneri", "img"), "bg_night": ("Tun foni", "img"), "bg_day": ("Kun foni", "img")}
for _r, _n in ROLE_NAMES.items(): SLOTS["role_" + _r] = ("Rol: " + _n, "img")
for _k, _n in [("tasks", "Ikona: Vazifalar"), ("history", "Ikona: Tarix"), ("rules", "Ikona: Qoidalar"), ("rating", "Ikona: Reyting"),
               ("clans", "Ikona: Ovullar"), ("profile", "Ikona: Profil"), ("create", "Ikona: Yangi xona"),
               ("quick", "Ikona: Tezkor qo'shilish"), ("code", "Ikona: Kod bilan kirish")]: SLOTS["ic_" + _k] = (_n, "img")
for _k, _n in [("night", "Tun"), ("day", "Kun"), ("kill", "O'ldirish"), ("heal", "Davolash"), ("check", "Tekshirish"),
               ("vote", "Ovoz"), ("crown", "Pro toj"), ("mic", "Mikrofon"), ("trophy", "Kubok"), ("clan", "Klan"),
               ("coin", "Ball"), ("fire", "Olov (seriya)")]: SLOTS["emo_" + _k] = ("Emoji: " + _n, "emoji")
for _k, _n in [("start", "O'yin boshlanishi"), ("night", "Tun"), ("day", "Kun"), ("kill", "O'ldirish"),
               ("heal", "Davolash"), ("vote", "Ovoz berish"), ("win", "G'alaba"), ("lose", "Mag'lubiyat"),
               ("click", "Tugma")]: SLOTS["snd_" + _k] = ("Ovoz: " + _n, "snd")
for _i in range(1, 6): SLOTS[f"ad_{_i}"] = (f"Reklama banneri {_i}", "img")
for _k in [k for k in list(SLOTS) if k.startswith("ic_")]: SLOTS[_k + "_dark"] = (SLOTS[_k][0] + " (tungi rejim)", "img")
AD_URL_KEYS = ["banner_ad"] + [f"ad_{i}" for i in range(1, 6)]
GROUPS = [("Banner va reklama", lambda k: k in ("logo", "bg_lobby", "banner_ad", "banner_pro") or k.startswith("ad_")),
          ("Rollar", lambda k: k.startswith("role_")),
          ("Ikonalar (kunduzgi)", lambda k: k.startswith("ic_") and not k.endswith("_dark")),
          ("Ikonalar (tungi)", lambda k: k.startswith("ic_") and k.endswith("_dark")),
          ("Emoji", lambda k: k.startswith("emo_")),
          ("Fonlar", lambda k: k in ("bg_night", "bg_day"))]

def amap(): return {r["key"]: f"/a/{r['key']}.{r['ext']}?v={r['ts']}" for r in allr("SELECT key,ext,ts FROM assets")}
def em(key, fb=""):  # premium emoji bo'lsa shu, bo'lmasa oddiy emoji
    r = one("SELECT emoji_id FROM assets WHERE key=?", (key,))
    if r and r["emoji_id"]: return f'<tg-emoji emoji-id="{r["emoji_id"]}">{fb or "."}</tg-emoji> '
    return (fb + " ") if fb else ""

# ============================ O'YIN MOTORI ============================
HUB, ROOMS, UROOM, COOL = {}, {}, {}, {}
LADDER = [(6, ["mafia", "doktor"]), (7, ["mafia"]), (8, ["izquvar"]), (9, ["mafiaboshi"]), (10, ["rais"]),
          (11, ["bezori"]), (12, ["qassob"]), (13, ["hamsoya"]), (14, ["folbin"]), (15, ["mirshab"]), (16, ["xufya"])]
IMPLEMENTED = {"tinch", "mafia", "mafiaboshi", "doktor", "izquvar"}  # qolganlarining qoidasi hali berilmagan
TZ = 5 * 3600  # Toshkent vaqti (UTC+5)
LEAVE_COOL = 1800  # o'yinni tark etgandan keyingi kutish (soniya)
QUESTIONS = ["Siz mafiamisiz? Shu savolga javob bering.", "Sizningcha, birinchi bo'lib kim shubhali?",
             "Kim ko'proq gapirdi, kim jim turdi? Fikringizni ayting.", "Sizni mafiya deb o'ylashsa, nima deysiz?",
             "O'zingizni tanishtiring: kimsiz va nega aholi sizga ishonishi kerak?"]
PREF_DEF = {"n_start": 0, "gap": 6, "n_friend": 1, "n_priv": 0, "voice": "auto", "insta": 1}

def make_roles(n, pro):
    rs = []
    for need, add in LADDER:
        if n >= need and (pro or need < 13): rs += add
    rs = rs[:n]; rs += ["tinch"] * (n - len(rs)); random.shuffle(rs); return rs

async def send(uid, msg):
    ws = HUB.get(uid)
    if ws:
        try: await ws.send_json(msg)
        except Exception: HUB.pop(uid, None)

def room_of(uid): return ROOMS.get(UROOM.get(uid))
def cool(uid): return max(0, int(COOL.get(uid, 0) - time.time()))

# ---------- vaqt yordamchilari ----------
def today(): return int((time.time() + TZ) // 86400)
def week_start():
    d = today(); return (d - (d + 3) % 7) * 86400 - TZ  # dushanba 00:00
def day_left(): return int((today() + 1) * 86400 - TZ - time.time())
def week_left(): return int(week_start() + 7 * 86400 - time.time())

def addp(uid, d):
    if d:
        sql("UPDATE users SET points=points+? WHERE id=?", (d, uid))
        sql("INSERT INTO points_log(uid,d,ts) VALUES(?,?,?)", (uid, d, int(time.time())))

def prefs(uid):
    r = one("SELECT prefs FROM users WHERE id=?", (uid,))
    try: p = json.loads((r["prefs"] if r else "") or "{}")
    except Exception: p = {}
    return {**PREF_DEF, **{k: v for k, v in p.items() if k in PREF_DEF}}

def set_ref(uid, arg):
    if not (arg or "").startswith("ref_") or not arg[4:].isdigit(): return
    rid = int(arg[4:]); u = one("SELECT ref,games FROM users WHERE id=?", (uid,))
    if rid != uid and u and u["ref"] == 0 and u["games"] == 0 and one("SELECT 1 FROM users WHERE id=?", (rid,)):
        sql("UPDATE users SET ref=? WHERE id=?", (rid, uid))

# ---------- kunlik vazifalar ----------
POOL = {0: [("play", 2), ("play", 1)], 1: [("win", 1), ("play", 3)], 2: [("win", 2), ("play", 5)]}
LV = ["OSON", "O'RTA", "QIYIN"]; REW = [1, 1, 2]
TT = {"play": "{n} ta o'yinni oxirigacha o'ynang", "win": "{n} ta o'yinda g'alaba qozoning"}

def ensure_tasks(uid):
    d = today()
    if not one("SELECT 1 FROM tasks WHERE uid=? AND day=?", (uid, d)):
        for s in range(3):
            k, n = random.choice(POOL[s]); sql("INSERT INTO tasks(uid,day,slot,kind,goal) VALUES(?,?,?,?,?)", (uid, d, s, k, n))

def tasks_view(uid):
    ensure_tasks(uid); d = today(); u = one("SELECT streak,best,last_day,keys,rr_day FROM users WHERE id=?", (uid,))
    st = u["streak"] if u["last_day"] >= d - 1 else 0
    rows = allr("SELECT * FROM tasks WHERE uid=? AND day=? ORDER BY slot", (uid, d))
    act = next((r["slot"] for r in rows if not r["done"]), -1)
    return {"t": "tasks", "streak": st, "best": u["best"], "today_done": u["last_day"] == d, "left": day_left(),
            "keys": u["keys"], "rr": 0 if u["rr_day"] == d else 1,
            "tasks": [{"slot": r["slot"], "lv": LV[r["slot"]], "text": TT[r["kind"]].format(n=r["goal"]), "prog": r["prog"],
                       "goal": r["goal"], "done": bool(r["done"]), "active": r["slot"] == act, "rew": REW[r["slot"]]} for r in rows]}

async def task_event(uid, kind):
    ensure_tasks(uid); d = today()
    r = one("SELECT * FROM tasks WHERE uid=? AND day=? AND done=0 ORDER BY slot LIMIT 1", (uid, d))
    if not r or r["kind"] != kind: return
    p = r["prog"] + 1; done = int(p >= r["goal"])
    sql("UPDATE tasks SET prog=?,done=? WHERE id=?", (p, done, r["id"]))
    if done:
        sql("UPDATE users SET keys=keys+? WHERE id=?", (REW[r["slot"]], uid))
        msg = f"Vazifa bajarildi: +{REW[r['slot']]} kalit"
        if not one("SELECT 1 FROM tasks WHERE uid=? AND day=? AND done=0", (uid, d)):
            u = one("SELECT streak,last_day FROM users WHERE id=?", (uid,))
            if u["last_day"] != d:
                st = u["streak"] + 1 if u["last_day"] == d - 1 else 1
                sql("UPDATE users SET streak=?,best=MAX(best,?),last_day=? WHERE id=?", (st, st, d, uid))
                sql("UPDATE users SET free_voice=free_voice+1 WHERE id=?", (uid,)); msg += ". +1 bepul ovozli o'yin"
                idx = (st - 1) % 7 + 1
                if idx == 5: sql("UPDATE users SET free_voice=free_voice+3 WHERE id=?", (uid,)); msg += " va seriya mukofoti +3"
                elif idx == 7: addp(uid, 50); msg += " va seriya sandig'i +50 ball"
        await send(uid, {"t": "toast", "m": msg})
    await send(uid, tasks_view(uid)); await send(uid, {"t": "me", "me": me_info(uid)})

# ---------- xona ----------
class Room:
    def __init__(s, owner, name, mode, closed, pro, minp=6, disc=120, req=0):
        s.id = os.urandom(3).hex(); s.owner = owner; s.name = name or "Xona"; s.mode = mode; s.closed = closed
        s.pro = pro; s.minp = minp; s.disc = disc; s.req = req
        s.p = {}; s.spec = set(); s.phase = "lobby"; s.day = 0; s.night = 0; s.deadline = 0; s.chat = []; s.cid = 0
        s.act = {}; s.votes = {}; s.wake = asyncio.Event(); s.task = None; s.winner = None; s.priv = {}; s.log = []
        s.speaker = None; s.skipd = set(); s.miss = {}; s.heal_prev = {}; s.selfheal = set(); s.lastchat = {}; s.notified = 0; s.talk = set(); s.nov = set(); s.q = random.choice(QUESTIONS)

    def add(s, uid, name):
        u = one("SELECT photo,pro_until FROM users WHERE id=?", (uid,))
        s.p[uid] = {"name": name, "alive": True, "role": None, "left": False, "reason": "",
                    "ph": (u["photo"] if u else "") or "", "pro": bool(u and u["pro_until"] > time.time())}
    def alive(s): return [u for u, x in s.p.items() if x["alive"]]
    def team(s, u): return "mafia" if s.p[u]["role"] in MAFIA_TEAM else "town"
    def say(s, m, u=None):
        s.log.append({"m": m, "d": s.day, "u": u}); s.cid += 1
        s.chat.append({"id": s.cid, "uid": 0, "u": "", "t": m, "ch": "all", "ts": int(time.time()), "re": None, "sys": 1})
        if len(s.chat) > 400: del s.chat[:150]

    def visible(s, uid, c):
        me = s.p.get(uid); end = s.phase == "ended"
        if c["ch"] == "all": return True
        if c["ch"] == "mafia": return end or bool(me and me["role"] in MAFIA_TEAM)
        return end or bool(me and not me["alive"])

    def view(s, uid):
        me = s.p.get(uid); end = s.phase == "ended"; mt = bool(me and me["role"] in MAFIA_TEAM); night = s.phase == "night"
        pl = []
        for u, x in s.p.items():
            show = end or u == uid or (mt and x["role"] in MAFIA_TEAM) or (not x["alive"] and x["role"] is not None)
            pl.append({"id": u, "name": x["name"], "alive": x["alive"], "left": x["left"], "ph": x["ph"], "pro": x["pro"],
                       "role": x["role"] if show else None, "v": sum(1 for t in s.votes.values() if t == u),
                       "mv": sum(1 for a, t in s.act.items() if t == u and s.p[a]["role"] in MAFIA_TEAM) if (mt and night) else 0,
                       "why": "" if x["alive"] else x["reason"]})
        return {"t": "room", "id": s.id, "name": s.name, "mode": s.mode, "closed": s.closed, "pro": s.pro,
                "owner": s.owner, "phase": s.phase, "day": s.day, "night": s.night, "q": s.q, "deadline": s.deadline, "players": pl,
                "spec": me is None, "specn": len(s.spec), "speaker": s.speaker, "skip": len(s.skipd & set(s.alive())),
                "me": {"role": me["role"] if me else None, "alive": bool(me and me["alive"]),
                       "act": s.act.get(uid), "vote": s.votes.get(uid), "skipped": uid in s.skipd, "nov": uid in s.nov},
                "chat": [c for c in s.chat if s.visible(uid, c)][-60:], "log": s.log[-8:],
                "priv": s.priv.get(uid, []), "winner": s.winner, "max": S("max_players"), "minp": s.minp, "disc": s.disc,
                "req": s.req}

    def members(s): return [u for u in s.p if not s.p[u]["left"]] + list(s.spec)
    async def push(s):
        for u in s.members(): await send(u, s.view(u))
    async def fx(s, k):
        for u in s.members(): await send(u, {"t": "fx", "k": k})

    async def popup(s, k, u=None, text="", role=None, ms=None):
        x = s.p.get(u) if u else None; secs = ms or S("popup")
        m = {"t": "ev", "k": k, "u": u, "n": x["name"] if x else "", "ph": x["ph"] if x else "", "role": role,
             "tx": text, "ms": int(secs * 1000)}
        for v in s.members(): await send(v, m)
        await asyncio.sleep(secs + 0.3)

    async def timed(s, secs, ready=None):
        s.wake.clear(); s.deadline = int(time.time() + secs); await s.push()
        while time.time() < s.deadline and not s.wake.is_set() and not s.winner:
            if ready and ready():
                await asyncio.sleep(1.5); break
            try: await asyncio.wait_for(s.wake.wait(), 1)
            except asyncio.TimeoutError: pass

    def die(s, u, reason):
        x = s.p[u]
        if x["alive"]: x["alive"] = False; x["reason"] = reason

    def actors(s):
        return [u for u in s.alive() if s.p[u]["role"] in MAFIA_TEAM or s.p[u]["role"] in ("doktor", "izquvar")]
    def night_ready(s): return all(u in s.act for u in s.actors())

    async def run(s):
        try:
            ids = list(s.p); roles = make_roles(len(ids), s.pro); random.shuffle(ids)
            for u, r in zip(ids, roles): s.p[u]["role"] = r
            for u in ids:
                sql("UPDATE users SET games=games+1 WHERE id=?", (u,))
                if s.mode == "voice" and not one("SELECT pro_until p FROM users WHERE id=?", (u,))["p"] > time.time():
                    sql("UPDATE users SET free_voice=MAX(0,free_voice-1) WHERE id=?", (u,))
            s.phase = "intro"; s.day = 0; s.say("O'yin boshlandi. Tanishuv vaqti."); await s.fx("start"); await s.timed(S("intro"))
            while not s.winner:
                await s.night_phase()
                if s.winner: break
                await s.day_phase()
                if s.winner: break
                await s.vote_phase()
            await s.finish()
        except asyncio.CancelledError:
            pass
        except Exception:
            import traceback; traceback.print_exc()
            await s.abort()

    async def night_phase(s):
        s.night += 1; s.act = {}; s.phase = "night"; s.say("Tun boshlanmoqda."); await s.fx("night")
        await s.timed(S("night"), s.night_ready)
        if s.winner: return
        pool = s.actors(); idle = [u for u in pool if u not in s.act]
        ev = await s.resolve_night()
        await s.announce(ev)
        await s.afk_update("n", idle, pool)

    async def resolve_night(s):
        ev = []; s.priv = {}
        kills = [(u, t) for u, t in s.act.items() if s.p[u]["alive"] and s.p[u]["role"] in MAFIA_TEAM and t in s.p]
        victim = None
        if kills:
            c = {}
            for u, t in kills: c[t] = c.get(t, 0) + 1
            top = max(c.values()); cands = [t for t, v in c.items() if v == top]
            boss = next((t for u, t in kills if s.p[u]["role"] == "mafiaboshi" and t in cands), None)
            victim = boss if boss is not None else random.choice(cands)
        docs = [(u, t) for u, t in s.act.items() if s.p[u]["alive"] and s.p[u]["role"] == "doktor" and t in s.p]
        has_doc = any(x["alive"] and x["role"] == "doktor" for x in s.p.values())
        healed = {t for u, t in docs}
        for u, t in docs:
            s.heal_prev[u] = t
            if t == u: s.selfheal.add(u)
            s.priv.setdefault(u, []).append(f"{s.p[t]['name']} davolandi")
        for u, t in s.act.items():
            if s.p[u]["alive"] and s.p[u]["role"] == "izquvar" and t in s.p:
                r = s.p[t]["role"]; ism = r in MAFIA_TEAM and r != "mafiaboshi"
                s.priv.setdefault(u, []).append(f"{s.p[t]['name']}: " + ("Mafia" if ism else "Mafia emas"))
        if victim is None:
            s.say("Bu tun hech kim o'lmadi.")
        elif victim in healed:
            s.say("Doktor mafiya nishonidagi o'yinchini qutqardi. Bu tun hech kim o'lmadi.")
            ev.append({"k": "heal", "u": victim, "role": "doktor", "tx": "Doktor {n}ni qutqardi"})
        else:
            r = s.p[victim]["role"]; nm = s.p[victim]["name"]
            s.die(victim, "Mafiya o'ldirdi")
            s.say(f"Mafia {nm}ni otib tashladi.", victim)
            ev.append({"k": "kill", "u": victim, "role": r, "tx": "Mafia {n}ni otib tashladi"})
            if has_doc:
                s.say(f"Doktor {nm}ni davolay olmadi.", victim)
                ev.append({"k": "heal_fail", "u": victim, "role": "doktor", "tx": "Doktor {n}ni davolay olmadi"})
        return ev

    async def announce(s, ev):
        dead = []
        for e in ev:
            await s.fx({"kill": "kill", "heal": "heal"}.get(e["k"], "vote"))
            await s.popup(e["k"], e["u"], e["tx"], e.get("role"))
            if e["k"] in ("kill", "out"): dead.append(e["u"])
        s.check()
        if s.winner: return
        for u in dead:
            if not s.p[u]["left"] and not s.winner: await s.last_word(u)

    async def last_word(s, u):
        await s.popup("last", u, "{n} so'nggi so'zini aytishi mumkin", None, ms=2.5)
        s.speaker = u; s.phase = "last"; s.say(f"{s.p[u]['name']} so'nggi so'zini aytishi mumkin.", u)
        await s.timed(S("last_word")); s.speaker = None

    async def afk_update(s, kind, idle, pool):
        for u in pool:
            k = f"{kind}{u}"; s.miss[k] = (s.miss.get(k, 0) + 1) if u in idle else 0
        for u in idle:
            x = s.p[u]
            if s.miss[f"{kind}{u}"] >= S("afk_max") and not x["left"]:
                if x["alive"]: s.die(u, "AFK")
                s.say(f"{x['name']} harakatsizlik sababli o'yindan chiqarildi.", u)
                addp(u, -S("leave_pen")); COOL[u] = time.time() + LEAVE_COOL
                x["left"] = True; UROOM.pop(u, None); await send(u, lobby())
        s.check()

    async def day_phase(s):
        s.day += 1; s.phase = "day"; s.skipd = set(); s.talk = set(); s.say(f"{s.day + 1}-kun. Muhokama boshlandi."); await s.fx("day")
        await s.timed(s.disc, lambda: len(s.skipd & set(s.alive())) > len(s.alive()) // 2)

    async def vote_phase(s):
        s.phase = "vote"; s.votes = {}; s.nov = {u for u in s.alive() if u not in s.talk}
        s.say(f"Ovoz berish boshlandi! {S('vote')} soniya ichida ovoz bering.")
        if s.nov: s.say(", ".join(s.p[u]["name"] for u in s.nov) + " muhokamada qatnashmagani uchun bugun ovoz bera olmaydi.")
        await s.fx("vote")
        await s.timed(S("vote"), lambda: all(u in s.votes for u in s.alive() if u not in s.nov))
        if s.winner: return
        pool = [u for u in s.alive() if u not in s.nov]; idle = [u for u in pool if u not in s.votes]
        ev = await s.resolve_vote()
        await s.announce(ev)
        await s.afk_update("v", idle, pool)

    async def resolve_vote(s):
        c = {}; skip = 0
        for u, t in s.votes.items():
            if not s.p.get(u, {}).get("alive"): continue
            if t == 0: skip += 1
            elif t in s.p: c[t] = c.get(t, 0) + 1
        tally = ", ".join(f"{s.p[t]['name']}: {v}" for t, v in sorted(c.items(), key=lambda i: -i[1]))
        s.say("Ovozlar: " + (tally or "yo'q") + (f" | o'tkazish: {skip}" if skip else ""))
        if not c: s.say("Hech kim chiqarilmadi."); return []
        top = max(c.values()); w = [t for t, v in c.items() if v == top]
        if len(w) > 1 or skip >= top: s.say("Ovozlar teng yoki o'tkazib yuborildi. Hech kim chiqarilmadi."); return []
        t = w[0]; r = s.p[t]["role"]; s.die(t, "Ovoz bilan chiqarildi")
        s.say(f"{s.p[t]['name']} ovoz bilan chiqarildi. U {ROLE_NAMES.get(r, r)} edi.", t)
        return [{"k": "out", "u": t, "role": r, "tx": "{n} shahar qarori bilan chiqarildi"}]

    def check(s):
        if not any(x["role"] for x in s.p.values()): return False
        a = s.alive(); m = sum(1 for u in a if s.p[u]["role"] in MAFIA_TEAM); t = len(a) - m
        if m == 0: s.winner = "town"
        elif m >= t: s.winner = "mafia"
        return bool(s.winner)

    async def finish(s):
        s.phase = "ended"; s.deadline = 0; s.speaker = None; mult = 1 if s.closed else S("open_mult"); now_ = int(time.time())
        gid = sql("INSERT INTO games(room,players,winner,days,ts) VALUES(?,?,?,?,?)",
                  (s.id, len(s.p), s.winner, s.day, now_)).lastrowid
        done = []
        for u, x in s.p.items():
            win = s.team(u) == s.winner
            why = x["reason"] or ("Omon qoldi" if x["alive"] else "")
            sql("INSERT INTO game_players(uid,gid,role,win,n,days,ts,reason,alive) VALUES(?,?,?,?,?,?,?,?,?)",
                (u, gid, x["role"], int(win), len(s.p), s.day, now_, why, int(x["alive"])))
            if x["left"]: continue
            d = int((S("win_pts") if win else S("lose_pts")) * mult)
            sql("UPDATE users SET wins=wins+?,survived=survived+? WHERE id=?", (int(win), int(x["alive"]), u)); addp(u, d)
            cl = one("SELECT clan_id FROM users WHERE id=?", (u,))
            if cl and cl["clan_id"]: sql("UPDATE clans SET points=points+? WHERE id=?", (d, cl["clan_id"]))
            g = one("SELECT games,ref FROM users WHERE id=?", (u,))
            if g["ref"] and g["games"] == 3:
                sql("UPDATE users SET ref_ok=ref_ok+1 WHERE id=?", (g["ref"],))
                if one("SELECT ref_ok FROM users WHERE id=?", (g["ref"],))["ref_ok"] % 25 == 0:
                    r0 = one("SELECT pro_until FROM users WHERE id=?", (g["ref"],))
                    sql("UPDATE users SET pro_until=? WHERE id=?", (max(now_, r0["pro_until"]) + 30 * 86400, g["ref"]))
            done.append((u, win))
        s.say("G'alaba: " + ("Shahar" if s.winner == "town" else "Mafiya"))
        s.say("Rollar: " + ", ".join(f"{x['name']} - {ROLE_NAMES.get(x['role'], '')}" for x in s.p.values()))
        await s.fx("win"); await s.push()
        for u, win in done:
            try:
                await task_event(u, "play")
                if win: await task_event(u, "win")
            except Exception: pass
        asyncio.create_task(s.cleanup())

    async def abort(s):
        s.winner = "none"; s.phase = "ended"; s.deadline = 0; s.speaker = None
        s.say("Texnik xato. O'yin bekor qilindi."); await s.push(); asyncio.create_task(s.cleanup())

    async def cleanup(s):
        await asyncio.sleep(90)
        if ROOMS.get(s.id) is not s: return
        for u in s.members():
            if UROOM.get(u) == s.id: UROOM.pop(u, None); await send(u, lobby())
        ROOMS.pop(s.id, None); await lobby_all()

def lobby():
    return {"t": "lobby", "rooms": [{"id": r.id, "name": r.name, "n": len(r.p), "max": S("max_players"), "mode": r.mode,
            "pro": r.pro, "phase": r.phase, "day": r.day, "alive": len(r.alive()), "minp": r.minp, "disc": r.disc, "req": r.req,
            "spec": len(r.spec), "host": r.p.get(r.owner, {}).get("name", ""),
            "ph": [{"p": x["ph"], "n": x["name"]} for x in list(r.p.values())[:5]]}
            for r in ROOMS.values() if not r.closed]}
async def lobby_all():
    m = lobby()
    for u in list(HUB):
        if u not in UROOM: await send(u, m)

async def leave(uid):
    r = ROOMS.get(UROOM.pop(uid, None))
    if not r: return
    if uid not in r.p:  # kuzatuvchi
        r.spec.discard(uid); return
    if r.phase in ("lobby", "ended"):
        r.p.pop(uid, None)
        if not r.p: ROOMS.pop(r.id, None)
        else:
            if r.owner == uid: r.owner = next(iter(r.p))
            await r.push()
    else:
        x = r.p[uid]
        if x["alive"]:
            r.die(uid, "O'yinni tark etdi"); r.say(f"{x['name']} o'yindan chiqib ketdi.", uid)
            addp(uid, -S("leave_pen")); COOL[uid] = time.time() + LEAVE_COOL
        x["left"] = True
        if all(v["left"] for v in r.p.values()):
            if r.task: r.task.cancel()
            ROOMS.pop(r.id, None)
        else:
            if r.check() or r.speaker == uid: r.wake.set()
            await r.push()
    await lobby_all()

async def close_room(rid):
    r = ROOMS.pop(rid, None)
    if not r: return
    if r.task: r.task.cancel()
    for u in r.members():
        UROOM.pop(u, None); await send(u, lobby())

def me_info(uid):
    u = one("SELECT * FROM users WHERE id=?", (uid,))
    c = one("SELECT name,tag FROM clans WHERE id=?", (u["clan_id"],)) if u["clan_id"] else None
    d = today()
    tdone = one("SELECT COUNT(*) n FROM tasks WHERE uid=? AND day=? AND done=1", (uid, d))["n"]
    return {"id": uid, "name": u["name"], "photo": u["photo"] or "", "points": u["points"], "games": u["games"], "wins": u["wins"],
            "survived": u["survived"], "pro": u["pro_until"] > time.time(), "pro_until": u["pro_until"], "free_voice": u["free_voice"],
            "clan": f"{c['name']} [{c['tag']}]" if c else None, "cool": cool(uid), "prefs": prefs(uid),
            "streak": u["streak"] if u["last_day"] >= d - 1 else 0, "tasks_done": tdone,
            "friends": one("SELECT COUNT(*) n FROM friends WHERE uid=?", (uid,))["n"],
            "ref_all": one("SELECT COUNT(*) n FROM users WHERE ref=?", (uid,))["n"], "ref_ok": u["ref_ok"],
            "rank": one("SELECT COUNT(*)+1 n FROM users WHERE points>?", (u["points"],))["n"]}

def chan(r, uid):
    x = r.p[uid]
    if r.phase in ("lobby", "ended"): return "all"
    if r.phase == "last": return "all" if r.speaker == uid else None
    if r.phase == "night": return "mafia" if x["alive"] and x["role"] in MAFIA_TEAM else None
    return "all" if x["alive"] else "dead"

# ---------- reyting ----------
def rating(uid, kind, scope):
    if kind == "clans":
        rows = allr("SELECT c.id,c.name,c.tag,c.points,(SELECT COUNT(*) FROM users WHERE clan_id=c.id) n FROM clans c ORDER BY c.points DESC LIMIT 50")
        return {"t": "rating", "kind": "clans", "scope": scope,
                "my": (one("SELECT clan_id FROM users WHERE id=?", (uid,)) or {"clan_id": 0})["clan_id"],
                "clans": [{**dict(x), "lvl": min(10, 1 + x["points"] // 400), "rank": i + 1, "open": x["n"] < 100} for i, x in enumerate(rows)]}
    now_ = int(time.time()); since = {"week": week_start(), "season": now_ - 90 * 86400}.get(scope)
    if since is not None:
        rows = allr("SELECT * FROM (SELECT u.id,u.name,u.photo,u.pro_until>? pro,"
                    "COALESCE((SELECT SUM(d) FROM points_log WHERE uid=u.id AND ts>=?),0) pts,"
                    "(SELECT COUNT(*) FROM game_players WHERE uid=u.id AND ts>=?) g,"
                    "(SELECT COALESCE(SUM(win),0) FROM game_players WHERE uid=u.id AND ts>=?) w FROM users u) "
                    "WHERE g>0 ORDER BY pts DESC LIMIT 30", (now_, since, since, since))
    else:
        ids = [uid]
        if scope == "friends": ids += [r["fid"] for r in allr("SELECT fid FROM friends WHERE uid=?", (uid,))]
        flt = f"WHERE u.id IN ({','.join('?' * len(ids))})" if scope == "friends" else ""
        rows = allr("SELECT u.id,u.name,u.photo,u.pro_until>? pro,u.points pts,u.games g,u.wins w FROM users u "
                    f"{flt} ORDER BY u.points DESC LIMIT 30", (now_, *(ids if scope == "friends" else ())))
    return {"t": "rating", "kind": "players", "scope": scope, "left": week_left() if scope == "week" else 0,
            "players": [{"id": x["id"], "name": x["name"], "ph": x["photo"] or "", "pro": bool(x["pro"]), "pts": x["pts"], "g": x["g"],
                         "wr": round(100 * x["w"] / x["g"]) if x["g"] else 0} for x in rows]}

def friends_list(uid):
    rows = allr("SELECT u.id,u.name,u.photo,u.points FROM friends f JOIN users u ON u.id=f.fid WHERE f.uid=?", (uid,))
    return {"t": "friends", "list": [{"id": x["id"], "name": x["name"], "ph": x["photo"] or "", "points": x["points"], "on": x["id"] in HUB} for x in rows]}

async def notify_start(r):
    """Xonada o'yin boshlanishiga bir necha kishi yetmaganda, n_start yoqilgan foydalanuvchilarga xabar."""
    try:
        need = r.minp - len(r.p); now_ = int(time.time())
        if r.phase != "lobby" or r.closed or not WEBAPP_URL or need <= 0 or need > 3 or now_ - r.notified < 600: return
        r.notified = now_
        for u in allr("SELECT id,last_n FROM users WHERE banned=0 AND last_n<? LIMIT 500", (now_ - 3600,)):
            uid = u["id"]
            if uid in UROOM or uid in r.p or uid in HUB: continue
            p = prefs(uid)
            if not p["n_start"] or now_ - u["last_n"] < p["gap"] * 3600: continue
            try:
                await bot.send_message(uid, f"<b>{html.escape(r.name)}</b> xonasida o'yin boshlanishiga {need} kishi yetmayapti ({len(r.p)}/{r.minp}).",
                                       reply_markup=KB(inline_keyboard=[[B(text="Qo'shilish", web_app=WebAppInfo(url=f"{WEBAPP_URL}/?room={r.id}"))]]))
                sql("UPDATE users SET last_n=? WHERE id=?", (now_, uid))
            except Exception: pass
            await asyncio.sleep(0.05)
    except Exception: pass

async def handle(uid, name, m):
    t = m.get("t"); r = room_of(uid)
    me = one("SELECT * FROM users WHERE id=?", (uid,)); name = me["name"] or name
    if t == "create" and not r:
        if cool(uid): return await send(uid, {"t": "err", "m": "Yaqinda o'yinni tark etgansiz. Biroz kuting"})
        pro = me["pro_until"] > time.time()
        mode = m.get("mode") if m.get("mode") in ("chat", "voice", "table") else "chat"
        if mode == "voice" and not pro and me["free_voice"] <= 0:
            return await send(uid, {"t": "err", "m": "Bepul ovozli o'yinlar tugadi"})
        try: minp = max(6, min(S("max_players"), int(m.get("minp", S("min_players")))))
        except Exception: minp = S("min_players")
        try: disc = max(60, min(300, int(m.get("disc", S("disc")))))
        except Exception: disc = S("disc")
        r = Room(uid, str(m.get("name", ""))[:24], mode, bool(m.get("closed")) and pro, pro, minp, disc, 50 if m.get("req") and pro else 0)
        r.add(uid, name); ROOMS[r.id] = r; UROOM[uid] = r.id; await r.push(); await lobby_all()
    elif t in ("join", "quick") and not r:
        if cool(uid) and t == "quick": return await send(uid, {"t": "err", "m": "Yaqinda o'yinni tark etgansiz. Biroz kuting"})
        if t == "join": x = ROOMS.get(str(m.get("id", "")).strip())
        else:
            c = [v for v in ROOMS.values() if not v.closed and v.phase == "lobby" and v.mode == "chat" and not v.req
                 and len(v.p) < S("max_players")]
            x = max(c, key=lambda v: len(v.p)) if c else None
        if t == "quick" and not x: return await handle(uid, name, {"t": "create", "name": "Tezkor xona", "mode": "chat"})
        if not x: return await send(uid, {"t": "err", "m": "Xona topilmadi"})
        if x.phase != "lobby":
            return await handle(uid, name, {"t": "watch", "id": x.id})
        if cool(uid): return await send(uid, {"t": "err", "m": "Yaqinda o'yinni tark etgansiz. Biroz kuting"})
        if len(x.p) >= S("max_players"): return await send(uid, {"t": "err", "m": "Xona to'la"})
        if me["games"] < x.req: return await send(uid, {"t": "err", "m": f"Kirish uchun kamida {x.req} ta o'yin kerak"})
        if x.mode == "voice" and me["pro_until"] < time.time() and me["free_voice"] <= 0:
            return await send(uid, {"t": "err", "m": "Bepul ovozli o'yinlar tugadi"})
        x.add(uid, name); UROOM[uid] = x.id; await x.push(); await lobby_all(); asyncio.create_task(notify_start(x))
    elif t == "watch" and not r:
        x = ROOMS.get(str(m.get("id", "")).strip())
        if not x: return await send(uid, {"t": "err", "m": "Xona topilmadi"})
        if uid in x.p: return
        x.spec.add(uid); UROOM[uid] = x.id; await send(uid, x.view(uid))
    elif t == "leave":
        await leave(uid); await send(uid, lobby()); await send(uid, {"t": "me", "me": me_info(uid)})
    elif t == "start" and r and r.owner == uid and r.phase == "lobby":
        if len(r.p) < r.minp: return await send(uid, {"t": "err", "m": f"Kamida {r.minp} o'yinchi kerak"})
        r.phase = "intro"; r.task = asyncio.create_task(r.run()); await lobby_all()
    elif t == "chat" and r and uid in r.p:
        txt = str(m.get("text", "")).strip()[:200]; ch = chan(r, uid)
        if not txt or not ch: return
        if time.time() - r.lastchat.get(uid, 0) < S("chat_gap"): return
        r.lastchat[uid] = time.time(); re = None
        if r.phase == "day": r.talk.add(uid)
        if m.get("re"):
            for c0 in r.chat:
                if c0["id"] == m["re"] and r.visible(uid, c0): re = {"u": c0["u"], "t": c0["t"][:60]}; break
        r.cid += 1
        c = {"id": r.cid, "uid": uid, "u": r.p[uid]["name"], "t": txt, "ch": ch, "ts": int(time.time()), "re": re}; r.chat.append(c)
        for u in r.members():
            if r.visible(u, c): await send(u, {"t": "chat", "c": c})
    elif t == "act" and r and r.phase == "night" and r.p.get(uid, {}).get("alive"):
        tg = m.get("to"); role = r.p[uid]["role"]
        if tg in r.p and r.p[tg]["alive"] and (role in MAFIA_TEAM or role in ("doktor", "izquvar")):
            if role == "doktor":
                if tg == uid and uid in r.selfheal: return await send(uid, {"t": "err", "m": "O'zingizni faqat bir marta davolay olasiz"})
                if r.heal_prev.get(uid) == tg: return await send(uid, {"t": "err", "m": "Ketma-ket ikki tun bir kishini davolab bo'lmaydi"})
            if role == "izquvar" and tg == uid: return await send(uid, {"t": "err", "m": "O'zingizni tekshira olmaysiz"})
            if role in MAFIA_TEAM and r.p[tg]["role"] in MAFIA_TEAM: return await send(uid, {"t": "err", "m": "Mafiya a'zosini tanlab bo'lmaydi"})
            r.act[uid] = tg
            for u in r.p:
                if u == uid or (role in MAFIA_TEAM and r.p[u]["role"] in MAFIA_TEAM): await send(u, r.view(u))
    elif t == "unact" and r and r.phase == "night" and r.p.get(uid, {}).get("alive"):
        r.act.pop(uid, None)
        for u in r.p:
            if u == uid or (r.p[uid]["role"] in MAFIA_TEAM and r.p[u]["role"] in MAFIA_TEAM): await send(u, r.view(u))
    elif t == "vote" and r and r.phase == "vote" and r.p.get(uid, {}).get("alive"):
        if uid in r.nov: return await send(uid, {"t": "err", "m": "Muhokamada qatnashmaganingiz uchun bugun ovoz bera olmaysiz"})
        tg = m.get("to")
        if tg == 0 or (tg in r.p and r.p[tg]["alive"] and tg != uid):
            r.votes[uid] = tg
            r.say(f"{r.p[uid]['name']} {r.p[tg]['name']}ga qarshi ovoz berdi." if tg else f"{r.p[uid]['name']} ovoz bermaslikni tanladi.")
            await r.push()
    elif t == "skipday" and r and r.phase == "day" and r.p.get(uid, {}).get("alive"):
        r.skipd.add(uid); await r.push()
    elif t == "lastdone" and r and r.phase == "last" and r.speaker == uid:
        r.wake.set()
    elif t == "rtc" and r and m.get("to") in r.p:
        await send(m["to"], {"t": "rtc", "from": uid, "data": m.get("data")})
    elif t == "clan_create":
        nm = str(m.get("name", "")).strip()[:20]; tag = str(m.get("tag", "")).strip().upper()[:4]
        if me["pro_until"] <= time.time(): return await send(uid, {"t": "err", "m": "Ovul yaratish uchun Pro kerak"})
        if me["clan_id"]: return await send(uid, {"t": "err", "m": "Siz allaqachon ovuldasiz"})
        if len(nm) < 3 or len(tag) < 2: return await send(uid, {"t": "err", "m": "Nom kamida 3, teg kamida 2 belgi bo'lsin"})
        if one("SELECT 1 FROM clans WHERE lower(name)=lower(?) OR tag=?", (nm, tag)): return await send(uid, {"t": "err", "m": "Bunday nom yoki teg band"})
        cid = sql("INSERT INTO clans(name,tag,owner_id,created) VALUES(?,?,?,?)", (nm, tag, uid, int(time.time()))).lastrowid
        sql("UPDATE users SET clan_id=? WHERE id=?", (cid, uid))
        await send(uid, {"t": "toast", "m": "Ovul yaratildi"}); await send(uid, rating(uid, "clans", "all")); await send(uid, {"t": "me", "me": me_info(uid)})
    elif t == "clan_join":
        try: cid = int(m.get("id", 0))
        except Exception: return
        c = one("SELECT id FROM clans WHERE id=?", (cid,))
        if not c: return await send(uid, {"t": "err", "m": "Ovul topilmadi"})
        if me["clan_id"]: return await send(uid, {"t": "err", "m": "Avval hozirgi ovuldan chiqing"})
        if one("SELECT COUNT(*) n FROM users WHERE clan_id=?", (cid,))["n"] >= 100: return await send(uid, {"t": "err", "m": "Ovul to'la"})
        sql("UPDATE users SET clan_id=? WHERE id=?", (cid, uid))
        await send(uid, {"t": "toast", "m": "Ovulga qo'shildingiz"}); await send(uid, rating(uid, "clans", "all")); await send(uid, {"t": "me", "me": me_info(uid)})
    elif t == "clan_leave":
        if me["clan_id"]:
            sql("UPDATE users SET clan_id=0 WHERE id=?", (uid,))
        await send(uid, rating(uid, "clans", "all")); await send(uid, {"t": "me", "me": me_info(uid)})
    elif t == "profile": await send(uid, {"t": "me", "me": me_info(uid)})
    elif t == "rating":
        kind = m.get("kind") if m.get("kind") in ("players", "clans") else "players"
        scope = m.get("scope") if m.get("scope") in ("week", "season", "all", "friends") else "week"
        await send(uid, rating(uid, kind, scope))
    elif t == "tasks": await send(uid, tasks_view(uid))
    elif t == "reroll":
        d = today(); u = one("SELECT rr_day FROM users WHERE id=?", (uid,)); ensure_tasks(uid)
        k = one("SELECT * FROM tasks WHERE uid=? AND day=? AND done=0 ORDER BY slot LIMIT 1", (uid, d))
        if k and u["rr_day"] != d:
            opts = [o for o in POOL[k["slot"]] if o != (k["kind"], k["goal"])] or POOL[k["slot"]]
            kind, goal = random.choice(opts)
            sql("UPDATE tasks SET kind=?,goal=?,prog=0 WHERE id=?", (kind, goal, k["id"])); sql("UPDATE users SET rr_day=? WHERE id=?", (d, uid))
        await send(uid, tasks_view(uid))
    elif t == "chest":
        if me["keys"] >= 25:
            sql("UPDATE users SET keys=keys-25 WHERE id=?", (uid,)); addp(uid, 100)
            await send(uid, {"t": "toast", "m": "Sandiq ochildi: +100 ball"})
        await send(uid, tasks_view(uid)); await send(uid, {"t": "me", "me": me_info(uid)})
    elif t == "history":
        rows = allr("SELECT gid,role,win,n,days,ts,reason,alive FROM game_players WHERE uid=? ORDER BY ts DESC LIMIT 40", (uid,))
        out = []
        for x in rows:
            d = dict(x)
            d["pl"] = [{"p": y["photo"] or "", "n": y["name"]} for y in
                       allr("SELECT u.name,u.photo FROM game_players g JOIN users u ON u.id=g.uid WHERE g.gid=? LIMIT 5", (x["gid"],))]
            out.append(d)
        await send(uid, {"t": "hist", "list": out, "g": me["games"], "w": me["wins"]})
    elif t == "friends": await send(uid, friends_list(uid))
    elif t == "fsearch":
        txt = str(m.get("q", "")).strip()[:30]
        if not txt: return
        rows = allr("SELECT id,name,photo FROM users WHERE id=?", (int(txt),)) if txt.isdigit() \
            else allr("SELECT id,name,photo FROM users WHERE name LIKE ? LIMIT 10", (f"%{txt}%",))
        fr = {x["fid"] for x in allr("SELECT fid FROM friends WHERE uid=?", (uid,))}
        await send(uid, {"t": "fsearch", "list": [{"id": x["id"], "name": x["name"], "ph": x["photo"] or "", "fr": x["id"] in fr}
                                                   for x in rows if x["id"] != uid]})
    elif t in ("fadd", "fdel"):
        try: fid = int(m.get("id", 0))
        except Exception: return
        if fid != uid and one("SELECT 1 FROM users WHERE id=?", (fid,)):
            if t == "fadd":
                sql("INSERT OR IGNORE INTO friends(uid,fid) VALUES(?,?)", (uid, fid)); sql("INSERT OR IGNORE INTO friends(uid,fid) VALUES(?,?)", (fid, uid))
            else:
                sql("DELETE FROM friends WHERE (uid=? AND fid=?) OR (uid=? AND fid=?)", (uid, fid, fid, uid))
        await send(uid, friends_list(uid)); await send(uid, {"t": "me", "me": me_info(uid)})
    elif t == "invite" and r and r.phase == "lobby":
        try: fid = int(m.get("to", 0))
        except Exception: return
        if one("SELECT 1 FROM friends WHERE uid=? AND fid=?", (uid, fid)):
            if fid in HUB and fid not in UROOM: await send(fid, {"t": "toast", "m": f"{name} sizni xonaga taklif qildi. Kod: {r.id}"})
            elif prefs(fid)["n_friend"] and WEBAPP_URL:
                try:
                    await bot.send_message(fid, f"{html.escape(name)} sizni <b>{html.escape(r.name)}</b> xonasiga taklif qildi.",
                                           reply_markup=KB(inline_keyboard=[[B(text="Qo'shilish", web_app=WebAppInfo(url=f"{WEBAPP_URL}/?room={r.id}"))]]))
                except Exception: pass
    elif t == "setprefs":
        k, v = m.get("k"), m.get("v"); p = prefs(uid)
        if k == "n_priv" and v == 1 and not me["pro_until"] > time.time(): return
        if k in ("n_start", "n_friend", "n_priv", "insta") and v in (0, 1): p[k] = v
        elif k == "gap" and v in (1, 3, 6, 12, 24): p[k] = v
        elif k == "voice" and v in ("auto", "direct", "proxy"): p[k] = v
        else: return
        sql("UPDATE users SET prefs=? WHERE id=?", (json.dumps(p), uid)); await send(uid, {"t": "me", "me": me_info(uid)})
    elif t == "setname":
        nn = str(m.get("name", "")).strip()[:24]
        if nn: sql("UPDATE users SET name=? WHERE id=?", (nn, uid)); await send(uid, {"t": "me", "me": me_info(uid)})
    elif t == "invoice":
        mo = str(m.get("mo"))
        if mo in PLANS:
            try:
                url = await bot.create_invoice_link(title=S("brand") + " Pro", description=f"{mo} oylik Pro obuna", payload="pro:" + mo,
                                                    currency="XTR", prices=[LabeledPrice(label="Pro", amount=S("star_" + mo))], provider_token="")
                await send(uid, {"t": "invoice", "url": url})
            except Exception as ex:
                await send(uid, {"t": "err", "m": "To'lovni ochib bo'lmadi"})

async def grace(uid):
    await asyncio.sleep(60)
    if uid not in HUB and uid in UROOM: await leave(uid)

# ============================ WEB (FastAPI) ============================
app = FastAPI()

def auth(init):
    try:
        d = dict(parse_qsl(init, keep_blank_values=True)); h = d.pop("hash")
        chk = "\n".join(f"{k}={v}" for k, v in sorted(d.items()))
        sec = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(hmac.new(sec, chk.encode(), hashlib.sha256).hexdigest(), h): return None
        if time.time() - int(d["auth_date"]) > 86400: return None
        u = json.loads(d["user"]); return u["id"], (u.get("first_name", "") + " " + u.get("last_name", "")).strip(), u.get("photo_url", "")
    except Exception:
        return None

@app.websocket("/ws")
async def ws_ep(ws: WebSocket):
    await ws.accept(); q = ws.query_params; a = auth(q.get("init", ""))
    if not a and DEV and q.get("dev"): a = (int(q["dev"]), q.get("name", "Dev"), "")
    if not a: return await ws.close(4001)
    uid, name, photo = a
    sql("INSERT OR IGNORE INTO users(id,name,created,free_voice) VALUES(?,?,?,?)", (uid, name or "Oyinchi", int(time.time()), S("free_voice")))
    if photo: sql("UPDATE users SET photo=? WHERE id=?", (photo, uid))
    set_ref(uid, q.get("sp", ""))
    if one("SELECT banned FROM users WHERE id=?", (uid,))["banned"]:
        await ws.send_json({"t": "banned"}); return await ws.close()
    HUB[uid] = ws
    await ws.send_json({"t": "hello", "me": me_info(uid), "assets": amap(), "owner": uid in OWNERS, "bot": BOT_USERNAME,
                        "stars": {m: S("star_" + m) for m in PLANS}, "brand": S("brand"), "ads": {k: S(k + "_url") for k in AD_URL_KEYS}, "ad_secs": S("ad_secs")})
    r = room_of(uid)
    await ws.send_json(r.view(uid) if r else lobby())
    try:
        while True:
            msg = await ws.receive_json()
            try: await handle(uid, name, msg)
            except (WebSocketDisconnect, RuntimeError): raise
            except Exception:
                import traceback; traceback.print_exc()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        if HUB.get(uid) is ws:
            HUB.pop(uid, None)
            if uid in UROOM: asyncio.create_task(grace(uid))

@app.get("/a/{fn}")
async def asset(fn: str):
    r = one("SELECT path FROM assets WHERE key=?", (fn.rsplit(".", 1)[0],))
    return FileResponse(r["path"], headers={"Cache-Control": "public,max-age=31536000"}) if r else Response(status_code=404)

@app.get("/health")
async def health(): return {"ok": True, "rooms": len(ROOMS), "online": len(HUB)}

@app.get("/")
async def index(): return HTMLResponse(HTML)

HTML = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>Play Mafia</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
:root{--bg:#0a0c13;--cd:#161a27;--cd2:#1d2232;--bd:#282e45;--tx:#f1f3fa;--mu:#8a91aa;--ac:#5b6cff;--ac2:#7c5cff;--go:#e2b44f;--go2:#b98a2b;--gr:#2fd16a;--rd:#e5484d;--sh:rgba(0,0,0,.5)}
[data-theme=light]{--bg:#eef0f7;--cd:#fff;--cd2:#f2f4fa;--bd:#e0e4ef;--tx:#151823;--mu:#6f7690;--sh:rgba(20,24,40,.25)}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html{background:var(--bg)}
body{margin:0;color:var(--tx);font:15px/1.35 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;padding:10px 12px 96px;min-height:100vh;
background:radial-gradient(130% 320px at 50% 0,#1a2050 0,transparent 75%) no-repeat,var(--bg);background-size:100% 100%;background-attachment:scroll}
[data-theme=light] body{background:var(--bg)}
button{font:inherit;color:inherit;cursor:pointer}
input,select{font:inherit;color:var(--tx);background:var(--cd2);border:1px solid var(--bd);border-radius:14px;padding:13px 14px;width:100%;outline:none}
input:focus{border-color:var(--ac)}
h2{margin:6px 0 10px;font-size:19px;text-align:center}
.mu{color:var(--mu);font-size:13px}.c{text-align:center}.go{color:var(--go)}.gr{color:var(--gr)}.rd{color:var(--rd)}
.card{background:var(--cd);border:1px solid var(--bd);border-radius:18px;padding:14px;margin:10px 0}
.row{display:flex;gap:10px;align-items:center}.row>.f{flex:1;min-width:0}
.sp{justify-content:space-between}
.btn{display:flex;align-items:center;justify-content:center;gap:8px;border:0;border-radius:16px;padding:14px;font-weight:700;font-size:16px;width:100%;color:#fff;background:linear-gradient(135deg,var(--ac),var(--ac2));box-shadow:0 6px 18px rgba(91,108,255,.28)}
.btn.sec{background:var(--cd);color:var(--tx);border:1px solid var(--bd);box-shadow:none}
.btn.gold{background:linear-gradient(135deg,#f3d58c,var(--go2));color:#241a05;box-shadow:0 6px 18px rgba(226,180,79,.25)}
.btn.red{background:var(--rd);box-shadow:none}.btn:disabled{opacity:.4;box-shadow:none}.btn.sm{padding:10px 14px;font-size:14px;width:auto;border-radius:12px}
.back{display:inline-flex;gap:8px;align-items:center;background:var(--cd);border:1px solid var(--bd);border-radius:999px;padding:9px 16px;font-weight:600;margin:4px 0 8px}
.hero{text-align:center;margin:6px 0 14px}.hero img,.hero video{max-width:100%;max-height:170px;border-radius:16px;object-fit:contain}
.logo{font-size:30px;font-weight:900;letter-spacing:1px;color:var(--go);text-shadow:0 2px 0 #6b4a0c;padding:34px 0}
.icons{display:grid;grid-template-columns:repeat(6,1fr);gap:8px;margin-bottom:10px}
.ib{aspect-ratio:1;border-radius:50%;background:var(--cd);border:1px solid var(--bd);display:flex;align-items:center;justify-content:center;font-size:25px;padding:0;position:relative;overflow:hidden}
.ib img{width:62%;height:62%;object-fit:contain}
.ib .dot{position:absolute;top:6px;right:8px;width:10px;height:10px;border-radius:50%;background:var(--rd)}
.act{display:flex;gap:10px;margin-bottom:12px}
.sq{flex:0 0 66px;height:66px;border-radius:18px;background:var(--cd);border:1px solid var(--bd);font-size:30px;display:flex;align-items:center;justify-content:center;padding:0;color:var(--go)}
.sq img{width:55%}.act .btn{height:66px;font-size:19px}
.streak{background:linear-gradient(135deg,rgba(226,180,79,.14),rgba(124,92,255,.10));border:1px solid rgba(226,180,79,.45);border-radius:18px;padding:12px 14px;margin:10px 0}
.streak .t{display:flex;justify-content:space-between;align-items:center;font-weight:800}
.streak .t small{color:#d98a1c;font-weight:700;font-size:14px}
.bars{display:flex;gap:8px;margin-top:10px}.bars i{flex:1;height:7px;border-radius:9px;background:var(--bd)}.bars i.on{background:linear-gradient(90deg,#f3d58c,var(--go2))}
.warn{background:#2a1115;border:1.5px solid #8a2c33;border-radius:18px;padding:14px;margin:10px 0;display:flex;gap:12px;font-size:16px}
[data-theme=light] .warn{background:#fdeaec}
.sec-t{display:flex;justify-content:space-between;align-items:center;color:var(--mu);letter-spacing:1.5px;font-weight:700;font-size:13px;margin:18px 4px 8px;text-transform:uppercase}
.rm{padding:14px;cursor:pointer}.rm .h{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.rm .n{font-weight:800;font-size:18px}.rm .cnt{margin-left:auto;font-weight:800;color:var(--gr);font-size:20px}
.rm .hs{display:flex;align-items:center;gap:8px;margin:8px 0;color:var(--mu);font-size:17px}.rm .hs .chev{margin-left:auto;font-size:22px}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}
.chip{display:inline-flex;gap:6px;align-items:center;background:var(--cd2);border:1px solid var(--bd);border-radius:999px;padding:6px 12px;font-size:14px;color:var(--mu);font-weight:600}
.bdg{display:inline-flex;align-items:center;gap:4px;border-radius:10px;padding:3px 9px;font-size:13px;font-weight:800;background:var(--cd2)}
.bdg.pro{background:linear-gradient(135deg,#f3d58c,var(--go2));color:#241a05}
.bdg.voice{background:rgba(47,209,106,.16);color:var(--gr)}
.bdg.req{background:rgba(229,72,77,.15);color:#ff6b70;border:1px solid rgba(229,72,77,.45)}
.stk{display:flex}.stk .av{margin-left:-10px;border:2px solid var(--cd)}.stk .av:first-child{margin-left:0}
.av{width:34px;height:34px;border-radius:50%;object-fit:cover;flex:0 0 auto;display:inline-flex;align-items:center;justify-content:center;font-weight:800;color:#fff;font-size:14px}
.av.pro{box-shadow:0 0 0 2px var(--go)}
.watch{padding:12px 14px}
.field{display:block;font-size:12px;font-weight:800;letter-spacing:1px;color:var(--mu);margin:14px 2px 6px;text-transform:uppercase}
.seg{display:flex;gap:8px}.seg button{flex:1;padding:13px 6px;border-radius:14px;border:1px solid var(--bd);background:var(--cd2);font-weight:700;display:flex;gap:6px;align-items:center;justify-content:center}
.seg button.on{background:linear-gradient(135deg,#3b5bff,#5b6cff);border-color:transparent;color:#fff}
.tabs{display:flex;gap:6px;background:var(--cd);border:1px solid var(--bd);border-radius:18px;padding:6px;margin:8px 0}
.tabs button{flex:1;border:0;background:transparent;padding:11px 4px;border-radius:13px;font-weight:700;color:var(--mu);white-space:nowrap}
.tabs button.on{background:linear-gradient(135deg,var(--ac),var(--ac2));color:#fff}
.pills{display:flex;gap:8px;overflow:auto;margin:8px 0}.pills button{flex:1;white-space:nowrap;padding:11px 14px;border-radius:14px;border:1px solid var(--bd);background:var(--cd);font-weight:700}
.pills button.on{background:linear-gradient(135deg,#3b5bff,#5b6cff);color:#fff;border-color:transparent}
.sw{position:relative;width:50px;height:30px;border-radius:30px;background:var(--bd);flex:0 0 50px;border:0;padding:0}
.sw::after{content:"";position:absolute;top:3px;left:3px;width:24px;height:24px;border-radius:50%;background:#fff;transition:.15s}
.sw.on{background:var(--ac)}.sw.on::after{left:23px}
.opt{display:flex;gap:12px;align-items:center;justify-content:space-between;background:var(--cd2);border:1px solid var(--bd);border-radius:16px;padding:13px 14px;margin:8px 0}
.cb{width:34px;height:34px;border-radius:10px;border:2px solid var(--bd);background:var(--cd2);flex:0 0 34px;display:flex;align-items:center;justify-content:center;color:#fff;font-weight:900}
.cb.on{background:var(--ac);border-color:var(--ac)}
.step{display:flex;align-items:center;justify-content:space-between;background:var(--cd2);border:1px solid var(--bd);border-radius:16px;padding:10px}
.step button{width:50px;height:46px;border-radius:12px;border:0;background:var(--cd);font-size:24px;font-weight:800}
.step b{font-size:22px}
input[type=range]{padding:0;background:transparent;border:0;accent-color:var(--ac)}
.ov{position:fixed;inset:0;background:var(--sh);z-index:30;display:flex;align-items:flex-end;justify-content:center;backdrop-filter:blur(3px)}
.ov.mid{align-items:center;padding:16px}
.sheet{background:var(--cd);width:100%;max-width:520px;border-radius:26px 26px 0 0;padding:16px 16px calc(16px + env(safe-area-inset-bottom));max-height:92vh;overflow:auto;border:1px solid var(--bd)}
.ov.mid .sheet{border-radius:26px}
.sheet .hd{display:flex;align-items:center;justify-content:space-between;font-weight:800;font-size:19px;border-bottom:1px solid var(--bd);padding-bottom:12px;margin-bottom:6px}
.x{width:42px;height:42px;border-radius:50%;border:1px solid var(--bd);background:var(--cd2);font-size:18px}
.crown{font-size:62px;text-align:center}.crown img{height:84px}
.plans{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:14px 0}
.plan{position:relative;background:var(--cd2);border:2px solid var(--bd);border-radius:18px;padding:16px 8px 12px;text-align:center}
.plan.on{border-color:var(--go);background:rgba(226,180,79,.10)}
.plan .d{position:absolute;top:-12px;left:50%;transform:translateX(-50%);background:var(--gr);color:#04210f;font-weight:800;font-size:13px;border-radius:10px;padding:2px 9px}
.plan b{display:block;font-size:30px;color:var(--go);margin:4px 0}.plan span{color:var(--mu);font-size:15px}
.tl{display:grid;grid-template-columns:repeat(7,1fr);gap:6px;margin-top:12px}
.dayt{border-radius:14px;background:var(--cd2);border:1px solid var(--bd);padding:8px 2px;text-align:center;font-size:12px;font-weight:700;color:var(--mu)}
.dayt i{display:block;font-style:normal;font-size:22px;margin-bottom:2px}.dayt.on{border:2px solid var(--go);color:var(--tx)}.dayt.ok{background:rgba(226,180,79,.18);color:var(--go)}
.tk{display:flex;gap:12px;align-items:center;padding:14px;border-radius:18px;background:var(--cd);border:1px solid var(--bd);margin:10px 0}
.tk.lock{border-style:dashed;opacity:.62}.tk .ti{width:60px;height:60px;border-radius:16px;display:flex;align-items:center;justify-content:center;font-size:28px;background:rgba(47,209,106,.18);flex:0 0 60px}
.tk.lock .ti{background:var(--cd2)}
.lv{display:inline-block;border-radius:10px;padding:2px 10px;font-size:12px;font-weight:800;margin-left:8px}
.lv0{background:rgba(47,209,106,.16);color:var(--gr)}.lv1{background:rgba(91,108,255,.18);color:#7d8bff}.lv2{background:rgba(229,72,77,.16);color:#ff6b70}
.pb{height:10px;border-radius:10px;background:var(--bd);overflow:hidden;margin:10px 0 6px}.pb i{display:block;height:100%;background:linear-gradient(90deg,var(--gr),#7be3a0);border-radius:10px}
.rew{color:#e08a1b;font-weight:800;white-space:nowrap}
.chest{background:linear-gradient(135deg,rgba(91,108,255,.22),rgba(124,92,255,.12));border:1px solid #4a54c8}
.stat{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:14px}.stat div{background:var(--cd2);border:1px solid var(--bd);border-radius:18px;padding:14px 16px}
.stat b{display:block;font-size:34px;line-height:1.1}.stat span{color:var(--mu);font-size:15px}
.big-av{width:92px;height:92px;font-size:34px}
.pill{display:inline-flex;gap:8px;align-items:center;border:1px solid rgba(226,180,79,.5);background:rgba(226,180,79,.12);border-radius:999px;padding:10px 24px;font-weight:800;font-size:19px}
.lnk{display:flex;align-items:center;justify-content:space-between;gap:10px;background:var(--cd2);border:1px solid var(--bd);border-radius:16px;padding:14px;color:var(--mu);overflow:hidden}
.lnk b{color:var(--ac);flex:0 0 auto}.lnk span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.nav{display:flex;justify-content:space-between;align-items:center;background:var(--cd);border:1px solid var(--bd);border-radius:16px;padding:16px;margin:8px 0;font-weight:600;font-size:17px;width:100%;text-align:left}
.pod{display:flex;gap:10px;align-items:flex-end;margin:16px 0}
.pod>div{flex:1;border-radius:20px;background:var(--cd);border:1px solid var(--bd);text-align:center;padding:12px 4px;position:relative}
.pod>div.p1{padding-top:22px;border-color:rgba(226,180,79,.6);background:linear-gradient(180deg,rgba(226,180,79,.18),var(--cd))}
.pod .rk{display:inline-block;background:var(--cd2);border-radius:999px;padding:2px 12px;font-weight:800;font-size:13px;margin-bottom:8px}
.pod .p1 .rk{background:var(--go);color:#241a05}.pod .av{width:62px;height:62px;font-size:22px}.pod .p1 .av{width:76px;height:76px}
.pod b{display:block;font-size:17px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding:0 4px}.pod .sc{font-size:30px;font-weight:900;color:var(--go)}.pod .p2 .sc,.pod .p3 .sc{font-size:26px}
.lr{display:flex;align-items:center;gap:12px;background:var(--cd);border:1px solid var(--bd);border-radius:18px;padding:12px 14px;margin:8px 0}
.lr .no{width:22px;font-weight:800;color:var(--mu);font-size:18px}.lr .sc{margin-left:auto;text-align:right;font-weight:800;color:var(--go);font-size:20px}.lr .sc small{display:block;color:var(--mu);font-size:13px;font-weight:600}
.cl{display:flex;align-items:center;gap:12px;border-radius:18px;padding:14px;margin:10px 0;border:1px solid var(--bd);background:var(--cd)}
.cl.r1{background:linear-gradient(135deg,#3a1d12,#2a1115);border-color:#7a3a1a}.cl.r2{background:linear-gradient(135deg,#2e1519,#241014);border-color:#6a2a2a}
.cl.r3,.cl.r4,.cl.r5{background:linear-gradient(135deg,#3a3010,#2a2410);border-color:#7a6420}
.cl .em{width:60px;height:60px;border-radius:14px;background:var(--cd2);display:flex;align-items:center;justify-content:center;font-size:28px;flex:0 0 60px}
.cl .tg{display:inline-block;background:var(--rd);color:#fff;border-radius:8px;padding:1px 10px;font-weight:800;font-size:13px;margin-left:6px}
.prize{border:1px solid rgba(226,180,79,.45);background:rgba(226,180,79,.10);border-radius:16px;padding:12px 14px;font-size:16px;margin:10px 0}
.rl{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.rc{border-radius:20px;overflow:hidden;position:relative;border:1px solid #27407a;background:linear-gradient(180deg,#14214a,#101a3a);min-height:140px;text-align:left}
.rc.m{border-color:#5a2230;background:linear-gradient(180deg,#321423,#241019)}
.rc .im{height:210px;display:flex;align-items:flex-end;justify-content:center;font-size:70px;padding-top:30px}.rc .im img,.rc .im video{height:100%;max-width:100%;object-fit:contain}
.rc b{display:block;padding:10px 14px 14px;font-size:21px}
.rc .nb{position:absolute;top:10px;right:10px;background:rgba(0,0,0,.55);color:#fff;border-radius:10px;padding:3px 10px;font-size:14px;font-weight:800}
table{width:100%;border-collapse:collapse}th,td{padding:12px;text-align:left;border-bottom:1px solid var(--bd)}th{color:var(--mu);font-size:13px;letter-spacing:1px}
.tray{display:grid;grid-template-columns:repeat(auto-fill,minmax(66px,1fr));gap:8px;background:var(--cd);border:1px solid var(--bd);border-radius:20px;padding:10px;margin:10px 0}
.pl{border-radius:14px;border:2px solid var(--bd);background:var(--cd2);padding:8px 2px 6px;text-align:center;font-size:12px;font-weight:700;position:relative;min-width:0}
.pl .av{width:42px;height:42px;display:flex;margin:0 auto 4px}.pl .nm{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding:0 3px}
.pl.dead{opacity:.35}.pl.sel{border-color:var(--ac);box-shadow:0 0 0 2px rgba(91,108,255,.4)}.pl.me{border-color:var(--gr)}
.pl .v{position:absolute;top:-5px;right:-3px;background:var(--rd);color:#fff;border-radius:10px;padding:0 6px;font-weight:800}
.pl .rl2{display:block;color:var(--rd);font-size:10px;margin-top:1px}.pl .lf{font-size:10px;color:var(--mu)}
.rtop{display:flex;justify-content:space-between;align-items:center;margin:6px 0}
.rtop .ib{width:46px;height:46px;aspect-ratio:auto;font-size:20px;background:#3b1418;border-color:#7a2a30}.rtop .sb{background:var(--cd);color:#6b8cff}
.rtitle{text-align:center;margin-top:-4px}.rtitle b{font-size:23px}.rtitle .mu{font-size:15px;display:block;margin-top:2px}
.hint{background:var(--cd);border:1px dashed var(--bd);border-radius:14px;padding:10px 14px;margin:8px 0;text-align:center}
.mini{display:flex;gap:12px;align-items:center}.mini .ri{width:56px;height:56px;border-radius:14px;background:var(--cd2);display:flex;align-items:center;justify-content:center;font-size:30px;overflow:hidden}.mini .ri img{height:100%}
#chat{background:var(--cd);border:1px solid var(--bd);border-radius:22px;padding:12px;height:46vh;min-height:260px;overflow:auto;margin-top:10px}
.m{display:flex;gap:10px;align-items:flex-end;margin:12px 0}.m.me{flex-direction:row-reverse}
.m .bub{background:var(--cd2);border-radius:18px;padding:9px 14px;max-width:72%;font-size:17px;word-break:break-word}
.m.me .bub{background:linear-gradient(135deg,#4a63ff,#5b6cff);color:#fff}
.m .nm{color:#7d8bff;font-weight:700;font-size:15px;margin-bottom:2px}.m.mafia .nm{color:#ff6b70}.m.dead .bub{opacity:.65}
.m .qt{border-left:3px solid var(--ac);background:rgba(0,0,0,.18);border-radius:8px;padding:4px 8px;margin:3px 0 5px;font-size:14px}.m .qt b{display:block;color:#8e9bff}
.m .qt span{opacity:.85;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.m .rp{width:38px;height:38px;border-radius:50%;border:1px solid var(--bd);background:var(--cd2);flex:0 0 38px;font-size:17px;padding:0;color:var(--mu)}
.sysl{text-align:center;color:var(--mu);font-size:13px;margin:8px 0}
.fab{position:fixed;right:22px;bottom:calc(24px + env(safe-area-inset-bottom));width:66px;height:66px;border-radius:50%;border:0;background:linear-gradient(135deg,#4a63ff,#5b6cff);color:#fff;font-size:26px;box-shadow:0 8px 22px rgba(91,108,255,.45);z-index:10}
#bar{position:fixed;left:0;right:0;bottom:0;padding:8px 12px calc(10px + env(safe-area-inset-bottom));background:var(--bg);border-top:1px solid var(--bd);display:none;z-index:15}
#bar .rep{display:flex;justify-content:space-between;align-items:center;background:var(--cd);border-left:3px solid var(--ac);border-radius:10px;padding:6px 10px;margin-bottom:8px;font-size:14px}
#bar .rep span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}#bar .rep button{border:0;background:none;font-size:18px;color:var(--mu)}
#bar .in{display:flex;gap:8px}#bar .in input{flex:1}#bar .in .btn{width:auto;padding:0 20px}
#toast{position:fixed;top:12px;left:50%;transform:translateX(-50%);background:#222a;backdrop-filter:blur(8px);background:rgba(20,22,32,.95);color:#fff;padding:10px 16px;border-radius:14px;display:none;z-index:50;max-width:90%;text-align:center;border:1px solid var(--bd)}
.reveal{text-align:center;padding:22px 16px;min-height:360px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;border:2px solid var(--bd);border-radius:30px;background:var(--cd);width:100%;max-width:340px}
.reveal.town{background:#112a1d;border-color:#1f9a4d}.reveal.maf{background:#2a1115;border-color:#b0343b}
.reveal .ri{height:160px;display:flex;align-items:center;justify-content:center;font-size:90px}.reveal .ri img,.reveal .ri video{height:100%}
.reveal small{letter-spacing:2px;color:var(--mu);font-weight:700}.reveal h1{margin:0;font-size:36px;letter-spacing:1px}.reveal.town h1{color:var(--gr)}.reveal.maf h1{color:#ff6b70}
.reveal p{color:var(--mu);margin:6px 0 0;font-size:16px}
.big{font-size:20px;font-weight:800;text-align:center}
.srch{position:relative}.srch input{padding-left:44px}.srch::before{content:"🔍";position:absolute;left:14px;top:50%;transform:translateY(-50%);font-size:18px}
.evc{background:linear-gradient(180deg,#0e2336,#0a1426);border:1.5px solid #2c6b9a;border-radius:30px;padding:26px 18px 22px;text-align:center;width:100%;max-width:340px;animation:pop .25s}
.evc.last{background:#0b1226;border-color:#2e4aa6}.evc.kill{background:linear-gradient(180deg,#2a1115,#14090c);border-color:#8a2c33}.evc.out{border-color:#b98a2b;background:linear-gradient(180deg,#2a2210,#12100a)}
.evc .pa{position:relative;width:150px;height:150px;margin:0 auto 16px}.evc .pa .av{width:150px;height:150px;font-size:56px}
.evc .bd{position:absolute;right:-8px;bottom:-4px;width:64px;height:64px;border-radius:50%;background:#16202e;border:3px solid #22334a;display:flex;align-items:center;justify-content:center;font-size:30px;overflow:hidden}
.evc .bd img,.evc .bd video{width:84%;height:84%;object-fit:contain}.evc .bd.mic{background:#3b5bff;border-color:#0b1226}
.evc p{margin:0;font-size:24px;line-height:1.3;color:#fff}.evc p b{color:#7fc4ff}
@keyframes pop{from{transform:scale(.85);opacity:0}to{transform:scale(1);opacity:1}}
.ban{display:flex;gap:10px;align-items:center;background:rgba(229,72,77,.10);border:1px solid rgba(229,72,77,.4);border-radius:18px;padding:10px 12px;margin:8px 0}
.ban.n{background:rgba(91,108,255,.10);border-color:rgba(91,108,255,.4)}.ban span{flex:1;font-weight:600;text-align:center}
.hf{display:flex;gap:8px;overflow:auto;margin:10px 0}.hf button{display:flex;gap:6px;align-items:center;padding:12px 16px;border-radius:14px;border:1px solid var(--bd);background:var(--cd);font-weight:800;white-space:nowrap}.hf button.on{background:#4f7bff;color:#fff;border-color:transparent}
.hc{display:flex;gap:12px;align-items:flex-start;border-radius:18px;background:var(--cd);border:1px solid var(--bd);border-left:5px solid var(--gr);padding:12px;margin:10px 0}.hc.l{border-left-color:var(--rd)}
.hc .ri{width:60px;height:60px;border-radius:14px;background:var(--cd2);display:flex;align-items:center;justify-content:center;font-size:30px;flex:0 0 60px;overflow:hidden}.hc .ri img{height:100%}
.hd2{color:var(--mu);font-weight:800;letter-spacing:1px;margin:18px 6px 6px}
[data-theme=light] body{background:#edeef3}
body[data-ph=night]{background:linear-gradient(180deg,#2b1018,#12070b) fixed}
[data-theme=light] body[data-ph=night]{background:linear-gradient(180deg,#fde9ed,#ecd3dc) fixed}
.rh{text-align:center;margin:2px 0 4px;min-height:44px}.rh b{font-size:21px}.rh .rs{font-size:14px;margin-top:2px}
.tp{display:inline-block;background:var(--cd2);border:1px solid var(--bd);border-radius:999px;padding:2px 10px;font-size:14px;font-weight:700;vertical-align:middle;margin-left:6px}
.rbar,.br{margin:10px 0;align-items:center}
.rav{width:44px;height:44px;border-radius:50%;overflow:hidden;flex:0 0 44px;opacity:.9;display:flex}.rav .av{width:44px;height:44px}
.spk{width:44px;height:44px;border-radius:50%;border:0;background:rgba(80,110,255,.12);color:#4f6bff;font-size:18px;flex:0 0 44px;padding:0}
.prow{display:flex;gap:8px;overflow:auto;padding:4px 2px 8px}
.pcd{flex:0 0 62px;background:var(--cd);border:1px solid var(--bd);border-radius:14px;padding:6px 2px;text-align:center;font-size:11px;font-weight:700}
.pcd .av{width:40px;height:40px;display:flex;margin:0 auto 3px}.pcd span{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding:0 3px}
.ctl{margin:12px 0;gap:10px}.ctl .btn{height:46px;font-size:15px}
.btn.wh{background:var(--cd);color:var(--tx);border:1px solid var(--bd);box-shadow:none}
.qb{flex:1;display:flex;gap:10px;align-items:center;border:1.5px solid #6b86ff;background:rgba(91,108,255,.10);border-radius:14px;padding:10px 12px;font-size:15px;font-weight:600;line-height:1.25}
.qb.x{border-color:#e5484d;background:rgba(229,72,77,.10)}
.skp{width:100%;margin:0 0 8px}
#chat{height:calc(100vh - 215px);height:calc(100dvh - 215px);min-height:260px}
#chat.lb{height:calc(100vh - 340px);height:calc(100dvh - 340px)}
#chat.vt{height:240px;min-height:200px}
#chat.pk{height:calc(100vh - 440px);height:calc(100dvh - 440px);min-height:200px;background:linear-gradient(#3a1620,#2a0f17);border:1.5px solid #8a2c33}
[data-theme=light] #chat.pk{background:linear-gradient(#f9d5db,#e9bcc6);border-color:#e5484d}
.sysl{display:block;width:fit-content;max-width:94%;margin:8px auto;background:rgba(229,72,77,.12);color:var(--tx);border-radius:999px;padding:6px 14px;font-size:13px;font-weight:600;text-align:center}
.qt2{display:flex;justify-content:center;gap:8px;font-size:14px;font-weight:800;letter-spacing:1px;color:var(--mu);margin:10px 0}.qt2.l{justify-content:flex-start}
.grid7{display:grid;grid-template-columns:repeat(7,1fr);gap:6px;margin:8px 0}
.gc{position:relative;text-align:center;font-size:10px;font-weight:700;border:1.5px solid transparent;border-radius:10px;padding:4px 1px 2px;min-width:0}
.gc .av{width:100%;height:auto;aspect-ratio:1;display:flex;border-radius:50%}
.gc span:last-child{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.gc.mf{border-color:#e5484d;background:rgba(229,72,77,.08)}.gc.sel{border-color:#4f6bff;background:rgba(79,107,255,.15)}.gc.dead{opacity:.3}
.gc .tg{position:absolute;top:-6px;left:50%;transform:translateX(-50%);background:#e5484d;color:#fff;border-radius:6px;padding:0 5px;font-size:9px;font-weight:800;z-index:2;white-space:nowrap}
.gc .tg.tn{background:#2f7dd1}
.dt{display:inline-block;width:8px;height:8px;border-radius:50%;background:#4f6bff;margin-right:6px;flex:0 0 8px;margin-top:3px}.dt.lg{background:#7be3a0}.dt.g{background:#2fd16a}
.gc .dt{position:absolute;right:3px;bottom:16px;margin:0;border:1.5px solid #fff}
.leg{display:flex;justify-content:space-between;gap:6px;font-size:11px;color:var(--mu);margin:6px 2px}.leg span{flex:1;display:flex;align-items:flex-start}
.info{display:flex;gap:10px;align-items:center;justify-content:center;text-align:center;border:1px solid #8fa0ff;background:rgba(91,108,255,.10);border-radius:12px;padding:10px 12px;margin:10px 0;font-size:14px}
.info.ok{border-color:#2fd16a;background:rgba(47,209,106,.12)}
.info.bad{border-color:#e5484d;background:rgba(229,72,77,.10);justify-content:flex-start;text-align:left}
.btn.gr{background:#2fd16a;box-shadow:none;color:#04210f}
.rx{width:46px;height:46px;border-radius:50%;border:0;background:#e5484d;color:#fff;font-size:18px;flex:0 0 46px}
.fab.rd2{background:linear-gradient(135deg,#e5484d,#c0343a);box-shadow:0 8px 22px rgba(229,72,77,.45)}
.card.vg{padding:10px;margin:6px 0}.grid4{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
.vc{position:relative;text-align:center;border:1.5px solid var(--bd);border-radius:12px;padding:6px 2px;font-size:12px;font-weight:700;background:var(--cd2);min-width:0}
.vc .av{width:56px;height:56px;display:flex;margin:0 auto 3px}.vc span:last-child{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding:0 3px}
.vc.sel{border-color:#4f6bff;background:rgba(79,107,255,.18)}.vc.off{opacity:.45}
.vc .v{position:absolute;top:-6px;right:-4px;background:#8a91aa;color:#fff;border-radius:10px;font-size:11px;padding:0 6px;z-index:2}
.evc .pa .xx{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:150px;line-height:1;color:#e5484d;font-weight:100;pointer-events:none}
.evc.kill p b{color:#ff8f94}
.reveal.lg{background:#fff;color:#14172a;border-color:#fff}.reveal.lg .btn{background:#eef1ff}
.reveal.maf{background:linear-gradient(180deg,#3a0d14,#220a10);border-color:#e5484d;box-shadow:0 0 50px rgba(229,72,77,.35)}.reveal.maf h1{color:#ff5a62}
.chero{background:linear-gradient(135deg,#5b3df5,#6d49ff);color:#fff;border-radius:22px;padding:18px;margin:8px 0 12px}.chero b{font-size:19px}.chero p{margin:6px 0 12px;color:#d9d3ff;font-size:14px}
.cf{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:14px}.cf span{background:rgba(255,255,255,.14);border-radius:10px;padding:8px 10px;font-size:13px}
.chero .btn.wh{background:#fff;color:#4a35d6;border:0}
.cr{display:flex;align-items:center;gap:12px;background:var(--cd);border:1px solid var(--bd);border-radius:16px;padding:10px 12px;margin:8px 0}
.cr .em{width:52px;height:52px;border-radius:12px;background:var(--cd2);display:flex;align-items:center;justify-content:center;font-size:26px;flex:0 0 52px;overflow:hidden}.cr .em img{height:100%}
.cr .cn b{font-size:17px}.cr .tg{display:inline-block;border-radius:7px;padding:0 8px;font-size:12px;font-weight:800;color:#fff;background:#e5484d;margin-left:4px}
.cr .tg.c1{background:#2fa84f}.cr .tg.c2{background:#2f7dd1}.cr .tg.c3{background:#b9561a}.cr .tg.c4{background:#7d4fd1}

:root{--bg:#0a0b10;--cd:#1f2330;--cd2:#262b3b;--bd:#2c3140;--tx:#f1f3fa;--mu:#8a91aa;--ac:#5476ff;--ac2:#7262fe;--go:#e2b44f}
[data-theme=light]{--bg:#edeef3;--cd:#fafbfe;--cd2:#f1f2f8;--bd:#e4e6ef;--tx:#151823;--mu:#6f7690;--ac:#3f5eed;--ac2:#6c5bfa}
html,body{background:var(--bg)}body{background:var(--bg)}
.btn{background:linear-gradient(90deg,var(--ac),var(--ac2));box-shadow:0 6px 18px rgba(98,92,250,.3)}.btn.sec,.btn.wh{background:var(--cd);box-shadow:none}.btn.gr{background:#2fd16a}.btn.gold{background:linear-gradient(135deg,#f3d58c,var(--go2))}.btn.red{background:var(--rd)}
.streak{background:linear-gradient(90deg,rgba(245,148,13,.16),rgba(108,91,250,.10));border:1px solid rgba(245,148,13,.45)}
[data-theme=light] .streak{background:linear-gradient(90deg,#fcefd8,#eaf2f7);border-color:#f0c88f}
.bars i{background:#3e3c44}[data-theme=light] .bars i{background:#ded7d7}.bars i.on{background:#f5940d}
.sq{background:#241e17;border:1px solid #6b5326;color:var(--go)}[data-theme=light] .sq{background:#ece9dc;border-color:#dcc890}
.ib{background:var(--cd);border:1px solid var(--bd)}.ib img{width:84%;height:84%}
[data-theme=light] .card,[data-theme=light] .rm{border-color:transparent;box-shadow:0 2px 10px rgba(30,40,80,.07)}
.chip{background:var(--cd2);border-color:transparent}
.hero>*{animation:fi .5s}@keyframes fi{from{opacity:0}to{opacity:1}}
.hero{cursor:pointer}
.emj{height:1.25em;width:1.25em;object-fit:contain;vertical-align:-.25em}
</style></head><body><div id="toast"></div><div id="app"></div><div id="mdl"></div>
<div id="bar"></div>
<script>
const tg=window.Telegram&&Telegram.WebApp;
if(tg){tg.ready();tg.expand();try{tg.setHeaderColor("#0a0c13");tg.setBackgroundColor("#0a0c13")}catch(x){}}
const $=i=>document.getElementById(i),q=new URLSearchParams(location.search);
let TH="auto";try{TH=localStorage.getItem("th")||"auto"}catch(x){}
const isDark=()=>document.documentElement.dataset.theme!="light";
function applyTheme(){const sys=(tg&&tg.colorScheme)?tg.colorScheme:(matchMedia("(prefers-color-scheme: light)").matches?"light":"dark");const t=TH=="auto"?sys:TH;
 if(t=="light")document.documentElement.dataset.theme="light";else document.documentElement.removeAttribute("data-theme");
 if(tg){try{tg.setHeaderColor(t=="light"?"#edeef3":"#0a0b10");tg.setBackgroundColor(t=="light"?"#edeef3":"#0a0b10")}catch(x){}}}
function setTheme(v){TH=v;try{localStorage.setItem("th",v)}catch(x){}applyTheme();render()}
applyTheme();if(tg&&tg.onEvent)tg.onEvent("themeChanged",()=>{applyTheme();render()});
const e=s=>String(s==null?"":s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
let ws,ME={},A={},OWN=false,BOT="",STARS={},ROOM=null,LOBBY=[],PAGE="home",MODAL=null,CR={},RAT=null,RS={kind:"players",scope:"week"},TASKS=null,HIST=null,FR=null,FS=null,REPLY=null,COMPOSE=false,REV="",SPK=true,PRO={mo:"3"},LS=null,PC={},RFILT="all",TBL=true,SPARAM="";
let BRD="Play Mafia",SEL=null,BN=0,CQ="",CV=null,ADS={},AD_SECS=4,adT=0;
const PH={lobby:"Kutish",intro:"Tanishuv",night:"Tun",day:"Muhokama",vote:"Ovoz berish",last:"So'nggi so'z",ended:"Tugadi"};
const MODE={chat:"Chat",voice:"Ovozli",table:"Stol"};const MT=["mafia","mafiaboshi","xufya"];
const RN={tinch:"Tinch aholi",izquvar:"Izquvar",doktor:"Doktor",bezori:"Bezori",rais:"Rais",qassob:"Qassob",hamsoya:"Hamsoya",folbin:"Folbin",mirshab:"Mirshab",mafia:"Mafia",mafiaboshi:"Mafiaboshi",xufya:"Xufya"};
const REM={tinch:"🧑",izquvar:"🕵️",doktor:"🩺",bezori:"🤠",rais:"🎩",qassob:"🔪",hamsoya:"🍵",folbin:"🔮",mirshab:"👮",mafia:"🔫",mafiaboshi:"👑",xufya:"🕶️"};
const RD={tinch:"Maxsus qobiliyat yo'q. Kunduzi boshqa aholi vakillari bilan muhokama qilib, Mafiyani topishga harakat qiladi.",
doktor:"Har tun bir o'yinchini davolaydi. Mafiya tanlagan qurbon davolansa, tirik qoladi.",
izquvar:"Har tun bir o'yinchini tekshirib, u Mafiya a'zosi yoki emasligini bilib oladi.",
mafia:"Tunda jamoadoshlari bilan qurbon tanlab, o'yindan chiqaradi va kunduzi o'zini tinch aholidek ko'rsatadi. Yutish uchun o'yinchilarni chiqarib tashlashi kerak.",
mafiaboshi:"Mafiya jamoasining boshlig'i. Izquvar tekshirganda u Mafia emas deb ko'rinadi."};
const LAD=[[6,"mafia"],[6,"doktor"],[7,"mafia"],[8,"izquvar"],[9,"mafiaboshi"],[10,"rais"],[11,"bezori"],[12,"qassob"],[13,"hamsoya"],[14,"folbin"],[15,"mirshab"],[16,"xufya"]];
const SINCE={tinch:"Ko'p",izquvar:"8+",doktor:"6+",bezori:"11+",rais:"10+",qassob:"12+",hamsoya:"13+ · Pro",folbin:"14+ · Pro",mirshab:"15+ · Pro",mafia:"1–2",mafiaboshi:"9+",xufya:"16 · Pro"};
const ORDER=["tinch","izquvar","doktor","bezori","rais","qassob","hamsoya","folbin","mirshab","mafia","mafiaboshi","xufya"];
function connect(){const u=(location.protocol=="https:"?"wss://":"ws://")+location.host+"/ws?init="+encodeURIComponent(tg?tg.initData:"")+"&sp="+encodeURIComponent(SPARAM)+(q.get("dev")?"&dev="+q.get("dev")+"&name="+(q.get("name")||"Dev"):"");
ws=new WebSocket(u);ws.onmessage=ev=>on(JSON.parse(ev.data));ws.onclose=()=>setTimeout(connect,2000)}
const send=o=>ws&&ws.readyState==1&&ws.send(JSON.stringify(o));
function toast(m){const t=$("toast");t.textContent=m;t.style.display="block";clearTimeout(toast.h);toast.h=setTimeout(()=>t.style.display="none",3000)}
const now=()=>Date.now()/1000;
function stamp(o){o._t=now();return o}
function rest(o,k){return o&&o[k]!=null?Math.max(0,o[k]-(now()-o._t)):0}
function fmt(s){s=Math.max(0,Math.floor(s));return Math.floor(s/60)+":"+String(s%60).padStart(2,"0")}
function fmtH(s){s=Math.max(0,Math.floor(s/60));return Math.floor(s/60)+":"+String(s%60).padStart(2,"0")}
function on(m){const t=m.t;
if(t=="hello"){ME=stamp(m.me);A=m.assets;OWN=m.owner;BOT=m.bot;STARS=m.stars||{};BRD=m.brand||BRD;document.title=BRD;ADS=m.ads||{};AD_SECS=m.ad_secs||4;
 const sp=(tg&&tg.initDataUnsafe&&tg.initDataUnsafe.start_param)||"";const rc=q.get("room")||(sp.startsWith("room_")?sp.slice(5):"");if(rc&&!ROOM)send({t:"join",id:rc})}
else if(t=="lobby"){LOBBY=m.rooms;if(ROOM){ROOM=null;EVQ=[];EVN=null;stopVoice();COMPOSE=false;REPLY=null;document.body.style.backgroundImage=""}}
else if(t=="room"){const was=ROOM;ROOM=m;if(!was||was.id!=m.id){MODAL=null;PAGE="home"}if(!was||was.phase!=m.phase)SEL=null;voiceSync()}
else if(t=="chat"){if(ROOM)ROOM.chat.push(m.c)}
else if(t=="fx")snd(m.k);else if(t=="err")toast(m.m);else if(t=="toast")toast(m.m);
else if(t=="me")ME=stamp(m.me);else if(t=="rating")RAT=m;else if(t=="tasks")TASKS=stamp(m);else if(t=="hist")HIST=m;
else if(t=="friends")FR=m.list;else if(t=="fsearch")FS=m.list;
else if(t=="invoice"){if(tg&&tg.openInvoice)tg.openInvoice(m.url,s=>{if(s=="paid"){toast("Pro faollashdi!");MODAL=null;send({t:"profile"});render()}});else window.open(m.url);return}
else if(t=="ev"){EVQ.push(m);evNext();return}else if(t=="rtc"){rtcIn(m);return}else if(t=="banned"){document.body.innerHTML="<h3 style='padding:30px'>Siz bloklangansiz</h3>";return}
render()}
function snd(k){if(A["snd_"+k])try{new Audio(A["snd_"+k]).play().catch(()=>{})}catch(x){}}
function media(k,c){const u=A[k];if(!u)return"";return u.includes(".webm")?`<video class="${c||""}" src="${u}" autoplay loop muted playsinline></video>`:`<img class="${c||""}" src="${u}">`}
function ico(k,fb){const u=isDark()&&A["ic_"+k+"_dark"]?A["ic_"+k+"_dark"]:A["ic_"+k];return u?`<img src="${u}">`:fb}
const hue=n=>{let h=0;for(const c of String(n))h=(h*31+c.charCodeAt(0))%360;return h};
function av(ph,n,cls){const nm=String(n||"?");const ini=e(nm.trim().charAt(0).toUpperCase()||"?");const st=`background:hsl(${hue(nm)} 55% 42%)`;
 return ph?`<img class="av ${cls||""}" src="${e(ph)}" onerror="this.outerHTML='<div class=&quot;av ${cls||""}&quot; style=&quot;${st}&quot;>${ini}</div>'">`:`<div class="av ${cls||""}" style="${st}">${ini}</div>`}
/* ---- navigatsiya ---- */
function go(p){PAGE=p;MODAL=null;
 if(p=="tasks")send({t:"tasks"});if(p=="history")send({t:"history"});if(p=="rating"||p=="clans"){RS.kind=p=="clans"?"clans":"players";rate()}
 if(p=="profile")send({t:"profile"});if(p=="friends"){FS=null;send({t:"friends"})}
 render();scrollTo(0,0)}
function rate(){RAT=null;send({t:"rating",kind:RS.kind,scope:RS.scope})}
function back(){if(MODAL){MODAL=null;render();return}if(ROOM){ROOM.phase=="lobby"||ROOM.phase=="ended"||ROOM.me.role==null?send({t:"leave"}):confirmLeave();return}
 if(PAGE=="settings"||PAGE=="friends"){go("profile");return}if(PAGE!="home")go("home")}
function confirmLeave(){if(ROOM.me.role==null||confirm("Chiqsangiz ball ayiriladi va 30 daqiqa o'ynay olmaysiz. Chiqasizmi?"))send({t:"leave"})}
if(tg&&tg.BackButton)tg.BackButton.onClick(back);
function render(){const a=$("app");
 if(tg&&tg.BackButton){(ROOM||PAGE!="home"||MODAL)?tg.BackButton.show():tg.BackButton.hide()}
 const keep=$("chat")?$("chat").scrollTop:0,atEnd=$("chat")?($("chat").scrollHeight-$("chat").scrollTop-$("chat").clientHeight<80):true;
 a.innerHTML=ROOM?roomView():({home:lobbyView,tasks:tasksView,history:histView,rules:rulesView,rating:ratingView,clans:clansView,profile:profileView,settings:settingsView,friends:friendsView}[PAGE]||lobbyView)();
 $("mdl").innerHTML=MODAL?modalView():(EVN?evView():(ROOM?revealView():""));
 const c=$("chat");if(c)c.scrollTop=atEnd?c.scrollHeight:keep;
 barRender();bgRender()}
function bgRender(){document.body.dataset.ph=ROOM?ROOM.phase:"";let k=ROOM&&(ROOM.phase=="night"?"bg_night":ROOM.phase=="day"||ROOM.phase=="vote"?"bg_day":"");
 document.body.style.backgroundImage=k&&A[k]?`linear-gradient(rgba(10,12,19,.7),rgba(10,12,19,.85)),url(${A[k]})`:"";document.body.style.backgroundSize="cover"}
const hd=t=>`<button class="back" onclick="back()">✕ Orqaga</button>${t?`<h2>${t}</h2>`:""}`;
/* ---- lobbi ---- */
function lobbyView(){
 const w=LOBBY.filter(r=>r.phase=="lobby"),l=LOBBY.filter(r=>r.phase!="lobby"),cool=rest(ME,"cool");
 const ib=[["tasks",isDark()?"☀️":"🌙"],["history","⏳"],["rules","📖"],["rating","🏆"],["clans","⛺"],["profile","🕴️"]];
 const td=ME.tasks_done||0;
 let h=`<div class="hero" id="hero" onclick="adClick()">${heroHtml()}</div>
<div class="icons">${ib.map(x=>`<button class="ib" onclick="go('${x[0]}')">${ico(x[0],x[1])}${x[0]=="tasks"&&td<3?'<span class="dot"></span>':""}</button>`).join("")}</div>
<div class="act"><button class="sq" onclick="openCreate()">${ico("create","＋")}</button><button class="btn" onclick="send({t:'quick'})">${ico("quick","⚡")} Tezkor qo'shilish</button><button class="sq" onclick="MODAL='code';render()">${ico("code","▦")}</button></div>
<div class="streak" onclick="go('tasks')"><div class="t"><span>${emj("fire","🔥")} ${ME.streak||0} kunlik seriya</span><small>Bugun: ${td}/3 vazifa</small></div><div class="bars">${[0,1,2].map(i=>`<i class="${i<td?"on":""}"></i>`).join("")}</div></div>`;
 if(cool>0)h+=`<div class="warn"><span>⏳</span><div><b>Siz o'yinni tark etgansiz.</b> Yangi o'yinga <b id="cool">${fmt(cool)}</b> dan keyin qo'shila olasiz. Hozircha o'yinlarni kuzatishingiz mumkin.</div></div>`;
 h+=`<div class="sec-t"><span>👥 Ochiq xonalar</span></div>`;
 h+=w.length?w.map(r=>`<div class="card rm" onclick="send({t:'join',id:'${r.id}'})"><div class="h">${r.req?`<span class="bdg req">🎮 ${r.req}+</span>`:""}${r.pro?'<span class="bdg pro">Pro</span>':""}${r.mode=="voice"?'<span class="bdg voice">🎙 Ovozli</span>':r.mode=="table"?'<span class="bdg">👥 Stol</span>':""}<span class="n">${e(r.name)}</span><span class="cnt">${r.n}/${r.max}</span></div>
<div class="hs"><div class="stk">${r.ph.map(x=>av(x.p,x.n)).join("")}</div><span>${e(r.host)}</span>${r.n>5?`<span class="mu">+${r.n-5}</span>`:""}<span class="chev">›</span></div>
<div class="chips"><span class="chip">👥 ${r.minp}+ kishi</span><span class="chip">⏱ ${Math.round(r.disc/60)} daq muhokama</span></div></div>`).join(""):'<div class="card mu c">Ochiq xona yo\'q. O\'zingiz yarating!</div>';
 if(l.length)h+=`<div class="sec-t"><span>👁 Davom etayotgan o'yinlar</span><span>${l.length} ta</span></div>`+l.map(r=>`<div class="card watch"><div class="row"><div class="f"><span class="bdg ${r.pro?"pro":""}">${r.pro?"Pro":"Ochiq"}</span> <span class="chip">${r.phase=="night"?"🌙 Tun":r.phase=="ended"?"🏁 Tugadi":"☀️ "+PH[r.phase]}</span>
<div class="mu" style="margin:8px 0">${r.alive}/${r.n} tirik · ${r.day}-kun · ${r.spec} kuzatuvchi</div><div class="stk">${r.ph.map(x=>av(x.p,x.n)).join("")}</div></div><button class="btn sec sm" onclick="send({t:'watch',id:'${r.id}'})">👁 Kuzatish</button></div></div>`).join("");
 return h}
/* ---- xona yaratish / kod / pro ---- */
function openCreate(){CR={name:"",mode:"chat",closed:false,req:false,minp:6,disc:2};MODAL="create";render()}
function comp(n,pro){const c={};LAD.forEach(([need,r])=>{if(n>=need&&(pro||need<13))c[r]=(c[r]||0)+1});let k=Object.values(c).reduce((a,b)=>a+b,0);if(k>n){}c.tinch=Math.max(0,n-k);
 return ORDER.filter(r=>c[r]).map(r=>c[r]+" "+RN[r]).join(", ")}
function crSet(k,v){CR[k]=v;if(!ME.pro&&(k=="closed"||k=="req")&&v){CR[k]=false;MODAL="pro";}render()}
function modalView(){
 if(MODAL=="create")return`<div class="ov" onclick="if(event.target==this){MODAL=null;render()}"><div class="sheet"><div class="hd"><span>＋ Yangi xona</span><button class="x" onclick="MODAL=null;render()">✕</button></div>
<label class="field">Xona nomi (ixtiyoriy)</label><input id="crn" value="${e(CR.name)}" maxlength="24" placeholder="Masalan: Do'stlar davrasi" oninput="CR.name=this.value">
<div class="seg" style="margin-top:12px"><button class="${CR.mode=="chat"?"on":""}" onclick="crSet('mode','chat')">➤ Chat</button><button class="${CR.mode=="voice"?"on":""}" onclick="crSet('mode','voice')">🎙 Ovozli</button><button class="${CR.mode=="table"?"on":""}" onclick="crSet('mode','table')">👥 Stol</button></div>
<div class="mu" style="margin:8px 4px">${CR.mode=="chat"?"O'yinchilar chat orqali yozishib o'ynaydi.":CR.mode=="voice"?"O'yinchilar mikrofon orqali gaplashadi. Bepul o'yinlar: "+ME.free_voice+" ta.":"Stol rejimi: o'yinchilar yonma-yon o'tirib o'ynaydi, ilova faqat boshqaradi."}</div>
<div class="opt"><div><b>🔒 Yopiq xona</b> <span class="bdg pro">Pro</span><div class="mu">O'chiq: xona "Ochiq xonalar" ro'yxatida hammaga ko'rinadi.</div></div><button class="sw ${CR.closed?"on":""}" onclick="crSet('closed',${!CR.closed})"></button></div>
<div class="opt"><div><b>🎮 Kirish talabi</b> <span class="bdg pro">Pro</span><div class="mu">O'chiq: istalgan o'yinchi kira oladi. Yoqiq: kamida 50 ta o'yin.</div></div><button class="sw ${CR.req?"on":""}" onclick="crSet('req',${!CR.req})"></button></div>
<label class="field">Kamida nechta o'yinchi kerak?</label><div class="step"><button onclick="crSet('minp',Math.max(6,CR.minp-1))">−</button><b>${CR.minp}</b><button onclick="crSet('minp',Math.min(16,CR.minp+1))">+</button></div>
<div class="mu" style="margin:8px 4px">${comp(CR.minp,ME.pro)}</div>
<label class="field">Muhokama vaqti</label><div class="row"><input type="range" class="f" min="1" max="5" value="${CR.disc}" oninput="CR.disc=+this.value;$('dv').textContent=CR.disc+' daqiqa'"><b id="dv" style="min-width:90px;text-align:right;font-size:18px">${CR.disc} daqiqa</b></div>
<div class="mu" style="margin:4px 4px 14px">Kunduzgi muhokama qancha davom etadi.</div>
<button class="btn" onclick="mk()">✓ Yaratish</button></div></div>`;
 if(MODAL=="code")return`<div class="ov" onclick="if(event.target==this){MODAL=null;render()}"><div class="sheet"><div class="hd"><span>▦ Xonaga kirish</span><button class="x" onclick="MODAL=null;render()">✕</button></div>
<label class="field">Xona kodi</label><input id="jc" placeholder="Masalan: a1b2c3" autocapitalize="off"><div style="height:12px"></div><button class="btn" onclick="send({t:'join',id:$('jc').value.trim()});MODAL=null">Kirish</button></div></div>`;
 if(MODAL=="pro"){const mos=["1","3","6","12"],lab={1:"1 oy",3:"3 oy",6:"6 oy",12:"1 yil"},base=STARS["1"]||100;
  return`<div class="ov mid" onclick="if(event.target==this){MODAL=null;render()}"><div class="sheet"><div class="crown">${media("emo_crown")?media("emo_crown"):"👑"}</div>
<div class="c mu" style="letter-spacing:2px;font-weight:700;margin-top:6px">${e(BRD.toUpperCase())} PRO · TELEGRAM STARS</div><div class="c" style="font-size:30px;font-weight:900;margin:6px 0">OBUNA OLISH</div>
<div class="plans">${mos.map(m=>{const p=STARS[m]||0,mm=+m,dis=Math.round(100-100*p/(base*mm));return`<div class="plan ${PRO.mo==m?"on":""}" onclick="PRO.mo='${m}';render()">${dis>0?`<span class="d">−${dis}%</span>`:""}<span style="color:var(--tx);font-size:20px;font-weight:800">${lab[m]}</span><b>⭐ ${p}</b><span>${mm==1?p+" / oy":"≈"+Math.round(p/mm)+" / oy"}</span></div>`}).join("")}</div>
<button class="btn gold" onclick="send({t:'invoice',mo:PRO.mo})">⭐ ${STARS[PRO.mo]||""} to'lash</button>
<div class="c mu" style="margin:10px 0">To'lov Telegram oynasida tasdiqlanadi. Pro darhol yoqiladi.</div>
<div class="c mu" style="border-top:1px solid var(--bd);padding-top:12px">Agar Stars sotib olish bo'yicha yordam kerak bo'lsa, admin bilan bog'laning.</div>
<button class="btn sec" style="margin-top:12px" onclick="MODAL=null;render()">Yopish</button></div></div>`}
 if(MODAL=="invite"){const code=ROOM?ROOM.id:"";return`<div class="ov" onclick="if(event.target==this){MODAL=null;render()}"><div class="sheet"><div class="hd"><span>➤ Taklif yuborish</span><button class="x" onclick="MODAL=null;render()">✕</button></div>
<div class="mu" style="margin:8px 0">Xona kodi: <b style="color:var(--tx)">${e(code)}</b></div><button class="btn" onclick="share()">🔗 Havolani ulashish</button>
<div class="sec-t"><span>Do'stlarga yuborish</span></div>${FR==null?'<div class="mu c">Yuklanmoqda...</div>':FR.length?FR.map(f=>`<div class="lr">${av(f.ph,f.name)}<b>${e(f.name)}</b><span class="mu">${f.on?"onlayn":""}</span><button class="btn sm" style="margin-left:auto" onclick="send({t:'invite',to:${f.id}});toast('Taklif yuborildi')">Yuborish</button></div>`).join(""):'<div class="mu c">Do\'stlar yo\'q</div>'}</div></div>`}
 if(MODAL=="clancreate")return`<div class="ov" onclick="if(event.target==this){MODAL=null;render()}"><div class="sheet"><div class="hd"><span>⛺ Yangi ovul</span><button class="x" onclick="MODAL=null;render()">✕</button></div><label class="field">Ovul nomi</label><input id="cn1" maxlength="20" placeholder="Masalan: Qora toj"><label class="field">Teg (2-4 harf)</label><input id="cn2" maxlength="4" placeholder="QTJ" style="text-transform:uppercase"><div style="height:14px"></div><button class="btn" onclick="send({t:'clan_create',name:$('cn1').value,tag:$('cn2').value});MODAL=null;render()">✓ Yaratish</button></div></div>`;
 if(MODAL=="clanview"&&CV)return`<div class="ov" onclick="if(event.target==this){MODAL=null;render()}"><div class="sheet"><div class="hd"><span>⛺ ${e(CV.name)}</span><button class="x" onclick="MODAL=null;render()">✕</button></div><div class="stat"><div><b class="go">${CV.points}</b><span>Ochko</span></div><div><b>${CV.n}</b><span>A'zo</span></div><div><b>#${CV.rank}</b><span>O'rin</span></div><div><b>${CV.lvl}</b><span>Daraja</span></div></div><div style="height:14px"></div>${ME.clan?'<div class="mu c">Siz allaqachon ovuldasiz</div>':CV.open?`<button class="btn" onclick="send({t:'clan_join',id:${CV.id}});MODAL=null;render()">Qo'shilish</button>`:'<div class="mu c">Ovul to\'la</div>'}</div></div>`;
 return""}
let EVN=null,EVQ=[],EVT=null,HF="all";
function evNext(){if(EVN||!EVQ.length)return;EVN=EVQ.shift();render();EVT=setTimeout(evClose,EVN.ms||3500)}
function evClose(){clearTimeout(EVT);EVN=null;render();if(EVQ.length)setTimeout(evNext,150)}
function evView(){const v=EVN,t=e(v.tx||"").replace("{n}","<b>"+e(v.n)+"</b>");
 const bd=v.k=="last"?'<div class="bd mic">'+emj("mic","🎙")+'</div>':v.role?`<div class="bd">${media("role_"+v.role)||REM[v.role]||""}</div>`:"";
 return`<div class="ov mid" onclick="evClose()"><div class="evc ${v.k}"><div class="pa">${av(v.ph,v.n)}${v.k=="kill"||v.k=="out"?'<div class="xx">✕</div>':""}${bd}</div><p>${t}</p></div></div>`}
function revealView(){if(!ROOM||ROOM.spec||!ROOM.me.role||ROOM.phase=="lobby"||REV==ROOM.id+":done")return"";const r=ROOM.me.role,maf=MT.includes(r);
 if(REV!=ROOM.id+":open")return`<div class="ov mid" onclick="REV=ROOM.id+':open';render()"><div class="reveal lg">${media("logo")||'<div class="logo" style="padding:10px 0">'+e(BRD.toUpperCase())+'</div>'}<div style="font-size:21px;font-weight:700;margin-top:8px">Rolingizni ko'rish uchun karta ustiga bosing</div><span class="btn sec sm" style="color:#7d8bff">⚡ Bosing</span></div></div>`;
 return`<div class="ov mid" onclick="REV=ROOM.id+':done';render()"><div class="reveal ${maf?"maf":"town"}"><div class="ri">${media("role_"+r)||REM[r]}</div><small>SIZNING ROLINGIZ</small><h1>${RN[r].toUpperCase()}</h1><p>${RD[r]||"Maxsus rol. Qoidalari tez orada qo'shiladi."}</p></div></div>`}
function mk(){send({t:"create",name:CR.name,mode:CR.mode,closed:CR.closed,req:CR.req,minp:CR.minp,disc:CR.disc*60});MODAL=null;render()}
function share(){const u="https://t.me/"+BOT+"?startapp=room_"+ROOM.id,tx=BRD+": o'yinga qo'shiling!";const l="https://t.me/share/url?url="+encodeURIComponent(u)+"&text="+encodeURIComponent(tx);tg?tg.openTelegramLink(l):window.open(l)}
/* ---- vazifalar ---- */
function tasksView(){const T=TASKS;if(!T)return hd("Kunlik vazifalar")+'<div class="mu c">Yuklanmoqda...</div>';
 const st=T.streak,idx=st>0?((st-1)%7)+1:0,doneToday=T.today_done;
 const days=[1,2,3,4,5,6,7].map(i=>{const ok=doneToday?i<=idx:i<=idx,cur=doneToday?i==idx:i==idx+1;return`<div class="dayt ${ok&&st>0?"ok":""} ${cur||(!doneToday&&st==0&&i==1)?"on":""}"><i>${i==7?"🧰":"🎙"}${i==5?"<small>×3</small>":""}</i>${i}-kun</div>`}).join("");
 let h=hd("Kunlik vazifalar")+`<div class="streak" style="padding:16px"><div class="t" style="font-size:22px"><span><span style="color:#ff8a1c">🔥</span> <b style="font-size:40px;color:#ff8a1c">${st}</b> kunlik seriya</span><span class="mu">Eng yaxshi: ${T.best}</span></div><div class="tl">${days}</div></div>
<div class="mu c" style="margin:12px 0;font-size:16px">🎙 Bugungi 3 ta vazifani bajaring — <b style="color:var(--tx)">1 ta bepul ovozli o'yin</b></div>
<div class="sec-t"><span>📜 Bugungi vazifalar</span><span>⏱ <span id="tleft">${fmtH(rest(T,"left"))}</span> qoldi</span></div>`;
 h+=T.tasks.map(k=>k.active?`<div class="tk"><div class="ti">🎮</div><div class="f" style="flex:1"><b style="font-size:20px">${e(k.text)}</b><div><span class="lv lv${k.slot}" style="margin:6px 0;display:inline-block">${k.lv}</span></div><div class="pb"><i style="width:${100*k.prog/k.goal}%"></i></div><div class="row sp"><span class="mu" style="font-size:15px">${k.prog}/${k.goal}${T.rr?` · <a style="color:#6b8cff" onclick="send({t:'reroll'})">⟳ almashtirish</a>`:""}</span><span class="rew">+${k.rew} 🗝</span></div></div></div>`
  :k.done?`<div class="tk" style="border-color:rgba(47,209,106,.5)"><div class="ti">✅</div><div class="f" style="flex:1"><b>${e(k.text)}</b> <span class="lv lv${k.slot}">${k.lv}</span><div class="gr" style="font-weight:700">Bajarildi · +${k.rew} 🗝</div></div></div>`
  :`<div class="tk lock"><div class="ti">🔒</div><div class="f" style="flex:1"><b style="font-size:20px">${k.slot+1}-vazifa</b><span class="lv lv${k.slot}">${k.lv}</span><div class="mu" style="font-size:15px;margin-top:4px">${k.slot}-vazifani bajaring — keyin ochiladi</div></div><span class="rew">+${k.rew} 🗝</span></div>`).join("");
 h+=`<div class="mu c">⟳ Vazifani almashtirish: <b style="color:#6b8cff">${T.rr} ta qoldi</b></div>
<div class="card chest"><div class="row"><div class="ti" style="width:64px;height:64px;border-radius:18px;background:rgba(91,108,255,.3);display:flex;align-items:center;justify-content:center;font-size:30px">🧰</div><div class="f"><b style="font-size:21px">Haftalik sandiq</b><div class="mu" style="font-size:15px">25 ta kalit yig'ing — sandiq ochiladi</div></div><b style="font-size:26px;color:#7d8bff">🗝 ${T.keys}/25</b></div>
<div class="pb" style="margin-top:14px"><i style="width:${Math.min(100,100*T.keys/25)}%;background:linear-gradient(90deg,#5b6cff,#9b8cff)"></i></div>${T.keys>=25?`<button class="btn gold" style="margin-top:10px" onclick="send({t:'chest'})">🧰 Sandiqni ochish (+100 ball)</button>`:""}</div>`;
 return h}
function histView(){let h=hd("O'yinlar tarixi");if(!HIST)return h+'<div class="mu c">Yuklanmoqda...</div>';
 const L=HIST.list,g=HIST.g||0,w=HIST.w||0,wr=g?Math.round(100*w/g):0;
 h+=`<div class="stat" style="grid-template-columns:repeat(4,1fr);gap:8px"><div style="padding:10px;text-align:center"><b style="font-size:26px">${g}</b><span style="font-size:12px">O'yinlar</span></div><div style="padding:10px;text-align:center"><b class="gr" style="font-size:26px">${w}</b><span style="font-size:12px">G'alaba</span></div><div style="padding:10px;text-align:center"><b class="rd" style="font-size:26px">${g-w}</b><span style="font-size:12px">Mag'lubiyat</span></div><div style="padding:10px;text-align:center"><b class="go" style="font-size:26px">${wr}%</b><span style="font-size:12px">G'alaba foizi</span></div></div>`;
 const fl={all:x=>1,win:x=>x.win,lose:x=>!x.win,live:x=>x.alive,dead:x=>!x.alive},FI=[["all","👥"],["win","🏆"],["lose","😵"],["live","🛡"],["dead","☠️"]];
 h+=`<div class="hf">${FI.map(f=>`<button class="${HF==f[0]?"on":""}" onclick="HF='${f[0]}';render()">${f[1]} ${L.filter(fl[f[0]]).length}</button>`).join("")}</div>`;
 const rows=L.filter(fl[HF]);if(!rows.length)return h+'<div class="card mu c">Hali o\'yin yo\'q</div>';
 let last="";
 rows.forEach(x=>{const d=new Date(x.ts*1000),dk=d.toLocaleDateString("uz",{day:"numeric",month:"short"}).toUpperCase();
  if(dk!=last){h+=`<div class="hd2">${e(dk)}</div>`;last=dk}
  const maf=MT.includes(x.role);
  h+=`<div class="hc ${x.win?"":"l"}"><div class="ri">${media("role_"+x.role)||REM[x.role]||"🎮"}</div><div class="f" style="flex:1;min-width:0"><div><b style="color:${x.win?"var(--gr)":"var(--rd)"};font-size:18px">${x.win?"G'alaba":"Mag'lubiyat"}</b> <span class="bdg ${maf?"req":""}" style="margin-left:6px">${RN[x.role]||"—"}</span></div><div class="mu">${x.n} o'yinchi · ${x.days} kun${x.reason?" · "+e(x.reason):""}</div><div class="stk" style="margin-top:8px">${(x.pl||[]).map(y=>av(y.p,y.n)).join("")}${x.n>5?`<span class="mu" style="margin-left:8px;align-self:center">+${x.n-5}</span>`:""}</div></div><div style="text-align:right"><b style="font-size:19px">${d.toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"})}</b></div></div>`});
 return h}
/* ---- qoidalar ---- */
function rulesView(){const maf=r=>MT.includes(r),list=ORDER.filter(r=>RFILT=="all"||(RFILT=="maf"?maf(r):!maf(r))),na=ORDER.filter(r=>!maf(r)).length;
 const rows=[];for(let n=6;n<=16;n++){const c={};LAD.forEach(([need,r])=>{if(n>=need)c[r]=(c[r]||0)+1});const prev=n>6?rows[n-7].c:{};const add=[],padd=[];ORDER.forEach(r=>{const d=(c[r]||0)-(prev[r]||0);if(d>0)(n>=13&&LAD.find(x=>x[1]==r&&x[0]==n)?padd:add).push((n==6||n==7?"":"+ ")+(c[r]>1&&n<=7?c[r]+" ":"")+RN[r])});rows.push({n,c,add,padd})}
 return hd("📖 O'yin qoidalari")+`<div class="tabs"><button class="${RFILT=="all"?"on":""}" onclick="RFILT='all';render()">Hammasi · ${ORDER.length}</button><button class="${RFILT=="tn"?"on":""}" onclick="RFILT='tn';render()">Aholi · ${na}</button><button class="${RFILT=="maf"?"on":""}" onclick="RFILT='maf';render()">Mafia · ${ORDER.length-na}</button></div>
<div class="rl">${list.map(r=>`<div class="rc ${maf(r)?"m":""}"><div class="im">${media("role_"+r)||REM[r]}</div><span class="nb">${SINCE[r]}</span><b>${RN[r]}</b></div>`).join("")}</div>
<div class="card" style="padding:0;overflow:hidden;margin-top:16px"><div class="row sp" style="padding:16px" onclick="TBL=!TBL;render()"><b style="font-size:19px">O'yinchilar soniga qarab rollar</b><span>${TBL?"▲":"▼"}</span></div>
${TBL?`<table><tr><th>O'YINCHI</th><th>ROLLAR</th><th>PRO XONA</th></tr>${rows.map(x=>`<tr><td><b>${x.n}</b></td><td>${x.add.join(" · ")||"—"}</td><td class="go">${x.padd.join(" · ")||"—"}</td></tr>`).join("")}</table>
<div class="mu" style="padding:14px"><b>Qolganlar</b> — Tinch aholi (6–12 kishida doim 4 ta). <b>Pro xona</b> — xonani Pro foydalanuvchi ochgan bo'lsa; oddiy xonada bu o'rinlar Tinch aholiga beriladi.</div>`:""}</div>`}
/* ---- reyting ---- */
function ratingView(){const R=RS;let h=hd("Reyting jadvali")+`<div class="tabs"><button class="${R.kind=="players"?"on":""}" onclick="RS.kind='players';rate();render()">👥 Top O'yinchilar</button><button class="${R.kind=="clans"?"on":""}" onclick="RS.kind='clans';rate();render()">⛺ Top Ovullar</button></div>`;
 if(R.kind=="players")h+=`<div class="pills">${[["week","Haftalik"],["season","3-mavsum"],["all","Umumiy"],["friends","Do'stlar"]].map(x=>`<button class="${R.scope==x[0]?"on":""}" onclick="RS.scope='${x[0]}';rate();render()">${x[1]}</button>`).join("")}</div>`;
 if(!RAT||RAT.kind!=R.kind)return h+'<div class="mu c">Yuklanmoqda...</div>';
 if(R.kind=="clans")return h+'<div class="mu" style="margin:6px 4px">ⓘ Ovullar umumiy ochkolar bo\'yicha tartiblangan</div>'+(RAT.clans.length?RAT.clans.map(c=>`<div class="cl ${c.rank<6?"r"+c.rank:""}"><div class="mu" style="font-size:20px;font-weight:800;width:18px">${c.rank}</div><div class="em">${ico("clan","⛺")}</div><div class="f" style="flex:1"><span class="mu" style="font-weight:800">⛺ OVUL</span><span class="tg">${e(c.tag)}</span><div style="font-size:21px;font-weight:800">${e(c.name)}</div><div class="mu">${c.n}/100 a'zo · ${c.lvl}-daraja</div></div><div style="text-align:right"><span class="bdg" style="background:rgba(226,180,79,.2);color:var(--go)">#${c.rank}</span><div class="go" style="font-size:26px;font-weight:900">${c.points}</div><div class="mu">ochko</div></div></div>`).join(""):'<div class="card mu c">Ovullar yo\'q</div>');
 if(R.scope=="week")h+=`<div class="mu c" style="margin:6px 0">⏱ Dushanba kuni yangilanadi · <span id="rleft">${Math.ceil(rest(RAT,"left")/3600)}</span> soat qoldi</div><div class="prize">👑 Hafta g'olibiga <b>1 haftalik Pro</b> obunasi sovg'a qilinadi!</div>`;
 const P=RAT.players;if(!P.length)return h+'<div class="card mu c">Hali natija yo\'q</div>';
 const pd=(p,i)=>p?`<div class="p${i}">${i==1?'<div style="font-size:30px;position:absolute;top:-24px;left:0;right:0">👑</div>':""}<span class="rk">${i}-o'rin</span>${av(p.ph,p.name,p.pro?"pro":"")}<b>${e(p.name)}</b><div class="mu" style="font-size:13px">${p.g} o'yin · ${p.wr}%</div><div class="sc">${p.pts}</div><div class="mu" style="font-size:12px">BALL</div></div>`:"<div></div>";
 h+=`<div class="pod">${pd(P[1],2)}${pd(P[0],1)}${pd(P[2],3)}</div>`;
 h+=P.slice(3).map((p,i)=>`<div class="lr"><span class="no">${i+4}</span>${av(p.ph,p.name,p.pro?"pro":"")}<div style="min-width:0"><b>${e(p.name)}</b><div class="mu">${p.g} o'yin · ${p.wr}% g'alaba</div></div><div class="sc">${p.pts}<small>ball</small></div></div>`).join("");return h}
/* ---- profil / sozlamalar / do'stlar ---- */
function profileView(){const link="https://t.me/"+BOT+"?start=ref_"+ME.id,wr=ME.games?Math.round(100*ME.wins/ME.games):0,sv=ME.games?Math.round(100*(ME.survived||0)/ME.games):0;
 return hd("Profil")+`<div class="card" style="padding:18px"><div class="row"><div>${av(ME.photo,ME.name,"big-av "+(ME.pro?"pro":""))}</div><div class="f"><div style="font-size:24px;font-weight:800;line-height:1.15">${e(ME.name)}</div><div class="mu" style="font-size:17px">ID ${ME.id}${ME.clan?" · "+e(ME.clan):""}</div></div>
<button class="ib" style="flex:0 0 58px;height:58px;aspect-ratio:auto;border-radius:16px" onclick="toast('Yutuqlar tez orada')">📜</button><button class="ib" style="flex:0 0 58px;height:58px;aspect-ratio:auto;border-radius:16px" onclick="go('settings')">⚙️</button></div>
<div class="c" style="margin-top:14px"><span class="pill">${emj("trophy","🏆")} ${ME.points} ball <span class="mu">|</span> #${ME.rank}</span></div>
<div class="stat"><div><b>${ME.games}</b><span>O'yinlar</span></div><div><b class="gr">${ME.wins}</b><span>G'alabalar</span></div><div><b class="go">${wr}%</b><span>G'alaba foizi</span></div><div><b>${sv}%</b><span>Omon qolish</span></div></div></div>
<div class="card row sp"><div class="row"><span style="font-size:26px">🎙</span><b style="font-size:19px">Bepul ovozli o'yinlar</b></div><span class="bdg voice" style="font-size:17px;padding:6px 14px">${ME.free_voice} ta</span></div>
<div class="sec-t"><span>👥 Do'stlar</span></div><button class="nav" onclick="go('friends')"><span>🔍 O'yinchini izlash</span><span>›</span></button><button class="nav" onclick="go('friends')"><span>Do'stlarim <b style="color:var(--ac);margin-left:6px">${ME.friends||0}</b></span><span>›</span></button>
<div class="sec-t"><span>👥 Do'stlarni taklif qiling</span></div><div class="card"><div style="font-size:16px;line-height:1.5" class="mu">Havolangiz orqali kelgan <b style="color:var(--tx)">25 ta yangi o'yinchi</b> 3 ta o'yinni oxirigacha o'ynasa — sizga <b style="color:var(--tx)">1 oylik Pro</b> obunasi beriladi.</div>
<div class="lnk" style="margin-top:14px"><span>🔗 ${e(link)}</span><b onclick="copy('${e(link)}')">Nusxalash</b></div><div class="stat" style="grid-template-columns:1fr 1fr 1fr"><div style="padding:10px"><b style="font-size:26px">${ME.ref_all||0}</b><span style="font-size:13px">Taklif qilingan</span></div><div style="padding:10px"><b class="gr" style="font-size:26px">${ME.ref_ok||0}</b><span style="font-size:13px">3 o'yin o'ynagan</span></div><div style="padding:10px"><b class="go" style="font-size:26px">${Math.floor((ME.ref_ok||0)/25)}</b><span style="font-size:13px">Mukofot</span></div></div></div>
<button class="btn gold" onclick="MODAL='pro';render()">${ME.pro?"👑 Pro faol. Uzaytirish":"👑 Play Pro olish"}</button>${OWN?'<button class="btn sec" style="margin-top:10px" onclick="bot(\'admin\')">Owner paneli</button>':""}`}
function copy(t){try{navigator.clipboard.writeText(t);toast("Nusxalandi")}catch(x){toast(t)}}
function bot(p){const u="https://t.me/"+BOT+"?start="+p;tg?tg.openTelegramLink(u):window.open(u)}
function pref(k,v){send({t:"setprefs",k,v});ME.prefs[k]=v;render()}
function settingsView(){const P=ME.prefs||{},cb=(k,t,d,pro)=>`<div class="row" style="padding:12px 0;align-items:flex-start" onclick="${pro&&!ME.pro?"MODAL='pro';render()":`pref('${k}',${P[k]?0:1})`}"><span class="cb ${P[k]?"on":""}">${P[k]?"✓":""}</span><div class="f"><b style="font-size:18px">${t}</b>${pro?' <span class="bdg pro">Pro</span>':""}<div class="mu">${d}</div></div></div>`;
 const seg=(k,arr)=>`<div class="seg">${arr.map(x=>`<button class="${P[k]==x[0]?"on":""}" onclick="pref('${k}','${x[0]}')">${x[1]}</button>`).join("")}</div>`;
 return hd("Sozlamalar")+`<div class="c">${av(ME.photo,ME.name,"big-av")}</div><label class="field">Ism</label><input id="nm" value="${e(ME.name)}" maxlength="24" onchange="if(this.value.trim()){send({t:'setname',name:this.value.trim()});toast('Saqlandi')}">
<div class="field go">👑 ${e(BRD)} Pro</div><div class="card"><div class="mu" style="font-size:16px">Pro a'zolarning nomi oldida maxsus belgi, avatari atrofida ramka va profilida qisqa ma'lumot ko'rsatadi.</div><button class="btn gold" style="margin-top:12px" onclick="MODAL='pro';render()">👑 Pro olish</button></div>
<div class="field">🎨 Mavzu</div><div class="card"><div class="seg">${[["auto","Avto"],["light","Kunduz"],["dark","Tun"]].map(x=>`<button class="${TH==x[0]?"on":""}" onclick="setTheme('${x[0]}')">${x[1]}</button>`).join("")}</div></div>
<div class="field">💬 Bildirishnomalar</div><div class="card">${cb("n_start","O'yin boshlanayotganda xabar berish","Xonada o'yin boshlanishiga bir necha kishi yetmay qolganda bot sizga yozadi.")}
<div class="row sp" style="padding:10px 0;border-top:1px solid var(--bd)"><b style="font-size:17px">Xabarlar orasidagi vaqt</b><select style="width:160px" onchange="pref('gap',+this.value)">${[1,3,6,12,24].map(h=>`<option value="${h}" ${P.gap==h?"selected":""}>${h} soat${h==6?" (standart)":""}</option>`).join("")}</select></div>
<div style="border-top:1px solid var(--bd)">${cb("n_friend","Do'stlar taklifi","Do'stlaringiz sizni o'z xonasiga chaqirganda bot sizga yozadi.")}</div>
<div style="border-top:1px solid var(--bd)">${cb("n_priv","Shaxsiy xabarlar","Ilovada bo'lmaganingizda do'stingiz yozsa, bot sizga Telegramda xabar beradi.",1)}</div></div>
<div class="field">🎧 Ovoz ulanishi</div><div class="card"><b style="font-size:18px">Ovozli xonaga ulanish usuli:</b><div style="height:10px"></div>${seg("voice",[["auto","Avtomatik"],["direct","Bevosita"],["proxy","Proksi orqali"]])}<div class="mu c" style="margin-top:8px">Agar bevosita ulanmasa, avtomatik proksi orqali</div></div>
<div class="field">🛡 Maxfiylik</div><div class="card"><b style="font-size:18px">Ishtirokimdagi o'yinlardan parchalar Instagramga joylanishiga:</b><div class="mu" style="margin:6px 0 10px">Ovozli o'yinlardan ba'zi qiziq lavhalar rasmiy sahifaga qo'yilishi mumkin.</div>
<div class="seg"><button class="${P.insta?"on":""}" style="${P.insta?"background:#12331f;border-color:var(--gr);color:var(--gr)":""}" onclick="pref('insta',1)">✓ Roziman</button><button class="${!P.insta?"on":""}" onclick="pref('insta',0)">Rozi emasman</button></div></div>`}
function friendsView(){let h=hd("Do'stlar")+`<div class="srch"><input id="fq" placeholder="Ism yoki ID bo'yicha izlash" onkeydown="if(event.key=='Enter')fsr()"></div><div style="height:8px"></div><button class="btn sec" onclick="fsr()">Izlash</button>`;
 if(FS)h+=`<div class="sec-t"><span>Natijalar</span></div>`+(FS.length?FS.map(f=>`<div class="lr">${av(f.ph,f.name)}<div><b>${e(f.name)}</b><div class="mu">ID ${f.id}</div></div>${f.fr?'<span class="mu" style="margin-left:auto">Do\'st</span>':`<button class="btn sm" style="margin-left:auto" onclick="send({t:'fadd',id:${f.id}});toast('Qo\\'shildi')">Qo'shish</button>`}</div>`).join(""):'<div class="mu c">Topilmadi</div>');
 h+=`<div class="sec-t"><span>Do'stlarim</span><span>${FR?FR.length:0}</span></div>`;
 h+=FR==null?'<div class="mu c">Yuklanmoqda...</div>':FR.length?FR.map(f=>`<div class="lr">${av(f.ph,f.name)}<div><b>${e(f.name)}</b><div class="mu">${f.on?"🟢 onlayn":"oflayn"} · ${f.points} ball</div></div><button class="btn sec sm" style="margin-left:auto" onclick="if(confirm('Do\\'stlikdan chiqarilsinmi?'))send({t:'fdel',id:${f.id}})">✕</button></div>`).join(""):'<div class="card mu c">Hali do\'stlar yo\'q</div>';return h}
function fsr(){const v=$("fq").value.trim();if(v)send({t:"fsearch",q:v})}
/* ---- xona ---- */
function fmt2(s){s=Math.max(0,Math.floor(s));return String(Math.floor(s/60)).padStart(2,"0")+":"+String(s%60).padStart(2,"0")}
function pick(id){const R=ROOM,m=R.me;if(!m.alive||R.spec)return;
 if(R.phase=="night"&&(MT.includes(m.role)||m.role=="doktor"||m.role=="izquvar")){if(m.act)return;SEL=SEL==id?null:id;render()}
 else if(R.phase=="vote"){if(m.nov)return toast("Muhokamada qatnashmaganingiz uchun bugun ovoz bera olmaysiz");send({t:"vote",to:id})}}
function confirmAct(){if(SEL)send({t:"act",to:SEL})}
function undoAct(){SEL=null;send({t:"unact"})}
function hint(){const R=ROOM,m=R.me;if(R.spec)return"Siz kuzatuvchisiz";if(!m.alive)return R.phase=="ended"?"":"Siz o'yindan chiqdingiz. Kuzatishingiz mumkin";
 if(R.phase=="night"&&!(MT.includes(m.role)||m.role=="doktor"||m.role=="izquvar"))return"Tun. Hamma uxlayapti";return""}
function canChat(){const R=ROOM;if(!R||R.spec)return false;if(R.phase=="last")return R.speaker==ME.id;if(R.phase=="night")return R.me.alive&&MT.includes(R.me.role);return true}
const SI=t=>/otib tashladi/.test(t)?emj("kill","🔫"):/davola|qutqar/.test(t)?emj("heal","🩺"):/ovoz/i.test(t)?emj("vote","🗳"):/^Tun/.test(t)?emj("night","🌙"):/kun/.test(t)?emj("day","☀️"):/chiqar|chiqib/.test(t)?"🚪":"ℹ️";
function chatHtml(){const R=ROOM,P={};R.players.forEach(p=>P[p.id]=p);
 return R.chat.map(c=>{if(c.sys)return`<div class="sysl">${SI(c.t)} ${e(c.t)}</div>`;const mine=c.uid==ME.id,p=P[c.uid]||{};return`<div class="m ${mine?"me":""} ${c.ch}">${av(p.ph,c.u,p.pro?"pro":"")}<div class="bub">${!mine?`<div class="nm">${e(c.u)}${c.ch=="mafia"?" 🔪":c.ch=="dead"?" 👻":""}</div>`:""}${c.re?`<div class="qt"><b>${e(c.re.u)}</b><span>${e(c.re.t)}</span></div>`:""}<div>${e(c.t)}</div></div>${R.spec?"":`<button class="rp" onclick="rep(${c.id})">↩</button>`}</div>`}).join("")||'<div class="sysl">Chat bo\'sh</div>'}
function nightHtml(R,m){const mt=MT.includes(m.role),act=m.alive&&!R.spec&&(mt||m.role=="doktor"||m.role=="izquvar");
 const ttl=mt?"KIMNI YO'Q QILAMIZ?":m.role=="doktor"?"KIMNI DAVOLAYMIZ?":m.role=="izquvar"?"KIMNI TEKSHIRAMIZ?":"TUN. SHAHAR UXLAYAPTI";
 const am=R.players.filter(p=>p.alive&&MT.includes(p.role)).length||1;
 const cells=R.players.map(p=>{const sel=SEL==p.id||m.act==p.id,tm=mt&&MT.includes(p.role);
  const tag=p.id==ME.id?'<span class="tg">Siz</span>':tm?`<span class="tg">${RN[p.role]}</span>`:"";
  const dot=p.mv?`<i class="dt ${p.mv>=am?"g":p.mv>1?"lg":"b"}"></i>`:"";
  return`<div class="gc ${p.alive?"":"dead"} ${sel?"sel":""} ${tm||p.id==ME.id&&mt?"mf":""}" onclick="pick(${p.id})">${tag}${dot}${av(p.ph,p.name)}<span>${e(p.name)}</span></div>`}).join("");
 let h=`<div class="qt2">◇ ${ttl}</div><div class="grid7">${cells}</div>`;
 if(mt)h+=`<div class="leg"><span><i class="dt b"></i>bitta mafiya tanladi</span><span><i class="dt lg"></i>bir nechta kelishdi</span><span><i class="dt g"></i>barchasi kelishdi</span></div>`;
 if(act){if(m.act){const t=R.players.find(p=>p.id==m.act);
   h+=`<div class="info ok">✓ <span>Siz <b>${e(t?t.name:"")}</b>ni tanladingiz. Tunning tugashini kuting.</span></div><div class="row"><button class="btn gr f" onclick="undoAct()">✓ O'zgartirish</button><button class="rx" onclick="undoAct()">✕</button></div>`}
  else h+=`<div class="info">⚠ <span>${mt?"Tasdiqlash uchun barcha mafiya a'zolari bitta o'yinchini tanlashi kerak.":"Bitta o'yinchini tanlang va tasdiqlang."}</span></div><div class="row"><button class="btn gr f" ${SEL?"":"disabled"} onclick="confirmAct()">✓ Tasdiqlash</button><button class="rx" onclick="SEL=null;render()">✕</button></div>`}
 return h}
function voteHtml(R,m){const nv=m.nov&&m.alive;let h="";
 if(nv)h+=`<div class="info bad">⚠ <span>Muhokamada qatnashmaganingiz uchun bugun ovoz bera olmaysiz.</span></div>`;
 h+=`<div class="qt2 l">👥 KIMGA OVOZ BERASIZ?</div><div class="card vg"><div class="grid4">${R.players.filter(p=>p.alive).map(p=>`<div class="vc ${m.vote==p.id?"sel":""} ${p.id==ME.id||nv?"off":""}" onclick="pick(${p.id})">${p.v?`<span class="v">${p.v}</span>`:""}${av(p.ph,p.name)}<span>${e(p.name)}</span></div>`).join("")}</div></div>`;
 if(m.alive&&!R.spec)h+=`<button class="btn sec sm skp" style="${m.vote===0?"border-color:var(--ac)":""}" onclick="send({t:'vote',to:0})">${m.vote===0?"✓ ":""}Ovoz bermayman</button>`;
 return h}
function endHtml(R){return`<div class="card big">${R.winner=="none"?"🏁 O'yin bekor qilindi":"🏁 G'alaba: "+(R.winner=="town"?"Shahar":"Mafiya")}</div><div class="grid7" style="margin-top:14px">${R.players.map(p=>`<div class="gc ${p.alive?"":"dead"}">${p.role?`<span class="tg ${MT.includes(p.role)?"":"tn"}">${RN[p.role]}</span>`:""}${av(p.ph,p.name)}<span>${e(p.name)}</span></div>`).join("")}</div>`}
function roomView(){const R=ROOM,m=R.me,own=R.owner==ME.id&&!R.spec,n=R.players.filter(p=>!p.left).length,pl=R.phase=="lobby",ph=R.phase;
 const spk=`<button class="spk" onclick="SPK=!SPK;voiceSync();render()">${SPK?"🔊":"🔇"}</button>`;
 const op=R.players.find(p=>p.id==ME.id)||R.players.find(p=>p.id==R.owner)||{};
 const ttl=pl?e(R.name):ph=="intro"?"1-kun":ph=="night"?R.night+"-tun":ph=="last"?"Oxirgi so'z":ph=="ended"?"Tugadi":(R.day+1)+"-kun";
 const sub=pl?`👥 ${n}/${R.max} o'yinchi · Kod: ${R.id}`:ph=="intro"?"Tanishuv":ph=="day"?"Muhokama":ph=="vote"?"Ovoz berish":"";
 let h=`<div class="rh">${tg?"":'<button class="back" style="float:left" onclick="back()">← Orqaga</button>'}<b>${ttl}</b>${!pl&&R.deadline?' <span class="tp" id="tm"></span>':""}${sub?`<div class="mu rs">${sub}</div>`:""}</div>`;
 if(pl){h+=`<div class="row sp rbar"><div class="rav">${av(op.ph,op.name)}</div>${spk}</div><div class="prow">${R.players.filter(p=>!p.left).map(p=>`<div class="pcd">${av(p.ph,p.name,p.pro?"pro":"")}<span>${e(p.name)}</span></div>`).join("")}</div>
<div class="row ctl">${own?`<button class="btn f" ${n<R.minp?"disabled":""} onclick="send({t:'start'})">✓ Boshlash (${n}/${R.minp}+)</button>`:'<div class="mu f c">Xona egasi o\'yinni boshlashini kuting</div>'}<button class="btn wh f" onclick="MODAL='invite';FR=null;send({t:'friends'});render()">➤ Taklif yuborish</button></div>`}
 else if(ph=="night")h+=nightHtml(R,m);
 else if(ph=="vote")h+=voteHtml(R,m);
 else if(ph=="ended")h+=endHtml(R);
 else{const sp=R.players.find(p=>p.id==R.speaker);
  const bn=ph=="intro"?e(R.q||"Salom! O'zingizni tanishtiring."):ph=="day"?"Muhokama: kim mafiya? Fikringizni yozing.":sp?`<b>${e(sp.name)}</b> o'yindan chiqarildi va oxirgi so'zini aytmoqda.`:"";
  h+=`<div class="row br"><div class="rav">${av(op.ph,op.name)}</div><div class="qb ${ph=="last"?"x":""}"><span>${ph=="last"?"✕":"💬"}</span><div>${bn}</div></div>${spk}</div>`;
  if(ph=="day"&&m.alive&&!R.spec)h+=`<button class="btn sec sm skp" onclick="send({t:'skipday'})" ${m.skipped?"disabled":""}>⏭ Muhokamani tugatish (${R.skip}/${Math.floor(R.players.filter(p=>p.alive).length/2)+1})</button>`;
  if(ph=="last"&&R.speaker==ME.id)h+=`<button class="btn skp" onclick="send({t:'lastdone'})">✓ Tugatdim</button>`}
 const hi=hint();if(hi&&!pl)h+=`<div class="hint">${hi}</div>`;
 if(R.priv.length)h+=`<div class="card">${R.priv.map(x=>`<div><b>🔍 ${e(x)}</b></div>`).join("")}</div>`;
 if(ph=="ended")h+=`<button class="btn" style="margin-bottom:10px" onclick="send({t:'leave'})">Chiqish</button>`;
 if(ph!="vote"||true)h+=`<div id="chat" class="${pl?"lb":ph=="night"?"pk":ph=="vote"?"vt":ph=="ended"?"vt":""}">${chatHtml()}</div>`;
 if(canChat()&&!COMPOSE)h+=`<button class="fab ${ph=="night"?"rd2":""}" onclick="COMPOSE=true;render();setTimeout(()=>$('ci')&&$('ci').focus(),50)">✎</button>`;
 return h}
/* ---- Ovul ---- */
const AD_ALL=["banner_pro","banner_ad","ad_1","ad_2","ad_3","ad_4","ad_5","logo"];
const adList=()=>AD_ALL.filter(k=>A[k]);
function heroHtml(){const L=adList();if(!L.length)return A.bg_lobby?media("bg_lobby"):'<div class="logo">'+e(BRD.toUpperCase())+'</div>';return media(L[BN%L.length])}
function adClick(){const L=adList();const k=L[BN%L.length];if(!k)return;if(k=="banner_pro"){MODAL="pro";render();return}const u=ADS[k];if(!u)return;
 if(/^https:\/\/t\.me\//.test(u)&&tg&&tg.openTelegramLink)tg.openTelegramLink(u);else if(tg&&tg.openLink)tg.openLink(u);else window.open(u)}
setInterval(()=>{const h=$("hero");if(!h||ROOM||PAGE!="home"||MODAL)return;if(adList().length<2)return;if(++adT>=AD_SECS){adT=0;BN++;h.innerHTML=heroHtml()}},1000);
function emj(k,fb){const u=A["emo_"+k];return u?(u.includes(".webm")?`<video class="emj" src="${u}" autoplay loop muted playsinline></video>`:`<img class="emj" src="${u}">`):fb}
function openClan(){if(!ME.pro){MODAL="pro"}else MODAL="clancreate";render()}
function clansRows(){if(!RAT||RAT.kind!="clans")return'<div class="mu c">Yuklanmoqda...</div>';const q=CQ.trim().toLowerCase();
 const L=RAT.clans.filter(c=>!q||c.name.toLowerCase().includes(q)||c.tag.toLowerCase().includes(q));
 return L.length?L.map(c=>`<div class="cr"><div class="em">${ico("clans","⛺")}</div><div class="f" style="flex:1;min-width:0"><div class="cn"><span class="mu">#${c.rank}</span> <b>${e(c.name)}</b> <span class="tg c${c.rank%5}">${e(c.tag)}</span></div><div class="mu" style="font-size:13px">${c.points} ochko · ${c.n} a'zo · ${c.open?"ochiq":"to'la"}</div></div>${c.id==RAT.my?'<span class="mu">Sizniki</span>':c.open?`<button class="btn sm" onclick="CV=RAT.clans.find(x=>x.id==${c.id});MODAL='clanview';render()">Ko'rish</button>`:'<span class="mu">To\'la</span>'}</div>`).join(""):'<div class="card mu c">Ovul topilmadi</div>'}
function clansList(){const x=$("cls");if(x)x.innerHTML=clansRows()}
function clansView(){return hd("Ovul")+`<div class="chero"><b>${ico("clans","⛺")} O'z ovulingizni yarating</b><p>Do'stlaringizni yig'ing va o'z davrangizda birga o'ynang.</p><div class="cf"><span>💬 Ovul suhbati</span><span>➤ Xonaga chaqiruv</span><span>🎮 Ovul o'yinlari</span><span>◎ Teg va emblema</span></div><button class="btn wh" onclick="${ME.clan?"toast('Siz allaqachon ovuldasiz')":"openClan()"}">＋ Ovul yaratish <span class="bdg pro">Pro</span></button></div>
<div class="srch"><input placeholder="Ovul nomi yoki tegi..." value="${e(CQ)}" oninput="CQ=this.value;clansList()"></div><div class="sec-t"><span>⛺ Faol ovullar</span></div><div id="cls">${clansRows()}</div>`}
function barRender(){const b=$("bar");if(!ROOM||!canChat()||!COMPOSE){b.style.display="none";b.innerHTML="";return}
 if(b.style.display=="block"&&$("ci")){const r=b.querySelector(".rep");if(!!r==!!REPLY)return}
 const v=$("ci")?$("ci").value:"";b.style.display="block";
 b.innerHTML=(REPLY?`<div class="rep"><span><b>${e(REPLY.u)}</b> ${e(REPLY.t)}</span><button onclick="REPLY=null;barRender()">✕</button></div>`:"")+`<div class="in"><input id="ci" maxlength="200" placeholder="Xabar" onkeydown="if(event.key=='Enter')sc()"><button class="btn" onclick="sc()">Yuborish</button></div>`;$("ci").value=v}
function rep(id){const c=ROOM.chat.find(x=>x.id==id);if(!c)return;REPLY={id:c.id,u:c.u,t:c.t.slice(0,50)};COMPOSE=true;render();setTimeout(()=>$("ci")&&$("ci").focus(),50)}
function sc(){const i=$("ci");if(i&&i.value.trim()){send({t:"chat",text:i.value,re:REPLY?REPLY.id:0});i.value="";REPLY=null;COMPOSE=false;render()}}
setInterval(()=>{const t=$("tm");if(t&&ROOM&&ROOM.deadline)t.textContent=fmt2(ROOM.deadline-Math.floor(now()));
 const c=$("cool");if(c){const r=rest(ME,"cool");c.textContent=fmt(r);if(r<=0)render()}
 const tl=$("tleft");if(tl&&TASKS)tl.textContent=fmtH(rest(TASKS,"left"))},500);
/* ---- ovozli xona: WebRTC (mesh) ---- */
function stopVoice(){for(const i in PC){PC[i].close()}PC={};document.querySelectorAll("audio.rtc").forEach(a=>a.remove());if(LS){LS.getTracks().forEach(t=>t.stop());LS=null}}
async function voiceSync(){if(!ROOM||ROOM.mode!="voice"||ROOM.spec){if(LS)stopVoice();return}
 if(!LS){try{LS=await navigator.mediaDevices.getUserMedia({audio:true})}catch(x){toast("Mikrofon ruxsati kerak");return}}
 const ids=ROOM.players.filter(p=>p.id!=ME.id&&!p.left).map(p=>p.id);
 for(const id of ids)if(!PC[id])mkpc(id,ME.id<id);
 for(const id in PC)if(!ids.includes(+id)){PC[id].close();delete PC[id];const a=$("au"+id);if(a)a.remove()}
 const talk=ROOM.me.alive&&["lobby","intro","day","ended"].includes(ROOM.phase);LS.getAudioTracks().forEach(t=>t.enabled=talk);
 document.querySelectorAll("audio.rtc").forEach(a=>a.muted=ROOM.phase=="night"||!SPK)}
function mkpc(id,init){const pc=new RTCPeerConnection({iceServers:[{urls:"stun:stun.l.google.com:19302"}]});PC[id]=pc;
 LS.getTracks().forEach(t=>pc.addTrack(t,LS));pc.onicecandidate=ev=>ev.candidate&&send({t:"rtc",to:id,data:{c:ev.candidate}});
 pc.ontrack=ev=>{let a=$("au"+id);if(!a){a=document.createElement("audio");a.id="au"+id;a.className="rtc";a.autoplay=true;document.body.appendChild(a)}a.srcObject=ev.streams[0]};
 if(init)pc.onnegotiationneeded=async()=>{await pc.setLocalDescription(await pc.createOffer());send({t:"rtc",to:id,data:{d:pc.localDescription}})};return pc}
async function rtcIn(m){let pc=PC[m.from];if(!pc&&LS)pc=mkpc(m.from,false);if(!pc)return;const d=m.data||{};
 if(d.d){await pc.setRemoteDescription(d.d);if(d.d.type=="offer"){await pc.setLocalDescription(await pc.createAnswer());send({t:"rtc",to:m.from,data:{d:pc.localDescription}})}}
 else if(d.c)try{await pc.addIceCandidate(d.c)}catch(x){}}
SPARAM=(tg&&tg.initDataUnsafe&&tg.initDataUnsafe.start_param)||q.get("sp")||"";
connect();
</script></body></html>"""

# ============================ BOT ============================
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML)); dp = Dispatcher()
PEND = {}
OW = F.from_user.id.in_(OWNERS)
adm = Router(); adm.message.filter(OW); adm.callback_query.filter(OW)
BACK = [B(text="Orqaga", callback_data="home")]
PLANS = {"1": 30, "3": 90, "6": 180, "12": 365}

# ---------- avto-zaxira ----------
async def send_backup(tag="avto"):
    from aiogram.types import FSInputFile
    tmp = DATA / "backup.db"; dst = sqlite3.connect(tmp); DB.backup(dst); dst.close()
    for o in OWNERS:
        try: await bot.send_document(o, FSInputFile(tmp, filename=f"bc-{time.strftime('%Y%m%d-%H%M')}.db"),
                                     caption=f"Zaxira ({tag}). Tiklash: shu faylni 'restore' izohi bilan yuboring.")
        except Exception: pass

async def backup_loop():
    while True:
        await asyncio.sleep(6 * 3600); await send_backup()

@adm.message(Command("backup"))
async def _bk(m): await send_backup("qo'lda")

@adm.message(F.document, F.caption == "restore")
async def _restore(m):
    tmp = DATA / "restore.db"; await bot.download(m.document.file_id, destination=tmp)
    try:
        src = sqlite3.connect(tmp); src.backup(DB); src.close(); await m.answer("Baza tiklandi.")
    except Exception as ex: await m.answer(f"Xato: {html.escape(str(ex))}")

def webkb():
    rows = [[B(text="🎮 O'ynash", web_app=WebAppInfo(url=WEBAPP_URL))]]
    if S("channel_url"): rows.append([B(text="📢 Rasmiy kanal", url=S("channel_url"))])
    if S("chat_url"): rows.append([B(text="👥 Mafia Chat", url=S("chat_url"))])
    return KB(inline_keyboard=rows)
def plans_kb(): return KB(inline_keyboard=[[B(text=f"{m} oy - {S('star_' + m)} Stars", callback_data="buy:" + m)] for m in PLANS])

async def show_plans(m): await m.answer(f"{em('emo_crown')}<b>{S('brand')} Pro</b>\nObuna muddatini tanlang:", reply_markup=plans_kb())

@dp.message(CommandStart())
async def start(m):
    sql("INSERT OR IGNORE INTO users(id,name,created,free_voice) VALUES(?,?,?,?)",
        (m.from_user.id, m.from_user.full_name, int(time.time()), S("free_voice")))
    arg = (m.text.split(maxsplit=1) + [""])[1]
    set_ref(m.from_user.id, arg)
    if arg == "pro": return await show_plans(m)
    if arg == "admin" and m.from_user.id in OWNERS: return await m.answer("Owner paneli", reply_markup=home_kb())
    ch = S("channel_url"); chn = ("@" + ch.rstrip("/").split("/")[-1]) if ch.startswith("https://t.me/") else ""
    txt = (f"🎭 <b>{html.escape(S('brand'))}</b>'ga xush kelibsiz, {html.escape(m.from_user.full_name or '')}!\n\n"
           f"{em('emo_night', '🌙')}Kechasi mafia ov qiladi, {em('emo_day', '☀️')}kunduzi aholi mafiani izlaydi.\n"
           f"{em('emo_clan', '👥')}6–16 o'yinchi · {em('emo_check', '🕵️')}yashirin rollar · {em('emo_vote', '🗳')}ovoz berish\n\n"
           "▶️ Boshlash uchun quyidagi tugmani bosing.\n\n"
           f"{em('emo_clan', '👥')}Guruhimizdan o'yin boshlash uchun o'yinchi yig'ishda foydalanishingiz mumkin!"
           + (f"\n\n📢 Yangiliklar va e'lonlar: {chn}" if chn else ""))
    await m.answer(txt, reply_markup=webkb())

@dp.message(Command("pro"))
async def pro_cmd(m): await show_plans(m)

@dp.callback_query(F.data.startswith("buy:"))
async def buy(c):
    mo = c.data.split(":")[1]
    await bot.send_invoice(c.from_user.id, title=S("brand") + " Pro", description=f"{mo} oylik Pro obuna", payload="pro:" + mo,
                           currency="XTR", prices=[LabeledPrice(label="Pro", amount=S("star_" + mo))], provider_token="")
    await c.answer()

@dp.pre_checkout_query()
async def pre(q): await q.answer(ok=True)

@dp.message(F.successful_payment)
async def paid(m):
    mo = m.successful_payment.invoice_payload.split(":")[1]; u = one("SELECT pro_until FROM users WHERE id=?", (m.from_user.id,))
    new = max(int(time.time()), u["pro_until"]) + PLANS[mo] * 86400
    sql("UPDATE users SET pro_until=? WHERE id=?", (new, m.from_user.id))
    await m.answer("Pro faollashdi. Rahmat!", reply_markup=webkb())
    await send(m.from_user.id, {"t": "me", "me": me_info(m.from_user.id)})

# ---------- owner paneli ----------
def home_kb():
    rows = [("Kontent (rasm / emoji / ovoz)", "cts"), ("Foydalanuvchilar", "us"), ("Klanlar", "cl"), ("Xonalar", "rm"),
            ("Sozlamalar", "st"), ("E'lon yuborish", "bc"), ("Statistika", "stat")]
    return KB(inline_keyboard=[[B(text=t, callback_data=d)] for t, d in rows])

async def show(c, text, kb=None):
    try: await c.message.edit_text(text, reply_markup=kb)
    except Exception: await c.message.answer(text, reply_markup=kb)
    await c.answer()

@adm.message(Command("admin"))
async def admin(m): PEND.pop(m.from_user.id, None); await m.answer("Owner paneli", reply_markup=home_kb())

@adm.callback_query(F.data == "home")
async def _home(c): PEND.pop(c.from_user.id, None); await show(c, "Owner paneli", home_kb())

@adm.callback_query(F.data == "cts")
async def _cts(c):
    await show(c, "Qaysi turdagi kontent?", KB(inline_keyboard=[[B(text="Rasmlar", callback_data="ct:img:0")],
               [B(text="Emojilar (rasm / premium)", callback_data="ct:emoji:0")], [B(text="Ovoz effektlari", callback_data="ct:snd:0")], BACK]))

@adm.callback_query(F.data.startswith("ct:"))
async def _ct(c):
    _, kind, pg = c.data.split(":"); pg = int(pg); per = 8
    keys = [k for k, (n, kd) in SLOTS.items() if kd == kind]; have = {r["key"] for r in allr("SELECT key FROM assets")}
    rows = [[B(text=SLOTS[k][0] + (" - bor" if k in have else ""), callback_data="slot:" + k)] for k in keys[pg * per:(pg + 1) * per]]
    nav = []
    if pg > 0: nav.append(B(text="Oldingi", callback_data=f"ct:{kind}:{pg - 1}"))
    if (pg + 1) * per < len(keys): nav.append(B(text="Keyingi", callback_data=f"ct:{kind}:{pg + 1}"))
    if nav: rows.append(nav)
    rows.append([B(text="Orqaga", callback_data="cts")]); await show(c, "Slotni tanlang:", KB(inline_keyboard=rows))

@adm.callback_query(F.data.startswith("slot:"))
async def _slot(c):
    k = c.data[5:]; PEND[c.from_user.id] = {"m": "asset", "k": k}
    hint = {"img": "Rasmni yuboring (sifat uchun fayl/hujjat sifatida yuborgan ma'qul).",
            "emoji": "Rasm, stiker yoki premium emoji yuboring.", "snd": "Audio yoki ovozli xabar yuboring (mp3/ogg)."}[SLOTS[k][1]]
    await c.message.answer(f"<b>{SLOTS[k][0]}</b>\n{hint}", reply_markup=KB(inline_keyboard=[BACK])); await c.answer()

async def save_asset(key, m):
    kind = SLOTS[key][1]; eid = None
    if kind == "img":
        if m.photo: fid, ext = m.photo[-1].file_id, "jpg"
        elif m.document and (m.document.mime_type or "").startswith("image"): fid, ext = m.document.file_id, (m.document.mime_type.split("/")[1] or "png").replace("jpeg", "jpg")
        else: return "Rasm yuboring."
    elif kind == "snd":
        if m.audio: fid, ext = m.audio.file_id, "mp3"
        elif m.voice: fid, ext = m.voice.file_id, "ogg"
        elif m.document and (m.document.mime_type or "").startswith("audio"): fid, ext = m.document.file_id, "mp3"
        else: return "Audio yuboring."
    elif m.photo or (m.document and (m.document.mime_type or "").startswith("image")) or (m.sticker and not m.sticker.custom_emoji_id):
        if m.photo: fid, ext = m.photo[-1].file_id, "jpg"
        elif m.document: fid, ext = m.document.file_id, (m.document.mime_type.split("/")[1] or "png").replace("jpeg", "jpg")
        else:
            st = m.sticker
            if st.is_video: fid, ext = st.file_id, "webm"
            elif st.is_animated and st.thumbnail: fid, ext = st.thumbnail.file_id, "webp"
            elif st.is_animated: return "Bu stiker animatsiyali, ishlamaydi."
            else: fid, ext = st.file_id, "webp"
    else:
        for en in (m.entities or []):
            if en.type == "custom_emoji": eid = en.custom_emoji_id; break
        if not eid and m.sticker and m.sticker.custom_emoji_id: eid = m.sticker.custom_emoji_id
        if not eid: return "Premium emoji topilmadi."
        st = (await bot.get_custom_emoji_stickers([eid]))[0]
        if st.is_animated and st.thumbnail: fid, ext = st.thumbnail.file_id, "webp"
        elif st.is_video: fid, ext = st.file_id, "webm"
        else: fid, ext = st.file_id, "webp"
    for old in (DATA / "assets").glob(key + ".*"): old.unlink()
    path = DATA / "assets" / f"{key}.{ext}"; await bot.download(fid, destination=path)
    sql("INSERT OR REPLACE INTO assets(key,path,ext,emoji_id,ts) VALUES(?,?,?,?,?)", (key, str(path), ext, eid, int(time.time())))
    return "Saqlandi."

@adm.callback_query(F.data == "st")
async def _st(c):
    rows = [[B(text=f"{k} = {S(k)}", callback_data="set:" + k)] for k in DEF]; rows.append(BACK)
    await show(c, "Sozlamalar (o'zgartirish uchun bosing):", KB(inline_keyboard=rows))

@adm.callback_query(F.data.startswith("set:"))
async def _set(c):
    k = c.data[4:]; PEND[c.from_user.id] = {"m": "set", "k": k}
    await c.message.answer(f"{k} uchun yangi qiymat yuboring (hozir: {S(k)})"); await c.answer()

def ucard(uid):
    u = one("SELECT * FROM users WHERE id=?", (uid,))
    if not u: return "Topilmadi", KB(inline_keyboard=[BACK])
    pro = "faol" if u["pro_until"] > time.time() else "yo'q"
    t = (f"ID {uid}\nIsm: {html.escape(u['name'] or '')}\nBall: {u['points']} | O'yin: {u['games']} | G'alaba: {u['wins']}\n"
         f"Pro: {pro} | Klan ID: {u['clan_id']} | Ban: {'ha' if u['banned'] else 'yo`q'}")
    return t, KB(inline_keyboard=[[B(text="Pro +30 kun", callback_data=f"ua:pro30:{uid}"), B(text="Pro +365 kun", callback_data=f"ua:pro365:{uid}")],
        [B(text="Pro olib tashlash", callback_data=f"ua:pro0:{uid}")], [B(text="Ban / Unban", callback_data=f"ua:ban:{uid}")],
        [B(text="Ball qo'shish", callback_data=f"ua:pts:{uid}"), B(text="Klanga qo'shish", callback_data=f"ua:clan:{uid}")], BACK])

@adm.callback_query(F.data == "us")
async def _us(c):
    PEND[c.from_user.id] = {"m": "uid"}; await show(c, "Foydalanuvchi ID raqamini yuboring:", KB(inline_keyboard=[BACK]))

@adm.callback_query(F.data.startswith("ua:"))
async def _ua(c):
    _, a, uid = c.data.split(":"); uid = int(uid)
    if a.startswith("pro"):
        d = int(a[3:]); u = one("SELECT pro_until FROM users WHERE id=?", (uid,))
        sql("UPDATE users SET pro_until=? WHERE id=?", (0 if d == 0 else max(int(time.time()), u["pro_until"]) + d * 86400, uid))
    elif a == "ban":
        sql("UPDATE users SET banned=1-banned WHERE id=?", (uid,))
        if one("SELECT banned FROM users WHERE id=?", (uid,))["banned"] and uid in HUB: await send(uid, {"t": "banned"})
    else:
        PEND[c.from_user.id] = {"m": a, "uid": uid}
        await c.message.answer("Qiymat yuboring" + (" (butun son, minus ham mumkin)" if a == "pts" else " (klan ID)")); return await c.answer()
    t, kb = ucard(uid); await show(c, t, kb)

@adm.callback_query(F.data == "cl")
async def _cl(c):
    rows = [f"{r['id']}. {r['name']} [{r['tag']}] - {r['points']} ball, a'zo: {one('SELECT COUNT(*) n FROM users WHERE clan_id=?', (r['id'],))['n']}" for r in allr("SELECT * FROM clans")]
    await show(c, "Klanlar:\n" + ("\n".join(rows) or "yo'q"), KB(inline_keyboard=[[B(text="Yangi klan", callback_data="cln"), B(text="Klanni o'chirish", callback_data="cld")], BACK]))

@adm.callback_query(F.data.in_({"cln", "cld"}))
async def _cln(c):
    new = c.data == "cln"; PEND[c.from_user.id] = {"m": "newclan" if new else "delclan"}
    await c.message.answer("Format: Nom | TEG | klan egasi ID" if new else "O'chiriladigan klan ID:"); await c.answer()

@adm.callback_query(F.data == "rm")
async def _rm(c):
    rows = [[B(text=f"{r.name} ({len(r.p)}) {r.phase} - yopish", callback_data="rc:" + r.id)] for r in ROOMS.values()]; rows.append(BACK)
    await show(c, f"Faol xonalar: {len(ROOMS)}", KB(inline_keyboard=rows))

@adm.callback_query(F.data.startswith("rc:"))
async def _rc(c): await close_room(c.data[3:]); await _rm(c)

@adm.callback_query(F.data == "stat")
async def _stat(c):
    n = lambda q: one(q)["n"]
    await show(c, f"Foydalanuvchilar: {n('SELECT COUNT(*) n FROM users')}\nPro: {one('SELECT COUNT(*) n FROM users WHERE pro_until>?', (int(time.time()),))['n']}\n"
                  f"O'yinlar: {n('SELECT COUNT(*) n FROM games')}\nOnlayn: {len(HUB)}\nFaol xonalar: {len(ROOMS)}\nKlanlar: {n('SELECT COUNT(*) n FROM clans')}",
               KB(inline_keyboard=[BACK]))

@adm.callback_query(F.data == "bc")
async def _bc(c):
    PEND[c.from_user.id] = {"m": "bc"}; await show(c, "E'lon xabarini yuboring (premium emoji va media saqlanadi):", KB(inline_keyboard=[BACK]))

@adm.callback_query(F.data == "bcgo")
async def _bcgo(c):
    p = PEND.pop(c.from_user.id, None)
    if not p or p.get("m") != "bcw": return await c.answer("Eskirgan")
    ok = 0
    for r in allr("SELECT id FROM users WHERE banned=0"):
        try: await bot.copy_message(r["id"], p["cid"], p["mid"]); ok += 1
        except Exception: pass
        await asyncio.sleep(0.05)
    await c.message.answer(f"Yuborildi: {ok}"); await c.answer()

@adm.message(F.func(lambda m: m.from_user.id in PEND))
async def _pend(m):
    uid = m.from_user.id; p = PEND[uid]; mo = p["m"]; txt = (m.text or "").strip()
    try:
        if mo == "photo":
            PEND.pop(uid)
            return await _photo_in(m) if (m.photo or m.sticker or m.document) else None
        if mo == "asset": PEND.pop(uid); return await m.answer(await save_asset(p["k"], m), reply_markup=KB(inline_keyboard=[[B(text="Kontent", callback_data="cts")]]))
        if mo == "set":
            v = "" if (txt == "-" and isinstance(DEF[p["k"]], str)) else type(DEF[p["k"]])(txt.replace(",", ".")); sql("INSERT OR REPLACE INTO settings(k,v) VALUES(?,?)", (p["k"], str(v))); PEND.pop(uid)
            return await m.answer(f"{p['k']} = {v}", reply_markup=KB(inline_keyboard=[[B(text="Sozlamalar", callback_data="st")]]))
        if mo == "uid":
            PEND.pop(uid); t, kb = ucard(int(txt)); return await m.answer(t, reply_markup=kb)
        if mo in ("pts", "clan"):
            v = int(txt)
            sql("UPDATE users SET points=points+? WHERE id=?" if mo == "pts" else "UPDATE users SET clan_id=? WHERE id=?", (v, p["uid"])); PEND.pop(uid)
            t, kb = ucard(p["uid"]); return await m.answer(t, reply_markup=kb)
        if mo == "newclan":
            n, tag, ow = [x.strip() for x in txt.split("|")]; sql("INSERT INTO clans(name,tag,owner_id,created) VALUES(?,?,?,?)", (n, tag, int(ow), int(time.time())))
            sql("UPDATE users SET clan_id=(SELECT MAX(id) FROM clans) WHERE id=?", (int(ow),)); PEND.pop(uid)
            return await m.answer("Klan yaratildi.", reply_markup=KB(inline_keyboard=[[B(text="Klanlar", callback_data="cl")]]))
        if mo == "delclan":
            sql("UPDATE users SET clan_id=0 WHERE clan_id=?", (int(txt),)); sql("DELETE FROM clans WHERE id=?", (int(txt),)); PEND.pop(uid)
            return await m.answer("O'chirildi.", reply_markup=KB(inline_keyboard=[[B(text="Klanlar", callback_data="cl")]]))
        if mo == "bc":
            PEND[uid] = {"m": "bcw", "cid": m.chat.id, "mid": m.message_id}
            return await m.answer("Hammaga yuboraymi?", reply_markup=KB(inline_keyboard=[[B(text="Yuborish", callback_data="bcgo")], BACK]))
    except Exception as ex:
        await m.answer(f"Xato: {html.escape(str(ex))}")

PHOTO_MEDIA = F.photo | F.sticker | (F.document & F.document.mime_type.startswith("image"))

def groups_kb(): return KB(inline_keyboard=[[B(text=n, callback_data=f"pg:{i}:0")] for i, (n, _) in enumerate(GROUPS)] + [BACK])

@adm.message(PHOTO_MEDIA)
async def _photo_in(m):
    PEND[m.from_user.id] = {"m": "photo", "msg": m}
    await m.answer("Bu rasm qayerga qo'yilsin?", reply_markup=groups_kb())

@adm.callback_query(F.data == "pgback")
async def _pgback(c): await show(c, "Bu rasm qayerga qo'yilsin?", groups_kb())

@adm.callback_query(F.data.startswith("pg:"))
async def _pg(c):
    _, gi, pg = c.data.split(":"); gi, pg = int(gi), int(pg); p = PEND.get(c.from_user.id)
    if not p or p.get("m") != "photo": return await c.answer("Avval rasm yuboring", show_alert=True)
    name, fn = GROUPS[gi]; keys = [k for k in SLOTS if fn(k)]; have = {r["key"] for r in allr("SELECT key FROM assets")}; per = 8
    rows = [[B(text=SLOTS[k][0] + (" - bor" if k in have else ""), callback_data="put:" + k)] for k in keys[pg * per:(pg + 1) * per]]
    nav = []
    if pg > 0: nav.append(B(text="Oldingi", callback_data=f"pg:{gi}:{pg - 1}"))
    if (pg + 1) * per < len(keys): nav.append(B(text="Keyingi", callback_data=f"pg:{gi}:{pg + 1}"))
    if nav: rows.append(nav)
    rows.append([B(text="Orqaga", callback_data="pgback")])
    await show(c, f"{name}: qaysi joyga?", KB(inline_keyboard=rows))

@adm.callback_query(F.data.startswith("put:"))
async def _put(c):
    p = PEND.get(c.from_user.id); key = c.data[4:]
    if not p or p.get("m") != "photo" or key not in SLOTS: return await c.answer("Avval rasm yuboring", show_alert=True)
    try: res = await save_asset(key, p["msg"])
    except Exception as ex: res = "Xato: " + html.escape(str(ex))
    PEND.pop(c.from_user.id, None)
    await c.message.answer(f"<b>{SLOTS[key][0]}</b>: {res}", reply_markup=KB(inline_keyboard=[[B(text="Kontent", callback_data="cts")]])); await c.answer()

dp.include_router(adm)

async def weekly_loop():
    """Har dushanba: o'tgan hafta g'olibiga 7 kunlik Pro."""
    while True:
        try:
            ws0 = week_start(); r0 = one("SELECT v FROM settings WHERE k='award_week'"); last = int(r0["v"]) if r0 else 0
            if last < ws0:
                top = one("SELECT uid,SUM(d) s FROM points_log WHERE ts>=? AND ts<? GROUP BY uid ORDER BY s DESC LIMIT 1", (ws0 - 7 * 86400, ws0))
                if last and top and top["s"] > 0:
                    u = one("SELECT pro_until FROM users WHERE id=?", (top["uid"],))
                    sql("UPDATE users SET pro_until=? WHERE id=?", (max(int(time.time()), u["pro_until"]) + 7 * 86400, top["uid"]))
                    try: await bot.send_message(top["uid"], "Tabriklaymiz! Siz hafta g'olibisiz: sizga 1 haftalik Pro berildi.")
                    except Exception: pass
                sql("INSERT OR REPLACE INTO settings(k,v) VALUES('award_week',?)", (str(ws0),))
        except Exception: pass
        await asyncio.sleep(1800)

async def main():
    global BOT_USERNAME
    BOT_USERNAME = (await bot.get_me()).username
    if WEBAPP_URL:
        await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="🎮 O'ynash", web_app=WebAppInfo(url=WEBAPP_URL)))
    asyncio.create_task(backup_loop()); asyncio.create_task(send_backup("ishga tushdi")); asyncio.create_task(weekly_loop())
    srv = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=PORT, log_level="warning"))
    await asyncio.gather(srv.serve(), dp.start_polling(bot))

if __name__ == "__main__":
    asyncio.run(main())
