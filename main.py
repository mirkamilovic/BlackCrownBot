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
WEBAPP_URL = os.environ.get("WEBAPP_URL", "").rstrip("/")
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
""")
def sql(q, a=()): c = DB.execute(q, a); DB.commit(); return c
def one(q, a=()): return DB.execute(q, a).fetchone()
def allr(q, a=()): return DB.execute(q, a).fetchall()

DEF = {"win_pts": 30, "lose_pts": 0, "leave_pen": 25, "kick_pen": 30, "open_mult": 1.5, "intro": 120, "night": 60,
       "vote": 40, "disc": 120, "star_1": 100, "star_3": 280, "star_6": 500, "star_12": 800, "free_voice": 10,
       "min_players": 6, "max_players": 16}
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
for _k, _n in [("night", "Tun"), ("day", "Kun"), ("kill", "O'ldirish"), ("heal", "Davolash"), ("check", "Tekshirish"),
               ("vote", "Ovoz"), ("crown", "Pro toj"), ("mic", "Mikrofon"), ("trophy", "Kubok"), ("clan", "Klan"),
               ("coin", "Ball")]: SLOTS["emo_" + _k] = ("Emoji: " + _n, "emoji")
for _k, _n in [("start", "O'yin boshlanishi"), ("night", "Tun"), ("day", "Kun"), ("kill", "O'ldirish"),
               ("heal", "Davolash"), ("vote", "Ovoz berish"), ("win", "G'alaba"), ("lose", "Mag'lubiyat"),
               ("click", "Tugma")]: SLOTS["snd_" + _k] = ("Ovoz: " + _n, "snd")

def amap(): return {r["key"]: f"/a/{r['key']}.{r['ext']}?v={r['ts']}" for r in allr("SELECT key,ext,ts FROM assets")}
def em(key):  # bot xabarlari uchun premium emoji (belgisiz bo'lsa bo'sh)
    r = one("SELECT emoji_id FROM assets WHERE key=?", (key,))
    return f'<tg-emoji emoji-id="{r["emoji_id"]}">.</tg-emoji> ' if r and r["emoji_id"] else ""

# ============================ O'YIN MOTORI ============================
HUB, ROOMS, UROOM = {}, {}, {}
LADDER = [(6, ["mafia", "doktor"]), (7, ["mafia"]), (8, ["izquvar"]), (9, ["mafiaboshi"]), (10, ["rais"]),
          (11, ["bezori"]), (12, ["qassob"]), (13, ["hamsoya"]), (14, ["folbin"]), (15, ["mirshab"]), (16, ["xufya"])]
IMPLEMENTED = {"tinch", "mafia", "mafiaboshi", "doktor", "izquvar"}  # qolganlarining qoidasi hali berilmagan

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

class Room:
    def __init__(s, owner, name, mode, closed, pro):
        s.id = os.urandom(3).hex(); s.owner = owner; s.name = name or "Xona"; s.mode = mode; s.closed = closed
        s.pro = pro; s.p = {}; s.phase = "lobby"; s.day = 0; s.night = 0; s.deadline = 0; s.chat = []
        s.act = {}; s.votes = {}; s.wake = asyncio.Event(); s.task = None; s.winner = None; s.priv = {}; s.log = []
    def add(s, uid, name): s.p[uid] = {"name": name, "alive": True, "role": None, "left": False}
    def alive(s): return [u for u, x in s.p.items() if x["alive"]]
    def team(s, u): return "mafia" if s.p[u]["role"] in MAFIA_TEAM else "town"
    def say(s, m): s.log.append({"m": m, "d": s.day})

    def visible(s, uid, c):
        me = s.p.get(uid); end = s.phase == "ended"
        if c["ch"] == "all": return True
        if c["ch"] == "mafia": return end or bool(me and me["role"] in MAFIA_TEAM)
        return end or bool(me and not me["alive"])

    def view(s, uid):
        me = s.p.get(uid); end = s.phase == "ended"; mt = bool(me and me["role"] in MAFIA_TEAM)
        pl = []
        for u, x in s.p.items():
            show = end or u == uid or (mt and x["role"] in MAFIA_TEAM)
            pl.append({"id": u, "name": x["name"], "alive": x["alive"], "left": x["left"],
                       "role": x["role"] if show else None, "v": sum(1 for t in s.votes.values() if t == u)})
        return {"t": "room", "id": s.id, "name": s.name, "mode": s.mode, "closed": s.closed, "pro": s.pro,
                "owner": s.owner, "phase": s.phase, "day": s.day, "deadline": s.deadline, "players": pl,
                "me": {"role": me["role"] if me else None, "alive": bool(me and me["alive"]),
                       "act": s.act.get(uid), "vote": s.votes.get(uid)},
                "chat": [c for c in s.chat if s.visible(uid, c)][-60:], "log": s.log[-8:],
                "priv": s.priv.get(uid, []), "winner": s.winner, "max": S("max_players"), "min": S("min_players")}

    async def push(s):
        for u in list(s.p):
            if not s.p[u]["left"]: await send(u, s.view(u))
    async def fx(s, k):
        for u in list(s.p): await send(u, {"t": "fx", "k": k})

    async def timed(s, secs):
        s.wake.clear(); s.deadline = int(time.time() + secs); await s.push()
        while time.time() < s.deadline and not s.wake.is_set():
            try: await asyncio.wait_for(s.wake.wait(), 1)
            except asyncio.TimeoutError: pass

    async def run(s):
        try:
            ids = list(s.p); roles = make_roles(len(ids), s.pro); random.shuffle(ids)
            for u, r in zip(ids, roles): s.p[u]["role"] = r
            for u in ids:
                sql("UPDATE users SET games=games+1 WHERE id=?", (u,))
                if s.mode == "voice" and not one("SELECT pro_until p FROM users WHERE id=?", (u,))["p"] > time.time():
                    sql("UPDATE users SET free_voice=MAX(0,free_voice-1) WHERE id=?", (u,))
            s.phase = "intro"; s.day = 0; s.say("O'yin boshlandi. Tanishuv."); await s.fx("start"); await s.timed(S("intro"))
            while True:
                s.night += 1; s.act = {}; s.phase = "night"; await s.fx("night")
                await s.timed(max(20, 70 - 10 * s.night)); await s.resolve_night()
                if s.check(): break
                s.day += 1; s.phase = "day"; await s.fx("day"); await s.timed(S("disc"))
                s.phase = "vote"; s.votes = {}; await s.fx("vote"); await s.timed(S("vote")); await s.resolve_vote()
                if s.check(): break
            await s.finish()
        except asyncio.CancelledError:
            pass

    async def resolve_night(s):
        s.priv = {}
        kills = [t for u, t in s.act.items() if s.p[u]["alive"] and s.p[u]["role"] in MAFIA_TEAM and t in s.p]
        victim = None
        if kills:
            c = {}
            for t in kills: c[t] = c.get(t, 0) + 1
            top = max(c.values()); victim = random.choice([t for t, v in c.items() if v == top])
        healed = {t for u, t in s.act.items() if s.p[u]["alive"] and s.p[u]["role"] == "doktor"}
        for u, t in s.act.items():
            if s.p[u]["alive"] and s.p[u]["role"] == "izquvar" and t in s.p:
                r = s.p[t]["role"]; ism = r in MAFIA_TEAM and r != "mafiaboshi"
                s.priv.setdefault(u, []).append(f"{s.p[t]['name']}: " + ("Mafia" if ism else "Mafia emas"))
        if victim is not None and victim not in healed:
            s.p[victim]["alive"] = False; s.say(f"Tunda {s.p[victim]['name']} o'ldirildi."); await s.fx("kill")
        elif victim is not None:
            s.say(f"Mafiya {s.p[victim]['name']}ni tanlagan edi, lekin Doktor uni davoladi. Bu tun hech kim o'lmadi."); await s.fx("heal")
        else:
            s.say("Bu tun hech kim o'lmadi.")

    async def resolve_vote(s):
        c = {}
        for u, t in s.votes.items():
            if s.p.get(u, {}).get("alive") and t in s.p: c[t] = c.get(t, 0) + 1
        if not c: s.say("Ovoz berilmadi, hech kim chiqarilmadi."); return
        top = max(c.values()); w = [t for t, v in c.items() if v == top]
        if len(w) > 1: s.say("Ovozlar teng bo'ldi, hech kim chiqarilmadi."); return
        s.p[w[0]]["alive"] = False; s.say(f"Muhokama natijasi: {s.p[w[0]]['name']} chiqarildi.")

    def check(s):
        a = s.alive(); m = sum(1 for u in a if s.p[u]["role"] in MAFIA_TEAM); t = len(a) - m
        if m == 0: s.winner = "town"
        elif m >= t: s.winner = "mafia"
        return bool(s.winner)

    async def finish(s):
        s.phase = "ended"; s.deadline = 0; mult = 1 if s.closed else S("open_mult")
        for u, x in s.p.items():
            if x["left"]: continue
            win = s.team(u) == s.winner
            d = int((S("win_pts") if win else S("lose_pts")) * mult)
            sql("UPDATE users SET points=points+?,wins=wins+? WHERE id=?", (d, int(win), u))
            cl = one("SELECT clan_id FROM users WHERE id=?", (u,))
            if cl and cl["clan_id"]: sql("UPDATE clans SET points=points+? WHERE id=?", (d, cl["clan_id"]))
        sql("INSERT INTO games(room,players,winner,days,ts) VALUES(?,?,?,?,?)",
            (s.id, len(s.p), s.winner, s.day, int(time.time())))
        s.say("G'alaba: " + ("Shahar" if s.winner == "town" else "Mafiya")); await s.fx("win"); await s.push()
        asyncio.create_task(s.cleanup())

    async def cleanup(s):
        await asyncio.sleep(90)
        if ROOMS.get(s.id) is not s: return
        for u in list(s.p):
            if UROOM.get(u) == s.id: UROOM.pop(u, None); await send(u, lobby())
        ROOMS.pop(s.id, None); await lobby_all()

def lobby():
    return {"t": "lobby", "rooms": [{"id": r.id, "name": r.name, "n": len(r.p), "max": S("max_players"), "mode": r.mode,
            "pro": r.pro, "phase": r.phase, "day": r.day, "alive": len(r.alive()),
            "host": r.p.get(r.owner, {}).get("name", "")} for r in ROOMS.values() if not r.closed]}
async def lobby_all():
    m = lobby()
    for u in list(HUB):
        if u not in UROOM: await send(u, m)

async def leave(uid):
    r = ROOMS.get(UROOM.pop(uid, None))
    if not r: return
    if r.phase in ("lobby", "ended"):
        r.p.pop(uid, None)
        if not r.p: ROOMS.pop(r.id, None)
        else:
            if r.owner == uid: r.owner = next(iter(r.p))
            await r.push()
    else:
        x = r.p[uid]
        if x["alive"]:
            x["alive"] = False; r.say(f"{x['name']} o'yindan chiqib ketdi.")
            sql("UPDATE users SET points=points-? WHERE id=?", (S("leave_pen"), uid))
        x["left"] = True
        if all(v["left"] for v in r.p.values()):
            if r.task: r.task.cancel()
            ROOMS.pop(r.id, None)
        else: await r.push()
    await lobby_all()

async def close_room(rid):
    r = ROOMS.pop(rid, None)
    if not r: return
    if r.task: r.task.cancel()
    for u in list(r.p):
        UROOM.pop(u, None); await send(u, lobby())

def me_info(uid):
    u = one("SELECT * FROM users WHERE id=?", (uid,))
    c = one("SELECT name,tag FROM clans WHERE id=?", (u["clan_id"],)) if u["clan_id"] else None
    return {"id": uid, "name": u["name"], "points": u["points"], "games": u["games"], "wins": u["wins"],
            "pro": u["pro_until"] > time.time(), "pro_until": u["pro_until"], "free_voice": u["free_voice"],
            "clan": f"{c['name']} [{c['tag']}]" if c else None,
            "rank": one("SELECT COUNT(*)+1 n FROM users WHERE points>?", (u["points"],))["n"]}

def chan(r, uid):
    x = r.p[uid]
    if r.phase in ("lobby", "ended"): return "all"
    if r.phase == "night": return "mafia" if x["alive"] and x["role"] in MAFIA_TEAM else None
    return "all" if x["alive"] else "dead"

async def handle(uid, name, m):
    t = m.get("t"); r = room_of(uid)
    if t == "create" and not r:
        u = one("SELECT * FROM users WHERE id=?", (uid,)); pro = u["pro_until"] > time.time()
        mode = m.get("mode") if m.get("mode") in ("chat", "voice", "table") else "chat"
        if mode == "voice" and not pro and u["free_voice"] <= 0:
            return await send(uid, {"t": "err", "m": "Bepul ovozli o'yinlar tugadi"})
        r = Room(uid, str(m.get("name", ""))[:24], mode, bool(m.get("closed")) and pro, pro)
        r.add(uid, name); ROOMS[r.id] = r; UROOM[uid] = r.id; await r.push(); await lobby_all()
    elif t in ("join", "quick") and not r:
        if t == "join": x = ROOMS.get(str(m.get("id", "")).strip())
        else:
            c = [v for v in ROOMS.values() if not v.closed and v.phase == "lobby" and v.mode == "chat" and len(v.p) < S("max_players")]
            x = max(c, key=lambda v: len(v.p)) if c else None
        if t == "quick" and not x: return await handle(uid, name, {"t": "create", "name": "Tezkor xona", "mode": "chat"})
        if not x or x.phase != "lobby": return await send(uid, {"t": "err", "m": "Xona topilmadi yoki o'yin boshlangan"})
        if len(x.p) >= S("max_players"): return await send(uid, {"t": "err", "m": "Xona to'la"})
        u = one("SELECT * FROM users WHERE id=?", (uid,))
        if x.mode == "voice" and u["pro_until"] < time.time() and u["free_voice"] <= 0:
            return await send(uid, {"t": "err", "m": "Bepul ovozli o'yinlar tugadi"})
        x.add(uid, name); UROOM[uid] = x.id; await x.push(); await lobby_all()
    elif t == "leave":
        await leave(uid); await send(uid, lobby())
    elif t == "start" and r and r.owner == uid and r.phase == "lobby":
        if len(r.p) < S("min_players"): return await send(uid, {"t": "err", "m": f"Kamida {S('min_players')} o'yinchi kerak"})
        r.phase = "intro"; r.task = asyncio.create_task(r.run()); await lobby_all()
    elif t == "chat" and r and uid in r.p:
        txt = str(m.get("text", "")).strip()[:200]; ch = chan(r, uid)
        if not txt or not ch: return
        c = {"u": r.p[uid]["name"], "t": txt, "ch": ch, "ts": int(time.time())}; r.chat.append(c)
        for u in r.p:
            if r.visible(u, c): await send(u, {"t": "chat", "c": c})
    elif t == "act" and r and r.phase == "night" and r.p.get(uid, {}).get("alive"):
        tg = m.get("to"); role = r.p[uid]["role"]
        if tg in r.p and r.p[tg]["alive"] and (role in MAFIA_TEAM or role in ("doktor", "izquvar")):
            r.act[uid] = tg
            for u in r.p:
                if u == uid or (role in MAFIA_TEAM and r.p[u]["role"] in MAFIA_TEAM): await send(u, r.view(u))
    elif t == "vote" and r and r.phase == "vote" and r.p.get(uid, {}).get("alive"):
        tg = m.get("to")
        if tg in r.p and r.p[tg]["alive"] and tg != uid:
            r.votes[uid] = tg; await r.push()
            if all(u in r.votes for u in r.alive()): r.wake.set()
    elif t == "rtc" and r and m.get("to") in r.p:
        await send(m["to"], {"t": "rtc", "from": uid, "data": m.get("data")})
    elif t == "profile": await send(uid, {"t": "me", "me": me_info(uid)})
    elif t == "rating":
        await send(uid, {"t": "rating",
            "players": [dict(x) for x in allr("SELECT name,points,games,wins FROM users ORDER BY points DESC LIMIT 20")],
            "clans": [dict(x) for x in allr("SELECT name,tag,points FROM clans ORDER BY points DESC LIMIT 20")]})

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
        u = json.loads(d["user"]); return u["id"], (u.get("first_name", "") + " " + u.get("last_name", "")).strip()
    except Exception:
        return None

@app.websocket("/ws")
async def ws_ep(ws: WebSocket):
    await ws.accept(); q = ws.query_params; a = auth(q.get("init", ""))
    if not a and DEV and q.get("dev"): a = (int(q["dev"]), q.get("name", "Dev"))
    if not a: return await ws.close(4001)
    uid, name = a
    sql("INSERT OR IGNORE INTO users(id,name,created,free_voice) VALUES(?,?,?,?)", (uid, name, int(time.time()), S("free_voice")))
    if one("SELECT banned FROM users WHERE id=?", (uid,))["banned"]:
        await ws.send_json({"t": "banned"}); return await ws.close()
    HUB[uid] = ws
    await ws.send_json({"t": "hello", "me": me_info(uid), "assets": amap(), "owner": uid in OWNERS, "bot": BOT_USERNAME})
    r = room_of(uid)
    await ws.send_json(r.view(uid) if r else lobby())
    try:
        while True: await handle(uid, name, await ws.receive_json())
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
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>BlackCrown</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
:root{--bg:#f4f5f9;--cd:#fff;--tx:#14161f;--mu:#7a8094;--ac:#4f5bf5;--rd:#e5484d;--gr:#1fa65a;--go:#c8921a;--bd:#e4e6ef}
@media(prefers-color-scheme:dark){:root{--bg:#0b0d14;--cd:#171a26;--tx:#eef0f7;--mu:#8b91a8;--bd:#262a3b}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:15px system-ui,sans-serif;padding:10px 10px 80px}
.card{background:var(--cd);border:1px solid var(--bd);border-radius:16px;padding:12px;margin:8px 0}
.row{display:flex;gap:8px;align-items:center}.row>*{flex:1}.mu{color:var(--mu);font-size:13px}
.btn{background:var(--ac);color:#fff;border:0;border-radius:12px;padding:12px;font-weight:600;font-size:15px;width:100%}
.btn.sec{background:var(--cd);color:var(--tx);border:1px solid var(--bd)}.btn.red{background:var(--rd)}.btn:disabled{opacity:.45}
.badge{display:inline-block;border-radius:8px;padding:2px 7px;font-size:12px;background:var(--bd);margin-left:6px}.badge.go{background:var(--go);color:#fff}
.tabs{display:flex;gap:6px;margin:8px 0}.tabs button{flex:1;padding:9px 4px;border:1px solid var(--bd);background:var(--cd);color:var(--tx);border-radius:10px}.tabs .on{background:var(--ac);color:#fff}
.hero{text-align:center}.hero img,.hero video{max-width:100%;border-radius:14px}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}.pl{background:var(--cd);border:2px solid var(--bd);border-radius:14px;padding:8px 4px;text-align:center;font-size:12px;position:relative;word-break:break-word}
.pl.dead{opacity:.35}.pl.sel{border-color:var(--ac)}.pl.me{border-color:var(--gr)}.pl .v{position:absolute;top:2px;right:6px;color:var(--rd);font-weight:700}
.pl .rl{display:block;color:var(--rd);font-size:11px;margin-top:2px}
.msg{padding:3px 0}.msg.mafia b{color:var(--rd)}.msg.dead{opacity:.6}#chat{max-height:34vh;overflow:auto}
#bar{position:fixed;left:0;right:0;bottom:0;padding:8px 10px calc(8px + env(safe-area-inset-bottom));background:var(--bg);gap:8px;display:none}
#bar input{flex:1;padding:12px;border-radius:12px;border:1px solid var(--bd);background:var(--cd);color:var(--tx);font-size:15px}
#bar button{width:auto;padding:0 18px}input,select{padding:11px;border-radius:10px;border:1px solid var(--bd);background:var(--cd);color:var(--tx);font-size:15px;width:100%;margin:4px 0}
.rc{display:block;margin:6px auto;max-height:150px}#toast{position:fixed;top:10px;left:50%;transform:translateX(-50%);background:#222;color:#fff;padding:8px 14px;border-radius:12px;display:none;z-index:9}
.big{font-size:20px;font-weight:700;text-align:center}.ic{height:20px;vertical-align:middle}
</style></head><body><div id="toast"></div><div id="app"></div>
<div id="bar" class="row"><input id="ci" maxlength="200" placeholder="Xabar" onkeydown="if(event.key=='Enter')sc()"><button class="btn" onclick="sc()">Yuborish</button></div>
<script>
const tg=window.Telegram&&Telegram.WebApp;if(tg){tg.ready();tg.expand()}
const $=i=>document.getElementById(i),q=new URLSearchParams(location.search);
const e=s=>String(s==null?"":s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
let ws,ME={},A={},OWN=false,BOT="",ROOM=null,LOBBY=[],RAT=null,tab="rooms",CR=false,LS=null,PC={};
const PH={lobby:"Kutish",intro:"Tanishuv",night:"Tun",day:"Muhokama",vote:"Ovoz berish",ended:"Tugadi"};
const MODE={chat:"Chat",voice:"Ovozli",table:"Stol"};const MT=["mafia","mafiaboshi","xufya"];
const RN={tinch:"Tinch aholi",izquvar:"Izquvar",doktor:"Doktor",bezori:"Bezori",rais:"Rais",qassob:"Qassob",hamsoya:"Hamsoya",folbin:"Folbin",mirshab:"Mirshab",mafia:"Mafia",mafiaboshi:"Mafiaboshi",xufya:"Xufya"};
const LAD=[[6,"mafia"],[6,"doktor"],[7,"mafia"],[8,"izquvar"],[9,"mafiaboshi"],[10,"rais"],[11,"bezori"],[12,"qassob"],[13,"hamsoya"],[14,"folbin"],[15,"mirshab"],[16,"xufya"]];
function connect(){const u=(location.protocol=="https:"?"wss://":"ws://")+location.host+"/ws?init="+encodeURIComponent(tg?tg.initData:"")+(q.get("dev")?"&dev="+q.get("dev")+"&name="+(q.get("name")||"Dev"):"");
ws=new WebSocket(u);ws.onmessage=ev=>on(JSON.parse(ev.data));ws.onclose=()=>setTimeout(connect,2000)}
const send=o=>ws&&ws.readyState==1&&ws.send(JSON.stringify(o));
function toast(m){const t=$("toast");t.textContent=m;t.style.display="block";setTimeout(()=>t.style.display="none",2500)}
function on(m){const t=m.t;
if(t=="hello"){ME=m.me;A=m.assets;OWN=m.owner;BOT=m.bot}
else if(t=="lobby"){LOBBY=m.rooms;if(ROOM){ROOM=null;stopVoice()}}
else if(t=="room"){ROOM=m;voiceSync()}
else if(t=="chat"){if(ROOM)ROOM.chat.push(m.c)}
else if(t=="fx")snd(m.k);else if(t=="err")toast(m.m);else if(t=="me")ME=m.me;else if(t=="rating")RAT=m;
else if(t=="rtc"){rtcIn(m);return}else if(t=="banned"){document.body.innerHTML="<h3>Siz bloklangansiz</h3>";return}
render()}
function snd(k){if(A["snd_"+k])try{new Audio(A["snd_"+k]).play().catch(()=>{})}catch(x){}}
function media(k,c){const u=A[k];if(!u)return"";return u.includes(".webm")?`<video class="${c||""}" src="${u}" autoplay loop muted playsinline></video>`:`<img class="${c||""}" src="${u}">`}
function sc(){const i=$("ci");if(i.value.trim()){send({t:"chat",text:i.value});i.value=""}}
function canChat(){return ROOM&&(ROOM.phase!="night"||(ROOM.me.alive&&MT.includes(ROOM.me.role)))}
function render(){const a=$("app");$("bar").style.display=canChat()?"flex":"none";a.innerHTML=ROOM?roomView():lobbyView();const c=$("chat");if(c)c.scrollTop=c.scrollHeight}
function setTab(t){tab=t;if(t=="rating")send({t:"rating"});if(t=="profile")send({t:"profile"});render()}
function lobbyView(){let h=`<div class="hero">${media("banner_ad")||media("bg_lobby")||media("logo")||"<div class=big>BlackCrown</div>"}</div>
<div class="row"><button class="btn" onclick="send({t:'quick'})">Tezkor qo'shilish</button><button class="btn sec" onclick="CR=!CR;render()">Yangi xona</button></div>`;
if(CR)h+=`<div class="card"><input id="cn" placeholder="Xona nomi (ixtiyoriy)"><select id="cm"><option value="chat">Chat</option><option value="voice">Ovozli</option><option value="table">Stol</option></select>
<label class="mu"><input type="checkbox" id="cc" style="width:auto"> Yopiq xona (Pro)</label><button class="btn" onclick="mk()">Yaratish</button></div>`;
h+=`<div class="tabs">${[["rooms","Xonalar"],["rating","Reyting"],["profile","Profil"],["rules","Qoidalar"]].map(x=>`<button class="${tab==x[0]?"on":""}" onclick="setTab('${x[0]}')">${x[1]}</button>`).join("")}</div>`;
if(tab=="rooms"){h+=`<div class="card row"><input id="jc" placeholder="Xona kodi"><button class="btn sec" style="flex:0 0 90px" onclick="send({t:'join',id:\$('jc').value})">Kirish</button></div>`;
const w=LOBBY.filter(r=>r.phase=="lobby"),l=LOBBY.filter(r=>r.phase!="lobby");
h+=w.length?w.map(r=>`<div class="card row"><div><b>${e(r.name)}</b>${r.pro?'<span class="badge go">Pro</span>':""}<span class="badge">${MODE[r.mode]}</span><div class="mu">${e(r.host)} · ${r.n}/${r.max}</div></div><button class="btn" style="flex:0 0 90px" onclick="send({t:'join',id:'${r.id}'})">Kirish</button></div>`).join(""):'<div class="card mu">Ochiq xona yo\'q</div>';
if(l.length)h+=`<div class="mu">Davom etayotgan o'yinlar: ${l.length} ta</div>`+l.map(r=>`<div class="card mu">${e(r.name)} · ${r.alive} tirik · ${r.day}-kun</div>`).join("")}
else if(tab=="rating"){h+=RAT?`<div class="card"><b>Top o'yinchilar</b>${RAT.players.map((p,i)=>`<div class="row"><span style="flex:0 0 26px">${i+1}</span><span>${e(p.name)}</span><span style="text-align:right">${p.points}</span></div>`).join("")}</div>
<div class="card"><b>Top klanlar</b>${RAT.clans.map((p,i)=>`<div class="row"><span style="flex:0 0 26px">${i+1}</span><span>${e(p.name)} [${e(p.tag)}]</span><span style="text-align:right">${p.points}</span></div>`).join("")||'<div class="mu">Klan yo\'q</div>'}</div>`:"<div class=mu>Yuklanmoqda</div>"}
else if(tab=="profile"){h+=`<div class="card"><div class="big">${e(ME.name)}</div><div class="mu" style="text-align:center">ID ${ME.id}${ME.clan?" · "+e(ME.clan):""}</div>
<div class="grid" style="grid-template-columns:repeat(2,1fr);margin-top:8px"><div class="pl">${ME.games}<br>O'yinlar</div><div class="pl">${ME.wins}<br>G'alabalar</div><div class="pl">${ME.points}<br>Ball (#${ME.rank})</div><div class="pl">${ME.free_voice}<br>Bepul ovozli</div></div></div>
<button class="btn" style="background:var(--go)" onclick="bot('pro')">${ME.pro?"Pro faol. Uzaytirish":"Pro obuna olish"}</button>${OWN?'<button class="btn sec" style="margin-top:8px" onclick="bot(\'admin\')">Owner paneli</button>':""}`}
else{h+=`<div class="card"><b>Rollar jadvali</b>${LAD.map(x=>`<div class="row"><span style="flex:0 0 60px">${x[0]}+</span><span>${RN[x[1]]}${x[0]>=13?'<span class="badge go">Pro xona</span>':""}</span></div>`).join("")}
<div class="mu">Qolganlar - Tinch aholi (6-12 kishida doim 4 ta).</div></div>`+Object.keys(RN).map(k=>A["role_"+k]?`<div class="card">${media("role_"+k,"rc")}<div class="big">${RN[k]}</div></div>`:"").join("")}
return h}
function bot(p){const u="https://t.me/"+BOT+"?start="+p;tg?tg.openTelegramLink(u):window.open(u)}
function mk(){send({t:"create",name:$("cn").value,mode:$("cm").value,closed:$("cc").checked});CR=false}
function pick(id){const R=ROOM,m=R.me;if(!m.alive)return;
if(R.phase=="night"&&(MT.includes(m.role)||m.role=="doktor"||m.role=="izquvar"))send({t:"act",to:id});
else if(R.phase=="vote")send({t:"vote",to:id})}
function hint(){const R=ROOM,m=R.me;if(!m.alive)return R.phase=="ended"?"":"Siz o'yindan chiqdingiz";
if(R.phase=="night"){if(MT.includes(m.role))return"Mafiya: qurbonni tanlang";if(m.role=="doktor")return"Doktor: kimni davolaysiz?";if(m.role=="izquvar")return"Izquvar: kimni tekshirasiz?";return"Tun. Hamma uxlayapti"}
if(R.phase=="vote")return"Kimni chiqarasiz? Ovoz bering";return""}
function roomView(){const R=ROOM,m=R.me,own=R.owner==ME.id,n=R.players.filter(p=>!p.left).length;
let h=`<div class="card"><b>${e(R.name)}</b> <span class="mu">${n}/${R.max}</span>${R.pro?'<span class="badge go">Pro</span>':""}<span class="badge">${MODE[R.mode]}</span>
<div class="mu">${PH[R.phase]}${R.day?" · "+R.day+"-kun":""} <b id="tm"></b>${R.phase=="lobby"?" · Kod: "+R.id:""}</div></div>`;
if(R.phase!="lobby"&&m.role)h+=`<div class="card">${media("role_"+m.role,"rc")}<div class="big">${RN[m.role]}</div></div>`;
if(R.phase=="ended")h+=`<div class="card big">G'alaba: ${R.winner=="town"?"Shahar":"Mafiya"}</div>`;
const hi=hint();if(hi)h+=`<div class="card mu">${hi}</div>`;
if(R.priv.length)h+=`<div class="card">${R.priv.map(x=>`<div><b>${e(x)}</b></div>`).join("")}</div>`;
h+=`<div class="grid">${R.players.map(p=>{const sel=(R.phase=="night"&&m.act==p.id)||(R.phase=="vote"&&m.vote==p.id);
return`<div class="pl ${p.alive?"":"dead"} ${sel?"sel":""} ${p.id==ME.id?"me":""}" onclick="pick(${p.id})">${p.v?'<span class="v">'+p.v+"</span>":""}${e(p.name)}${p.role?`<span class="rl">${RN[p.role]}</span>`:""}${p.left?'<span class="mu">chiqdi</span>':""}</div>`}).join("")}</div>`;
if(R.phase=="lobby")h+=`<div class="row" style="margin-top:10px">${own?`<button class="btn" ${n<R.min?"disabled":""} onclick="send({t:'start'})">Boshlash (${n}/${R.min}+)</button>`:'<div class="mu">Xona egasi o\'yinni boshlashini kuting</div>'}<button class="btn sec" onclick="send({t:'leave'})">Chiqish</button></div>`;
else if(R.phase=="ended")h+=`<button class="btn" style="margin-top:10px" onclick="send({t:'leave'})">Chiqish</button>`;
else h+=`<button class="btn sec" style="margin-top:10px" onclick="if(confirm('Chiqsangiz ball ayiriladi'))send({t:'leave'})">O'yindan chiqish</button>`;
if(R.log.length)h+=`<div class="card">${R.log.map(x=>`<div class="mu">${e(x.m)}</div>`).join("")}</div>`;
h+=`<div class="card" id="chat">${R.chat.map(c=>`<div class="msg ${c.ch}"><b>${e(c.u)}</b> ${e(c.t)}</div>`).join("")||'<div class="mu">Chat bo\'sh</div>'}</div>`;return h}
setInterval(()=>{const t=$("tm");if(t&&ROOM&&ROOM.deadline)t.textContent=Math.max(0,ROOM.deadline-Math.floor(Date.now()/1000))+" s"},500);
/* ---- ovozli xona: WebRTC (mesh) ---- */
function stopVoice(){for(const i in PC){PC[i].close()}PC={};document.querySelectorAll("audio.rtc").forEach(a=>a.remove());if(LS){LS.getTracks().forEach(t=>t.stop());LS=null}}
async function voiceSync(){if(!ROOM||ROOM.mode!="voice"){if(LS)stopVoice();return}
if(!LS){try{LS=await navigator.mediaDevices.getUserMedia({audio:true})}catch(x){toast("Mikrofon ruxsati kerak");return}}
const ids=ROOM.players.filter(p=>p.id!=ME.id&&!p.left).map(p=>p.id);
for(const id of ids)if(!PC[id])mkpc(id,ME.id<id);
for(const id in PC)if(!ids.includes(+id)){PC[id].close();delete PC[id];const a=$("au"+id);if(a)a.remove()}
const talk=ROOM.me.alive&&["lobby","intro","day","ended"].includes(ROOM.phase);LS.getAudioTracks().forEach(t=>t.enabled=talk);
document.querySelectorAll("audio.rtc").forEach(a=>a.muted=ROOM.phase=="night")}
function mkpc(id,init){const pc=new RTCPeerConnection({iceServers:[{urls:"stun:stun.l.google.com:19302"}]});PC[id]=pc;
LS.getTracks().forEach(t=>pc.addTrack(t,LS));pc.onicecandidate=ev=>ev.candidate&&send({t:"rtc",to:id,data:{c:ev.candidate}});
pc.ontrack=ev=>{let a=$("au"+id);if(!a){a=document.createElement("audio");a.id="au"+id;a.className="rtc";a.autoplay=true;document.body.appendChild(a)}a.srcObject=ev.streams[0]};
if(init)pc.onnegotiationneeded=async()=>{await pc.setLocalDescription(await pc.createOffer());send({t:"rtc",to:id,data:{d:pc.localDescription}})};return pc}
async function rtcIn(m){let pc=PC[m.from];if(!pc&&LS)pc=mkpc(m.from,false);if(!pc)return;const d=m.data||{};
if(d.d){await pc.setRemoteDescription(d.d);if(d.d.type=="offer"){await pc.setLocalDescription(await pc.createAnswer());send({t:"rtc",to:m.from,data:{d:pc.localDescription}})}}
else if(d.c)try{await pc.addIceCandidate(d.c)}catch(x){}}
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

def webkb(): return KB(inline_keyboard=[[B(text="O'ynash", web_app=WebAppInfo(url=WEBAPP_URL))]])
def plans_kb(): return KB(inline_keyboard=[[B(text=f"{m} oy - {S('star_' + m)} Stars", callback_data="buy:" + m)] for m in PLANS])

async def show_plans(m): await m.answer(f"{em('emo_crown')}<b>BlackCrown Pro</b>\nObuna muddatini tanlang:", reply_markup=plans_kb())

@dp.message(CommandStart())
async def start(m):
    sql("INSERT OR IGNORE INTO users(id,name,created,free_voice) VALUES(?,?,?,?)",
        (m.from_user.id, m.from_user.full_name, int(time.time()), S("free_voice")))
    arg = (m.text.split(maxsplit=1) + [""])[1]
    if arg == "pro": return await show_plans(m)
    if arg == "admin" and m.from_user.id in OWNERS: return await m.answer("Owner paneli", reply_markup=home_kb())
    txt = f"{em('emo_night')}<b>BlackCrown Mafia</b>\nKechasi mafia ov qiladi, kunduzi aholi mafiani izlaydi.\n6-16 o'yinchi, yashirin rollar, ovoz berish."
    lg = one("SELECT path FROM assets WHERE key='logo'")
    if lg:
        from aiogram.types import FSInputFile
        return await m.answer_photo(FSInputFile(lg["path"]), caption=txt, reply_markup=webkb())
    await m.answer(txt, reply_markup=webkb())

@dp.message(Command("pro"))
async def pro_cmd(m): await show_plans(m)

@dp.callback_query(F.data.startswith("buy:"))
async def buy(c):
    mo = c.data.split(":")[1]
    await bot.send_invoice(c.from_user.id, title="BlackCrown Pro", description=f"{mo} oylik Pro obuna", payload="pro:" + mo,
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
               [B(text="Premium emojilar", callback_data="ct:emoji:0")], [B(text="Ovoz effektlari", callback_data="ct:snd:0")], BACK]))

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
            "emoji": "Bitta premium emoji yuboring (xabar ichida).", "snd": "Audio yoki ovozli xabar yuboring (mp3/ogg)."}[SLOTS[k][1]]
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
        if mo == "asset": PEND.pop(uid); return await m.answer(await save_asset(p["k"], m), reply_markup=KB(inline_keyboard=[[B(text="Kontent", callback_data="cts")]]))
        if mo == "set":
            v = type(DEF[p["k"]])(txt.replace(",", ".")); sql("INSERT OR REPLACE INTO settings(k,v) VALUES(?,?)", (p["k"], str(v))); PEND.pop(uid)
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

dp.include_router(adm)

async def main():
    global BOT_USERNAME
    BOT_USERNAME = (await bot.get_me()).username
    if WEBAPP_URL:
        await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="O'ynash", web_app=WebAppInfo(url=WEBAPP_URL)))
    asyncio.create_task(backup_loop()); asyncio.create_task(send_backup("ishga tushdi"))
    srv = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=PORT, log_level="warning"))
    await asyncio.gather(srv.serve(), dp.start_polling(bot))

if __name__ == "__main__":
    asyncio.run(main())
