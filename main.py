"""
BlackCrownUzBot - bitta fayl: aiogram bot + FastAPI web-game (WebSocket) + owner paneli.
Ishga tushirish:
  pip install "aiogram>=3.13" fastapi "uvicorn[standard]"
  BOT_TOKEN=... OWNER_ID=123,456 WEBAPP_URL=https://domen.up.railway.app DATA_DIR=./data python main.py
Lokal sinov: DEV=1 va brauzerda  /?dev=111&name=Ali  (har xil dev=ID bilan bir necha oyna)
"""
import asyncio, base64, hashlib, hmac, html, io, json, os, random, re, sqlite3, time, zipfile
from pathlib import Path
from urllib.parse import parse_qsl, quote

import uvicorn
try:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps
    HAS_PIL = True
except Exception:
    HAS_PIL = False
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, Response
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (InlineKeyboardButton as B, InlineKeyboardMarkup as KB, LabeledPrice,
                           MenuButtonWebApp, WebAppInfo, FSInputFile)

BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNERS = {int(x) for x in os.environ.get("OWNER_ID", "").split(",") if x.strip()}
WEBAPP_URL = (os.environ.get("WEBAPP_URL", "").split() or [""])[0].rstrip("/")
DATA = Path(os.environ.get("DATA_DIR") or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or "./data"); (DATA / "assets").mkdir(parents=True, exist_ok=True)
AVDIR = DATA / "avatars"; AVDIR.mkdir(parents=True, exist_ok=True)
AV_LAST = {}
PORT = int(os.environ.get("PORT", "8080")); DEV = os.environ.get("DEV") == "1"
BOT_USERNAME = ""

def ice_servers():
    """WebRTC uchun STUN + TURN. Ishonchli ovoz uchun o'z TURN serveringizni ulang:
    TURN_URLS=turn:host:3478,turn:host:443?transport=tcp,turns:host:443?transport=tcp  TURN_USER=...  TURN_PASS=...
    Berilmasa, sinov uchun ochiq (bepul) TURN ishlatiladi: u ishonchsiz bo'lishi mumkin."""
    out = [{"urls": ["stun:stun.l.google.com:19302", "stun:stun1.l.google.com:19302"]}]
    urls = [u.strip() for u in os.environ.get("TURN_URLS", "").split(",") if u.strip()]
    if urls: out.append({"urls": urls, "username": os.environ.get("TURN_USER", ""), "credential": os.environ.get("TURN_PASS", "")})
    else: out.append({"urls": ["turn:openrelay.metered.ca:80", "turn:openrelay.metered.ca:443", "turns:openrelay.metered.ca:443?transport=tcp"],
                      "username": "openrelayproject", "credential": "openrelayproject"})
    return out

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
CREATE TABLE IF NOT EXISTS blocks(uid INTEGER,bid INTEGER,PRIMARY KEY(uid,bid));
CREATE TABLE IF NOT EXISTS frames(num INTEGER PRIMARY KEY,price INTEGER DEFAULT 0,on_sale INTEGER DEFAULT 0,created INTEGER);
CREATE TABLE IF NOT EXISTS user_frames(uid INTEGER,num INTEGER,ts INTEGER,PRIMARY KEY(uid,num));
CREATE TABLE IF NOT EXISTS admins(uid INTEGER PRIMARY KEY,ts INTEGER);
CREATE TABLE IF NOT EXISTS btns(id INTEGER PRIMARY KEY AUTOINCREMENT,text TEXT,url TEXT,pos INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS payments(uid INTEGER,kind TEXT,ref TEXT,stars INTEGER,ts INTEGER);
CREATE TABLE IF NOT EXISTS badges(num INTEGER PRIMARY KEY,name TEXT DEFAULT '',created INTEGER);
""")
for _c, _d in [("streak", "INTEGER DEFAULT 0"), ("best", "INTEGER DEFAULT 0"), ("last_day", "INTEGER DEFAULT 0"),
               ("keys", "INTEGER DEFAULT 0"), ("rr_day", "INTEGER DEFAULT 0"), ("photo", "TEXT DEFAULT ''"),
               ("ref", "INTEGER DEFAULT 0"), ("ref_ok", "INTEGER DEFAULT 0"), ("prefs", "TEXT DEFAULT '{}'"),
               ("survived", "INTEGER DEFAULT 0"), ("frame", "INTEGER DEFAULT 0"), ("uname", "TEXT DEFAULT ''"),
               ("bio", "TEXT DEFAULT ''"), ("badge", "INTEGER DEFAULT 0"), ("tg_photo", "TEXT DEFAULT ''"), ("custom_photo", "TEXT DEFAULT ''")]:
    try: DB.execute(f"ALTER TABLE users ADD COLUMN {_c} {_d}")
    except sqlite3.OperationalError: pass
for _c, _d in [("reason", "TEXT DEFAULT ''"), ("alive", "INTEGER DEFAULT 0")]:
    try: DB.execute(f"ALTER TABLE game_players ADD COLUMN {_c} {_d}")
    except sqlite3.OperationalError: pass
for _c, _d in [("hr", "INTEGER DEFAULT 0"), ("pro", "INTEGER DEFAULT 0")]:
    try: DB.execute(f"ALTER TABLE frames ADD COLUMN {_c} {_d}")
    except sqlite3.OperationalError: pass
try: DB.execute("ALTER TABLE users ADD COLUMN last_n INTEGER DEFAULT 0")
except sqlite3.OperationalError: pass
DB.commit()
for _c, _d in [("emblem", "INTEGER DEFAULT 1"), ("descr", "TEXT DEFAULT ''"), ("rules", "TEXT DEFAULT ''"), ("join_mode", "TEXT DEFAULT 'open'"),
               ("min_games", "INTEGER DEFAULT 0"), ("special", "INTEGER DEFAULT 0")]:
    try: DB.execute(f"ALTER TABLE clans ADD COLUMN {_c} {_d}")
    except sqlite3.OperationalError: pass
for _c, _d in [("clan_role", "TEXT DEFAULT ''"), ("clan_ts", "INTEGER DEFAULT 0")]:
    try: DB.execute(f"ALTER TABLE users ADD COLUMN {_c} {_d}")
    except sqlite3.OperationalError: pass
DB.executescript("""
CREATE TABLE IF NOT EXISTS clan_reqs(cid INTEGER,uid INTEGER,ts INTEGER,PRIMARY KEY(cid,uid));
CREATE TABLE IF NOT EXISTS clan_chat(id INTEGER PRIMARY KEY AUTOINCREMENT,cid INTEGER,uid INTEGER,name TEXT,txt TEXT,ts INTEGER);
CREATE INDEX IF NOT EXISTS i_cc ON clan_chat(cid,id);
""")
DB.commit()
try: DB.execute("ALTER TABLE assets ADD COLUMN dflt INTEGER DEFAULT 0"); DB.commit()  # dflt=1: standart (yuklab qo'yilgan) rasm
except sqlite3.OperationalError: pass
def sql(q, a=()): c = DB.execute(q, a); DB.commit(); return c
def one(q, a=()): return DB.execute(q, a).fetchone()
def allr(q, a=()): return DB.execute(q, a).fetchall()

DEF = {"win_pts": 30, "lose_pts": 0, "leave_pen": 25, "kick_pen": 30, "open_mult": 1.5, "intro": 120, "night": 60,
       "vote": 40, "disc": 120, "star_1": 100, "star_3": 280, "star_6": 500, "star_12": 800, "free_voice": 10,
       "min_players": 6, "max_players": 16, "last_word": 20, "afk_max": 2, "popup": 3.5, "chat_gap": 0.8,
       "channel_url": "", "chat_url": "", "brand": "Play Mafia", "ad_secs": 4.0, "banner_ad_url": "", "ad_1_url": "", "ad_2_url": "", "ad_3_url": "", "ad_4_url": "", "ad_5_url": "", "sale_channel": "", "dm_user": "", "sale_mode": "dm", "pro_free": 0,
       "clan_max": 100, "clan_pro": 1, "clan_mirza": 5, "emb_cols": 6, "emb_rows": 4, "emb_n": 24}
def S(k):
    r = one("SELECT v FROM settings WHERE k=?", (k,))
    return type(DEF[k])(r["v"]) if r else DEF[k]
def PN():
    """Pro tekshiruvi uchun 'hozir': bepul rejimda -1 (hamma Pro), aks holda real vaqt."""
    return -1 if S("pro_free") else time.time()

# ============================ KONTENT SLOTLARI ============================
ROLE_NAMES = {"tinch": "Tinch axoli", "komissar": "Komissar katani", "doktor": "Doktor", "serjant": "Serjant", "admiral": "Admiral",
              "daydi": "Daydi", "koldun": "Koldun", "mashuqa": "Mashuqa", "afsungar": "Afsungar", "buqalamun": "Buqalamun",
              "don": "Don", "mafia": "Mafia", "advokat": "Advokat", "ubiytsa": "Убийца"}
MAFIA_TEAM = {"mafia", "don", "advokat", "ubiytsa"}
SLOTS = {"logo": ("Logo", "img"), "bg_lobby": ("Lobbi banneri", "img"), "banner_ad": ("Reklama banneri", "img"),
         "banner_pro": ("Pro banneri", "img"), "bg_night": ("Tun foni", "img"), "bg_day": ("Kun foni", "img")}
for _r, _n in ROLE_NAMES.items(): SLOTS["role_" + _r] = ("Rol: " + _n, "img")
for _k, _n in [("tasks", "Tema tugmasi (oy / quyosh)"), ("history", "Ikona: Tarix"), ("rules", "Ikona: Qoidalar"), ("rating", "Ikona: Reyting"),
               ("clans", "Ikona: Ovullar"), ("profile", "Ikona: Profil"), ("create", "Ikona: Yangi xona"),
               ("quick", "Ikona: Tezkor qo'shilish"), ("code", "Ikona: Kod bilan kirish")]: SLOTS["ic_" + _k] = (_n, "img")
for _k, _n in [("night", "Tun"), ("day", "Kun"), ("kill", "O'ldirish"), ("heal", "Davolash"), ("check", "Tekshirish"),
               ("vote", "Ovoz"), ("crown", "Pro toj"), ("mic", "Mikrofon"), ("trophy", "Kubok"), ("clan", "Klan"),
               ("coin", "Ball"), ("fire", "Olov (seriya)")]: SLOTS["emo_" + _k] = ("Emoji: " + _n, "emoji")
for _k, _n in [("start", "O'yin boshlanishi"), ("night", "Tun"), ("day", "Kun"), ("kill", "O'ldirish"),
               ("heal", "Davolash"), ("vote", "Ovoz berish"), ("win", "G'alaba"), ("lose", "Mag'lubiyat"),
               ("click", "Tugma")]: SLOTS["snd_" + _k] = ("Ovoz: " + _n, "snd")
for _i in range(1, 6): SLOTS[f"ad_{_i}"] = (f"Reklama banneri {_i}", "img")
SLOTS["emb_sheet"] = ("Ovul belgilari: BITTA katta rasm (setka)", "img")
for _i in range(1, 41): SLOTS[f"emb_{_i}"] = (f"Ovul belgisi {_i}", "img")
for _k in [k for k in list(SLOTS) if k.startswith("ic_")]: SLOTS[_k + "_dark"] = (SLOTS[_k][0] + " (tungi rejim)", "img")
# --- UI ikonalar: web-o'yindagi oddiy emoji o'rniga rasm qo'yiladi (emoji -> slot) ---
UI_ICONS = [("players", "👥", "Oyinchilar"), ("crown", "👑", "Toj"), ("gamepad", "🎮", "O'yin"), ("mic", "🎙", "Mikrofon"),
 ("tent", "⛺", "Chodir"), ("detective", "🕵", "Izquvar"), ("citizen", "🧑", "Aholi"), ("cowboy", "🤠", "Bezori"),
 ("tophat", "🎩", "Rais shlyapasi"), ("tea", "🍵", "Choy"), ("crystal_ball", "🔮", "Folbin shari"), ("police_cap", "👮", "Mirshab"),
 ("sunglasses", "🕶", "Ko'zoynak"), ("mafioso", "🕴", "Mafia"), ("dizzy", "😵", "Halok"), ("skull", "☠", "Bosh suyagi"),
 ("ghost", "👻", "Arvoh"), ("stethoscope", "🩺", "Doktor"), ("knife", "🔪", "Pichoq"), ("pistol", "🔫", "To'pponcha"),
 ("magnifier", "🔍", "Qidiruv"), ("key", "🗝", "Kalit"), ("shield", "🛡", "Qalqon"), ("ballot_box", "🗳", "Ovoz qutisi"),
 ("sun", "☀", "Kun"), ("moon", "🌙", "Tun"), ("stopwatch", "⏱", "Taymer"), ("hourglass", "⏳", "Qum soati"),
 ("lightning", "⚡", "Chaqmoq"), ("fire", "🔥", "Olov"), ("star", "⭐", "Yulduz"), ("trophy", "🏆", "Kubok"),
 ("flag", "🏁", "Marra bayrogi"), ("toolbox", "🧰", "Asboblar"), ("chat", "💬", "Chat"), ("book", "📖", "Kitob"),
 ("scroll", "📜", "Qoidalar varagi"), ("megaphone", "📢", "E'lon"), ("link", "🔗", "Havola"), ("lock", "🔒", "Qulf"),
 ("eye", "👁", "Ko'z"), ("warning", "⚠", "Ogohlantirish"), ("check_box", "✅", "Tasdiq"), ("green_dot", "🟢", "Onlayn"),
 ("door", "🚪", "Eshik"), ("gear", "⚙", "Sozlamalar"), ("palette", "🎨", "Ranglar"), ("headphones", "🎧", "Quloqchin"),
 ("sound", "🔊", "Ovoz yoqiq"), ("mute", "🔇", "Ovoz ochiq"), ("skip", "⏭", "Keyingi"), ("play", "▶", "Boshlash"),
 ("masks", "🎭", "Niqoblar"), ("check", "✓", "Galochka"), ("cross", "✕", "Yopish"), ("arrow_right", "➤", "Ong strelka"),
 ("up", "▲", "Tepaga"), ("down", "▼", "Pastga"), ("arrow_left", "←", "Orqaga"), ("undo", "↩", "Qaytarish"),
 ("grid", "▦", "Katakcha"), ("diamond", "◇", "Olmos"), ("pencil", "✎", "Qalam"), ("target", "◎", "Nishon")]
EMAP = {e: "ui_" + n for n, e, _ in UI_ICONS}
for _n, _e, _t in UI_ICONS: SLOTS["ui_" + _n] = ("UI " + _e + " " + _t, "img")
# standart rasmlar: slot -> paket ichidagi fayl nomi
DEFAULT_MAP = {"ui_" + n: "ui_" + n for n, _, _ in UI_ICONS}
DEFAULT_MAP["ic_tasks_dark"] = "ui_sun"
DEFAULT_MAP.update({"ic_" + k: "ic_" + k for k in ("tasks", "history", "rules", "rating", "clans", "profile", "create", "quick", "code")})
DEFAULT_MAP.update({"emo_" + k: "ui_" + v for k, v in dict(night="moon", day="sun", kill="knife", heal="stethoscope", check="detective", vote="ballot_box",
                    crown="crown", mic="mic", trophy="trophy", clan="players", coin="star", fire="fire").items()})
AD_URL_KEYS = ["banner_ad"] + [f"ad_{i}" for i in range(1, 6)]
GROUPS = [("Banner va reklama", lambda k: k in ("logo", "bg_lobby", "banner_ad", "banner_pro") or k.startswith("ad_")),
          ("Rollar", lambda k: k.startswith("role_")),
          ("Ikonalar (kunduzgi)", lambda k: k.startswith("ic_") and not k.endswith("_dark")),
          ("Ikonalar (tungi)", lambda k: k.startswith("ic_") and k.endswith("_dark")),
          ("Emoji", lambda k: k.startswith("emo_")),
          ("Fonlar", lambda k: k in ("bg_night", "bg_day")),
          ("UI ikonalar (emoji o'rniga)", lambda k: k.startswith("ui_")),
          ("Ovul belgilari", lambda k: k.startswith("emb_"))]

def amap():
    hr = {f"frame_{r['num']}": r["hr"] for r in allr("SELECT num,hr FROM frames")}
    return {r["key"]: f"/a/{r['key']}.{r['ext']}?v={r['ts']}" + (f"&hr={hr[r['key']]}" if hr.get(r["key"]) else "") for r in allr("SELECT key,ext,ts FROM assets")}

def seed_defaults():
    """Standart rasmlarni (assets_pack.py) slotlarga joylaydi. Owner yuklagan rasmga tegmaydi: yuklasangiz u standartni almashtiradi."""
    try: from assets_pack import PACK_B64, PACK_VER; z = zipfile.ZipFile(io.BytesIO(base64.b64decode(PACK_B64)))
    except Exception as ex: print("standart rasmlar paketi topilmadi:", ex); return
    have = set(z.namelist()); n = 0
    for key, src in DEFAULT_MAP.items():
        fn = src + ".webp"
        if fn not in have: continue
        r = one("SELECT path,ts,dflt FROM assets WHERE key=?", (key,))
        if r and not r["dflt"]: continue  # owner yuklagan - saqlanadi
        if r and r["ts"] >= PACK_VER and Path(r["path"]).exists(): continue
        for old in (DATA / "assets").glob(key + ".*"): old.unlink()
        path = DATA / "assets" / f"{key}.webp"; path.write_bytes(z.read(fn))
        sql("INSERT OR REPLACE INTO assets(key,path,ext,emoji_id,ts,dflt) VALUES(?,?,?,?,?,1)", (key, str(path), "webp", None, PACK_VER)); n += 1
    if n: print(f"standart rasmlar yuklandi: {n} ta")

def seed_roles():
    """Yangi rol rasmlari (roles_pack.py). Eski standart rol rasmlarini o'chiradi; owner yuklaganiga tegmaydi."""
    try: from roles_pack import ROLE_B64, ROLE_VER; z = zipfile.ZipFile(io.BytesIO(base64.b64decode(ROLE_B64)))
    except Exception as ex: print("rol rasmlari paketi topilmadi:", ex); return
    for old in ("izquvar", "bezori", "rais", "qassob", "hamsoya", "folbin", "mirshab", "mafiaboshi", "xufya"):
        r = one("SELECT path,dflt FROM assets WHERE key=?", ("role_" + old,))
        if r and r["dflt"]:
            try: Path(r["path"]).unlink()
            except Exception: pass
            sql("DELETE FROM assets WHERE key=?", ("role_" + old,))
    have = set(z.namelist()); n = 0
    for role in ROLE_NAMES:
        key = "role_" + role; fn = key + ".webp"
        if fn not in have: continue
        r = one("SELECT path,ts,dflt FROM assets WHERE key=?", (key,))
        if r and not r["dflt"]: continue
        if r and r["ts"] >= ROLE_VER and Path(r["path"]).exists(): continue
        for old in (DATA / "assets").glob(key + ".*"): old.unlink()
        path = DATA / "assets" / f"{key}.webp"; path.write_bytes(z.read(fn))
        sql("INSERT OR REPLACE INTO assets(key,path,ext,emoji_id,ts,dflt) VALUES(?,?,?,?,?,1)", (key, str(path), "webp", None, ROLE_VER)); n += 1
    if n: print(f"rol rasmlari yuklandi: {n} ta")

async def push_assets():
    """Rasm almashtirilganda ochiq turgan o'yinlarga yangi rasmlarni yuboradi (qayta kirish shart emas)."""
    for ws in list(HUB.values()):
        try: await ws.send_json({"t": "assets", "assets": amap(), "emb": emb_cfg()})
        except Exception: pass
def em(key, fb=""):  # premium emoji bo'lsa shu, bo'lmasa oddiy emoji
    r = one("SELECT emoji_id FROM assets WHERE key=?", (key,))
    if r and r["emoji_id"]: return f'<tg-emoji emoji-id="{r["emoji_id"]}">{fb or "."}</tg-emoji> '
    return (fb + " ") if fb else ""

# ============================ O'YIN MOTORI ============================
HUB, ROOMS, UROOM, COOL = {}, {}, {}, {}
BLK = {}  # uid -> bloklagan odamlar (kesh)
FRIEND_MAX = 200; TABLE_MAX = 12; SEASON0 = 20454  # 1-mavsum: 2026-01-01
CODE_AL = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
def new_code():
    while True:
        c = "".join(random.choice(CODE_AL) for _ in range(4))
        if c not in ROOMS: return c
def blocked(uid):
    if uid not in BLK: BLK[uid] = {r["bid"] for r in allr("SELECT bid FROM blocks WHERE uid=?", (uid,))}
    return BLK[uid]
LADDER = [(6, ["mafia", "doktor"]), (7, ["mafia"]), (8, ["komissar"]), (9, ["don"])]  # faqat o'yinda ishlaydigan rollar tarqatiladi
IMPLEMENTED = {"tinch", "mafia", "don", "doktor", "komissar"}  # qolgan rollar hozircha faqat qoidalar sahifasida (mexanikasi keyin)
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

def photo_suffix(uid):
    """Avatar havolasiga '#f=ramka&b=belgi' qo'shimchasi: shunda barcha ro'yxatlarda (reyting, xona, profil) ramka va belgi avtomatik chiqadi."""
    r = one("SELECT frame,badge FROM users WHERE id=?", (uid,)); q = []
    if r and r["frame"]: q.append(f"f={r['frame']}")
    if r and r["badge"]: q.append(f"b={r['badge']}")
    return ("#" + "&".join(q)) if q else ""
def frag(uid): return photo_suffix(uid)
def refresh_photo(uid, tg=None):
    """photo = (o'z rasmi | Telegram rasmi) + qo'shimcha. tg: Telegramdan kelgan yangi havola."""
    r = one("SELECT photo,tg_photo,custom_photo FROM users WHERE id=?", (uid,))
    if not r: return
    old = (r["photo"] or "").split("#")[0]
    if old.startswith("/u/"): old = ""
    tgp = tg if tg else (r["tg_photo"] or ("" if r["custom_photo"] else old))
    base = r["custom_photo"] or tgp
    sql("UPDATE users SET tg_photo=?,photo=? WHERE id=?", (tgp, base + photo_suffix(uid), uid))
def set_frame(uid, n):
    sql("UPDATE users SET frame=? WHERE id=?", (n, uid)); refresh_photo(uid)
def fix_perks(uid):
    """Pro tugagan yoki o'chirilgan belgi/ramka avtomatik yechiladi."""
    u = one("SELECT frame,badge,pro_until FROM users WHERE id=?", (uid,))
    if not u: return
    pro = u["pro_until"] > PN(); ch = False
    if u["badge"] and (not pro or not one("SELECT 1 FROM badges WHERE num=?", (u["badge"],))):
        sql("UPDATE users SET badge=0 WHERE id=?", (uid,)); ch = True
    if u["frame"]:
        fr = one("SELECT pro FROM frames WHERE num=?", (u["frame"],))
        own = one("SELECT 1 FROM user_frames WHERE uid=? AND num=?", (uid, u["frame"]))
        if not fr or not (own or (fr["pro"] and pro)): sql("UPDATE users SET frame=0 WHERE id=?", (uid,)); ch = True
    if ch: refresh_photo(uid)
def owned_frames(uid): return [r["num"] for r in allr("SELECT num FROM user_frames WHERE uid=? ORDER BY num", (uid,))]
def grant_frame(uid, n, equip=True):
    sql("INSERT OR IGNORE INTO user_frames(uid,num,ts) VALUES(?,?,?)", (uid, n, int(time.time())))
    if equip: set_frame(uid, n)

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
        s.id = new_code(); s.owner = owner; s.name = name or "Xona"; s.mode = mode; s.closed = closed
        s.pro = pro; s.minp = minp; s.disc = disc; s.req = req
        s.cap = min(S("max_players"), TABLE_MAX) if mode == "table" else S("max_players")
        s.ready = set(); s.ack = set(); s.ext = set(); s.dur = 0; s.specinfo = {}
        s.p = {}; s.spec = set(); s.phase = "lobby"; s.day = 0; s.night = 0; s.deadline = 0; s.chat = []; s.cid = 0
        s.act = {}; s.votes = {}; s.wake = asyncio.Event(); s.task = None; s.winner = None; s.priv = {}; s.log = []
        s.speaker = None; s.skipd = set(); s.miss = {}; s.heal_prev = {}; s.selfheal = set(); s.lastchat = {}; s.notified = 0; s.talk = set(); s.nov = set(); s.q = random.choice(QUESTIONS)

    def add(s, uid, name):
        u = one("SELECT photo,pro_until FROM users WHERE id=?", (uid,))
        s.p[uid] = {"name": name, "alive": True, "role": None, "left": False, "reason": "", "mic": False,
                    "ph": (u["photo"] if u else "") or "", "pro": bool(u and u["pro_until"] > PN())}
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
                       "why": "" if x["alive"] else x["reason"], "mic": x.get("mic", False), "cb": cb_of(u)})
        return {"t": "room", "id": s.id, "name": s.name, "mode": s.mode, "closed": s.closed, "pro": s.pro,
                "owner": s.owner, "phase": s.phase, "day": s.day, "night": s.night, "q": s.q, "deadline": s.deadline, "players": pl,
                "spec": me is None, "specn": len(s.spec), "speaker": s.speaker, "skip": len(s.skipd & set(s.alive())),
                "me": {"role": me["role"] if me else None, "alive": bool(me and me["alive"]),
                       "act": s.act.get(uid), "vote": s.votes.get(uid), "skipped": uid in s.skipd, "nov": uid in s.nov,
                       "ready": uid in s.ready, "ack": uid in s.ack, "ext": uid in s.ext},
                "chat": [c for c in s.chat if s.visible(uid, c) and c["uid"] not in blocked(uid)][-60:], "log": s.log[-8:],
                "priv": s.priv.get(uid, []), "winner": s.winner, "max": s.cap, "cap": s.cap, "minp": s.minp, "disc": s.disc,
                "req": s.req, "dur": s.dur, "ready": [u for u in s.ready if u in s.p], "ack": [u for u in s.ack if u in s.p],
                "extn": len(s.ext & set(s.alive())), "skipl": list(s.skipd & set(s.alive())), "extl": list(s.ext & set(s.alive())), "vt": ({str(a): b for a, b in s.votes.items()} if s.phase == "vote" else {}),
                "specs": [{"id": u, "name": i[0], "ph": i[1]} for u, i in list(s.specinfo.items())[:40] if u in s.spec]}

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
        s.wake.clear(); s.dur = secs; s.deadline = int(time.time() + secs); await s.push()
        while time.time() < s.deadline and not s.wake.is_set() and not s.winner:
            if ready and ready():
                await asyncio.sleep(1.5); break
            try: await asyncio.wait_for(s.wake.wait(), 1)
            except asyncio.TimeoutError: pass

    def die(s, u, reason):
        x = s.p[u]
        if x["alive"]: x["alive"] = False; x["reason"] = reason

    def actors(s):
        return [u for u in s.alive() if s.p[u]["role"] in MAFIA_TEAM or s.p[u]["role"] in ("doktor", "komissar")]
    def night_ready(s): return all(u in s.act for u in s.actors())

    async def run(s):
        try:
            ids = list(s.p); roles = make_roles(len(ids), s.pro); random.shuffle(ids)
            for u, r in zip(ids, roles): s.p[u]["role"] = r
            for u in ids:
                sql("UPDATE users SET games=games+1 WHERE id=?", (u,))
                if s.mode == "voice" and not one("SELECT pro_until p FROM users WHERE id=?", (u,))["p"] > PN():
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

    async def run_table(s):
        """Stol rejimi: o'yinchilar yonma-yon o'tiradi, telefon faqat boshqaradi."""
        try:
            ids = list(s.p); roles = make_roles(len(ids), s.pro); random.shuffle(ids)
            for u, r in zip(ids, roles): s.p[u]["role"] = r
            for u in ids: sql("UPDATE users SET games=games+1 WHERE id=?", (u,))
            s.phase = "deal"; s.say("O'yin boshlandi."); await s.fx("start"); await s.timed(10)
            s.phase = "reveal"; s.ack = set(); s.say("Rollar tarqatildi.")
            await s.timed(60, lambda: all(u in s.ack for u, x in s.p.items() if not x["left"]))
            s.day = 0
            while not s.winner:
                await s.day_phase()
                if s.winner: break
                await s.vote_phase()
                if s.winner: break
                await s.night_phase()
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
            boss = next((t for u, t in kills if s.p[u]["role"] == "don" and t in cands), None)
            victim = boss if boss is not None else random.choice(cands)
        docs = [(u, t) for u, t in s.act.items() if s.p[u]["alive"] and s.p[u]["role"] == "doktor" and t in s.p]
        has_doc = any(x["alive"] and x["role"] == "doktor" for x in s.p.values())
        healed = {t for u, t in docs}
        for u, t in docs:
            s.heal_prev[u] = t
            if t == u: s.selfheal.add(u)
            s.priv.setdefault(u, []).append(f"{s.p[t]['name']} davolandi")
        for u, t in s.act.items():
            if s.p[u]["alive"] and s.p[u]["role"] == "komissar" and t in s.p:
                r = s.p[t]["role"]; ism = r in MAFIA_TEAM
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
        s.day += 1; s.phase = "day"; s.skipd = set(); s.talk = set(); s.ext = set()
        s.say(f"{s.day if s.mode == 'table' else s.day + 1}-kun. Muhokama boshlandi."); await s.fx("day")
        await s.timed(s.disc, lambda: len(s.skipd & set(s.alive())) > len(s.alive()) // 2)

    async def vote_phase(s):
        s.phase = "vote"; s.votes = {}; s.nov = set() if s.mode == "table" else {u for u in s.alive() if u not in s.talk}
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
    return {"t": "lobby", "rooms": [{"id": r.id, "name": r.name, "n": len(r.p), "max": r.cap, "mode": r.mode,
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
    if uid not in r.p:  # kuzatuvchi / tinglovchi
        r.spec.discard(uid); r.specinfo.pop(uid, None)
        if r.phase == "lobby" and r.mode == "voice": await r.push()
        return
    if r.phase in ("lobby", "ended"):
        r.p.pop(uid, None); r.ready.discard(uid)
        if not r.p:
            ROOMS.pop(r.id, None)
            for u in list(r.spec): UROOM.pop(u, None); await send(u, lobby())
        else:
            if r.owner == uid: r.owner = next(iter(r.p))
            await r.push(); await table_try_start(r)
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
    c, crole = my_clan(uid)
    d = today()
    tdone = one("SELECT COUNT(*) n FROM tasks WHERE uid=? AND day=? AND done=1", (uid, d))["n"]
    return {"id": uid, "name": u["name"], "photo": u["photo"] or "", "frames": owned_frames(uid), "frame": u["frame"] or 0,
            "bio": u["bio"] or "", "badge": u["badge"] or 0, "custom_av": bool(u["custom_photo"]),
            "badges": [x["num"] for x in allr("SELECT num FROM badges ORDER BY num")], "pfr": [x["num"] for x in allr("SELECT num FROM frames WHERE pro=1 ORDER BY num")], "points": u["points"], "games": u["games"], "wins": u["wins"],
            "survived": u["survived"], "pro": u["pro_until"] > PN(), "pro_until": u["pro_until"], "free_voice": u["free_voice"],
            "clan": f"{c['name']} [{c['tag']}]" if c else None, "cid": c["id"] if c else 0, "crole": crole, "cool": cool(uid), "prefs": prefs(uid),
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
        rows = allr("SELECT c.*,(SELECT COUNT(*) FROM users WHERE clan_id=c.id) n FROM clans c ORDER BY c.points DESC,c.id LIMIT 300")
        CM = S("clan_max"); rk = 0; cur = 0; last = None; out = []
        for x in rows:
            if not x["special"]:
                rk += 1
                if x["points"] != last: cur, last = rk, x["points"]
            out.append({"id": x["id"], "name": x["name"], "tag": x["tag"], "points": x["points"], "n": x["n"], "lvl": clan_lvl(x["points"]),
                        "rank": 0 if x["special"] else cur, "tier": clan_tier(x["points"], x["special"]), "emb": x["emblem"] or 1, "sp": bool(x["special"]),
                        "jm": x["join_mode"] or "open", "mg": x["min_games"] or 0, "full": x["n"] >= CM})
        return {"t": "rating", "kind": "clans", "scope": scope, "my": (one("SELECT clan_id FROM users WHERE id=?", (uid,)) or {"clan_id": 0})["clan_id"], "clans": out}
    now_ = int(time.time()); since = {"week": week_start(), "season": now_ - 90 * 86400}.get(scope)
    if since is not None:
        rows = allr("SELECT * FROM (SELECT u.id,u.name,u.photo,u.pro_until>? pro,"
                    "COALESCE((SELECT SUM(d) FROM points_log WHERE uid=u.id AND ts>=?),0) pts,"
                    "(SELECT COUNT(*) FROM game_players WHERE uid=u.id AND ts>=?) g,"
                    "(SELECT COALESCE(SUM(win),0) FROM game_players WHERE uid=u.id AND ts>=?) w FROM users u) "
                    "WHERE g>0 ORDER BY pts DESC LIMIT 30", (PN(), since, since, since))
    else:
        ids = [uid]
        if scope == "friends": ids += [r["fid"] for r in allr("SELECT fid FROM friends WHERE uid=?", (uid,))]
        flt = f"WHERE u.id IN ({','.join('?' * len(ids))})" if scope == "friends" else ""
        rows = allr("SELECT u.id,u.name,u.photo,u.pro_until>? pro,u.points pts,u.games g,u.wins w FROM users u "
                    f"{flt} ORDER BY u.points DESC LIMIT 30", (PN(), *(ids if scope == "friends" else ())))
    return {"t": "rating", "kind": "players", "scope": scope, "left": week_left() if scope == "week" else 0,
            "players": [{"id": x["id"], "name": x["name"], "ph": x["photo"] or "", "pro": bool(x["pro"]), "pts": x["pts"], "g": x["g"],
                         "wr": round(100 * x["w"] / x["g"]) if x["g"] else 0, "cb": cb_of(x["id"])} for x in rows]}

def friends_list(uid):
    rows = allr("SELECT u.id,u.name,u.photo,u.points FROM friends f JOIN users u ON u.id=f.fid WHERE f.uid=?", (uid,))
    out = []
    for x in rows:
        rr = ROOMS.get(UROOM.get(x["id"]))
        out.append({"id": x["id"], "name": x["name"], "ph": x["photo"] or "", "points": x["points"], "on": x["id"] in HUB, "cb": cb_of(x["id"]),
                    "room": {"id": rr.id, "name": rr.name, "mode": rr.mode, "phase": rr.phase} if rr else None})
    out.sort(key=lambda f: (f["room"] is None, not f["on"]))
    return {"t": "friends", "max": FRIEND_MAX, "list": out}

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

async def table_try_start(r):
    """Stol rejimi: hamma "Tayyorman" bossa, 10 soniyalik sanoq boshlanadi."""
    if r.mode != "table" or r.phase != "lobby": return
    pl = set(r.p)
    if len(pl) >= r.minp and pl <= r.ready:
        r.phase = "deal"; r.task = asyncio.create_task(r.run_table()); await lobby_all()

def clan_lvl(p): return min(10, 1 + p // 400)
def clan_next(p): return 0 if clan_lvl(p) >= 10 else clan_lvl(p) * 400

def clan_tier(p, special=0):
    """Teg rangi ochkoga qarab: 7=rasmiy (maxsus yorug'), 6=oltin (2000+), 1=qizil (1000+), 2=binafsha (500+), 3=ko'k (200+), 4=yashil (50+), 5=kulrang."""
    if special: return 7
    return 6 if p >= 2000 else 1 if p >= 1000 else 2 if p >= 500 else 3 if p >= 200 else 4 if p >= 50 else 5

def clan_rank(c):
    """Reyting o'rni. Rasmiy (maxsus) ovullar raqamlanmaydi (0)."""
    return 0 if c["special"] else one("SELECT COUNT(*)+1 n FROM clans WHERE special=0 AND points>?", (c["points"],))["n"]

def emb_n(): return max(1, min(40, S("emb_n")))
def emb_cfg(): return {"cols": max(1, S("emb_cols")), "rows": max(1, S("emb_rows")), "n": emb_n()}
def emb_hint():
    return (f"\n\nSetka: {S('emb_cols')} ustun × {S('emb_rows')} qator = {S('emb_cols') * S('emb_rows')} ta belgi (kataklar teng va kvadrat bo'lsin). "
            'Rasmni FAYL (hujjat) sifatida yuboring, sifati buzilmaydi. Soni boshqacha bo\'lsa, Sozlamalardan emb_cols, emb_rows, emb_n ni o\'zgartiring.')
def _i(v, lo, hi, d=0):
    try: return max(lo, min(hi, int(v)))
    except Exception: return d
def _txt(v, n, one_line=False):
    s = str(v or "").replace("\r", "")
    s = re.sub(r"\s+", " ", s) if one_line else re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", s))
    return s.strip()[:n]

# ---- ism boshidagi ovul belgisi (cache: 60 soniya, ovul o'zgarsa tozalanadi) ----
CBC = {}
def cb_flush(): CBC.clear()
def cb_of(uid):
    x = CBC.get(uid)
    if x and time.time() - x[0] < 60: return x[1]
    u = one("SELECT clan_id FROM users WHERE id=?", (uid,)); b = None
    if u and u["clan_id"]:
        c = one("SELECT id,tag,points,emblem,special FROM clans WHERE id=?", (u["clan_id"],))
        if c: b = {"c": c["id"], "e": c["emblem"] or 1, "g": c["tag"], "t": clan_tier(c["points"], c["special"])}
    CBC[uid] = (time.time(), b); return b

def my_clan(uid):
    """(ovul qatori | None, rol: 'owner' | 'mirza' | 'member' | '')"""
    u = one("SELECT clan_id,clan_role FROM users WHERE id=?", (uid,))
    if not u or not u["clan_id"]: return None, ""
    c = one("SELECT * FROM clans WHERE id=?", (u["clan_id"],))
    if not c: return None, ""
    return c, ("owner" if c["owner_id"] == uid else "mirza" if u["clan_role"] == "mirza" else "member")

def clan_join_user(uid, cid):
    sql("UPDATE users SET clan_id=?,clan_role='',clan_ts=? WHERE id=?", (cid, int(time.time()), uid))
    sql("DELETE FROM clan_reqs WHERE uid=?", (uid,)); CBC.pop(uid, None)
def clan_drop_user(uid):
    sql("UPDATE users SET clan_id=0,clan_role='',clan_ts=0 WHERE id=?", (uid,)); CBC.pop(uid, None)
def clan_delete(cid):
    sql("UPDATE users SET clan_id=0,clan_role='',clan_ts=0 WHERE clan_id=?", (cid,))
    for q in ("DELETE FROM clan_reqs WHERE cid=?", "DELETE FROM clan_chat WHERE cid=?", "DELETE FROM clans WHERE id=?"): sql(q, (cid,))
    cb_flush()
def clan_count(cid): return one("SELECT COUNT(*) n FROM users WHERE clan_id=?", (cid,))["n"]
def clan_online(cid): return [r["id"] for r in allr("SELECT id FROM users WHERE clan_id=?", (cid,)) if r["id"] in HUB]

def pinfo(viewer, tid):
    u = one("SELECT * FROM users WHERE id=?", (tid,))
    if not u: return None
    tw = {"town": [0, 0], "mafia": [0, 0]}; rl = {}
    for x in allr("SELECT role,win FROM game_players WHERE uid=?", (tid,)):
        k = "mafia" if x["role"] in MAFIA_TEAM else "town"; w = x["win"] or 0
        tw[k][0] += 1; tw[k][1] += w
        d = rl.setdefault(x["role"] or "tinch", [0, 0]); d[0] += 1; d[1] += w
    g = u["games"] or 0; c = None
    if u["clan_id"]:
        cc = one("SELECT * FROM clans WHERE id=?", (u["clan_id"],))
        if cc:
            c = {"id": cc["id"], "name": cc["name"], "tag": cc["tag"], "emb": cc["emblem"] or 1, "lvl": clan_lvl(cc["points"]), "max": S("clan_max"),
                 "n": clan_count(cc["id"]), "rank": clan_rank(cc), "tier": clan_tier(cc["points"], cc["special"])}
    d = today()
    return {"t": "pinfo", "p": {"id": tid, "name": u["name"] or "?", "ph": u["photo"] or "", "pro": u["pro_until"] > PN(), "on": tid in HUB,
            "bio": (u["bio"] or "") if u["pro_until"] > PN() else "",
            "g": g, "w": u["wins"] or 0, "wr": round(100 * (u["wins"] or 0) / g) if g else 0, "sv": round(100 * (u["survived"] or 0) / g) if g else 0,
            "tw": tw, "rl": rl, "clan": c, "cb": cb_of(tid), "me": tid == viewer, "bl": tid in blocked(viewer),
            "fr": bool(one("SELECT 1 FROM friends WHERE uid=? AND fid=?", (viewer, tid))), "season": (d - SEASON0) // 90 + 1, "sleft": 90 - (d - SEASON0) % 90}}

def cinfo(viewer, cid, bg=False):
    c = one("SELECT * FROM clans WHERE id=?", (cid,))
    if not c: return None
    since = int(time.time()) - 7 * 86400
    rows = allr("SELECT u.id,u.name,u.photo,u.pro_until,u.points,u.clan_role,COALESCE((SELECT SUM(d) FROM points_log WHERE uid=u.id AND ts>=?),0) p7 "
                "FROM users u WHERE u.clan_id=?", (since, cid))
    rows = sorted(rows, key=lambda x: (0 if x["id"] == c["owner_id"] else 1 if x["clan_role"] == "mirza" else 2, -x["p7"], -(x["points"] or 0)))
    own = one("SELECT name FROM users WHERE id=?", (c["owner_id"],))
    mem = [{"id": x["id"], "name": x["name"] or "?", "ph": x["photo"] or "", "pro": x["pro_until"] > PN(), "on": x["id"] in HUB, "p7": x["p7"],
            "role": "owner" if x["id"] == c["owner_id"] else "mirza" if x["clan_role"] == "mirza" else "member"} for x in rows[:200]]
    vc, vrole = my_clan(viewer); mine = bool(vc and vc["id"] == cid); lead = mine and vrole in ("owner", "mirza")
    vu = one("SELECT games FROM users WHERE id=?", (viewer,)); CM = S("clan_max")
    reqs = []
    if lead:
        reqs = [{"id": x["id"], "name": x["name"] or "?", "ph": x["photo"] or "", "g": x["games"] or 0, "w": x["wins"] or 0}
                for x in allr("SELECT u.id,u.name,u.photo,u.games,u.wins FROM clan_reqs r JOIN users u ON u.id=r.uid WHERE r.cid=? ORDER BY r.ts", (cid,))]
    nxt = clan_next(c["points"])
    return {"t": "cinfo", "bg": bg, "c": {"id": cid, "tier": clan_tier(c["points"], c["special"]), "ntier": clan_tier(nxt) if nxt else 0, "name": c["name"], "tag": c["tag"],
            "emb": c["emblem"] or 1, "sp": bool(c["special"]), "pts": c["points"], "n": len(rows), "max": CM, "lvl": clan_lvl(c["points"]), "nxt": nxt,
            "prog": round(100 * (c["points"] % 400) / 400) if nxt else 100, "rank": clan_rank(c), "owner": own["name"] if own else "", "oid": c["owner_id"],
            "mine": mine, "has": bool(vc), "role": vrole if mine else "", "jm": c["join_mode"] or "open", "mg": c["min_games"] or 0,
            "mgok": (vu["games"] or 0) >= (c["min_games"] or 0) if vu else True, "mygames": (vu["games"] or 0) if vu else 0,
            "full": len(rows) >= CM, "req": bool(one("SELECT 1 FROM clan_reqs WHERE cid=? AND uid=?", (cid, viewer))),
            "rules": c["rules"] or "", "descr": c["descr"] or "", "reqs": reqs, "mem": mem, "p7": sum(x["p7"] for x in rows)}}

CCOOL = {}; CINV = {}
def cchat_list(cid):
    rows = allr("SELECT id,uid,name,txt,ts FROM clan_chat WHERE cid=? ORDER BY id DESC LIMIT 60", (cid,))
    return [{"id": x["id"], "uid": x["uid"], "u": x["name"], "t": x["txt"], "ts": x["ts"]} for x in reversed(rows)]

CLAN_T = {"clan_create", "clan_edit", "clan_join", "clan_cancel", "clan_leave", "clan_req", "clan_kick", "clan_role", "clan_transfer",
          "clan_disband", "clan_chat", "clan_send", "clan_invite"}

async def clan_handle(uid, name, t, m):
    me = one("SELECT * FROM users WHERE id=?", (uid,))
    if not me: return
    async def err(s): await send(uid, {"t": "err", "m": s})
    async def toast(s, to=None): await send(to or uid, {"t": "toast", "m": s})
    c, role = my_clan(uid); CM = S("clan_max")
    lead = role in ("owner", "mirza")

    async def refresh(cid, rate=True):
        """Amaldan keyin: o'zingizga yangi profil + ovul sahifasi, ovul a'zolariga fon yangilanishi."""
        cb_flush()
        await send(uid, {"t": "me", "me": me_info(uid)})
        if rate: await send(uid, rating(uid, "clans", "all"))
        v = cinfo(uid, cid) if cid else None
        if v: await send(uid, v)
        elif cid is not None: await send(uid, {"t": "cgone"})
        if cid:
            for o in clan_online(cid):
                if o != uid:
                    vv = cinfo(o, cid, True)
                    if vv: await send(o, vv)

    def fields():
        return (_txt(m.get("name"), 24, True), str(m.get("tag", "")).strip().upper(), _i(m.get("emb"), 1, emb_n(), 1),
                m.get("jm") if m.get("jm") in ("open", "request", "closed") else "open", _i(m.get("mg"), 0, 1000, 0),
                _txt(m.get("rules"), 600), _txt(m.get("descr"), 120, True))

    if t == "clan_create":
        nm, tag, emb, jm, mg, rules, descr = fields()
        if S("clan_pro") and me["pro_until"] <= PN(): return await err("Ovul yaratish uchun Pro kerak")
        if c: return await err("Siz allaqachon ovuldasiz")
        if len(nm) < 3: return await err("Nom kamida 3 belgi bo'lsin")
        if not re.fullmatch(r"[A-Z0-9]{2,5}", tag): return await err("Teg 2–5 ta lotin harfi yoki raqam bo'lsin")
        if one("SELECT 1 FROM clans WHERE lower(name)=lower(?) OR tag=?", (nm, tag)): return await err("Bunday nom yoki teg band")
        cid = sql("INSERT INTO clans(name,tag,owner_id,created,emblem,join_mode,min_games,rules,descr) VALUES(?,?,?,?,?,?,?,?,?)",
                  (nm, tag, uid, int(time.time()), emb, jm, mg, rules, descr)).lastrowid
        clan_join_user(uid, cid); await toast("Ovul yaratildi"); await refresh(cid)
    elif t == "clan_edit":
        if role != "owner": return await err("Faqat Oqsoqol o'zgartira oladi")
        nm, tag, emb, jm, mg, rules, descr = fields()
        if len(nm) < 3: return await err("Nom kamida 3 belgi bo'lsin")
        if not re.fullmatch(r"[A-Z0-9]{2,5}", tag): return await err("Teg 2–5 ta lotin harfi yoki raqam bo'lsin")
        if one("SELECT 1 FROM clans WHERE id!=? AND (lower(name)=lower(?) OR tag=?)", (c["id"], nm, tag)): return await err("Bunday nom yoki teg band")
        sql("UPDATE clans SET name=?,tag=?,emblem=?,join_mode=?,min_games=?,rules=?,descr=? WHERE id=?", (nm, tag, emb, jm, mg, rules, descr, c["id"]))
        await toast("Saqlandi"); await refresh(c["id"])
    elif t == "clan_join":
        cid = _i(m.get("id"), 0, 10 ** 12); cl = one("SELECT * FROM clans WHERE id=?", (cid,))
        if not cl: return await err("Ovul topilmadi")
        if c: return await err("Avval hozirgi ovuldan chiqing")
        if clan_count(cid) >= CM: return await err("Ovul to'la")
        if cl["join_mode"] == "closed": return await err("Ovul yopiq")
        if (me["games"] or 0) < (cl["min_games"] or 0): return await err(f"Qo'shilish uchun kamida {cl['min_games']} ta o'yin kerak")
        if cl["join_mode"] == "request":
            if one("SELECT COUNT(*) n FROM clan_reqs WHERE uid=?", (uid,))["n"] >= 3 and not one("SELECT 1 FROM clan_reqs WHERE uid=? AND cid=?", (uid, cid)):
                return await err("Bir vaqtda ko'pi bilan 3 ta so'rov yuborish mumkin")
            sql("INSERT OR IGNORE INTO clan_reqs(cid,uid,ts) VALUES(?,?,?)", (cid, uid, int(time.time())))
            for lid in [r["id"] for r in allr("SELECT id FROM users WHERE clan_id=? AND (clan_role='mirza' OR id=?)", (cid, cl["owner_id"]))]:
                if lid in HUB: await toast(f"{me['name'] or name} ovulga qo'shilish so'rovi yubordi", lid)
            await toast("So'rov yuborildi"); await refresh(cid, False)
        else:
            clan_join_user(uid, cid); await toast("Ovulga qo'shildingiz"); await refresh(cid)
    elif t == "clan_cancel":
        cid = _i(m.get("id"), 0, 10 ** 12); sql("DELETE FROM clan_reqs WHERE cid=? AND uid=?", (cid, uid))
        v = cinfo(uid, cid)
        if v: await send(uid, v)
    elif t == "clan_leave":
        if not c: return
        cid = c["id"]
        if role == "owner":
            if clan_count(cid) <= 1:
                clan_delete(cid); await toast("Ovul tarqatildi"); await send(uid, {"t": "me", "me": me_info(uid)}); await send(uid, {"t": "cgone"}); return
            nx = one("SELECT id FROM users WHERE clan_id=? AND id!=? ORDER BY (clan_role='mirza') DESC, points DESC LIMIT 1", (cid, uid))
            sql("UPDATE clans SET owner_id=? WHERE id=?", (nx["id"], cid)); sql("UPDATE users SET clan_role='' WHERE id=?", (nx["id"],))
            if nx["id"] in HUB: await toast("Siz endi ovulning Oqsoqolisiz", nx["id"])
        clan_drop_user(uid); await toast("Ovuldan chiqdingiz"); await refresh(cid)
    elif t == "clan_req":
        if not lead: return await err("Ruxsat yo'q")
        tid = _i(m.get("id"), 0, 10 ** 15)
        if not one("SELECT 1 FROM clan_reqs WHERE cid=? AND uid=?", (c["id"], tid)): return await err("So'rov topilmadi")
        sql("DELETE FROM clan_reqs WHERE cid=? AND uid=?", (c["id"], tid))
        if m.get("ok"):
            tu = one("SELECT clan_id FROM users WHERE id=?", (tid,))
            if not tu or tu["clan_id"]: await err("Bu o'yinchi boshqa ovulda")
            elif clan_count(c["id"]) >= CM: await err("Ovul to'la")
            else:
                clan_join_user(tid, c["id"]); await toast("Qabul qilindi")
                if tid in HUB: await send(tid, {"t": "me", "me": me_info(tid)}); await toast(f"Siz «{c['name']}» ovuliga qabul qilindingiz", tid)
        else: await toast("Rad etildi")
        await refresh(c["id"], False)
    elif t == "clan_kick":
        if not lead: return await err("Ruxsat yo'q")
        tid = _i(m.get("id"), 0, 10 ** 15); tu = one("SELECT clan_id,clan_role FROM users WHERE id=?", (tid,))
        if not tu or tu["clan_id"] != c["id"] or tid == uid: return
        if tid == c["owner_id"]: return await err("Oqsoqolni chiqarib bo'lmaydi")
        if role == "mirza" and tu["clan_role"] == "mirza": return await err("Mirzani faqat Oqsoqol chiqara oladi")
        clan_drop_user(tid)
        if tid in HUB: await send(tid, {"t": "me", "me": me_info(tid)}); await send(tid, {"t": "cgone"}); await toast("Siz ovuldan chiqarildingiz", tid)
        await toast("Ovuldan chiqarildi"); await refresh(c["id"], False)
    elif t == "clan_role":
        if role != "owner": return await err("Faqat Oqsoqol tayinlay oladi")
        tid = _i(m.get("id"), 0, 10 ** 15); tu = one("SELECT clan_id FROM users WHERE id=?", (tid,))
        if not tu or tu["clan_id"] != c["id"] or tid == uid: return
        mz = 1 if m.get("mirza") else 0
        if mz and one("SELECT COUNT(*) n FROM users WHERE clan_id=? AND clan_role='mirza'", (c["id"],))["n"] >= S("clan_mirza"):
            return await err(f"Mirzalar soni {S('clan_mirza')} tadan oshmasin")
        sql("UPDATE users SET clan_role=? WHERE id=?", ("mirza" if mz else "", tid))
        if tid in HUB: await send(tid, {"t": "me", "me": me_info(tid)})
        await toast("Mirza tayinlandi" if mz else "Mirzalik olib tashlandi"); await refresh(c["id"], False)
    elif t == "clan_transfer":
        if role != "owner": return await err("Faqat Oqsoqol topshira oladi")
        tid = _i(m.get("id"), 0, 10 ** 15); tu = one("SELECT clan_id FROM users WHERE id=?", (tid,))
        if not tu or tu["clan_id"] != c["id"] or tid == uid: return
        sql("UPDATE clans SET owner_id=? WHERE id=?", (tid, c["id"])); sql("UPDATE users SET clan_role='' WHERE id=?", (tid,)); sql("UPDATE users SET clan_role='mirza' WHERE id=?", (uid,))
        if tid in HUB: await send(tid, {"t": "me", "me": me_info(tid)}); await toast("Siz endi ovulning Oqsoqolisiz", tid)
        await toast("Oqsoqollik topshirildi"); await refresh(c["id"], False)
    elif t == "clan_disband":
        if role != "owner": return await err("Faqat Oqsoqol tarqata oladi")
        cid = c["id"]; mem = [r["id"] for r in allr("SELECT id FROM users WHERE clan_id=?", (cid,))]
        clan_delete(cid)
        for o in mem:
            if o in HUB: await send(o, {"t": "me", "me": me_info(o)}); await send(o, {"t": "cgone"})
        await toast("Ovul tarqatildi")
    elif t == "clan_chat":
        if c: await send(uid, {"t": "cchat", "list": cchat_list(c["id"])})
    elif t == "clan_send":
        if not c: return
        txt = _txt(m.get("text"), 300)
        if not txt or time.time() - CCOOL.get(uid, 0) < S("chat_gap"): return
        CCOOL[uid] = time.time(); nm = me["name"] or name
        cur = sql("INSERT INTO clan_chat(cid,uid,name,txt,ts) VALUES(?,?,?,?,?)", (c["id"], uid, nm, txt, int(time.time())))
        sql("DELETE FROM clan_chat WHERE cid=? AND id NOT IN (SELECT id FROM clan_chat WHERE cid=? ORDER BY id DESC LIMIT 200)", (c["id"], c["id"]))
        msg = {"id": cur.lastrowid, "uid": uid, "u": nm, "t": txt, "ts": int(time.time())}
        for v in clan_online(c["id"]):
            if uid not in blocked(v): await send(v, {"t": "cchat", "add": msg})
    elif t == "clan_invite":
        r = room_of(uid)
        if not c: return await err("Siz ovulda emassiz")
        if not r or r.phase != "lobby": return await err("Avval xonaga kiring")
        if time.time() - CINV.get(uid, 0) < 20: return await err("Chaqiruv yaqinda yuborilgan. Biroz kuting")
        CINV[uid] = time.time(); nm = me["name"] or name; n = 0
        for x in allr("SELECT id FROM users WHERE clan_id=? AND id!=?", (c["id"], uid)):
            v = x["id"]
            if v in HUB and v not in UROOM:
                await send(v, {"t": "cinv", "n": nm, "rid": r.id, "rn": r.name, "tag": c["tag"]}); n += 1
            elif v not in HUB and n < 25 and prefs(v)["n_friend"] and WEBAPP_URL:
                try:
                    await bot.send_message(v, f"{html.escape(nm)} ovul a'zolarini <b>{html.escape(r.name)}</b> xonasiga chaqirdi.",
                                           reply_markup=KB(inline_keyboard=[[B(text="Qo'shilish", web_app=WebAppInfo(url=f"{WEBAPP_URL}/?room={r.id}"))]])); n += 1
                except Exception: pass
        await toast(f"Chaqiruv yuborildi ({n})")


async def handle(uid, name, m):
    t = m.get("t"); r = room_of(uid)
    me = one("SELECT * FROM users WHERE id=?", (uid,)); name = me["name"] or name
    if t == "create" and not r:
        if cool(uid): return await send(uid, {"t": "err", "m": "Yaqinda o'yinni tark etgansiz. Biroz kuting"})
        pro = me["pro_until"] > PN()
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
        if t == "join": x = ROOMS.get(str(m.get("id", "")).strip().upper())
        else:
            c = [v for v in ROOMS.values() if not v.closed and v.phase == "lobby" and v.mode == "chat" and not v.req
                 and len(v.p) < v.cap]
            x = max(c, key=lambda v: len(v.p)) if c else None
        if t == "quick" and not x: return await handle(uid, name, {"t": "create", "name": "Tezkor xona", "mode": "chat"})
        if not x: return await send(uid, {"t": "err", "m": "Xona topilmadi"})
        if x.phase != "lobby":
            return await handle(uid, name, {"t": "watch", "id": x.id})
        if x.mode == "voice":  # ovozli xonaga avval tinglovchi bo'lib kiriladi, "O'yinga qo'shilish" bilan joy olinadi
            x.spec.add(uid); x.specinfo[uid] = (name, me["photo"] or ""); UROOM[uid] = x.id
            await x.push(); return await lobby_all()
        if cool(uid): return await send(uid, {"t": "err", "m": "Yaqinda o'yinni tark etgansiz. Biroz kuting"})
        if len(x.p) >= x.cap: return await send(uid, {"t": "err", "m": "Xona to'la"})
        if me["games"] < x.req: return await send(uid, {"t": "err", "m": f"Kirish uchun kamida {x.req} ta o'yin kerak"})
        if x.mode == "voice" and me["pro_until"] < PN() and me["free_voice"] <= 0:
            return await send(uid, {"t": "err", "m": "Bepul ovozli o'yinlar tugadi"})
        x.add(uid, name); UROOM[uid] = x.id; await x.push(); await lobby_all(); asyncio.create_task(notify_start(x))
    elif t == "watch" and not r:
        x = ROOMS.get(str(m.get("id", "")).strip().upper())
        if not x: return await send(uid, {"t": "err", "m": "Xona topilmadi"})
        if uid in x.p: return
        x.spec.add(uid); x.specinfo[uid] = (name, me["photo"] or ""); UROOM[uid] = x.id; await send(uid, x.view(uid))
    elif t == "leave":
        await leave(uid); await send(uid, lobby()); await send(uid, {"t": "me", "me": me_info(uid)})
    elif t == "start" and r and r.owner == uid and r.phase == "lobby" and r.mode != "table":
        if len(r.p) < r.minp: return await send(uid, {"t": "err", "m": f"Kamida {r.minp} o'yinchi kerak"})
        r.phase = "intro"; r.task = asyncio.create_task(r.run()); await lobby_all()
    elif t == "chat" and r and uid in r.p and r.mode != "table":
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
        if tg in r.p and r.p[tg]["alive"] and (role in MAFIA_TEAM or role in ("doktor", "komissar")):
            if role == "doktor":
                if tg == uid and uid in r.selfheal: return await send(uid, {"t": "err", "m": "O'zingizni faqat bir marta davolay olasiz"})
                if r.heal_prev.get(uid) == tg: return await send(uid, {"t": "err", "m": "Ketma-ket ikki tun bir kishini davolab bo'lmaydi"})
            if role == "komissar" and tg == uid: return await send(uid, {"t": "err", "m": "O'zingizni tekshira olmaysiz"})
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
        if r.mode == "table" and uid in r.skipd: r.skipd.discard(uid)
        else: r.skipd.add(uid)
        await r.push()
        if r.mode == "table" and len(r.skipd & set(r.alive())) > len(r.alive()) // 2: r.wake.set()
    elif t == "extend" and r and r.mode == "table" and r.phase == "day" and r.p.get(uid, {}).get("alive"):
        r.ext ^= {uid}; al = set(r.alive())
        if len(r.ext & al) > len(al) // 2:
            r.deadline += 120; r.dur += 120; r.ext = set(); r.say("Muhokama vaqti 2 daqiqaga uzaytirildi.")
        await r.push()
    elif t == "ready" and r and r.mode == "table" and r.phase == "lobby" and uid in r.p:
        r.ready ^= {uid}; await r.push(); await table_try_start(r)
    elif t == "ack" and r and r.phase == "reveal" and uid in r.p:
        r.ack ^= {uid}; await r.push()
        if all(u in r.ack for u, x in r.p.items() if not x["left"]): r.wake.set()
    elif t == "sit" and r and r.mode == "voice" and r.phase == "lobby" and uid in r.spec:
        pro = me["pro_until"] > PN()
        if cool(uid): return await send(uid, {"t": "err", "m": "Yaqinda o'yinni tark etgansiz. Biroz kuting"})
        if len(r.p) >= r.cap: return await send(uid, {"t": "err", "m": "Xona to'la"})
        if me["games"] < r.req: return await send(uid, {"t": "err", "m": f"Kirish uchun kamida {r.req} ta o'yin kerak"})
        if not pro and me["free_voice"] <= 0: return await send(uid, {"t": "err", "m": "Bepul ovozli o'yinlar tugadi"})
        r.spec.discard(uid); r.specinfo.pop(uid, None); r.add(uid, name)
        if not pro: await send(uid, {"t": "toast", "m": f"Bepul ovozli o'yinlar: {me['free_voice']} ta qoldi. O'yin boshlanganda bittasi hisoblanadi."})
        await r.push(); await lobby_all(); asyncio.create_task(notify_start(r))
    elif t == "mic" and r and uid in r.p:
        r.p[uid]["mic"] = bool(m.get("on")); await r.push()
    elif t == "pinfo":
        try: v = pinfo(uid, int(m.get("id", 0)))
        except Exception: v = None
        await send(uid, v or {"t": "err", "m": "O'yinchi topilmadi"})
    elif t == "cinfo":
        try: v = cinfo(uid, int(m.get("id", 0)))
        except Exception: v = None
        await send(uid, v or {"t": "err", "m": "Ovul topilmadi"})
    elif t in ("block", "unblock"):
        try: bid = int(m.get("id", 0))
        except Exception: return
        if bid and bid != uid:
            if t == "block": sql("INSERT OR IGNORE INTO blocks(uid,bid) VALUES(?,?)", (uid, bid))
            else: sql("DELETE FROM blocks WHERE uid=? AND bid=?", (uid, bid))
            BLK.pop(uid, None)
            await send(uid, {"t": "toast", "m": "Bloklandi" if t == "block" else "Blokdan chiqarildi"})
            v = pinfo(uid, bid)
            if v: await send(uid, v)
            if r: await send(uid, r.view(uid))
    elif t == "report":
        try: tid = int(m.get("id", 0))
        except Exception: return
        for o in OWNERS:
            try: await bot.send_message(o, f"Shikoyat: <b>{html.escape(name)}</b> (ID {uid}) -> ID {tid}" + (f"\nXona: {r.name} ({r.id})" if r else ""))
            except Exception: pass
        await send(uid, {"t": "toast", "m": "Shikoyat yuborildi"})
    elif t == "lastdone" and r and r.phase == "last" and r.speaker == uid:
        r.wake.set()
    elif t == "rtc" and r:
        try: to = int(m.get("to"))
        except Exception: return
        data = m.get("data")
        if (uid in r.p or uid in r.spec) and (to in r.p or to in r.spec) and to != uid and isinstance(data, dict) and len(json.dumps(data)) < 20000:
            await send(to, {"t": "rtc", "from": uid, "data": data})
    elif t in CLAN_T: await clan_handle(uid, name, t, m)
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
        if t == "fadd" and one("SELECT COUNT(*) n FROM friends WHERE uid=?", (uid,))["n"] >= FRIEND_MAX:
            return await send(uid, {"t": "err", "m": f"Do'stlar soni {FRIEND_MAX} tadan oshmasligi kerak"})
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
        if k == "n_priv" and v == 1 and not me["pro_until"] > PN(): return
        if k in ("n_start", "n_friend", "n_priv", "insta") and v in (0, 1): p[k] = v
        elif k == "gap" and v in (1, 3, 6, 12, 24): p[k] = v
        elif k == "voice" and v in ("auto", "direct", "proxy"): p[k] = v
        else: return
        sql("UPDATE users SET prefs=? WHERE id=?", (json.dumps(p), uid)); await send(uid, {"t": "me", "me": me_info(uid)})
    elif t == "setname":
        nn = _txt(m.get("name"), 24, True)
        if not nn: return await send(uid, {"t": "err", "m": "Ism bo'sh bo'lmasin"})
        sql("UPDATE users SET name=? WHERE id=?", (nn, uid)); await send(uid, {"t": "me", "me": me_info(uid)}); await send(uid, {"t": "toast", "m": "Ism saqlandi ✅"})
    elif t == "setbio":
        if not me["pro_until"] > PN(): return await send(uid, {"t": "err", "m": "Bio faqat Pro a'zolar uchun"})
        sql("UPDATE users SET bio=? WHERE id=?", (_txt(m.get("bio"), 150), uid)); await send(uid, {"t": "me", "me": me_info(uid)}); await send(uid, {"t": "toast", "m": "Saqlandi ✅"})
    elif t == "setavatar":
        d = str(m.get("d") or "")
        if "," in d[:80]: d = d.split(",", 1)[1]
        try: raw = base64.b64decode(d)
        except Exception: return await send(uid, {"t": "err", "m": "Rasm o'qilmadi"})
        if len(raw) < 200 or len(raw) > 900_000: return await send(uid, {"t": "err", "m": "Rasm hajmi mos emas"})
        if time.time() - AV_LAST.get(uid, 0) < 3: return await send(uid, {"t": "err", "m": "Biroz kuting..."})
        AV_LAST[uid] = time.time()
        try: ok = await asyncio.to_thread(save_avatar_jpg, str(AVDIR / f"{uid}.jpg"), raw)
        except Exception: ok = False
        if not ok: return await send(uid, {"t": "err", "m": "Rasm formati mos emas (JPG / PNG tanlang)"})
        sql("UPDATE users SET custom_photo=? WHERE id=?", (f"/u/{uid}.jpg?v={int(time.time())}", uid)); refresh_photo(uid)
        await send(uid, {"t": "me", "me": me_info(uid)}); await send(uid, {"t": "toast", "m": "Rasm yangilandi ✅"})
    elif t == "delavatar":
        sql("UPDATE users SET custom_photo='' WHERE id=?", (uid,)); refresh_photo(uid)
        try: (AVDIR / f"{uid}.jpg").unlink()
        except Exception: pass
        await send(uid, {"t": "me", "me": me_info(uid)}); await send(uid, {"t": "toast", "m": "Telegram rasmi qaytarildi"})
    elif t == "setbadge":
        n = _i(m.get("n"), 0, 10 ** 9, 0)
        if n:
            if not me["pro_until"] > PN(): return await send(uid, {"t": "err", "m": "Belgi faqat Pro a'zolar uchun"})
            if not one("SELECT 1 FROM badges WHERE num=?", (n,)): return await send(uid, {"t": "err", "m": "Belgi topilmadi"})
        sql("UPDATE users SET badge=? WHERE id=?", (n, uid)); refresh_photo(uid)
        await send(uid, {"t": "me", "me": me_info(uid)}); await send(uid, {"t": "toast", "m": "Belgi kiyildi ✅" if n else "Belgi yechildi"})
    elif t == "equip":
        n = _i(m.get("n"), 0, 10 ** 9, 0); fr = one("SELECT pro FROM frames WHERE num=?", (n,)) if n else None
        if n and not fr: return await send(uid, {"t": "err", "m": "Ramka topilmadi"})
        if n and not (one("SELECT 1 FROM user_frames WHERE uid=? AND num=?", (uid, n)) or (fr["pro"] and me["pro_until"] > PN())):
            return await send(uid, {"t": "err", "m": "Bu ramka sizda yo'q"})
        set_frame(uid, n); await send(uid, {"t": "me", "me": me_info(uid)}); await send(uid, {"t": "toast", "m": "Ramka kiyildi ✅" if n else "Ramka yechildi"})
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
    fix_perks(uid); refresh_photo(uid, photo or None)
    set_ref(uid, q.get("sp", ""))
    if one("SELECT banned FROM users WHERE id=?", (uid,))["banned"]:
        await ws.send_json({"t": "banned"}); return await ws.close()
    HUB[uid] = ws
    await ws.send_json({"t": "hello", "me": me_info(uid), "assets": amap(), "owner": uid in OWNERS, "bot": BOT_USERNAME,
                        "stars": {m: S("star_" + m) for m in PLANS}, "brand": S("brand"), "emap": EMAP, "ads": {k: S(k + "_url") for k in AD_URL_KEYS}, "ad_secs": S("ad_secs"),
                        "emb": emb_cfg(), "cc": {"pro": S("clan_pro"), "max": S("clan_max")}, "ice": ice_servers()})
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

@app.get("/u/{fn}")
async def user_avatar(fn: str):
    p = AVDIR / fn if re.fullmatch(r"\d{1,15}\.jpg", fn) else None
    return FileResponse(p, headers={"Cache-Control": "public,max-age=86400"}) if p and p.exists() else Response(status_code=404)

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
.afw{position:relative;display:inline-flex;flex:0 0 auto;line-height:0;vertical-align:middle}.afw .afr{position:absolute;left:-14%;top:-14%;width:128%;height:128%;max-width:none;pointer-events:none;object-fit:contain}
.gc .afw{display:flex;width:100%}.pl .afw,.pcd .afw,.vc .afw,.vcc .afw{display:flex;width:max-content;margin:0 auto}
.frs{display:flex;gap:10px;flex-wrap:wrap}.frb{width:68px;height:68px;border-radius:16px;border:2px solid var(--bd);background:var(--cd2);padding:6px;font-weight:700;color:inherit}.frb img{width:100%;height:100%;object-fit:contain}.frb.on{border-color:var(--go)}
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
.cl .tg{margin-left:6px;font-size:13px}
.prize{border:1px solid rgba(226,180,79,.45);background:rgba(226,180,79,.10);border-radius:16px;padding:12px 14px;font-size:16px;margin:10px 0}
.rdt{padding-bottom:30px}.rhero{position:relative;border-radius:22px;overflow:hidden;background:linear-gradient(180deg,#2a4a9c,#101a3a);height:340px;display:flex;align-items:flex-end;justify-content:center}
.rhero.m{background:linear-gradient(180deg,#7a2a3c,#24101a)}.rhero img{height:100%;max-width:100%;object-fit:contain;object-position:center bottom}.rhero .rem{font-size:120px;padding-bottom:30px}
.rhero .rb,.rhero .rpg{position:absolute;top:12px;background:rgba(0,0,0,.5);color:#fff;border:0;border-radius:14px;padding:7px 12px;font-weight:700;font-size:14px}
.rhero .rb{left:12px}.rhero .rpg{right:12px;display:flex;gap:10px;align-items:center;padding:4px 8px}.rhero .rpg button{background:none;border:0;color:#fff;font-size:20px;padding:0 6px}
.rdt h1{margin:18px 4px 2px;font-size:30px}.rsub{margin:0 4px 12px;font-size:12px;letter-spacing:1.5px;font-weight:800;color:#6b8cff}.rsub.m{color:#ff6b78}
.rds{margin:0 4px 14px;color:var(--mu);line-height:1.55;font-size:16px}.rbl>div{background:var(--cd);border:1px solid var(--bd);border-radius:12px;padding:11px 14px;margin:8px 0;font-size:15px}
.rtip{background:rgba(91,108,255,.14);border:1px solid rgba(91,108,255,.4);border-radius:14px;padding:13px 15px;margin-top:14px;line-height:1.5;font-size:15px}.rtip.w{background:rgba(226,180,79,.12);border-color:rgba(226,180,79,.45)}
.rc{cursor:pointer}
.rl{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.rc{border-radius:20px;overflow:hidden;position:relative;border:1px solid #27407a;background:linear-gradient(180deg,#14214a,#101a3a);min-height:140px;text-align:left}
.rc.m{border-color:#5a2230;background:linear-gradient(180deg,#321423,#241019)}
.rc .im{height:210px;display:flex;align-items:flex-end;justify-content:center;font-size:70px;padding-top:30px}.rc .im img,.rc .im video{width:100%;height:100%;min-width:0;object-fit:contain;object-position:center bottom}
.rc b{color:#fff;display:block;padding:10px 14px 14px;font-size:21px}
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
.mini{display:flex;gap:12px;align-items:center}.mini .ri{width:56px;height:56px;border-radius:14px;background:var(--cd2);display:flex;align-items:center;justify-content:center;font-size:30px;overflow:hidden}.mini .ri img{width:100%;height:100%;object-fit:contain}
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
.reveal .ri{height:160px;display:flex;align-items:center;justify-content:center;font-size:90px}.reveal .ri img,.reveal .ri video{height:100%;max-width:100%;object-fit:contain}
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
.hc .ri{width:60px;height:60px;border-radius:14px;background:var(--cd2);display:flex;align-items:center;justify-content:center;font-size:30px;flex:0 0 60px;overflow:hidden}.hc .ri img{width:100%;height:100%;object-fit:contain}
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
.cr .cn b{font-size:17px}.cr .tg{margin-left:4px;font-size:12px}
.tg.t1,.tg.t2,.tg.t3,.tg.t4{display:inline-block;color:#fff;border-radius:8px;padding:2px 10px;font-weight:800;letter-spacing:1px;text-shadow:0 1px 2px rgba(0,0,0,.35);box-shadow:inset 0 0 0 1px rgba(255,255,255,.18);background-size:220% 100%;animation:tgs 2.6s linear infinite}
@keyframes tgs{0%{background-position:120% 0}100%{background-position:-120% 0}}
.tg.t1{background-image:linear-gradient(110deg,#b3202a 30%,#ff7a80 50%,#b3202a 70%)}
.tg.t2{background-image:linear-gradient(110deg,#5f33b8 30%,#c9a2ff 50%,#5f33b8 70%)}
.tg.t3{background-image:linear-gradient(110deg,#1b58b8 30%,#74b6ff 50%,#1b58b8 70%)}
.tg.t4{background-image:linear-gradient(110deg,#1b8038 30%,#78e496 50%,#1b8038 70%)}
.tgpv{text-align:center;margin:12px 0 0;min-height:30px;font-size:15px}

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
.btn img,.chero b img{height:1.3em;width:1.3em;object-fit:contain;vertical-align:-.25em;flex:0 0 auto}.act .btn{white-space:nowrap;min-width:0}
/* ---- ovozli xona / stol / do'stlar / profil ---- */
.trow{display:flex;align-items:center;gap:10px;margin:8px 0}.trow .f{flex:1}
.xr{border:1px solid rgba(229,72,77,.45);background:rgba(229,72,77,.12);color:var(--rd);border-radius:999px;font-weight:700;padding:6px 14px;font-size:14px}
.xr:not(.pill){width:30px;height:30px;padding:0;display:flex;align-items:center;justify-content:center;font-size:14px}
.tb{width:36px;height:36px;border-radius:50%;border:1px solid var(--bd);background:var(--cd);display:flex;align-items:center;justify-content:center;color:var(--mu);padding:0}.tb.on{color:var(--ac)}
.hline{height:2px;margin:2px 0 8px;background:linear-gradient(90deg,transparent,var(--ac),transparent);opacity:.5}
.pbar{position:fixed;left:0;right:0;top:0;height:3px;z-index:40}.pbar i{display:block;height:100%;width:100%;background:linear-gradient(90deg,var(--rd),var(--ac2))}
.qrc{text-align:center;padding:18px 14px}.qrb{display:inline-block;background:#fff;padding:6px;border-radius:12px}.qrb svg{display:block}
.cdd{letter-spacing:2px;color:var(--tx)}.shl{background:none;border:0;color:var(--ac);font-weight:700;display:inline-flex;gap:6px;align-items:center;margin-top:10px}
.chips2{display:flex;flex-wrap:wrap;gap:8px;justify-content:center;margin:6px 0}
.pc{display:inline-flex;align-items:center;gap:6px;background:var(--cd);border:1px solid var(--bd);border-radius:999px;padding:5px 12px 5px 6px;font-weight:600;font-size:13px}.pc .av{width:22px;height:22px;font-size:10px}.pc.rdy{background:rgba(47,209,106,.16);border-color:rgba(47,209,106,.5)}.pc i{color:var(--gr);font-style:normal;display:flex}
.cbar{position:fixed;left:12px;right:12px;bottom:calc(10px + env(safe-area-inset-bottom));display:flex;flex-direction:column;gap:8px;z-index:20;max-width:520px;margin:0 auto}
.cons{position:relative;overflow:hidden;text-align:left;border:1px solid var(--bd);background:var(--cd);border-radius:14px;padding:10px 14px;width:100%;display:block}.cons.on{border-color:var(--ac)}
.cons i{position:absolute;left:0;top:0;bottom:0;background:rgba(91,108,255,.28);transition:width .3s}
.cons .ct{position:relative;display:flex;justify-content:space-between;align-items:center;font-size:15px}.cons .ct b{display:flex;align-items:center;gap:6px}.cons .ct span{font-weight:800;color:var(--mu);font-size:14px}.cons.on .ct span{color:var(--ac)}
.cons .cs{position:relative;font-size:12px;color:var(--mu);margin-top:2px}
.warn2{display:flex;align-items:center;gap:10px;background:#f3e8d6;border:1px solid #e0a15a;color:#3a2a10;border-radius:14px;padding:10px 12px;font-size:13px}
html:not([data-theme=light]) .warn2{background:#2d2414;color:#f1dcb8;border-color:#7a5a22}
.deal{text-align:center;margin-top:18vh}.ring{width:100px;height:100px;border-radius:50%;border:6px solid var(--bd);display:flex;align-items:center;justify-content:center;margin:0 auto 22px}.ring b{font-size:40px}
.deal p{max-width:320px;margin:0 auto;line-height:1.5}
.rvw{display:flex;align-items:center;justify-content:center;min-height:56vh}
.rcard{width:100%;max-width:340px;background:var(--cd);border:1px solid var(--bd);border-radius:22px;text-align:center;padding:26px 20px;box-shadow:0 14px 34px var(--sh);background-image:radial-gradient(120% 90px at 50% 0,rgba(229,72,77,.28),transparent 70%)}
.rcard.maf{border-color:var(--rd)}
.rcard .ri{height:96px;display:flex;align-items:center;justify-content:center;margin-bottom:8px;font-size:60px}.rcard .ri img{max-height:96px;max-width:100%;object-fit:contain}
.rcard small{letter-spacing:2px;color:var(--mu);font-weight:700;font-size:12px}.rcard h1{margin:6px 0 10px;font-size:28px}.rcard p{margin:0;color:var(--mu);line-height:1.5;font-size:14px}
.bigt{text-align:center;margin-top:24vh}.bigt b{display:block;font-size:68px;font-weight:800;letter-spacing:1px}.bigt p{color:var(--mu);margin:8px 0 0}
.moon{font-size:64px}.moon img{height:84px}
.vstrip{display:flex;gap:6px;background:var(--cd);border:1px solid var(--bd);border-radius:16px;padding:6px;margin:8px 0;overflow:auto}
.vcc{position:relative;flex:1 0 54px;text-align:center;border:1.5px solid rgba(229,72,77,.25);border-radius:11px;padding:6px 2px;font-size:11px;font-weight:700;background:var(--cd);min-width:0}
.vcc .av{width:34px;height:34px;display:flex;margin:0 auto 3px}.vcc span{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.vcc.sel{border-color:#f0a020;box-shadow:0 0 0 1px #f0a020}.vcc.off{opacity:.45;pointer-events:none}.vcc .vb{position:absolute;top:-6px;right:-4px;min-width:18px;height:18px;border-radius:9px;background:var(--rd);color:#fff;font-style:normal;font-size:11px;display:flex;align-items:center;justify-content:center;padding:0 4px}
.vrow{display:flex;align-items:center;gap:8px;background:var(--cd);border:1px solid var(--bd);border-radius:12px;padding:8px 10px;margin:6px 0;font-size:13px}.vrow .av{width:24px;height:24px;font-size:11px}.vrow i{color:var(--mu);font-style:normal}
body[data-mode=table][data-ph=vote]{background:#1c1114}
[data-theme=light] body[data-mode=table][data-ph=vote]{background:#f7ebea}
.vchip{display:inline-flex;align-items:center;gap:6px;background:var(--cd);border:1px solid var(--bd);border-radius:999px;padding:5px 12px;font-size:13px;font-weight:700;color:var(--mu)}.vchip.g{color:var(--gr);background:rgba(47,209,106,.14);border-color:rgba(47,209,106,.4)}
.vbn{display:flex;gap:10px;align-items:flex-start;background:var(--cd);border:1px solid var(--bd);border-radius:14px;padding:12px 14px;margin:10px 0;font-size:14px;line-height:1.4}.vbn.l{background:rgba(124,92,255,.18);border-color:rgba(124,92,255,.4)}
.vwrap{display:flex;flex-direction:column;min-height:calc(100vh - 110px);clear:both}.vbot{margin-top:auto;position:sticky;bottom:0;background:var(--bg);padding:4px 0 calc(8px + env(safe-area-inset-bottom))}
.seats{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:10px 0}
.seat{position:relative;text-align:center;border-radius:12px;padding:10px 2px 8px;min-width:0;font-size:12px;font-weight:700;display:flex;flex-direction:column;align-items:center;gap:6px;min-height:98px}
.seat.on{background:var(--cd);border:1px solid var(--bd)}.seat.me{border-color:var(--ac)}
.seat .sav{position:relative}.seat .sav .av{width:46px;height:46px;font-size:18px}
.seat .mc{position:absolute;right:-6px;bottom:-4px;width:18px;height:18px;border-radius:50%;background:var(--cd2);border:1px solid var(--bd);display:flex;align-items:center;justify-content:center;color:var(--mu)}.seat .mc.on{background:var(--gr);color:#fff;border-color:transparent}
.seat span.nm{display:block;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding:0 3px}
.seat .siz{position:absolute;top:3px;left:6px;font-size:9px;color:var(--ac);font-weight:800}.seat .crw{position:absolute;top:3px;right:6px;font-size:11px}
.seat.em{color:var(--mu);opacity:.75}.seat.em .plus{width:46px;height:46px;border-radius:50%;border:1.5px dashed var(--bd);display:flex;align-items:center;justify-content:center;font-size:22px;font-weight:400}
.vctl{display:flex;justify-content:center;align-items:center;gap:14px;margin:16px 0 4px}
.vb2{width:46px;height:46px;border-radius:50%;background:var(--cd);border:1px solid var(--bd);display:flex;align-items:center;justify-content:center;padding:0;color:var(--tx)}.vb2.off{color:var(--mu)}
.vmic{width:62px;height:62px;border-radius:50%;border:0;background:#5b6068;color:#fff;display:flex;align-items:center;justify-content:center;padding:0}.vmic.red{background:var(--rd);box-shadow:0 6px 18px rgba(229,72,77,.4)}.vmic.on{background:var(--gr);box-shadow:0 6px 18px rgba(47,209,106,.4)}
.vst{text-align:center;color:var(--mu);font-size:12px;margin-bottom:10px}
.free{background:#2fd16a;color:#06330f;border-radius:6px;padding:1px 6px;font-size:11px;font-weight:800}
.fcnt{text-align:center;color:var(--mu);font-size:16px;margin:-4px 0 6px}
.fr{display:flex;align-items:center;gap:12px;background:var(--cd);border:1px solid var(--bd);border-radius:22px;padding:14px;margin:10px 0}.fr .av{width:54px;height:54px;font-size:20px}.fr .f{min-width:0}
.fr b{font-size:18px}.fr .fs{display:flex;align-items:center;gap:8px;color:var(--mu);font-size:14px;margin-top:3px;min-width:0}.fr .fs span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sdot{width:11px;height:11px;border-radius:50%;flex:0 0 auto;background:#9aa0b4}.sdot.g{background:var(--gr)}.sdot.b{background:#3f5eed}
.fr .fj{flex:0 0 auto;width:auto;padding:13px 20px;font-size:16px;border-radius:14px}
.pm{position:relative;padding-top:22px}.pmtag{position:absolute;top:-10px;left:14px}
.pmh{display:flex;align-items:center;gap:12px}.pmh .f{min-width:0;flex:1}.pmn{font-size:22px;font-weight:800;line-height:1.2}
.pmb{width:44px;height:44px;border-radius:12px;border:0;background:linear-gradient(135deg,#3b5bff,#5b6cff);color:#fff;font-size:20px;padding:0}.pmb.on{background:var(--cd2);color:var(--gr);border:1px solid var(--bd)}
.pmc{display:flex;align-items:center;gap:12px;background:linear-gradient(135deg,#2b1d12,#4a2f17);border:1px solid #8a6a2b;border-radius:16px;padding:12px;margin:14px 0 10px;color:#fff}
.pml{width:52px;height:52px;border-radius:12px;background:rgba(0,0,0,.35);display:flex;align-items:center;justify-content:center;font-size:26px}.pml img{width:38px}
.pmk{display:flex;align-items:center;gap:8px;font-size:11px;font-weight:800;color:#d9b45a;letter-spacing:1px}.pmcn{font-weight:800;font-size:19px}.pmr{margin-left:auto;background:linear-gradient(135deg,#f3d58c,#b98a2b);color:#241a05;font-weight:800;border-radius:10px;padding:3px 9px;font-size:13px}
.pmx{display:flex;gap:8px;margin:8px 0}.pmx button{flex:1;padding:12px;border-radius:12px;border:1px solid var(--bd);background:var(--cd2);font-weight:700}.pmx .pbl{background:rgba(229,72,77,.14);color:var(--rd);border-color:rgba(229,72,77,.4)}
.pms{max-height:46vh;overflow:auto;margin-bottom:10px}
.stat2{display:grid;grid-template-columns:1fr 1fr;gap:8px}.stat2>div{background:var(--cd2);border:1px solid var(--bd);border-radius:14px;padding:12px}.stat2 b{display:block;font-size:26px}.stat2 span{font-size:13px;color:var(--mu)}
.tm2{background:var(--cd2);border:1px solid var(--bd);border-radius:14px;padding:12px}.tm2 .tr{display:grid;grid-template-columns:70px 1fr auto;align-items:center;gap:10px;margin:8px 0}.tm2 .bar{height:7px;border-radius:5px;background:var(--bd);overflow:hidden}.tm2 .bar i{display:block;height:100%;border-radius:5px}
.rr{display:flex;align-items:center;gap:10px;background:var(--cd2);border:1px solid var(--bd);border-radius:12px;padding:8px 10px;margin:6px 0}.rr .ri2{width:36px;height:36px;display:flex;align-items:center;justify-content:center;font-size:22px}.rr .ri2 img{max-width:100%;max-height:100%;object-fit:contain}.rr .f{min-width:0;flex:1}
.cmh{display:flex;justify-content:space-between;align-items:center;font-weight:800;margin-bottom:10px}
.cmm{display:flex;align-items:center;gap:10px;background:var(--cd2);border:1px solid var(--bd);border-radius:14px;padding:10px;margin:8px 0}.cmm .av{width:42px;height:42px}.cmm .rl{font-size:12px;font-weight:700}.cmm .p7{margin-left:auto;background:rgba(47,209,106,.16);color:var(--gr);font-weight:800;border-radius:12px;padding:6px 12px;white-space:nowrap}
.sheet.cm{max-height:88vh}
/* ===== OVUL v2: belgi (emblema), teg, ro'yxat, sahifa ===== */
textarea{font:inherit;color:var(--tx);background:var(--cd2);border:1px solid var(--bd);border-radius:14px;padding:12px 14px;width:100%;outline:none;resize:vertical}textarea:focus{border-color:var(--ac)}
.emb{display:inline-block;width:18px;height:18px;border-radius:5px;background-repeat:no-repeat;overflow:hidden;vertical-align:-4px;flex:0 0 auto;font-size:11px;line-height:1;color:#fff}
.emb img{width:100%;height:100%;object-fit:cover;display:block}
.emb.fb{display:inline-flex;align-items:center;justify-content:center}
.emb.s{width:22px;height:22px;border-radius:6px;font-size:13px}
.emb.m{width:52px;height:52px;border-radius:13px;font-size:26px;box-shadow:0 3px 10px rgba(0,0,0,.28)}
.emb.l{width:72px;height:72px;border-radius:18px;font-size:38px;box-shadow:0 4px 14px rgba(0,0,0,.32)}
.emb.q{width:100%;height:auto;aspect-ratio:1;border-radius:12px;font-size:24px;vertical-align:top;display:block}.emb.fb.q{display:flex}
.pml .emb{width:100%;height:100%;border-radius:12px;box-shadow:none}
.nbe{display:inline-block;margin-right:4px;vertical-align:-3px;line-height:1}.nbe .emb{width:16px;height:16px;border-radius:4px;font-size:10px;vertical-align:top}
.gc .nbe .emb,.vc .nbe .emb,.vcc .nbe .emb,.pcd .nbe .emb,.seat .nbe .emb{width:13px;height:13px;border-radius:3px;font-size:8px}
/* ===== profil: belgi, ramka, Pro bloki, avatar ===== */
.nbp{display:inline-block;margin-right:4px;vertical-align:-4px;line-height:1}.nbp img{width:18px;height:18px;object-fit:contain;display:block}
.gc .nbp img,.vc .nbp img,.vcc .nbp img,.pcd .nbp img,.seat .nbp img{width:14px;height:14px}
.afw.bg{margin:18px 24px 18px 16px}.card .afw.bg .big-av{width:76px;height:76px;font-size:28px}.card .row>.f{min-width:0}.avs{position:relative;width:max-content;margin:0 auto;padding:30px}.avs .afw.bg{margin:0}.avs .big-av{width:104px;height:104px;font-size:38px}
.avs .cam{position:absolute;right:12px;bottom:12px;width:36px;height:36px;border-radius:50%;border:3px solid var(--bg);background:var(--ac);color:#fff;display:flex;align-items:center;justify-content:center;font-size:16px;padding:0;line-height:1}
.lnkb{background:none;border:0;color:var(--ac);font-weight:700;padding:6px 10px;font-size:14px}
.prb{border:1px solid var(--bd);background:var(--cd);border-radius:20px;padding:14px;margin:8px 0}
.prb .ph{display:flex;align-items:center;gap:8px;font-weight:800;font-size:17px;color:var(--go)}.prb .ph i{width:7px;height:7px;border-radius:50%;background:var(--mu);display:inline-block}
.prb .pd{color:var(--mu);margin:6px 0 14px;font-size:16px}
.prr{display:flex;align-items:center;gap:14px;background:var(--cd2);border:1px solid var(--bd);border-radius:16px;padding:12px 14px;margin:10px 0;cursor:pointer;min-height:56px}
.prr b{font-size:14px;letter-spacing:1px;color:var(--mu);min-width:68px}.prr .v{flex:1;display:flex;align-items:center;gap:10px;color:var(--mu);font-size:19px;min-width:0}
.prr .v .afw{margin:0 8px 0 10px}.prr .v>img{width:34px;height:34px;object-fit:contain}.prr .v .av{width:34px;height:34px;font-size:14px}.prr .ch{color:var(--mu);font-size:24px}.prr.lk{opacity:.55}
.bio{display:block;width:100%;min-height:96px;resize:none;background:var(--cd2);border:1px solid var(--bd);border-radius:16px;padding:12px 14px;color:var(--tx);font:inherit;font-size:16px;outline:none}
.bio:focus{border-color:var(--ac)}
.pkg{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:12px 0}.pkg.c3{grid-template-columns:repeat(3,1fr)}
.pkg button{aspect-ratio:1;border-radius:16px;border:2px solid var(--bd);background:var(--cd2);padding:8px;display:flex;align-items:center;justify-content:center;font-weight:800;color:var(--mu);font-size:15px}
.pkg button.on{border-color:var(--go)}.pkg.bd button>img{width:100%;height:100%;object-fit:contain}.pkg .av{width:50px;height:50px;font-size:19px}
.pmbio{margin:12px 0 2px;padding:10px 12px;border-radius:14px;background:var(--cd2);border:1px solid var(--bd);font-size:15px;line-height:1.4;white-space:pre-wrap;word-break:break-word}
.ctg{display:inline-block;color:#fff;border-radius:6px;padding:1px 7px;font-size:11px;font-weight:800;letter-spacing:.3px;vertical-align:middle;line-height:1.55;white-space:nowrap}
.ctg.lg{font-size:13px;padding:2px 10px;border-radius:8px}.ctg.nbt{margin-right:5px;font-size:10px;padding:0 5px;border-radius:5px}
.ctg.c1{background:#e5383b}.ctg.c2{background:#7c4dff}.ctg.c3{background:#2f6fe4}.ctg.c4{background:#1fa65a}.ctg.c5{background:#7a8099}
.ctg.c6{background:linear-gradient(135deg,#ffe28a,#e0a41c);color:#4a3000;text-shadow:none}
.ctg.c7{background:linear-gradient(180deg,#3a0808,#8a1414);border:1px solid #ff4b4b;box-shadow:0 0 8px rgba(255,59,59,.75);letter-spacing:.8px}
.rkp{font-size:12px;font-weight:800;color:var(--mu)}.rkp.r1,.rkp.r2,.rkp.r3{background:rgba(243,193,74,.22);color:#b7791f;border-radius:6px;padding:1px 6px}
.cr{cursor:pointer}.cr .cn{display:flex;flex-wrap:wrap;align-items:center;gap:3px 7px;line-height:1.25}.cr .cn b{font-size:16px}.cr .cs{font-size:12.5px;margin-top:3px}
.chd{padding:16px}.chd .chr{display:flex;align-items:center;gap:14px}.chd .cnm{display:flex;align-items:center;flex-wrap:wrap;gap:6px 8px}.chd .cnm b{font-size:20px;line-height:1.2}
.lvrow{display:flex;align-items:center;gap:10px;font-size:12.5px;color:var(--mu);margin-top:14px;font-weight:600}
.lvrow>span{display:inline-flex;align-items:center;gap:6px;white-space:nowrap}
.lvbar{height:7px;border-radius:7px;background:var(--bd);flex:1;overflow:hidden;min-width:40px}.lvbar i{display:block;height:100%;border-radius:7px;background:linear-gradient(90deg,#ff6a6f,#e5383b)}
.cpl{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
.pill2{display:inline-flex;align-items:center;gap:5px;border-radius:9px;padding:3px 10px;font-weight:800;font-size:13px;background:linear-gradient(135deg,#ffe9a6,#f3c14a);color:#5a3d00}
.pill2.sp{background:linear-gradient(135deg,#5a0f0f,#a31b1b);color:#fff}
.own{margin:8px 2px;font-size:13.5px}.own b{color:var(--tx)}
.acc{display:flex;align-items:center;gap:8px;color:var(--mu);font-size:13px;font-weight:700;padding:8px 4px;cursor:pointer}
.rulesb{background:var(--cd2);border:1px solid var(--bd);border-radius:14px;padding:12px;white-space:pre-wrap;font-size:14px;margin:0 0 8px;word-break:break-word}
.cact{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:10px 0}.cact .btn{padding:12px 8px;font-size:14px}
.rcn{display:inline-block;background:var(--rd);color:#fff;border-radius:10px;padding:0 7px;font-size:12px;line-height:19px}
.cmm .dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:#9aa0b5;margin-right:4px;vertical-align:1px}.cmm .dot.on{background:#2fd16a}
.cmm b{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.cmm .mg{border:0;background:transparent;color:var(--mu);font-size:22px;padding:2px 8px;font-weight:800}
.rq{margin-left:auto;display:flex;gap:8px}.rq button{width:40px;height:40px;border-radius:12px;border:0;font-size:18px;font-weight:800;color:#fff}.rqy{background:#2fd16a}.rqn{background:var(--rd)}
.epk{display:grid;grid-template-columns:repeat(6,1fr);gap:7px}.epk button{border:2px solid transparent;border-radius:14px;padding:2px;background:var(--cd2)}.epk button.on{border-color:var(--ac);box-shadow:0 0 0 3px rgba(91,108,255,.25)}
.npv{display:flex;align-items:center;gap:6px;background:var(--cd2);border:1px solid var(--bd);border-radius:14px;padding:11px 12px;margin-top:10px;font-weight:700;min-height:46px}
.sheet.cfm .seg{margin-top:2px}
.cch{display:flex;flex-direction:column;gap:8px;padding:6px 0 90px}
.cbm{max-width:82%;align-self:flex-start;background:var(--cd);border:1px solid var(--bd);border-radius:16px 16px 16px 4px;padding:8px 12px}
.cbm.me{align-self:flex-end;background:linear-gradient(135deg,var(--ac),var(--ac2));color:#fff;border-color:transparent;border-radius:16px 16px 4px 16px}
.cbm .cbn{font-size:12px;font-weight:800;color:var(--go)}.cbm.me .cbn{display:none}.cbm .cbb{word-break:break-word;white-space:pre-wrap}.cbm .cbt{font-size:10.5px;opacity:.6;text-align:right;margin-top:2px}
.cchbar{position:fixed;left:0;right:0;bottom:0;display:flex;gap:8px;padding:10px 12px calc(10px + env(safe-area-inset-bottom));background:var(--bg);border-top:1px solid var(--bd);z-index:5}.cchbar input{flex:1}.cchbar .btn{width:auto;padding:0 18px}
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
let ws,ME={},A={},OWN=false,BOT="",STARS={},ROOM=null,LOBBY=[],PAGE="home",MODAL=null,CR={},RAT=null,RS={kind:"players",scope:"week"},TASKS=null,HIST=null,FR=null,FS=null,REPLY=null,COMPOSE=false,REV="",SPK=true,PRO={mo:"3"},LS=null,PC={},RFILT="all",RSEL=null,TBL=true,SPARAM="";
let PI=null,CI=null,VIB=true,MIC=false,FRMAX=200;let BRD="Play Mafia",SEL=null,BN=0,CQ="",CV=null,ADS={},AD_SECS=4,adT=0;
const PH={lobby:"Kutish",deal:"Boshlanmoqda",reveal:"Rollar",intro:"Tanishuv",night:"Tun",day:"Muhokama",vote:"Ovoz berish",last:"So'nggi so'z",ended:"Tugadi"};
const MODE={chat:"Chat",voice:"Ovozli",table:"Stol"};const MT=["mafia","don","advokat","ubiytsa"];
const ROLES={
tinch:{n:"Tinch axoli",e:"🧑",s:"t",since:"Ko'p",live:1,d:"Sizning vazifangiz mafiani topish va ovoz berish jarayonida ularni osish.",
 b:["Maxsus qobiliyati yo'q","Kunduzi ovoz berishda qatnashadi","Maqsad — mafiyani topib osish"],
 t:"Kunduzi diqqat bilan tinglang: kim ko'p gapirdi, kim jim turdi — mafiyani shu bilan topasiz."},
komissar:{n:"Komissar katani",e:"🕵️",s:"t",since:"8+",live:1,d:"Shahar himoyachisi. Har tun bir kishini tekshirasiz yoki otasiz. Mafiyani topib, shaharni qutqaring.",
 b:["Shahar himoyachisi","Har tun 1 kishini tekshiradi yoki otadi","Mafiyani topib shaharni qutqaradi"],
 note:"Hozircha o'yinda faqat tekshirish ishlaydi; otish keyin qo'shiladi.",
 t:"Topgan narsangizni darhol oshkor qilmang — avval ishonchli odam toping. Sizni mafiya birinchi nishonga oladi."},
doktor:{n:"Doktor",e:"🩺",s:"t",since:"6+",live:1,d:"Har tun bitta o'yinchini davolaysiz: unga hujum bo'lsa, o'lmaydi. Boshqalarni ketma-ket davolay olasiz, o'zingizni esa o'yinda faqat bir marta.",
 b:["Har tun 1 kishini davolaydi","Davolangan o'yinchiga hujum bo'lsa, u o'lmaydi","Boshqalarni ketma-ket davolay oladi","O'zini o'yinda faqat 1 marta davolaydi"],
 t:"O'zingizni davolashni oxirgi chora uchun saqlang."},
serjant:{n:"Serjant",e:"👮",s:"t",since:"Tez orada",d:"Komissarning yordamchisi: u qilgan ishlardan xabar topib turasiz. Komissar o'lsa, uning o'rnini egallaysiz.",
 b:["Komissarning yordamchisi","Komissar qilgan ishlardan xabar topadi","Komissar o'lsa, uning o'rnini egallaydi"],
 t:"Komissar o'lsa — endi siz shaharning asosiy tekshiruvchisisiz."},
admiral:{n:"Admiral",e:"🎖️",s:"t",since:"Tez orada",d:"Komissar katani yoki Serjant tirik ekan, sizni tunda hech kim o'ldira olmaydi. Ikkalasi ham o'lsa, siz Komissar katani bo'lasiz.",
 b:["Komissar yoki Serjant tirik ekan, tunda o'ldirib bo'lmaydi","Ikkalasi ham o'lsa, Komissar katani bo'ladi"],
 t:"Kunduzi ovoz bilan chiqarib yuborilishingiz mumkin — ehtiyot bo'ling."},
daydi:{n:"Daydi",e:"🍾",s:"t",since:"Tez orada",d:"Har tun kimningdir uyiga butilka olgani borasiz va u yerda hujum qilganni ko'rib qolishingiz mumkin.",
 b:["Har tun birovning uyiga butilka olgani boradi","U yerda hujum qilganni ko'rib qolishi mumkin"],
 t:"Ko'rganingizni kunduzi shaharga aytib bering."},
koldun:{n:"Koldun",e:"⚡",s:"t",since:"Tez orada",d:"Tunda tanlagan Shahar tomoni o'yinchisini ertangi osishdan himoya qilasiz. Boshqa tomon bo'lsa, u halok bo'ladi.",
 b:["Tunda 1 o'yinchini ertangi osishdan himoya qiladi","Tanlangan o'yinchi Shahar tomonida bo'lmasa, halok bo'ladi"],
 t:"Faqat ishonchli Shahar o'yinchisini tanlang — xato qilsangiz, u halok bo'ladi."},
mashuqa:{n:"Mashuqa",e:"💋",s:"t",since:"Tez orada",d:"Har tun bir kishini band qilasiz: u bu tun harakat qila olmaydi. Shahar tomonida o'ynaysiz.",
 b:["Har tun 1 kishini band qiladi","Band qilingan o'yinchi bu tun harakat qila olmaydi","Shahar tomonida o'ynaydi"],
 t:"Faol rolli (masalan, Komissar yoki Doktor) o'yinchini band qilib, mafiyaga imkon bermang."},
afsungar:{n:"Afsungar",e:"☠",s:"t",since:"Tez orada",d:"Tunda o'ldirilsangiz, sizni o'ldirgan ham o'ladi. Kunduzi osilsangiz, bitta o'yinchini o'zingiz bilan olib ketasiz.",
 b:["Tunda o'ldirilsa, qotili ham o'ladi","Kunduzi osilsa, 1 o'yinchini o'zi bilan olib ketadi"],
 t:"Sizga hujum qilish xavfli — buni mafiya bilmasligi ham mumkin."},
buqalamun:{n:"Buqalamun",e:"🦎",s:"t",since:"Tez orada",d:"Komissar o'lsa Serjantga, Don o'lsa Mafiaga, Qotil o'lsa Qotilga aylanasiz.",
 b:["Komissar o'lsa — Serjantga aylanadi","Don o'lsa — Mafiaga aylanadi","Qotil o'lsa — Qotilga aylanadi"],
 t:"Kim o'lishiga qarab o'z tomoningiz o'zgaradi — o'yinni kuzatib turing."},
don:{n:"Don",e:"🎩",s:"m",since:"9+",live:1,d:"Mafiya sardori: bu tunda kim o'lishini siz hal qilasiz.",
 b:["Mafiya sardori","Tunda kim o'lishini o'zi hal qiladi"],
 t:"Mafiya ovozlari teng bo'lsa, Donning tanlovi hal qiluvchi bo'ladi."},
mafia:{n:"Mafia",e:"🔫",s:"m",since:"6+",live:1,d:"Donga bo'ysunasiz va qarshilik qilganlarni o'ldirasiz. Don o'lsa, yangi Don bo'lishingiz mumkin.",
 b:["Donga bo'ysunadi","Qarshilik qilganlarni o'ldiradi","Don o'lsa, yangi Don bo'lishi mumkin"],
 note:"Hozircha Don o'lganda yangi Don tayinlanmaydi (keyin qo'shiladi).",
 t:"Kunduzi o'zingizni tinch aholidek tuting va o'z sheriklaringizni ochib qo'ymang."},
advokat:{n:"Advokat",e:"👨‍💼",s:"m",since:"Tez orada",d:"Tunda mafiyalardan birini himoya qilasiz: Komissar uni Tinch axoli deb ko'radi.",
 b:["Tunda mafiyalardan birini himoya qiladi","Komissar himoyalangan mafiyani Tinch axoli deb ko'radi"],
 t:"Komissar tekshirishi mumkin bo'lgan mafiyani himoya qiling."},
ubiytsa:{n:"Убийца",e:"🔪",s:"m",since:"Tez orada",d:"Mafia tomonida yakka ov qilasiz. Komissarni nishonga olsangiz, o'zingiz halok bo'lasiz.",
 b:["Mafia tomonida yakka ov qiladi","Komissarni nishonga olsa, o'zi halok bo'ladi"],
 t:"Komissarni nishonga olmang — bu sizning o'limingiz."}};
const ORDER=Object.keys(ROLES);
const RN={},REM={},RD={},SINCE={};ORDER.forEach(k=>{const r=ROLES[k];RN[k]=r.n;REM[k]=r.e;RD[k]=r.d;SINCE[k]=r.since});
const LAD=[[6,"mafia"],[6,"doktor"],[7,"mafia"],[8,"komissar"],[9,"don"]];
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
if(t=="hello"){ME=stamp(m.me);A=m.assets;if(m.ice)ICE=m.ice;if(m.emb)EMBC=m.emb;if(m.cc)CCFG=m.cc;emSetup(m.emap);OWN=m.owner;BOT=m.bot;STARS=m.stars||{};BRD=m.brand||BRD;document.title=BRD;ADS=m.ads||{};AD_SECS=m.ad_secs||4;
 const sp=(tg&&tg.initDataUnsafe&&tg.initDataUnsafe.start_param)||"";const rc=q.get("room")||(sp.startsWith("room_")?sp.slice(5):"");if(rc&&!ROOM)send({t:"join",id:rc})}
else if(t=="lobby"){LOBBY=m.rooms;if(ROOM){ROOM=null;EVQ=[];EVN=null;stopVoice();COMPOSE=false;REPLY=null;document.body.style.backgroundImage=""}}
else if(t=="room"){const was=ROOM;ROOM=m;if(!was||was.id!=m.id){MODAL=null;PAGE="home"}if(!was||was.phase!=m.phase)SEL=null;voiceSync();phaseFx(was,m)}
else if(t=="chat"){if(ROOM)ROOM.chat.push(m.c)}
else if(t=="assets"){A=m.assets;if(m.emb)EMBC=m.emb}else if(t=="fx")snd(m.k);else if(t=="err")toast(m.m);else if(t=="toast")toast(m.m);
else if(t=="me")ME=stamp(m.me);else if(t=="rating")RAT=m;else if(t=="tasks")TASKS=stamp(m);else if(t=="hist")HIST=m;
else if(t=="friends"){FR=m.list;FRMAX=m.max||200}else if(t=="fsearch")FS=m.list;else if(t=="pinfo"){PI=m.p;MODAL="pview"}else if(t=="cinfo"){if(m.bg){if(CI&&CI.id==m.c.id&&(PAGE=="clan"||MODAL=="cview"||MODAL=="cmem"))CI=m.c;else return}else{CI=m.c;if(ROOM)MODAL="cview";else{if(MODAL=="cview"||MODAL=="cmem")MODAL=null;if(PAGE!="clan"&&PAGE!="cchat"){PAGE="clan";scrollTo(0,0)}}}}
else if(t=="cgone"){CI=null;if(MODAL=="cview"||MODAL=="cmem")MODAL=null;if(PAGE=="clan"||PAGE=="cchat")PAGE="clans";RAT=null;send({t:"rating",kind:"clans",scope:"all"})}
else if(t=="cchat"){if(m.list)CCH=m.list;if(m.add){CCH.push(m.add);if(CCH.length>200)CCH.shift();if(PAGE!="cchat"||ROOM)return;const l=$("cchl");if(l&&CCH.length>1){l.insertAdjacentHTML("beforeend",cchMsg(m.add));scrollTo(0,document.body.scrollHeight);return}}}
else if(t=="cinv"){if(ROOM||MODAL)return;INV=m;MODAL="cinv"}
else if(t=="invoice"){if(tg&&tg.openInvoice)tg.openInvoice(m.url,s=>{if(s=="paid"){toast("Pro faollashdi!");MODAL=null;send({t:"profile"});render()}});else window.open(m.url);return}
else if(t=="ev"){EVQ.push(m);evNext();return}else if(t=="rtc"){rtcIn(m);return}else if(t=="banned"){document.body.innerHTML="<h3 style='padding:30px'>Siz bloklangansiz</h3>";return}
if(PAGE=="cchat"&&!ROOM&&!MODAL&&document.activeElement&&document.activeElement.id=="cci"&&(t=="lobby"||t=="me"||t=="rating"))return;
if(PAGE=="settings"&&!ROOM&&!MODAL&&document.activeElement&&(document.activeElement.id=="bio"||document.activeElement.id=="nm")&&(t=="lobby"||t=="me"||t=="rating"))return;
render()}
/* oddiy emoji -> rasm (owner "UI ikonalar" bo'limida almashtiradi) */
let EM={},EMRE=null,EMBUSY=false,EMQ=false;
function emSetup(m){EM=m||{};const ks=Object.keys(EM).sort((a,b)=>b.length-a.length).map(k=>k.replace(/[.*+?^${}()|[\]\\]/g,"\\$&"));EMRE=ks.length?new RegExp("("+ks.join("|")+")\\uFE0F?","g"):null;emSched()}
function emWalk(root){if(!EMRE||!root)return;const L=[],w=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);let n;
 while(n=w.nextNode()){const p=n.parentNode;if(!p||/^(SCRIPT|STYLE|TEXTAREA|OPTION|TITLE)$/.test(p.nodeName))continue;EMRE.lastIndex=0;if(EMRE.test(n.nodeValue))L.push(n)}
 L.forEach(n=>{const s=n.nodeValue,f=document.createDocumentFragment();let last=0,m;EMRE.lastIndex=0;
  while(m=EMRE.exec(s)){const k=EM[m[1]],u=k&&A[k];if(!u)continue;if(m.index>last)f.appendChild(document.createTextNode(s.slice(last,m.index)));const i=document.createElement("img");i.className="emj";i.src=u;i.alt=m[1];f.appendChild(i);last=m.index+m[0].length}
  if(!last)return;if(last<s.length)f.appendChild(document.createTextNode(s.slice(last)));n.parentNode.replaceChild(f,n)})}
function emSched(){if(EMQ)return;EMQ=true;requestAnimationFrame(()=>{EMQ=false;EMBUSY=true;try{emWalk(document.body)}catch(x){}EMBUSY=false;EMOBS.takeRecords()})}
const EMOBS=new MutationObserver(()=>{if(!EMBUSY)emSched()});EMOBS.observe(document.body,{childList:true,subtree:true,characterData:true});
function snd(k){if(A["snd_"+k])try{new Audio(A["snd_"+k]).play().catch(()=>{})}catch(x){}}
function media(k,c){const u=A[k];if(!u)return"";return u.includes(".webm")?`<video class="${c||""}" src="${u}" autoplay loop muted playsinline></video>`:`<img class="${c||""}" src="${u}">`}
function ico(k,fb){const u=isDark()&&A["ic_"+k+"_dark"]?A["ic_"+k+"_dark"]:A["ic_"+k];return u?`<img src="${u}">`:fb}
const hue=n=>{let h=0;for(const c of String(n))h=(h*31+c.charCodeAt(0))%360;return h};
function frSt(u){const m=/[?&]hr=(\d+)/.exec(u),hr=m?+m[1]:0;if(hr<35||hr>90)return"";const W=96*100/hr,X=(W-100)/2;return' style="left:-'+X.toFixed(1)+'%;top:-'+X.toFixed(1)+'%;width:'+W.toFixed(1)+'%;height:'+W.toFixed(1)+'%"'}
function av(ph,n,cls){ph=String(ph||"");let fr="";const hi=ph.indexOf("#");if(hi>=0){const m=/(?:^|[#&])f=(\d+)/.exec(ph.slice(hi));if(m)fr=m[1];ph=ph.slice(0,hi)}const h=avBase(ph,n,cls),fu=fr&&typeof A!=="undefined"&&A&&A["frame_"+fr];return fu?'<span class="afw'+(/big-av/.test(cls||"")?" bg":"")+'">'+h+'<img class="afr"'+frSt(fu)+' src="'+e(fu)+'" alt="">'+"</span>":h}
function avBase(ph,n,cls){const nm=String(n||"?");const ini=e(nm.trim().charAt(0).toUpperCase()||"?");const st=`background:hsl(${hue(nm)} 55% 42%)`;
 return ph?`<img class="av ${cls||""}" src="${e(ph)}" onerror="this.outerHTML='<div class=&quot;av ${cls||""}&quot; style=&quot;${st}&quot;>${ini}</div>'">`:`<div class="av ${cls||""}" style="${st}">${ini}</div>`}
/* ---- navigatsiya ---- */
function go(p){PAGE=p;MODAL=null;
 if(p=="tasks")send({t:"tasks"});if(p=="history")send({t:"history"});if(p=="rating"||p=="clans"){RS.kind=p=="clans"?"clans":"players";rate()}
 if(p=="cchat")send({t:"clan_chat"});if(p=="profile")send({t:"profile"});if(p=="friends"){FS=null;send({t:"friends"})}
 render();scrollTo(0,0)}
function rate(){RAT=null;send({t:"rating",kind:RS.kind,scope:RS.scope})}
function back(){if(MODAL){MODAL=null;render();return}if(PAGE=="rules"&&RSEL&&!ROOM){RSEL=null;render();return}if(ROOM){ROOM.phase=="lobby"||ROOM.phase=="ended"||ROOM.me.role==null?send({t:"leave"}):confirmLeave();return}
 if(PAGE=="cchat"){PAGE="clan";render();return}if(PAGE=="clan"){CI=null;go("clans");return}if(PAGE=="settings"||PAGE=="friends"){go("profile");return}if(PAGE!="home")go("home")}
function confirmLeave(){if(ROOM.me.role==null||confirm("Chiqsangiz ball ayiriladi va 30 daqiqa o'ynay olmaysiz. Chiqasizmi?"))send({t:"leave"})}
if(tg&&tg.BackButton)tg.BackButton.onClick(back);
function render(){const a=$("app");
 if(tg&&tg.BackButton){(ROOM||PAGE!="home"||MODAL)?tg.BackButton.show():tg.BackButton.hide()}
 const keep=$("chat")?$("chat").scrollTop:0,atEnd=$("chat")?($("chat").scrollHeight-$("chat").scrollTop-$("chat").clientHeight<80):true;
 a.innerHTML=ROOM?roomView():({home:lobbyView,tasks:tasksView,history:histView,rules:rulesView,rating:ratingView,clans:clansView,clan:clanPage,cchat:cchatPage,profile:profileView,settings:settingsView,friends:friendsView}[PAGE]||lobbyView)();
 $("mdl").innerHTML=MODAL?modalView():(EVN?evView():(ROOM?revealView():""));
 const c=$("chat");if(c)c.scrollTop=atEnd?c.scrollHeight:keep;
 barRender();bgRender()}
function bgRender(){document.body.dataset.ph=ROOM?ROOM.phase:"";document.body.dataset.mode=ROOM?ROOM.mode:"";let k=ROOM&&(ROOM.phase=="night"?"bg_night":ROOM.phase=="day"||ROOM.phase=="vote"?"bg_day":"");
 document.body.style.backgroundImage=k&&A[k]?`linear-gradient(rgba(10,12,19,.7),rgba(10,12,19,.85)),url(${A[k]})`:"";document.body.style.backgroundSize="cover"}
const hd=t=>`<button class="back" onclick="back()">✕ Orqaga</button>${t?`<h2>${t}</h2>`:""}`;
/* ---- lobbi ---- */
function lobbyView(){
 const w=LOBBY.filter(r=>r.phase=="lobby"),l=LOBBY.filter(r=>r.phase!="lobby"),cool=rest(ME,"cool");
 const ib=[["tasks",isDark()?"☀️":"🌙"],["history","⏳"],["rules","📖"],["rating","🏆"],["clans","⛺"],["profile","🕴️"]];
 const td=ME.tasks_done||0;
 let h=`<div class="hero" id="hero" onclick="adClick()">${heroHtml()}</div>
<div class="icons">${ib.map(x=>`<button class="ib" onclick="${x[0]=="tasks"?"toggleTheme()":"go('"+x[0]+"')"}">${ico(x[0],x[1])}</button>`).join("")}</div>
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
function modalView(){if(MODAL=="badge"||MODAL=="frame")return pickModal(MODAL);if(MODAL=="pview"&&PI)return pviewHtml();if(MODAL=="cview"&&CI)return cviewHtml();if(MODAL=="cform")return cfView();if(MODAL=="cmem"&&CI)return cmemView();if(MODAL=="cinv"&&INV)return cinvView();
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
<div class="mu" style="margin:8px 0">Xona kodi: <b style="color:var(--tx)">${e(code)}</b></div><button class="btn" onclick="share()">🔗 Havolani ulashish</button>${ME.cid?`<button class="btn sec" style="margin-top:8px" onclick="send({t:'clan_invite'})">⛺ Ovul a'zolarini chaqirish</button>`:""}
<div class="sec-t"><span>Do'stlarga yuborish</span></div>${FR==null?'<div class="mu c">Yuklanmoqda...</div>':FR.length?FR.map(f=>`<div class="lr">${av(f.ph,f.name)}<b>${nbd(f)}${e(f.name)}</b><span class="mu">${f.on?"onlayn":""}</span><button class="btn sm" style="margin-left:auto" onclick="send({t:'invite',to:${f.id}});toast('Taklif yuborildi')">Yuborish</button></div>`).join(""):'<div class="mu c">Do\'stlar yo\'q</div>'}</div></div>`}
 return""}
let EVN=null,EVQ=[],EVT=null,HF="all";
function evNext(){if(EVN||!EVQ.length)return;EVN=EVQ.shift();render();EVT=setTimeout(evClose,EVN.ms||3500)}
function evClose(){clearTimeout(EVT);EVN=null;render();if(EVQ.length)setTimeout(evNext,150)}
function evView(){const v=EVN,t=e(v.tx||"").replace("{n}","<b>"+e(v.n)+"</b>");
 const bd=v.k=="last"?'<div class="bd mic">'+emj("mic","🎙")+'</div>':v.role?`<div class="bd">${media("role_"+v.role)||REM[v.role]||""}</div>`:"";
 return`<div class="ov mid" onclick="evClose()"><div class="evc ${v.k}"><div class="pa">${av(v.ph,v.n)}${v.k=="kill"||v.k=="out"?'<div class="xx">✕</div>':""}${bd}</div><p>${t}</p></div></div>`}
function revealView(){if(!ROOM||ROOM.spec||ROOM.mode=="table"||!ROOM.me.role||ROOM.phase=="lobby"||REV==ROOM.id+":done")return"";const r=ROOM.me.role,maf=MT.includes(r);
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
function rulesView(){
 const maf=r=>ROLES[r].s=="m",list=ORDER.filter(r=>RFILT=="all"||(RFILT=="maf"?maf(r):!maf(r))),na=ORDER.filter(r=>!maf(r)).length;
 if(RSEL&&ROLES[RSEL])return roleDetail(list.includes(RSEL)?list:ORDER);
 const rows=[];for(let n=6;n<=16;n++){const c={};LAD.forEach(([need,r])=>{if(n>=need)c[r]=(c[r]||0)+1});const prev=n>6?rows[n-7].c:{};const add=[];ORDER.forEach(r=>{const d=(c[r]||0)-(prev[r]||0);if(d>0)add.push((n==6?"":"+ ")+(c[r]>1&&n<=7?c[r]+" ":"")+RN[r])});rows.push({n,c,add})}
 return hd("📖 O'yin qoidalari")+`<div class="tabs"><button class="${RFILT=="all"?"on":""}" onclick="RFILT='all';render()">Hammasi · ${ORDER.length}</button><button class="${RFILT=="tn"?"on":""}" onclick="RFILT='tn';render()">Shahar · ${na}</button><button class="${RFILT=="maf"?"on":""}" onclick="RFILT='maf';render()">Mafiya · ${ORDER.length-na}</button></div>
<div class="rl">${list.map(r=>`<div class="rc ${maf(r)?"m":""}" onclick="RSEL='${r}';render();scrollTo(0,0)"><div class="im">${media("role_"+r)||REM[r]}</div><span class="nb">${SINCE[r]}</span><b>${RN[r]}</b></div>`).join("")}</div>
<div class="card" style="padding:0;overflow:hidden;margin-top:16px"><div class="row sp" style="padding:16px" onclick="TBL=!TBL;render()"><b style="font-size:19px">O'yinchilar soniga qarab rollar</b><span>${TBL?"▲":"▼"}</span></div>
${TBL?`<table><tr><th>O'YINCHI</th><th>ROLLAR</th></tr>${rows.map(x=>`<tr><td><b>${x.n}</b></td><td>${x.add.join(" · ")||"—"}</td></tr>`).join("")}</table>
<div class="mu" style="padding:14px"><b>Qolganlar</b> — Tinch axoli. Jadvalda hozir o'yinda ishlaydigan rollar ko'rsatilgan; qolganlari «Tez orada» deb belgilangan.</div>`:""}</div>`}
function roleDetail(list){const r=RSEL,R=ROLES[r],m=R.s=="m",i=list.indexOf(r),n=list.length;
 const gt=d=>`RSEL='${list[(i+d+n)%n]}';render();scrollTo(0,0)`;
 return`<div class="rdt"><div class="rhero ${m?"m":""}">${media("role_"+r)||'<div class="rem">'+R.e+'</div>'}<button class="rb" onclick="RSEL=null;render()">‹ Qoidalar</button><div class="rpg"><button onclick="${gt(-1)}">‹</button><span>${i+1} / ${n}</span><button onclick="${gt(1)}">›</button></div></div>
<h1>${e(R.n)}</h1><div class="rsub ${m?"m":""}">${m?"MAFIYA TOMONI":"SHAHAR TOMONI"} · ${R.live?(R.since=="Ko'p"?"ko'pchilik":R.since+" o'yinchi"):"tez orada"}</div>
<p class="rds">${e(R.d)}</p><div class="rbl">${R.b.map(x=>`<div>${e(x)}</div>`).join("")}</div>
${R.note?`<div class="rtip w">🚧 <b>Holat:</b> ${e(R.note)}</div>`:""}${R.live?"":`<div class="rtip w">🚧 <b>Holat:</b> bu rol o'yinda hali tarqatilmaydi — qoidasi ko'rsatilgan, mexanikasi keyin qo'shiladi.</div>`}
<div class="rtip">💡 <b>Maslahat:</b> ${e(R.t)}</div></div>`}
/* ---- reyting ---- */
function ratingView(){const R=RS;let h=hd("Reyting jadvali")+`<div class="tabs"><button class="${R.kind=="players"?"on":""}" onclick="RS.kind='players';rate();render()">👥 Top O'yinchilar</button><button class="${R.kind=="clans"?"on":""}" onclick="RS.kind='clans';rate();render()">⛺ Top Ovullar</button></div>`;
 if(R.kind=="players")h+=`<div class="pills">${[["week","Haftalik"],["season",SEASON_N()+"-mavsum"],["all","Umumiy"],["friends","Do'stlar"]].map(x=>`<button class="${R.scope==x[0]?"on":""}" onclick="RS.scope='${x[0]}';rate();render()">${x[1]}</button>`).join("")}</div>`;
 if(!RAT||RAT.kind!=R.kind)return h+'<div class="mu c">Yuklanmoqda...</div>';
 if(R.kind=="clans")return h+'<div class="mu" style="margin:6px 4px">ⓘ Ovullar umumiy ochkolar bo\'yicha tartiblangan</div>'+(RAT.clans.length?clansRows(true):'<div class="card mu c">Ovullar yo\'q</div>');
 if(R.scope=="week")h+=`<div class="mu c" style="margin:6px 0">⏱ Dushanba kuni yangilanadi · <span id="rleft">${Math.ceil(rest(RAT,"left")/3600)}</span> soat qoldi</div><div class="prize">👑 Hafta g'olibiga <b>1 haftalik Pro</b> obunasi sovg'a qilinadi!</div>`;
 const P=RAT.players;if(!P.length)return h+'<div class="card mu c">Hali natija yo\'q</div>';
 const pd=(p,i)=>p?`<div class="p${i}" onclick="openPi(${p.id})">${i==1?'<div style="font-size:30px;position:absolute;top:-24px;left:0;right:0">👑</div>':""}<span class="rk">${i}-o'rin</span>${av(p.ph,p.name,p.pro?"pro":"")}<b>${nbd(p)}${e(p.name)}</b><div class="mu" style="font-size:13px">${p.g} o'yin · ${p.wr}%</div><div class="sc">${p.pts}</div><div class="mu" style="font-size:12px">BALL</div></div>`:"<div></div>";
 h+=`<div class="pod">${pd(P[1],2)}${pd(P[0],1)}${pd(P[2],3)}</div>`;
 h+=P.slice(3).map((p,i)=>`<div class="lr" onclick="openPi(${p.id})"><span class="no">${i+4}</span>${av(p.ph,p.name,p.pro?"pro":"")}<div style="min-width:0"><b>${nbd(p)}${e(p.name)}</b><div class="mu">${p.g} o'yin · ${p.wr}% g'alaba</div></div><div class="sc">${p.pts}<small>ball</small></div></div>`).join("");return h}
/* ---- profil / sozlamalar / do'stlar ---- */
function profileView(){const link="https://t.me/"+BOT+"?start=ref_"+ME.id,wr=ME.games?Math.round(100*ME.wins/ME.games):0,sv=ME.games?Math.round(100*(ME.survived||0)/ME.games):0;
 return hd("Profil")+`<div class="card" style="padding:18px"><div class="row"><div>${av(ME.photo,ME.name,"big-av "+(ME.pro?"pro":""))}</div><div class="f"><div style="font-size:24px;font-weight:800;line-height:1.15">${nbd(ME)}${e(ME.name)}</div><div class="mu" style="font-size:17px">ID ${ME.id}${ME.clan?" · "+e(ME.clan):""}</div></div>
<button class="ib" style="flex:0 0 58px;height:58px;aspect-ratio:auto;border-radius:16px" onclick="toast('Yutuqlar tez orada')">📜</button><button class="ib" style="flex:0 0 58px;height:58px;aspect-ratio:auto;border-radius:16px" onclick="go('settings')">⚙️</button></div>
<div class="c" style="margin-top:14px"><span class="pill">${emj("trophy","🏆")} ${ME.points} ball <span class="mu">|</span> #${ME.rank}</span></div>
<div class="stat"><div><b>${ME.games}</b><span>O'yinlar</span></div><div><b class="gr">${ME.wins}</b><span>G'alabalar</span></div><div><b class="go">${wr}%</b><span>G'alaba foizi</span></div><div><b>${sv}%</b><span>Omon qolish</span></div></div></div>
<div class="card row sp"><div class="row"><span style="font-size:26px">🎙</span><b style="font-size:19px">Bepul ovozli o'yinlar</b></div><span class="bdg voice" style="font-size:17px;padding:6px 14px">${ME.free_voice} ta</span></div>
<div class="sec-t"><span>🖼 Ramkalarim</span></div><div class="card"><div class="frs">${myFrames().length?myFrames().map(n=>`<button class="frb ${ME.frame==n?"on":""}" onclick="send({t:'equip',n:${n}})">${A&&A["frame_"+n]?`<img src="${e(A["frame_"+n])}">`:"№"+n}</button>`).join(""):`<div class="mu">Hali ramkangiz yo'q</div>`}</div><div class="row" style="gap:8px;margin-top:12px"><button class="btn sec" onclick="send({t:'equip',n:0})">Yechish</button><button class="btn gold" onclick="bot('shop')">🛍 Ramka do'koni</button></div></div>
<div class="sec-t"><span>👥 Do'stlar</span></div><button class="nav" onclick="go('friends')"><span>🔍 O'yinchini izlash</span><span>›</span></button><button class="nav" onclick="go('friends')"><span>Do'stlarim <b style="color:var(--ac);margin-left:6px">${ME.friends||0}</b></span><span>›</span></button>
<div class="sec-t"><span>👥 Do'stlarni taklif qiling</span></div><div class="card"><div style="font-size:16px;line-height:1.5" class="mu">Havolangiz orqali kelgan <b style="color:var(--tx)">25 ta yangi o'yinchi</b> 3 ta o'yinni oxirigacha o'ynasa — sizga <b style="color:var(--tx)">1 oylik Pro</b> obunasi beriladi.</div>
<div class="lnk" style="margin-top:14px"><span>🔗 ${e(link)}</span><b onclick="copy('${e(link)}')">Nusxalash</b></div><div class="stat" style="grid-template-columns:1fr 1fr 1fr"><div style="padding:10px"><b style="font-size:26px">${ME.ref_all||0}</b><span style="font-size:13px">Taklif qilingan</span></div><div style="padding:10px"><b class="gr" style="font-size:26px">${ME.ref_ok||0}</b><span style="font-size:13px">3 o'yin o'ynagan</span></div><div style="padding:10px"><b class="go" style="font-size:26px">${Math.floor((ME.ref_ok||0)/25)}</b><span style="font-size:13px">Mukofot</span></div></div></div>
<button class="btn gold" onclick="MODAL='pro';render()">${ME.pro?"👑 Pro faol. Uzaytirish":"👑 Play Pro olish"}</button>${OWN?'<button class="btn sec" style="margin-top:10px" onclick="bot(\'admin\')">Owner paneli</button>':""}`}
function copy(t){try{navigator.clipboard.writeText(t);toast("Nusxalandi")}catch(x){toast(t)}}
function bot(p){const u="https://t.me/"+BOT+"?start="+p;tg?tg.openTelegramLink(u):window.open(u)}
function pref(k,v){send({t:"setprefs",k,v});ME.prefs[k]=v;render()}
function myFrames(){const o=ME.frames||[];return ME.pro?[...new Set([...o,...(ME.pfr||[])])].sort((a,b)=>a-b):o}
function nmSave(el){const v=el.value.replace(/\s+/g," ").trim();if(!v){el.value=ME.name;toast("Ism bo'sh bo'lmasin");return}if(v==ME.name){el.value=v;return}send({t:"setname",name:v})}
function bioSave(el){const v=el.value.trim();if(v==(ME.bio||""))return;send({t:"setbio",bio:v})}
function avPick(inp){const f=inp.files&&inp.files[0];inp.value="";if(!f)return;if(!/^image\//.test(f.type)){toast("Rasm tanlang");return}
 const rd=new FileReader();rd.onerror=()=>toast("Rasm o'qilmadi");rd.onload=()=>{const im=new Image();im.onerror=()=>toast("Bu rasm formati mos emas (JPG yoki PNG tanlang)");im.onload=()=>{
  const S=256,c=document.createElement("canvas");c.width=c.height=S;const x=c.getContext("2d"),m=Math.min(im.width,im.height),sx=(im.width-m)/2,sy=(im.height-m)/2;
  x.fillStyle="#fff";x.fillRect(0,0,S,S);x.drawImage(im,sx,sy,m,m,0,0,S,S);send({t:"setavatar",d:c.toDataURL("image/jpeg",.88)});toast("Yuklanmoqda...")};im.src=rd.result};rd.readAsDataURL(f)}
function proBlock(){const lk=!ME.pro,dt=ME.pro_until>now()?new Date(ME.pro_until*1000).toISOString().slice(0,10):"",go=m=>lk?"MODAL='pro';render()":"MODAL='"+m+"';render()",
 bu=ME.badge&&A["badge_"+ME.badge]?A["badge_"+ME.badge]:"",base=String(ME.photo||"").split("#")[0],
 bd=bu?`<img src="${e(bu)}" alt=""><span>№${ME.badge}</span>`:"Yo'q",fr=`${av(base+(ME.frame?"#f="+ME.frame:""),ME.name,"")}<span>${ME.frame?"№"+ME.frame:"Yo'q"}</span>`;
 return`<div class="prb"><div class="ph">${emj("crown","👑")} ${lk?"Pro faol emas":e(BRD)+" Pro faol"} ${lk?"":"<i style='background:var(--gr)'></i>"}</div>
<div class="pd">${lk?"Belgi, ramka va bio Pro a'zolar uchun":dt?"Amal qiladi: "+dt:"Hozircha hamma uchun bepul"}</div>
<button class="btn gold" onclick="MODAL='pro';render()">${emj("crown","👑")} ${lk?"Pro olish":"Uzaytirish"}</button>
<div class="prr ${lk?"lk":""}" style="margin-top:12px" onclick="${go("badge")}"><b>BELGI</b><div class="v">${bd}</div><span class="ch">›</span></div>
<div class="prr ${lk?"lk":""}" onclick="${go("frame")}"><b>RAMKA</b><div class="v">${fr}</div><span class="ch">›</span></div>
<div class="row sp" style="margin:14px 2px 6px"><b class="mu" style="font-size:14px;letter-spacing:1px">MEN HAQIMDA</b><span class="mu" id="bioc">${(ME.bio||"").length}/150</span></div>
<textarea class="bio" id="bio" maxlength="150" placeholder="Qisqacha o'zingiz haqingizda..." ${lk?`readonly onclick="MODAL='pro';render()"`:""} oninput="$('bioc').textContent=this.value.length+'/150'" onchange="bioSave(this)">${e(ME.bio||"")}</textarea></div>`}
function pickModal(k){const isB=k=="badge",list=isB?(ME.badges||[]):myFrames(),cur=isB?ME.badge:ME.frame,base=String(ME.photo||"").split("#")[0],
 act=n=>isB?`MODAL=null;send({t:'setbadge',n:${n}})`:`MODAL=null;send({t:'equip',n:${n}})`,
 tile=n=>`<button class="${cur==n?"on":""}" onclick="${act(n)}">${isB?(A["badge_"+n]?`<img src="${e(A["badge_"+n])}" alt="">`:"№"+n):av(base+"#f="+n,ME.name,"")}</button>`;
 return`<div class="ov" onclick="if(event.target==this){MODAL=null;render()}"><div class="sheet"><div class="hd"><span>${isB?"🏅 Belgi tanlang":"🖼 Ramka tanlang"}</span><button class="x" onclick="MODAL=null;render()">✕</button></div>
<div class="pkg ${isB?"bd":"c3"}"><button class="${!cur?"on":""}" onclick="${act(0)}">Yo'q</button>${list.map(tile).join("")}</div>
${list.length?"":`<div class="mu c" style="margin:6px 0 10px">${isB?"Hozircha belgilar qo'shilmagan":"Sizda hali ramka yo'q"}</div>`}
${isB?"":`<button class="btn gold" onclick="MODAL=null;render();bot('shop')">🛍 Ramka do'koni</button>`}</div></div>`}
function settingsView(){const P=ME.prefs||{},cb=(k,t,d,pro)=>`<div class="row" style="padding:12px 0;align-items:flex-start" onclick="${pro&&!ME.pro?"MODAL='pro';render()":`pref('${k}',${P[k]?0:1})`}"><span class="cb ${P[k]?"on":""}">${P[k]?"✓":""}</span><div class="f"><b style="font-size:18px">${t}</b>${pro?' <span class="bdg pro">Pro</span>':""}<div class="mu">${d}</div></div></div>`;
 const seg=(k,arr)=>`<div class="seg">${arr.map(x=>`<button class="${P[k]==x[0]?"on":""}" onclick="pref('${k}','${x[0]}')">${x[1]}</button>`).join("")}</div>`;
 return hd("Sozlamalar")+`<div class="avs"><div onclick="$('avf').click()" style="cursor:pointer">${av(ME.photo,ME.name,"big-av")}</div><button class="cam" onclick="$('avf').click()">📷</button></div>
<input type="file" id="avf" accept="image/*" style="display:none" onchange="avPick(this)">${ME.custom_av?`<div class="c"><button class="lnkb" onclick="send({t:'delavatar'})">Telegram rasmiga qaytarish</button></div>`:""}
<label class="field">Ism</label><input id="nm" value="${e(ME.name)}" maxlength="24" onchange="nmSave(this)">
<div class="field go">${emj("crown","👑")} ${e(BRD)} Pro</div>${proBlock()}
<div class="field">🎨 Mavzu</div><div class="card"><div class="seg">${[["auto","Avto"],["light","Kunduz"],["dark","Tun"]].map(x=>`<button class="${TH==x[0]?"on":""}" onclick="setTheme('${x[0]}')">${x[1]}</button>`).join("")}</div></div>
<div class="field">💬 Bildirishnomalar</div><div class="card">${cb("n_start","O'yin boshlanayotganda xabar berish","Xonada o'yin boshlanishiga bir necha kishi yetmay qolganda bot sizga yozadi.")}
<div class="row sp" style="padding:10px 0;border-top:1px solid var(--bd)"><b style="font-size:17px">Xabarlar orasidagi vaqt</b><select style="width:160px" onchange="pref('gap',+this.value)">${[1,3,6,12,24].map(h=>`<option value="${h}" ${P.gap==h?"selected":""}>${h} soat${h==6?" (standart)":""}</option>`).join("")}</select></div>
<div style="border-top:1px solid var(--bd)">${cb("n_friend","Do'stlar taklifi","Do'stlaringiz sizni o'z xonasiga chaqirganda bot sizga yozadi.")}</div>
<div style="border-top:1px solid var(--bd)">${cb("n_priv","Shaxsiy xabarlar","Ilovada bo'lmaganingizda do'stingiz yozsa, bot sizga Telegramda xabar beradi.",1)}</div></div>
<div class="field">🎧 Ovoz ulanishi</div><div class="card"><b style="font-size:18px">Ovozli xonaga ulanish usuli:</b><div style="height:10px"></div>${seg("voice",[["auto","Avtomatik"],["direct","Bevosita"],["proxy","Proksi orqali"]])}<div class="mu c" style="margin-top:8px">Agar bevosita ulanmasa, avtomatik proksi orqali</div></div>
<div class="field">🛡 Maxfiylik</div><div class="card"><b style="font-size:18px">Ishtirokimdagi o'yinlardan parchalar Instagramga joylanishiga:</b><div class="mu" style="margin:6px 0 10px">Ovozli o'yinlardan ba'zi qiziq lavhalar rasmiy sahifaga qo'yilishi mumkin.</div>
<div class="seg"><button class="${P.insta?"on":""}" style="${P.insta?"background:#12331f;border-color:var(--gr);color:var(--gr)":""}" onclick="pref('insta',1)">✓ Roziman</button><button class="${!P.insta?"on":""}" onclick="pref('insta',0)">Rozi emasman</button></div></div>`}
function fsr(){const v=$("fq").value.trim();if(v)send({t:"fsearch",q:v})}
/* ---- xona ---- */
function fmt2(s){s=Math.max(0,Math.floor(s));return String(Math.floor(s/60)).padStart(2,"0")+":"+String(s%60).padStart(2,"0")}
function pick(id){const R=ROOM,m=R.me;if(!m.alive||R.spec)return;
 if(R.phase=="night"&&(MT.includes(m.role)||m.role=="doktor"||m.role=="komissar")){if(m.act)return;SEL=SEL==id?null:id;render()}
 else if(R.phase=="vote"){if(m.nov)return toast("Muhokamada qatnashmaganingiz uchun bugun ovoz bera olmaysiz");send({t:"vote",to:id})}}
function confirmAct(){if(SEL)send({t:"act",to:SEL})}
function undoAct(){SEL=null;send({t:"unact"})}
function hint(){const R=ROOM,m=R.me;if(R.spec)return"Siz kuzatuvchisiz";if(!m.alive)return R.phase=="ended"?"":"Siz o'yindan chiqdingiz. Kuzatishingiz mumkin";
 if(R.phase=="night"&&!(MT.includes(m.role)||m.role=="doktor"||m.role=="komissar"))return"Tun. Hamma uxlayapti";return""}
function canChat(){const R=ROOM;if(!R||R.spec||R.mode=="table")return false;if(R.phase=="last")return R.speaker==ME.id;if(R.phase=="night")return R.me.alive&&MT.includes(R.me.role);return true}
const SI=t=>/otib tashladi/.test(t)?emj("kill","🔫"):/davola|qutqar/.test(t)?emj("heal","🩺"):/ovoz/i.test(t)?emj("vote","🗳"):/^Tun/.test(t)?emj("night","🌙"):/kun/.test(t)?emj("day","☀️"):/chiqar|chiqib/.test(t)?"🚪":"ℹ️";
function chatHtml(){const R=ROOM,P={};R.players.forEach(p=>P[p.id]=p);
 return R.chat.map(c=>{if(c.sys)return`<div class="sysl">${SI(c.t)} ${e(c.t)}</div>`;const mine=c.uid==ME.id,p=P[c.uid]||{};return`<div class="m ${mine?"me":""} ${c.ch}"><span onclick="openPi(${c.uid})">${av(p.ph,c.u,p.pro?"pro":"")}</span><div class="bub">${!mine?`<div class="nm">${nbd(p)}${e(c.u)}${c.ch=="mafia"?" 🔪":c.ch=="dead"?" 👻":""}</div>`:""}${c.re?`<div class="qt"><b>${e(c.re.u)}</b><span>${e(c.re.t)}</span></div>`:""}<div>${e(c.t)}</div></div>${R.spec?"":`<button class="rp" onclick="rep(${c.id})">↩</button>`}</div>`}).join("")||'<div class="sysl">Chat bo\'sh</div>'}
function nightHtml(R,m){const mt=MT.includes(m.role),act=m.alive&&!R.spec&&(mt||m.role=="doktor"||m.role=="komissar");
 const ttl=mt?"KIMNI YO'Q QILAMIZ?":m.role=="doktor"?"KIMNI DAVOLAYMIZ?":m.role=="komissar"?"KIMNI TEKSHIRAMIZ?":"TUN. SHAHAR UXLAYAPTI";
 const am=R.players.filter(p=>p.alive&&MT.includes(p.role)).length||1;
 const cells=R.players.map(p=>{const sel=SEL==p.id||m.act==p.id,tm=mt&&MT.includes(p.role);
  const tag=p.id==ME.id?'<span class="tg">Siz</span>':tm?`<span class="tg">${RN[p.role]}</span>`:"";
  const dot=p.mv?`<i class="dt ${p.mv>=am?"g":p.mv>1?"lg":"b"}"></i>`:"";
  return`<div class="gc ${p.alive?"":"dead"} ${sel?"sel":""} ${tm||p.id==ME.id&&mt?"mf":""}" onclick="pick(${p.id})">${tag}${dot}${av(p.ph,p.name)}<span>${nbd(p)}${e(p.name)}</span></div>`}).join("");
 let h=`<div class="qt2">◇ ${ttl}</div><div class="grid7">${cells}</div>`;
 if(mt)h+=`<div class="leg"><span><i class="dt b"></i>bitta mafiya tanladi</span><span><i class="dt lg"></i>bir nechta kelishdi</span><span><i class="dt g"></i>barchasi kelishdi</span></div>`;
 if(act){if(m.act){const t=R.players.find(p=>p.id==m.act);
   h+=`<div class="info ok">✓ <span>Siz <b>${e(t?t.name:"")}</b>ni tanladingiz. Tunning tugashini kuting.</span></div><div class="row"><button class="btn gr f" onclick="undoAct()">✓ O'zgartirish</button><button class="rx" onclick="undoAct()">✕</button></div>`}
  else h+=`<div class="info">⚠ <span>${mt?"Tasdiqlash uchun barcha mafiya a'zolari bitta o'yinchini tanlashi kerak.":"Bitta o'yinchini tanlang va tasdiqlang."}</span></div><div class="row"><button class="btn gr f" ${SEL?"":"disabled"} onclick="confirmAct()">✓ Tasdiqlash</button><button class="rx" onclick="SEL=null;render()">✕</button></div>`}
 return h}
function voteHtml(R,m){const nv=m.nov&&m.alive;let h="";
 if(nv)h+=`<div class="info bad">⚠ <span>Muhokamada qatnashmaganingiz uchun bugun ovoz bera olmaysiz.</span></div>`;
 h+=`<div class="qt2 l">👥 KIMGA OVOZ BERASIZ?</div><div class="card vg"><div class="grid4">${R.players.filter(p=>p.alive).map(p=>`<div class="vc ${m.vote==p.id?"sel":""} ${p.id==ME.id||nv?"off":""}" onclick="pick(${p.id})">${p.v?`<span class="v">${p.v}</span>`:""}${av(p.ph,p.name)}<span>${nbd(p)}${e(p.name)}</span></div>`).join("")}</div></div>`;
 if(m.alive&&!R.spec)h+=`<button class="btn sec sm skp" style="${m.vote===0?"border-color:var(--ac)":""}" onclick="send({t:'vote',to:0})">${m.vote===0?"✓ ":""}Ovoz bermayman</button>`;
 return h}
function endHtml(R){return`<div class="card big">${R.winner=="none"?"🏁 O'yin bekor qilindi":"🏁 G'alaba: "+(R.winner=="town"?"Shahar":"Mafiya")}</div><div class="grid7" style="margin-top:14px">${R.players.map(p=>`<div class="gc ${p.alive?"":"dead"}">${p.role?`<span class="tg ${MT.includes(p.role)?"":"tn"}">${RN[p.role]}</span>`:""}${av(p.ph,p.name)}<span>${nbd(p)}${e(p.name)}</span></div>`).join("")}</div>`}
function roomView(){if(ROOM.mode=="table"&&!ROOM.spec){const v=tblView(ROOM);if(v!==null)return v}if(ROOM.mode=="voice"&&ROOM.phase=="lobby")return voiceLobby(ROOM);
 const R=ROOM,m=R.me,own=R.owner==ME.id&&!R.spec,n=R.players.filter(p=>!p.left).length,pl=R.phase=="lobby",ph=R.phase;
 const spk=(R.mode=="voice"&&!R.spec?`<button class="spk" onclick="micToggle()" style="display:inline-flex;align-items:center;justify-content:center">${MIC?IC.mic:IC.micoff}</button>`:"")+`<button class="spk" onclick="SPK=!SPK;voiceSync();render()">${SPK?"🔊":"🔇"}</button>`;
 const op=R.players.find(p=>p.id==ME.id)||R.players.find(p=>p.id==R.owner)||{};
 const ttl=pl?e(R.name):ph=="intro"?"1-kun":ph=="night"?R.night+"-tun":ph=="last"?"Oxirgi so'z":ph=="ended"?"Tugadi":(R.day+1)+"-kun";
 const sub=pl?`👥 ${n}/${R.max} o'yinchi · Kod: ${R.id}`:ph=="intro"?"Tanishuv":ph=="day"?"Muhokama":ph=="vote"?"Ovoz berish":"";
 let h=`<div class="rh">${tg?"":'<button class="back" style="float:left" onclick="back()">← Orqaga</button>'}<b>${ttl}</b>${!pl&&R.deadline?' <span class="tp" id="tm"></span>':""}${sub?`<div class="mu rs">${sub}</div>`:""}</div>`;
 if(pl){h+=`<div class="row sp rbar"><div class="rav">${av(op.ph,op.name)}</div>${spk}</div><div class="prow">${R.players.filter(p=>!p.left).map(p=>`<div class="pcd" onclick="openPi(${p.id})">${av(p.ph,p.name,p.pro?"pro":"")}<span>${nbd(p)}${e(p.name)}</span></div>`).join("")}</div>
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
/* ====== OVUL v2: belgi, ro'yxat, sahifa, forma, suhbat ====== */
let EMBC={cols:6,rows:4,n:24},CCFG={pro:1,max:100},CF={},CRULES=false,CREQ=false,CCH=[],CCD="",MM=0,INV=null;
const EMBFB=["🐺","🦅","🦁","🐉","🌹","👑","⚔️","💎","🔥","⚡","🌙","☀️","🦂","🐍","🏆","🛡️","💀","🃏","🌸","🦇","🐯","🐻","🦄","⭐","🐎","🦈","🦉","🕊️","🔱","🗡️","🎭","🍀","❄️","🌊","🪐","🧿","🦊","🐗","🦍","🐲"];
const embOk=i=>!!(A.emb_sheet||A["emb_"+i]);
function embHtml(i,cls){i=+i||1;const u=A["emb_"+i],c=cls||"";
 if(u)return`<span class="emb ${c}"><img src="${u}" alt=""></span>`;
 if(A.emb_sheet){const C=Math.max(1,EMBC.cols),R=Math.max(1,EMBC.rows),k=i-1,x=k%C,y=Math.floor(k/C);
  return`<span class="emb ${c}" style="background-image:url(${A.emb_sheet});background-size:${C*100}% ${R*100}%;background-position:${C>1?x*100/(C-1):0}% ${R>1?y*100/(R-1):0}%"></span>`}
 return`<span class="emb fb ${c}" style="background:linear-gradient(135deg,hsl(${(i*47)%360} 62% 38%),hsl(${(i*47+40)%360} 66% 18%))">${EMBFB[(i-1)%EMBFB.length]}</span>`}
const ctag=(tag,tier,cls)=>`<span class="ctg c${tier} ${cls||""}">${e(tag)}</span>`;
/* ism boshidagi ovul belgisi: belgi rasmi bor bo'lsa - rasm, bo'lmasa - rangli teg */
function pbd(x){const ph=String((x&&(x.ph||x.photo))||""),m=/#(?:[^#]*&)?b=(\d+)/.exec(ph),u=m&&typeof A!=="undefined"&&A&&A["badge_"+m[1]];return u?'<i class="nbp"><img src="'+e(u)+'" alt=""></i>':""}
function nbd(x){const b=x&&x.cb,p=pbd(x);if(!b)return p;return p+(embOk(b.e)?`<i class="nbe">${embHtml(b.e)}</i>`:`<b class="ctg c${b.t} nbt">${e(b.g)}</b>`)}
function nbc(c){return embOk(c.emb)?`<i class="nbe">${embHtml(c.emb)}</i>`:`<b class="ctg c${c.tier} nbt">${e(c.tag)}</b>`}
const cHm=ts=>{const d=new Date(ts*1000);return String(d.getHours()).padStart(2,"0")+":"+String(d.getMinutes()).padStart(2,"0")};
function openClan(){if(!ME.pro&&CCFG.pro){MODAL="pro";render()}else cfOpen(null)}
function clansRows(all){if(!RAT||RAT.kind!="clans")return'<div class="mu c">Yuklanmoqda...</div>';const q=all?"":CQ.trim().toLowerCase();
 const L=RAT.clans.filter(c=>!q||c.name.toLowerCase().includes(q)||c.tag.toLowerCase().includes(q));
 return L.length?L.map(c=>`<div class="cr" onclick="openCi(${c.id})">${embHtml(c.emb,"m")}<div class="f" style="flex:1;min-width:0"><div class="cn">${c.rank?`<span class="rkp r${Math.min(c.rank,4)}">#${c.rank}</span>`:""}<b>${e(c.name)}</b>${ctag(c.tag,c.tier)}</div><div class="mu cs">${c.points} ochko · ${c.n} a'zo · ${c.full?"to'la":c.jm=="open"?"ochiq":c.jm=="closed"?"yopiq":"so'rov bilan"}${c.mg?` · ${c.mg}+ o'yin`:""}</div></div>${c.id==RAT.my?'<span class="mu">Sizniki</span>':`<button class="btn sm" onclick="event.stopPropagation();openCi(${c.id})">Ko'rish</button>`}</div>`).join(""):'<div class="card mu c">Ovul topilmadi</div>'}
function clansList(){const x=$("cls");if(x)x.innerHTML=clansRows()}
function clansView(){return hd("Ovul")+`<div class="chero"><b>${ico("clans","⛺")} O'z ovulingizni yarating</b><p>Do'stlaringizni yig'ing va o'z davrangizda birga o'ynang.</p><div class="cf"><span>💬 Ovul suhbati</span><span>➤ Xonaga chaqiruv</span><span>🎮 Ovul o'yinlari</span><span>◎ Teg va emblema</span></div>${ME.cid?`<button class="btn wh" onclick="openCi(${ME.cid})">${ico("clans","⛺")} Mening ovulim</button>`:`<button class="btn wh" onclick="openClan()">＋ Ovul yaratish <span class="bdg pro">Pro</span></button>`}</div>
<div class="srch"><input placeholder="Ovul nomi yoki tegi..." value="${e(CQ)}" oninput="CQ=this.value;clansList()"></div><div class="sec-t"><span>🔥 Faol ovullar</span></div><div id="cls">${clansRows()}</div>`}
/* ---- ovul sahifasi (xona ichida - oyna) ---- */
function clanPage(){const c=CI;return hd("Ovul")+(c?clanBody(c):'<div class="mu c" style="margin:30px">Yuklanmoqda...</div>')}
function clanLeave(){const c=CI;if(!c)return;const m=c.role=="owner"?(c.n>1?"Oqsoqollik eng faol a'zoga o'tadi. Ovuldan chiqasizmi?":"Siz yagona a'zosiz, ovul tarqatiladi. Davom etasizmi?"):"Ovuldan chiqasizmi?";if(confirm(m))send({t:"clan_leave"})}
function clanBody(c){const lead=c.role=="owner"||c.role=="mirza",own=c.role=="owner";
 const hdr=`<div class="card chd"><div class="chr">${embHtml(c.emb,"l")}<div class="f" style="min-width:0"><div class="cnm"><b>${e(c.name)}</b>${ctag(c.tag,c.tier,"lg")}</div><div class="mu" style="font-size:13px;margin-top:3px">${c.n}/${c.max} a'zo</div></div></div>
<div class="lvrow"><span>${c.lvl}-daraja</span><div class="lvbar"><i style="width:${c.prog}%"></i></div><span>${c.nxt?`${c.nxt} ochkoda ${ctag(c.tag,c.ntier)}`:"Maksimal"}</span></div>
<div class="cpl">${c.rank?`<span class="pill2">#${c.rank}</span>`:""}${c.sp?'<span class="pill2 sp">Rasmiy</span>':""}<span class="pill2">${emj("trophy","🏆")} ${c.pts} ochko</span></div>${c.descr?`<div class="mu" style="margin-top:10px;font-size:14px">${e(c.descr)}</div>`:""}</div>
<div class="mu own">👑 Oqsoqol: <b>${e(c.owner)}</b></div>
<div class="acc" onclick="CRULES=!CRULES;render()">📖 Qoidalar <span>${CRULES?"⌃":"⌄"}</span></div>${CRULES?`<div class="rulesb">${c.rules?e(c.rules):"Qoidalar kiritilmagan."}</div>`:""}`;
 let act="";
 if(c.mine)act=`<div class="cact"><button class="btn sec" onclick="go('cchat')">💬 Ovul suhbati</button>${lead?`<button class="btn sec" onclick="CREQ=!CREQ;render()">📩 So'rovlar${c.reqs.length?` <span class="rcn">${c.reqs.length}</span>`:""}</button>`:""}${own?`<button class="btn sec" onclick="cfOpen(CI)">⚙ Sozlamalar</button>`:""}<button class="btn sec" onclick="clanLeave()">↩ Ovuldan chiqish</button></div>`;
 else if(c.has)act=`<div class="card mu c">Avval hozirgi ovuldan chiqing</div>`;
 else{const why=c.jm=="closed"?"Ovul yopiq: yangi a'zo qabul qilinmaydi":c.full?"Ovul to'la":!c.mgok?`Qo'shilish uchun kamida ${c.mg} ta o'yin kerak (sizda: ${c.mygames})`:"";
  act=why?`<div class="card mu c">${why}</div>`:c.req?`<div class="card c"><div class="mu" style="margin-bottom:10px">✓ So'rov yuborilgan. Oqsoqol javobini kuting.</div><button class="btn sec" onclick="send({t:'clan_cancel',id:${c.id}})">So'rovni bekor qilish</button></div>`
  :`<div class="card c" style="margin:8px 0"><div class="opt" style="border:0;padding:0;background:none;margin:0"><div><b>Qoidalarni o'qidim va roziman</b></div><button class="sw ${CAGREE?"on":""}" onclick="CAGREE=!CAGREE;render()"></button></div><button class="btn" style="margin-top:12px" ${CAGREE?"":"disabled"} onclick="CAGREE=false;send({t:'clan_join',id:${c.id}})">${IC.users} ${c.jm=="request"?"So'rov yuborish":"Ovulga qo'shilish"}</button></div>`}
 const rq=c.mine&&lead&&CREQ?`<div class="sec-t"><span>📩 So'rovlar · ${c.reqs.length}</span></div>`+(c.reqs.length?c.reqs.map(x=>`<div class="cmm" onclick="openPi(${x.id})">${av(x.ph,x.name)}<div style="min-width:0"><b>${e(x.name)}</b><div class="mu" style="font-size:12px">${x.g} o'yin · ${x.w} g'alaba</div></div><span class="rq"><button class="rqy" onclick="event.stopPropagation();send({t:'clan_req',id:${x.id},ok:1})">✓</button><button class="rqn" onclick="event.stopPropagation();send({t:'clan_req',id:${x.id},ok:0})">✕</button></span></div>`).join(""):'<div class="mu c" style="margin:8px">So\'rov yo\'q</div>'):"";
 const mem=`<div class="sec-t"><span>${IC.users12} A'zolar · ${c.n}</span><span style="text-transform:none;letter-spacing:0">7 kunda olib kelgan ochkosi</span></div>`+c.mem.map(x=>{const canM=c.mine&&x.id!=ME.id&&(own||(lead&&x.role=="member"));
  return`<div class="cmm" onclick="openPi(${x.id})">${av(x.ph,x.name,x.pro?"pro":"")}<div style="min-width:0;flex:1"><b>${nbc(c)}${e(x.name)}</b><div class="rl">${x.role=="owner"?'<span class="go">Oqsoqol</span> ':x.role=="mirza"?'<span class="go">Mirza</span> ':""}<span class="mu"><i class="dot ${x.on?"on":""}"></i>${x.on?"onlayn":"oflayn"}</span></div></div><span class="p7">${emj("trophy","🏆")} +${x.p7}</span>${canM?`<button class="mg" onclick="event.stopPropagation();MM=${x.id};MODAL='cmem';render()">⋮</button>`:""}</div>`}).join("");
 return hdr+act+rq+mem}
function cviewHtml(){const c=CI;return`<div class="ov mid" onclick="if(event.target==this){MODAL=null;render()}"><div class="sheet cm"><div class="cmh"><span>${nbc(c)}${e(c.name)}</span><button class="x" onclick="MODAL=null;render()">✕</button></div>${clanBody(c)}</div></div>`}
let CAGREE=false;
function cmemView(){const c=CI,x=c&&c.mem.find(y=>y.id==MM);if(!x){MODAL=null;return""}const own=c.role=="owner";
 return`<div class="ov mid" onclick="if(event.target==this){MODAL=null;render()}"><div class="sheet cm"><div class="cmh"><span>${nbc(c)}${e(x.name)}</span><button class="x" onclick="MODAL=null;render()">✕</button></div>
${own?`<button class="btn sec" style="margin:8px 0" onclick="send({t:'clan_role',id:${x.id},mirza:${x.role=="mirza"?0:1}});MODAL=null;render()">${x.role=="mirza"?"Mirzalikni olib tashlash":"⭐ Mirza qilish"}</button><button class="btn sec" style="margin:8px 0" onclick="if(confirm('Oqsoqollikni shu a\\'zoga topshirasizmi?')){send({t:'clan_transfer',id:${x.id}});MODAL=null;render()}">👑 Oqsoqollikni topshirish</button>`:""}
<button class="btn red" style="margin:8px 0" onclick="if(confirm('Ovuldan chiqarasizmi?')){send({t:'clan_kick',id:${x.id}});MODAL=null;render()}">Ovuldan chiqarish</button></div></div>`}
function cinvView(){return`<div class="ov mid" onclick="if(event.target==this){MODAL=null;render()}"><div class="sheet cm"><div class="cmh"><span>⛺ Ovul chaqiruvi</span><button class="x" onclick="MODAL=null;render()">✕</button></div>
<div style="margin:12px 2px;line-height:1.5"><b>${e(INV.n)}</b> sizni <b>${e(INV.rn)}</b> xonasiga chaqirdi.</div><button class="btn" onclick="send({t:'join',id:INV.rid});INV=null;MODAL=null;render()">Qo'shilish</button><button class="btn sec" style="margin-top:8px" onclick="INV=null;MODAL=null;render()">Keyinroq</button></div></div>`}
/* ---- yaratish / sozlash formasi ---- */
function cfOpen(c){CF=c?{name:c.name,tag:c.tag,emb:c.emb,jm:c.jm,mg:c.mg,rules:c.rules||"",descr:c.descr||"",edit:c.id}:{name:"",tag:"",emb:1,jm:"open",mg:0,rules:"",descr:"",edit:0};MODAL="cform";render()}
function cfPvHtml(){const t=(CF.tag||"").toUpperCase();return`${embHtml(CF.emb,"s")} ${t?ctag(t,4):""} <span>${e(CF.name||"Ovul nomi")}</span><span class="mu" style="margin-left:auto;font-weight:600;font-size:12px">${e(ME.name||"Ism")}</span>`}
function cfPv(){const x=$("cfpv");if(x)x.innerHTML=cfPvHtml()}
function cfSeg(b,k){CF.jm=k;[...b.parentNode.children].forEach(x=>x.classList.toggle("on",x===b))}
function cfEmb(k,b){CF.emb=k;[...b.parentNode.children].forEach(x=>x.classList.toggle("on",x===b));cfPv()}
function cfSave(){const o={name:CF.name,tag:CF.tag,emb:CF.emb,jm:CF.jm,mg:+CF.mg||0,rules:CF.rules,descr:CF.descr};
 if(String(CF.name).trim().length<3){toast("Nom kamida 3 belgi bo'lsin");return}if(!/^[A-Z0-9]{2,5}$/.test(CF.tag||"")){toast("Teg 2–5 ta lotin harfi yoki raqam bo'lsin");return}
 send(CF.edit?{t:"clan_edit",...o}:{t:"clan_create",...o});MODAL=null;render()}
function cfView(){const ed=!!CF.edit,n=EMBC.n||24;
 return`<div class="ov" onclick="if(event.target==this){MODAL=null;render()}"><div class="sheet cfm"><div class="hd"><span>${ed?"⚙ Ovul sozlamalari":"⛺ Yangi ovul"}</span><button class="x" onclick="MODAL=null;render()">✕</button></div>
<label class="field">Ovul belgisi</label><div class="epk">${Array.from({length:n},(_,k)=>`<button class="${CF.emb==k+1?"on":""}" onclick="cfEmb(${k+1},this)">${embHtml(k+1,"q")}</button>`).join("")}</div>
<label class="field">Ovul nomi</label><input maxlength="24" value="${e(CF.name)}" placeholder="Masalan: Qora toj" oninput="CF.name=this.value;cfPv()">
<label class="field">Teg (2–5 ta lotin harfi yoki raqam)</label><input maxlength="5" value="${e(CF.tag)}" placeholder="QTJ" style="text-transform:uppercase" oninput="this.value=this.value.toUpperCase().replace(/[^A-Z0-9]/g,'');CF.tag=this.value;cfPv()">
<div class="mu" style="margin:10px 2px 0;font-size:12px">Ismingiz ovulga qo'shilgach shunday ko'rinadi:</div><div class="npv" id="cfpv">${cfPvHtml()}</div>
<label class="field">Qo'shilish turi</label><div class="seg"><button class="${CF.jm=="open"?"on":""}" onclick="cfSeg(this,'open')">Ochiq</button><button class="${CF.jm=="request"?"on":""}" onclick="cfSeg(this,'request')">So'rov bilan</button><button class="${CF.jm=="closed"?"on":""}" onclick="cfSeg(this,'closed')">Yopiq</button></div>
<label class="field">Minimal o'yinlar soni (0 — cheklov yo'q)</label><input type="number" inputmode="numeric" min="0" max="1000" value="${e(CF.mg)}" oninput="CF.mg=this.value">
<label class="field">Qisqa tavsif</label><input maxlength="120" value="${e(CF.descr)}" placeholder="Ovul haqida bir jumla" oninput="CF.descr=this.value">
<label class="field">Ovul qoidalari</label><textarea rows="4" maxlength="600" placeholder="Masalan: 1. Hurmat. 2. Faol bo'ling." oninput="CF.rules=this.value">${e(CF.rules)}</textarea>
<div style="height:14px"></div><button class="btn" onclick="cfSave()">✓ ${ed?"Saqlash":"Yaratish"}</button>
${ed?`<button class="btn red" style="margin-top:8px" onclick="if(confirm('Ovul butunlay tarqatiladi, barcha a\\'zolar chiqarib yuboriladi. Davom etasizmi?')){send({t:'clan_disband'});MODAL=null;render()}">Ovulni tarqatish</button>`:""}</div></div>`}
/* ---- ovul suhbati ---- */
function cchMsg(x){return`<div class="cbm ${x.uid==ME.id?"me":""}"><div class="cbn">${e(x.u)}</div><div class="cbb">${e(x.t)}</div><div class="cbt">${cHm(x.ts)}</div></div>`}
function cchatPage(){return hd("Ovul suhbati")+`<div class="cch" id="cchl">${CCH.length?CCH.map(cchMsg).join(""):'<div class="mu c" style="margin:30px 0">Hali xabar yo\'q. Birinchi bo\'ling!</div>'}</div><div class="cchbar"><input id="cci" maxlength="300" autocomplete="off" placeholder="Xabar yozing..." value="${e(CCD)}" oninput="CCD=this.value" onkeydown="if(event.key=='Enter')cSend()"><button class="btn" onclick="cSend()">➤</button></div>`}
function cSend(){const i=$("cci"),v=(i?i.value:CCD).trim();if(!v)return;send({t:"clan_send",text:v});CCD="";if(i){i.value="";i.focus()}}

function barRender(){const b=$("bar");if(!ROOM||!canChat()||!COMPOSE){b.style.display="none";b.innerHTML="";return}
 if(b.style.display=="block"&&$("ci")){const r=b.querySelector(".rep");if(!!r==!!REPLY)return}
 const v=$("ci")?$("ci").value:"";b.style.display="block";
 b.innerHTML=(REPLY?`<div class="rep"><span><b>${e(REPLY.u)}</b> ${e(REPLY.t)}</span><button onclick="REPLY=null;barRender()">✕</button></div>`:"")+`<div class="in"><input id="ci" maxlength="200" placeholder="Xabar" onkeydown="if(event.key=='Enter')sc()"><button class="btn" onclick="sc()">Yuborish</button></div>`;$("ci").value=v}
function rep(id){const c=ROOM.chat.find(x=>x.id==id);if(!c)return;REPLY={id:c.id,u:c.u,t:c.t.slice(0,50)};COMPOSE=true;render();setTimeout(()=>$("ci")&&$("ci").focus(),50)}
function sc(){const i=$("ci");if(i&&i.value.trim()){send({t:"chat",text:i.value,re:REPLY?REPLY.id:0});i.value="";REPLY=null;COMPOSE=false;render()}}
setInterval(()=>{const t=$("tm");if(t&&ROOM&&ROOM.deadline)t.textContent=fmt2(ROOM.deadline-Math.floor(now()));
  const dl=$("dl");if(dl&&ROOM&&ROOM.deadline)dl.textContent=Math.max(0,ROOM.deadline-Math.floor(now()));const pb=$("pb");if(pb&&ROOM&&ROOM.deadline&&ROOM.dur)pb.style.width=Math.max(0,Math.min(100,100*(ROOM.deadline-now())/ROOM.dur))+"%";
 const c=$("cool");if(c){const r=rest(ME,"cool");c.textContent=fmt(r);if(r<=0)render()}
 const tl=$("tleft");if(tl&&TASKS)tl.textContent=fmtH(rest(TASKS,"left"))},500);
/* ---- yangi: ikonalar, QR, ovozli xona, stol rejimi, profil / ovul oynalari ---- */
const svg=(d,w)=>`<svg viewBox="0 0 24 24" width="${w||20}" height="${w||20}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
const IP={mic:'<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',micoff:'<path d="M9 9v2a3 3 0 0 0 5 2.2M15 9.5V6a3 3 0 0 0-5.5-1.7M5 11a7 7 0 0 0 11 5.7M19 11a7 7 0 0 1-.6 2.8M12 18v3M3 3l18 18"/>',
 lock:'<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',spk:'<path d="M4 9v6h4l5 4V5L8 9H4z"/><path d="M16 9a4 4 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11"/>',
 spkoff:'<path d="M4 9v6h4l5 4V5L8 9H4z"/><path d="M17 9l5 6M22 9l-5 6"/>',vib:'<rect x="8" y="3" width="8" height="18" rx="2"/><path d="M4 8v8M20 8v8M1.5 10.5v3M22.5 10.5v3"/>',
 exit:'<path d="M10 4H5v16h5"/><path d="M15 8l4 4-4 4M19 12H9"/>',hp:'<path d="M4 15v-3a8 8 0 0 1 16 0v3"/><rect x="3" y="14" width="4" height="6" rx="1.5"/><rect x="17" y="14" width="4" height="6" rx="1.5"/>',
 send:'<path d="M21 3L3 11l7 2 2 7 9-17z"/>',users:'<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><circle cx="17" cy="9" r="2.5"/><path d="M17 14a5 5 0 0 1 5 5"/>',
 eye:'<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',check:'<path d="M5 12l5 5 9-10"/>'};
const IC={};for(const k in IP){IC[k]=svg(IP[k],20);IC[k+"12"]=svg(IP[k],12)}
/*QR*/
function qrMatrix(text){
 const data=Array.from(new TextEncoder().encode(text));
 const ECC=[10,16,26,18,24,16,18,22,22,26],NB=[1,1,1,2,2,4,4,4,5,5];
 const raw=v=>{let r=(16*v+128)*v+64;if(v>=2){const a=Math.floor(v/7)+2;r-=(25*a-10)*a-55;if(v>=7)r-=36}return r};
 let ver=1;for(;ver<=10;ver++){const cw=Math.floor(raw(ver)/8)-ECC[ver-1]*NB[ver-1];if(4+(ver<10?8:16)+data.length*8<=cw*8)break}
 if(ver>10)return null;
 const size=ver*4+17,totalCw=Math.floor(raw(ver)/8),dataCw=totalCw-ECC[ver-1]*NB[ver-1];
 let bits=[];const put=(v,n)=>{for(let i=n-1;i>=0;i--)bits.push((v>>>i)&1)};
 put(4,4);put(data.length,ver<10?8:16);data.forEach(b=>put(b,8));
 put(0,Math.min(4,dataCw*8-bits.length));while(bits.length%8)bits.push(0);
 for(let p=0xEC;bits.length<dataCw*8;p^=0xEC^0x11)put(p,8);
 const cw=[];for(let i=0;i<bits.length;i+=8){let b=0;for(let j=0;j<8;j++)b=b<<1|bits[i+j];cw.push(b)}
 const mul=(x,y)=>{let z=0;for(let i=7;i>=0;i--){z=(z<<1)^((z>>>7)*0x11D);z^=((y>>>i)&1)*x}return z};
 const gen=d=>{const r=new Array(d).fill(0);r[d-1]=1;let root=1;for(let i=0;i<d;i++){for(let j=0;j<d;j++){r[j]=mul(r[j],root);if(j+1<d)r[j]^=r[j+1]}root=mul(root,2)}return r};
 const rs=(blk,g)=>{const r=new Array(g.length).fill(0);for(const b of blk){const f=b^r.shift();r.push(0);g.forEach((c,i)=>r[i]^=mul(c,f))}return r};
 const nb=NB[ver-1],eb=ECC[ver-1],sb=Math.floor(totalCw/nb),short=nb-totalCw%nb,g=gen(eb),blocks=[];
 for(let i=0,k=0;i<nb;i++){const len=sb-eb+(i<short?0:1);const d=cw.slice(k,k+len);k+=len;const e=rs(d,g);if(i<short)d.push(0);blocks.push(d.concat(e))}
 const all=[];for(let i=0;i<blocks[0].length;i++)blocks.forEach((b,j)=>{if(i!=sb-eb||j>=short)all.push(b[i])});
 const M=Array.from({length:size},()=>new Array(size).fill(false)),F=Array.from({length:size},()=>new Array(size).fill(false));
 const set=(x,y,v)=>{M[y][x]=v;F[y][x]=true};
 for(let i=0;i<size;i++){set(6,i,i%2==0);set(i,6,i%2==0)}
 const finder=(cx,cy)=>{for(let dy=-4;dy<=4;dy++)for(let dx=-4;dx<=4;dx++){const x=cx+dx,y=cy+dy;if(x<0||y<0||x>=size||y>=size)continue;const d=Math.max(Math.abs(dx),Math.abs(dy));set(x,y,d!=2&&d!=4)}};
 finder(3,3);finder(size-4,3);finder(3,size-4);
 let pos=[];if(ver>1){const n=Math.floor(ver/7)+2,step=ver==32?26:Math.ceil((ver*4+4)/(n*2-2))*2;pos=[6];for(let p=size-7;pos.length<n;p-=step)pos.splice(1,0,p)}
 pos.forEach((px,i)=>pos.forEach((py,j)=>{if((i==0&&j==0)||(i==0&&j==pos.length-1)||(i==pos.length-1&&j==0))return;for(let dy=-2;dy<=2;dy++)for(let dx=-2;dx<=2;dx++)set(px+dx,py+dy,Math.max(Math.abs(dx),Math.abs(dy))!=1)}));
 const fmt=mask=>{const d=0<<3|mask;let r=d;for(let i=0;i<10;i++)r=(r<<1)^((r>>>9)*0x537);const b=(d<<10|r)^0x5412,bit=i=>((b>>>i)&1)!=0;
  for(let i=0;i<=5;i++)set(8,i,bit(i));set(8,7,bit(6));set(8,8,bit(7));set(7,8,bit(8));for(let i=9;i<15;i++)set(14-i,8,bit(i));
  for(let i=0;i<8;i++)set(size-1-i,8,bit(i));for(let i=8;i<15;i++)set(8,size-15+i,bit(i));set(8,size-8,true)};
 fmt(0);
 if(ver>=7){let r=ver;for(let i=0;i<12;i++)r=(r<<1)^((r>>>11)*0x1F25);const b=ver<<12|r;for(let i=0;i<18;i++){const v=((b>>>i)&1)!=0,a=size-11+i%3,c=Math.floor(i/3);set(a,c,v);set(c,a,v)}}
 const fixed=F.map(r=>r.slice());
 let i=0;for(let right=size-1;right>=1;right-=2){if(right==6)right=5;for(let vert=0;vert<size;vert++)for(let j=0;j<2;j++){const x=right-j,up=((right+1)&2)==0,y=up?size-1-vert:vert;if(!fixed[y][x]&&i<all.length*8){M[y][x]=((all[i>>>3]>>>(7-(i&7)))&1)!=0;i++}}}
 const mk=m=>(x,y)=>[(x+y)%2==0,y%2==0,x%3==0,(x+y)%3==0,(Math.floor(x/3)+Math.floor(y/2))%2==0,x*y%2+x*y%3==0,(x*y%2+x*y%3)%2==0,((x+y)%2+x*y%3)%2==0][m];
 const apply=m=>{const f=mk(m);for(let y=0;y<size;y++)for(let x=0;x<size;x++)if(!fixed[y][x])M[y][x]=M[y][x]!=f(x,y)};
 const pen=()=>{let r=0;const line=a=>{let run=1;for(let k=1;k<=a.length;k++){if(k<a.length&&a[k]==a[k-1])run++;else{if(run>=5)r+=run-2;run=1}}
   for(let k=0;k+11<=a.length;k++){const w=a.slice(k,k+11).map(v=>v?1:0).join("");if(w=="10111010000"||w=="00001011101")r+=40}};
  for(let y=0;y<size;y++){line(M[y]);line(M.map(row=>row[y]))}
  for(let y=0;y<size-1;y++)for(let x=0;x<size-1;x++)if(M[y][x]==M[y][x+1]&&M[y][x]==M[y+1][x]&&M[y][x]==M[y+1][x+1])r+=3;
  let d=0;M.forEach(row=>row.forEach(v=>{if(v)d++}));r+=Math.floor(Math.abs(d*20-size*size*10)/(size*size))*10;return r};
 let best=0,bp=1e9;for(let m=0;m<8;m++){apply(m);fmt(m);const p=pen();if(p<bp){bp=p;best=m}apply(m)}
 apply(best);fmt(best);return M}
function qrSvg(text,px){let M=null;try{M=qrMatrix(text)}catch(x){}if(!M)return"";const n=M.length,q=2;let d="";M.forEach((row,y)=>row.forEach((v,x)=>{if(v)d+=`M${x+q},${y+q}h1v1h-1z`}));
 return`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${n+q*2} ${n+q*2}" width="${px}" height="${px}" shape-rendering="crispEdges"><rect width="100%" height="100%" fill="#fff"/><path d="${d}" fill="#000"/></svg>`}
const SEASON_N=()=>Math.floor((Math.floor((Date.now()/1000+18000)/86400)-20454)/90)+1;
/*tema*/
function toggleTheme(){setTheme(isDark()?"light":"dark")}
/*taplar*/
function openPi(id){if(id)send({t:"pinfo",id})}
function openCi(id){CI=null;send({t:"cinfo",id})}
function vib(p){try{if(VIB&&navigator.vibrate)navigator.vibrate(p)}catch(x){}}
function phaseFx(was,m){if(!was||was.phase==m.phase)return;
 if(m.mode=="voice"&&m.phase=="last"&&m.speaker==ME.id&&LS&&!MIC){MIC=true;send({t:"mic",on:true});voiceApply()}
 if(m.mode=="table"&&["reveal","day","vote","night"].includes(m.phase))vib(m.phase=="night"?[200,100,200]:150);
 if(m.mode=="voice"&&was.phase=="lobby"&&m.phase=="intro"&&LS&&!MIC){MIC=true;send({t:"mic",on:true});voiceApply()}}
/*umumiy bo'laklar*/
const dName=id=>{const p=ROOM&&ROOM.players.find(x=>x.id==id);return p?p.name:"?"};
function waitTxt(ids){const nm=ids.map(dName);if(!nm.length)return"Hammasi tayyor";const s=nm.slice(0,3).join(", ");return nm.length>3?s+" va yana "+(nm.length-3)+" kishi":s+" kutilmoqda"}
function cons(ic,label,cnt,need,on,ids,act){const pct=need?Math.min(100,100*cnt/need):0;
 return`<button class="cons ${on?"on":""}" onclick="${act}"><i style="width:${pct}%"></i><div class="ct"><b>${ic} ${label}</b><span>${cnt}/${need}</span></div><div class="cs">${e(waitTxt(ids))}</div></button>`}
function tHead(title,sub,chip){const R=ROOM;return`<div class="pbar"><i id="pb" style="width:${R&&R.deadline&&R.dur?Math.max(0,Math.min(100,100*(R.deadline-now())/R.dur)):0}%"></i></div><div class="rh">${tg?"":'<button class="back" style="float:left" onclick="back()">← Orqaga</button>'}<b>${title}</b>${chip||""}${sub?`<div class="mu rs">${sub}</div>`:""}</div><div class="hline"></div>`}
function tTools(exit,lobby){return`<div class="trow">${lobby?`<button class="xr pill" onclick="back()">✕ Chiqish</button>`:exit?`<button class="xr" onclick="back()">✕</button>`:""}<span class="f"></span><button class="tb ${VIB?"on":""}" onclick="VIB=!VIB;render()">${IC.vib}</button><button class="tb ${SPK?"on":""}" onclick="SPK=!SPK;render()">${SPK?IC.spk:IC.spkoff}</button></div>`}
const tmr=R=>fmt2(Math.max(0,(R.deadline||0)-Math.floor(now())));
function tblView(R){const m=R.me,ph=R.phase,al=R.players.filter(p=>p.alive),need=Math.floor(al.length/2)+1;
 if(ph=="lobby"){const n=R.players.length,rd=R.ready,link="https://t.me/"+BOT+"?startapp=room_"+R.id;
  return tHead(e(R.name)+" · Stol","👥 "+n+"/"+R.cap+" o'yinchi")+tTools(false,true)+
  `<div class="card qrc"><div class="qrb">${qrSvg(link,124)}</div><div class="mu c" style="margin-top:8px">yoki kod: <b class="cdd">${e(R.id)}</b></div><button class="shl" onclick="share()">${IC.send12} Chatga yuborish</button></div>
<div class="sec-t"><span>${IC.users12} Kim keldi</span></div><div class="chips2">${R.players.map(p=>`<span class="pc ${rd.includes(p.id)?"rdy":""}" onclick="openPi(${p.id})">${av(p.ph,p.name)}<span>${nbd(p)}${e(p.name)}</span>${rd.includes(p.id)?`<i>${IC.check12}</i>`:""}</span>`).join("")}</div>
<div style="height:90px"></div><div class="cbar">${n<R.minp?`<div class="mu c">O'yin boshlanishi uchun kamida ${R.minp} o'yinchi kerak</div>`:""}${cons(IC.check,"Tayyorman",rd.length,n,m.ready,R.players.filter(p=>!rd.includes(p.id)).map(p=>p.id),"send({t:'ready'})")}</div>`}
 if(ph=="deal")return tHead("O'yin boshlanmoqda","")+`<div class="deal"><div class="ring"><b id="dl">${Math.max(0,(R.deadline||0)-Math.floor(now()))}</b></div><p>10 soniyadan so'ng rolingiz taqdim etiladi, ungacha telefoningiz ekrani boshqalarga ko'rinmasligiga ishonch hosil qiling</p></div>`;
 if(ph=="reveal"){const r=m.role,maf=MT.includes(r),pl=R.players.filter(p=>!p.left),ak=R.ack;
  return tHead("Rollar tarqatildi","Telefoningizni hech kimga ko'rsatmang")+`<div class="rvw"><div class="rcard ${maf?"maf":""}"><div class="ri">${media("role_"+r)||REM[r]||""}</div><small>SIZNING ROLINGIZ</small><h1>${e(RN[r]||r||"")}</h1><p>${e(RD[r]||"Maxsus rol. Qoidalari tez orada qo'shiladi.")}</p></div></div>
<div style="height:150px"></div><div class="cbar"><div class="warn2">${IC.eye}<span>Ko'rib bo'lgach yoping. Hamma yopgach, birinchi kun boshlanadi.</span></div>${cons(IC.check,"Tushunarli",ak.length,pl.length,m.ack,pl.filter(p=>!ak.includes(p.id)).map(p=>p.id),"send({t:'ack'})")}</div>`}
 if(ph=="day"){const dead=!m.alive;
  return tHead(R.day+"-kun · Muhokama","")+tTools(true)+`<div class="bigt"><b id="tm">${tmr(R)}</b><p>Telefonni qo'ying, gaplashing.</p></div>`+
  (dead?`<div class="cbar"><div class="hint">Siz o'yindan chiqdingiz. Kuzatishingiz mumkin</div></div>`:`<div style="height:150px"></div><div class="cbar">${cons(IC.clock,"Vaqtni cho'zish · +2 daqiqa",R.extn,need,m.ext,al.filter(p=>!(R.extl||[]).includes(p.id)).map(p=>p.id),"send({t:'extend'})")}${cons(IC.check,"Ovoz berishga o'tish",R.skip,need,m.skipped,al.filter(p=>!(R.skipl||[]).includes(p.id)).map(p=>p.id),"send({t:'skipday'})")}</div>`)}
 if(ph=="vote"){const vt=R.vt||{},voted=al.filter(p=>vt[p.id]!=null),rest_=al.filter(p=>vt[p.id]==null);
  const row=p=>{const t=vt[p.id];return`<div class="vrow">${av(p.ph,p.name)}<span>${nbd(p)}${e(p.name)}</span><i>→</i>${t==null?'<span class="mu">hali ovoz bermadi</span>':t===0?"<b>ovoz bermadi</b>":`<b>${e(dName(t))}</b>`}</div>`};
  return tHead(R.day+"-kun · Ovoz berish","",`<span class="tp" id="tm">${tmr(R)}</span>`)+
  `<div class="vstrip">${R.players.filter(p=>!p.left).map(p=>{const off=!p.alive||p.id==ME.id||!m.alive;return`<div class="vcc ${m.vote==p.id?"sel":""} ${off?"off":""}" onclick="pick(${p.id})">${p.v?`<i class="vb">${p.v}</i>`:""}${av(p.ph,p.name)}<span>${nbd(p)}${e(p.name)}</span></div>`}).join("")}</div>
<div class="sec-t"><span>${IC.eye12} Kim kimga</span></div>${voted.map(row).join("")}${rest_.map(row).join("")}
${m.alive&&!R.spec?`<button class="btn sec sm skp" style="${m.vote===0?"border-color:var(--ac)":""}" onclick="send({t:'vote',to:0})">${m.vote===0?"✓ ":""}Ovoz bermayman</button>`:""}`}
 if(ph=="night"){const act=m.alive&&(MT.includes(m.role)||m.role=="doktor"||m.role=="komissar");
  return tHead(R.night+"-tun","",R.deadline?`<span class="tp" id="tm">${tmr(R)}</span>`:"")+(act?nightHtml(R,m):`<div class="bigt"><div class="moon">${emj("night","🌙")}</div><p>${m.alive?"Tun. Ko'zingizni yuming va telefonni qo'ying.":"Siz o'yindan chiqdingiz. Kuzatishingiz mumkin"}</p></div>`)+
  (R.priv.length?`<div class="card">${R.priv.map(x=>`<div><b>🔍 ${e(x)}</b></div>`).join("")}</div>`:"")}
 if(ph=="last"){const sp=R.players.find(p=>p.id==R.speaker);
  return tHead("So'nggi so'z","")+`<div class="bigt"><b id="tm">${tmr(R)}</b><p><b style="display:inline;font-size:inherit">${e(sp?sp.name:"")}</b> so'nggi so'zini aytmoqda</p></div>`+(R.speaker==ME.id?`<div class="cbar"><button class="btn" onclick="send({t:'lastdone'})">✓ Tugatdim</button></div>`:"")}
 return null}
/*ovozli xona (lobbi)*/
function voiceLobby(R){const lis=R.spec,n=R.players.filter(p=>!p.left).length,need=Math.max(0,R.minp-n),own=R.owner==ME.id&&!lis;
 let seats="";for(let i=0;i<R.cap;i++){const p=R.players[i];seats+=p?`<div class="seat on ${p.id==ME.id?"me":""}" onclick="openPi(${p.id})">${p.id==R.owner?'<span class="crw">👑</span>':""}${p.id==ME.id?'<span class="siz">SIZ</span>':""}<div class="sav">${av(p.ph,p.name,p.pro?"pro":"")}<i class="mc ${p.mic?"on":""}">${p.mic?IC.mic12:IC.micoff12}</i></div><span class="nm">${nbd(p)}${e(p.name)}</span></div>`:`<div class="seat em"><div class="plus">+</div><span class="nm">Bo'sh joy</span></div>`}
 const ban=lis?`<div class="vbn l">${IC.hp}<div><b>Siz tinglovchisiz.</b> ${ME.pro?"Pro obunangiz bilan cheksiz ovozli o'yinlar — qo'shilib o'ynang!":`Sizda <b>${ME.free_voice}</b> ta bepul ovozli o'yin bor — qo'shilib o'ynang!`}</div></div>`
  :`<div class="vbn">${IC.mic}<div><b>Ovozli xona.</b> Mikrofonni yoqing va salomlashing.${need?` Yana <b>${need}</b> o'yinchi kerak.`:" O'yinni boshlash mumkin."}</div></div>`;
 const link=!lis,st=lis?"Tinglovchi rejimi":!LS?(LSFAIL?"Mikrofon ruxsati yo'q — bosib qayta urinib ko'ring":"Mikrofon ulanmoqda..."):MIC?"Mikrofon yoqiq — bosib o'chiring":"Mikrofon o'chiq — bosib yoqing";
 const bm=link?`<button class="vmic ${MIC?"on":"red"}" onclick="micToggle()">${MIC?IC.mic:IC.micoff}</button>`:`<button class="vmic" disabled>${IC.lock}</button>`;
 return`<div class="rh">${tg?"":'<button class="back" style="float:left" onclick="back()">← Orqaga</button>'}<b>${e(R.name)}</b></div><div class="vwrap"><div class="row sp"><span class="vchip g">${IC.mic12} Ovozli</span><span class="row" style="gap:8px"><span class="vchip">${IC.hp12} ${R.specn}</span><span class="vchip">${IC.users12} ${n}/${R.cap}</span></span></div>${ban}<div class="seats">${seats}</div>
<div class="vbot"><div class="vctl"><button class="vb2 ${VIB?"":"off"}" onclick="VIB=!VIB;render()">${IC.vib}</button><button class="vb2 ${SPK?"":"off"}" onclick="SPK=!SPK;voiceApply();render()">${SPK?IC.spk:IC.spkoff}</button>${bm}<button class="vb2" onclick="back()">${IC.exit}</button></div><div class="vst">${st}</div>
${lis?`<button class="btn" onclick="send({t:'sit'})">${IC.mic} O'yinga qo'shilish ${ME.pro?"":`<span class="free">${ME.free_voice} ta bepul</span>`}</button>`
 :`${own&&n>=R.minp?`<button class="btn" style="margin-bottom:8px" onclick="send({t:'start'})">✓ Boshlash (${n}/${R.minp}+)</button>`:""}<button class="btn sec sm" style="width:100%" onclick="MODAL='invite';FR=null;send({t:'friends'});render()">${IC.send} Do'stlarni chaqirish</button>`}</div></div>`}
/*do'stlar*/
function friendsView(){const n=FR?FR.length:0;
 let h=hd("Do'stlar")+`<div class="fcnt">${n} / ${FRMAX}</div><div class="srch"><input id="fq" placeholder="Ism yoki ID bo'yicha izlash" onkeydown="if(event.key=='Enter')fsr()"></div><div style="height:8px"></div><button class="btn sec" onclick="fsr()">Izlash</button>`;
 if(FS)h+=`<div class="sec-t"><span>Natijalar</span></div>`+(FS.length?FS.map(f=>`<div class="lr" onclick="openPi(${f.id})">${av(f.ph,f.name)}<div><b>${nbd(f)}${e(f.name)}</b><div class="mu">ID ${f.id}</div></div>${f.fr?'<span class="mu" style="margin-left:auto">Do\'st</span>':`<button class="btn sm" style="margin-left:auto" onclick="event.stopPropagation();send({t:'fadd',id:${f.id}});toast('Qo\'shildi')">Qo'shish</button>`}</div>`).join(""):'<div class="mu c">Topilmadi</div>');
 h+=`<div class="sec-t"><span>${IC.users12} Do'stlarim</span></div>`;
 h+=FR==null?'<div class="mu c">Yuklanmoqda...</div>':FR.length?FR.map(f=>{const rm=f.room;
  const st=rm?`<i class="sdot b"></i><span>xonada · ${e(rm.name)}</span>`:f.on?`<i class="sdot g"></i><span>onlayn</span>`:`<i class="sdot"></i><span>oflayn · ${f.points} ball</span>`;
  return`<div class="fr" onclick="openPi(${f.id})">${av(f.ph,f.name)}<div class="f" style="flex:1"><b>${nbd(f)}${e(f.name)}</b><div class="fs">${st}</div></div>${rm?`<button class="btn fj" onclick="event.stopPropagation();send({t:'join',id:'${rm.id}'})">Qo'shilish</button>`:""}</div>`}).join(""):'<div class="card mu c">Hali do\'stlar yo\'q</div>';return h}
/*profil oynasi*/
function pviewHtml(){const p=PI,c=p.clan,pc=(a)=>a[0]?Math.round(100*a[1]/a[0]):0;
 const side=(nm,col,a)=>`<div class="tr"><span><i style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${col}"></i> ${nm}</span><div class="bar"><i style="width:${pc(a)}%;background:${col}"></i></div><span><b>${a[1]}</b>/${a[0]} · ${pc(a)}%</span></div>`;
 const rls=ORDER.filter(r=>p.rl[r]).map(r=>{const a=p.rl[r];return`<div class="rr"><div class="ri2">${media("role_"+r)||REM[r]||""}</div><div class="f"><b>${RN[r]}</b><div class="mu">${a[0]} ta o'yin</div></div><b class="${pc(a)>=50?"gr":""}">${pc(a)}%</b></div>`}).join("");
 return`<div class="ov mid" onclick="if(event.target==this){MODAL=null;render()}"><div class="sheet pm">${p.pro?'<span class="bdg pro pmtag">Pro</span>':""}
<div class="pmh">${av(p.ph,p.name,"big-av "+(p.pro?"pro":""))}<div class="f"><div class="pmn">${nbd(p)}${e(p.name)}</div><div class="mu">ID ${p.id}${p.on?" · onlayn":""}</div></div>${p.me?"":`<button class="pmb ${p.fr?"on":""}" onclick="send({t:'${p.fr?"fdel":"fadd"}',id:${p.id}});PI.fr=!PI.fr;render()">${p.fr?"✓":"＋"}</button>`}</div>
${p.bio?`<div class="pmbio">${e(p.bio)}</div>`:""}
${c?`<div class="pmc" onclick="openCi(${c.id})"><div class="pml">${embHtml(c.emb,"m")}</div><div class="f" style="flex:1;min-width:0"><div class="pmk"><span>OVUL</span>${ctag(c.tag,c.tier)}</div><div class="pmcn">${e(c.name)}</div><div style="opacity:.8;font-size:13px">${c.n}/${c.max} a'zo · ${c.lvl}-daraja</div></div>${c.rank?`<span class="pmr">#${c.rank}</span>`:""}</div>`:""}
${p.me?"":`<div class="pmx"><button class="pbl" onclick="send({t:'${p.bl?"unblock":"block"}',id:${p.id}})">${p.bl?"Blokdan chiqarish":"Bloklash"}</button><button onclick="if(confirm('Shikoyat yuborilsinmi?'))send({t:'report',id:${p.id}})">⚠ Shikoyat</button></div>`}
<div class="pms"><div class="sec-t" style="margin-top:8px"><span>${emj("trophy","🏆")} Statistika</span><span>${p.season}-mavsum · ${p.sleft} kun qoldi</span></div>
<div class="stat2"><div><b>${p.g}</b><span>O'yinlar</span></div><div><b class="gr">${p.w}</b><span>G'alabalar</span></div><div><b class="go">${p.wr}%</b><span>G'alaba foizi</span></div><div><b>${p.sv}%</b><span>Omon qolish</span></div></div>
<div class="sec-t"><span>${IC.users12} Tomonlar bo'yicha</span></div><div class="tm2">${side("Aholi","#5b6cff",p.tw.town)}${side("Mafia","#e5484d",p.tw.mafia)}</div>
${rls?`<div class="sec-t"><span>Rollar bo'yicha</span></div>${rls}`:""}</div>
<button class="btn sec" onclick="MODAL=null;render()">✕ Yopish</button></div></div>`}
/*ovozli xona: WebRTC (mesh). TURN, perfect negotiation, ICE restart, audio unlock, mikrofon xatosi*/
let ICE=[{urls:["stun:stun.l.google.com:19302"]}],LSFAIL=0,LSBUSY=false,ACX=null;
function iceCfg(){const p=(ME.prefs&&ME.prefs.voice)||"auto";let s=ICE;if(p=="direct"){s=ICE.filter(x=>!x.username);if(!s.length)s=ICE}
 const c={iceServers:s,iceCandidatePoolSize:2};if(p=="proxy")c.iceTransportPolicy="relay";return c}
function dropPc(id){const S=PC[id];if(S){clearTimeout(S.t);try{S.pc.close()}catch(x){}delete PC[id]}const a=$("au"+id);if(a){a.srcObject=null;a.remove()}}
function closePCs(){for(const i of Object.keys(PC))dropPc(i);document.querySelectorAll("audio.rtc").forEach(a=>a.remove())}
function stopVoice(){closePCs();if(LS){LS.getTracks().forEach(t=>t.stop());LS=null}MIC=false;LSFAIL=0}
function unlockAudio(){document.querySelectorAll("audio.rtc").forEach(a=>{if(a.paused&&a.srcObject&&!a.muted)a.play().catch(()=>{})});
 try{if(!ACX&&window.AudioContext)ACX=new AudioContext();if(ACX&&ACX.state=="suspended")ACX.resume()}catch(x){}}
document.addEventListener("pointerdown",unlockAudio,{passive:true});document.addEventListener("touchend",unlockAudio,{passive:true});
function canTalk(){const R=ROOM;if(!R||R.spec)return false;if(R.phase=="last")return R.speaker==ME.id;if(R.phase=="ended")return true;return R.me.alive&&["lobby","intro","day"].includes(R.phase)}
function voiceApply(){if(!ROOM)return;if(LS)LS.getAudioTracks().forEach(t=>t.enabled=MIC&&canTalk());
 document.querySelectorAll("audio.rtc").forEach(a=>{a.muted=ROOM.phase=="night"||!SPK;if(!a.muted&&a.paused&&a.srcObject)a.play().catch(()=>{})})}
async function getMic(manual){if(LS||LSBUSY)return;if(!manual&&LSFAIL)return;LSBUSY=true;
 try{if(!navigator.mediaDevices||!navigator.mediaDevices.getUserMedia)throw new Error("nomedia");
  LS=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true}});MIC=false;LSFAIL=0;
  LS.getAudioTracks().forEach(t=>{t.enabled=false;t.onended=()=>{if(LS){LS.getTracks().forEach(x=>x.stop());LS=null;MIC=false;LSFAIL=Date.now();render()}}});
  for(const id of Object.keys(PC)){const pc=PC[id].pc;LS.getTracks().forEach(t=>{try{pc.addTrack(t,LS)}catch(x){}})}
 }catch(x){LSFAIL=Date.now();toast("Mikrofon ruxsati kerak. Mikrofon tugmasini bosib qayta urinib ko'ring")}
 LSBUSY=false;render()}
async function micToggle(){if(!LS){await getMic(true);if(!LS)return;voiceSync()}MIC=!MIC;voiceApply();send({t:"mic",on:MIC});render()}
function wantIds(){const R=ROOM;if(!R||R.mode!="voice")return[];const player=!R.spec,sp=(R.specs||[]).slice(0,12).map(p=>p.id),pl=R.players.filter(p=>p.id!=ME.id&&!p.left).map(p=>p.id);return player?[...pl,...sp]:pl}
function limitBr(pc){try{pc.getSenders().forEach(s=>{if(!s.track)return;const p=s.getParameters();if(!p.encodings||!p.encodings.length)p.encodings=[{}];p.encodings[0].maxBitrate=32000;s.setParameters(p).catch(()=>{})})}catch(x){}}
function recover(id){const S=PC[id];if(!S||!ROOM||ROOM.mode!="voice")return;S.tries++;
 if(S.tries<=2&&S.pc.restartIce){try{S.pc.restartIce();return}catch(x){}}
 dropPc(id);send({t:"rtc",to:id,data:{reset:1}});if(wantIds().includes(id))mkpc(id)}
function mkpc(id){dropPc(id);const pc=new RTCPeerConnection(iceCfg());
 const S={pc,making:false,ignore:false,polite:ME.id>id,q:[],tries:0,t:null,born:Date.now()};PC[id]=S;
 if(LS)LS.getTracks().forEach(t=>pc.addTrack(t,LS));else if(ROOM&&!ROOM.spec)pc.addTransceiver("audio",{direction:"recvonly"});
 pc.onicecandidate=ev=>{if(ev.candidate)send({t:"rtc",to:id,data:{c:ev.candidate}})};
 pc.ontrack=ev=>{let a=$("au"+id);if(!a){a=document.createElement("audio");a.id="au"+id;a.className="rtc";a.autoplay=true;a.setAttribute("playsinline","");document.body.appendChild(a)}
  a.srcObject=ev.streams&&ev.streams[0]?ev.streams[0]:new MediaStream([ev.track]);voiceApply();if(!a.muted)a.play().catch(()=>{})};
 pc.onnegotiationneeded=async()=>{try{S.making=true;await pc.setLocalDescription(await pc.createOffer());send({t:"rtc",to:id,data:{d:pc.localDescription}})}catch(x){console.warn(x)}finally{S.making=false}};
 pc.oniceconnectionstatechange=()=>{const st=pc.iceConnectionState;clearTimeout(S.t);
  if(st=="connected"||st=="completed"){S.tries=0;limitBr(pc)}
  else if(st=="disconnected")S.t=setTimeout(()=>{if(PC[id]===S&&pc.iceConnectionState!="connected"&&pc.iceConnectionState!="completed")recover(id)},4000);
  else if(st=="failed")recover(id)};
 return S}
async function rtcIn(m){const id=m.from,d=m.data||{};if(!ROOM||ROOM.mode!="voice"||id==ME.id)return;
 if(d.reset){const S0=PC[id];if(S0&&Date.now()-S0.born<1500)return;dropPc(id);mkpc(id);return}
 let S=PC[id];if(!S)S=mkpc(id);const pc=S.pc;
 try{
  if(d.d){const off=d.d.type=="offer",coll=off&&(S.making||pc.signalingState!="stable");
   S.ignore=!S.polite&&coll;if(S.ignore)return;
   if(coll){try{await pc.setLocalDescription({type:"rollback"})}catch(x){}}
   await pc.setRemoteDescription(d.d);
   for(const c of S.q.splice(0)){try{await pc.addIceCandidate(c)}catch(x){}}
   if(off){await pc.setLocalDescription(await pc.createAnswer());send({t:"rtc",to:id,data:{d:pc.localDescription}})}}
  else if(d.c){if(S.ignore)return;if(!pc.remoteDescription){S.q.push(d.c);return}
   try{await pc.addIceCandidate(d.c)}catch(x){console.warn(x)}}
 }catch(x){console.warn(x)}}
async function voiceSync(){if(!ROOM||ROOM.mode!="voice"){if(LS||Object.keys(PC).length)stopVoice();return}
 const player=!ROOM.spec;
 if(!player&&LS){LS.getTracks().forEach(t=>t.stop());LS=null;MIC=false;closePCs()}
 if(player&&!LS)await getMic(false);
 if(!ROOM||ROOM.mode!="voice")return;
 const ids=wantIds();
 for(const id of ids)if(!PC[id])mkpc(id);
 for(const id of Object.keys(PC))if(!ids.includes(+id))dropPc(id);
 voiceApply()}
SPARAM=(tg&&tg.initDataUnsafe&&tg.initDataUnsafe.start_param)||q.get("sp")||"";
connect();
</script></body></html>"""

# ============================ BOT ============================
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML)); dp = Dispatcher()
PEND = {}
OW = F.from_user.id.in_(OWNERS)
def is_staff(uid): return uid in OWNERS or bool(one("SELECT 1 FROM admins WHERE uid=?", (uid,)))
STAFF = F.func(lambda ev: bool(ev.from_user) and is_staff(ev.from_user.id))
adm = Router(); adm.message.filter(STAFF); adm.callback_query.filter(STAFF)
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
async def _bk(m):
    if m.from_user.id not in OWNERS: return
    await send_backup("qo'lda")

@adm.message(F.document, F.caption == "restore")
async def _restore(m):
    if m.from_user.id not in OWNERS: return
    tmp = DATA / "restore.db"; await bot.download(m.document.file_id, destination=tmp)
    try:
        src = sqlite3.connect(tmp); src.backup(DB); src.close(); await m.answer("Baza tiklandi.")
    except Exception as ex: await m.answer(f"Xato: {html.escape(str(ex))}")

def webkb():
    rows = [[B(text="🎮 O'ynash", web_app=WebAppInfo(url=WEBAPP_URL))]]
    if S("channel_url"): rows.append([B(text="📢 Rasmiy kanal", url=S("channel_url"))])
    if S("chat_url"): rows.append([B(text="👥 Mafia Chat", url=S("chat_url"))])
    for b in allr("SELECT text,url FROM btns ORDER BY pos,id"):
        if ok_url(b["url"]): rows.append([B(text=b["text"], url=b["url"])])
    if one("SELECT 1 FROM frames WHERE on_sale=1") or one("SELECT 1 FROM user_frames LIMIT 1"):
        rows.append([B(text="🛍 Ramka do'koni", callback_data="shop:0"), B(text="🖼 Ramkalarim", callback_data="mine")])
    return KB(inline_keyboard=rows)
def plans_kb(): return KB(inline_keyboard=[[B(text=f"{m} oy - {S('star_' + m)} Stars", callback_data="buy:" + m)] for m in PLANS])

async def show_plans(m): await m.answer(f"{em('emo_crown')}<b>{S('brand')} Pro</b>\nObuna muddatini tanlang:", reply_markup=plans_kb())

@dp.message(CommandStart())
async def start(m):
    sql("INSERT OR IGNORE INTO users(id,name,created,free_voice) VALUES(?,?,?,?)",
        (m.from_user.id, m.from_user.full_name, int(time.time()), S("free_voice")))
    arg = (m.text.split(maxsplit=1) + [""])[1]
    sql("UPDATE users SET uname=? WHERE id=?", ((m.from_user.username or "").lower(), m.from_user.id))
    set_ref(m.from_user.id, arg)
    if arg == "pro": return await show_plans(m)
    if arg == "admin" and is_staff(m.from_user.id): return await m.answer("Owner paneli", reply_markup=home_kb(m.from_user.id))
    if arg.startswith("frame_") and arg[6:].isdigit(): return await frame_card(m.chat.id, m.from_user.id, int(arg[6:]))
    if arg == "shop": t_, kb_ = shop_kb(0); return await m.answer(t_, reply_markup=kb_)
    if arg.startswith("adm_"):
        r_ = one("SELECT v FROM settings WHERE k=?", ("inv_" + arg[4:],))
        if r_ and int(r_["v"]) > time.time():
            sql("DELETE FROM settings WHERE k=?", ("inv_" + arg[4:],)); sql("INSERT OR IGNORE INTO admins(uid,ts) VALUES(?,?)", (m.from_user.id, int(time.time())))
            await notify_owners(f"🛡 {uname_of(m.from_user.id)} (ID <code>{m.from_user.id}</code>) taklif havolasi orqali admin bo'ldi.")
            return await m.answer("🛡 Siz admin bo'ldingiz. /admin buyrug'ini yuboring.")
        return await m.answer("Havola eskirgan yoki ishlatilgan.")
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
    sp = m.successful_payment; kind, _, mo = sp.invoice_payload.partition(":")
    sql("INSERT INTO payments(uid,kind,ref,stars,ts) VALUES(?,?,?,?,?)", (m.from_user.id, kind, mo, sp.total_amount, int(time.time())))
    if kind == "frame":
        grant_frame(m.from_user.id, int(mo)); await m.answer(f"🖼 Ramka №{mo} sizniki! Profilingizga kiyildi.", reply_markup=webkb()); await refresh_me(m.from_user.id)
        return await notify_owners(f"💰 {uname_of(m.from_user.id)} №{mo} ramkani {sp.total_amount}⭐ ga sotib oldi.")
    await notify_owners(f"💰 {uname_of(m.from_user.id)} Pro ({mo} oy) sotib oldi: {sp.total_amount}⭐")
    u = one("SELECT pro_until FROM users WHERE id=?", (m.from_user.id,))
    new = max(int(time.time()), u["pro_until"]) + PLANS[mo] * 86400
    sql("UPDATE users SET pro_until=? WHERE id=?", (new, m.from_user.id))
    await m.answer("Pro faollashdi. Rahmat!", reply_markup=webkb())
    await send(m.from_user.id, {"t": "me", "me": me_info(m.from_user.id)})

# ---------- owner paneli ----------
def home_kb(uid=0):
    if uid in OWNERS:
        rows = [("📊 Dashboard", "dash"), ("👥 Foydalanuvchilar", "us"), ("🖼 Ramkalar", "fl:0"), ("🏅 Belgilar", "bl:0"), ("👑 Pro boshqaruvi", "pro"), ("🔘 /start tugmalari", "bt"),
                ("🛡 Adminlar", "adl"), ("📢 E'lon yuborish", "bc"), ("🎨 Kontent (rasm / emoji / ovoz)", "cts"), ("🏰 Klanlar", "cl"), ("🚪 Xonalar", "rm"), ("⚙️ Sozlamalar", "st")]
    else: rows = [("📊 Dashboard", "dash"), ("👥 Foydalanuvchilar", "us"), ("🚪 Xonalar", "rm")]
    return KB(inline_keyboard=[[B(text=t, callback_data=d)] for t, d in rows])

async def show(c, text, kb=None):
    try: await c.message.edit_text(text, reply_markup=kb)
    except Exception: await c.message.answer(text, reply_markup=kb)
    await c.answer()

@adm.message(Command("admin"))
async def admin(m): PEND.pop(m.from_user.id, None); await m.answer("Owner paneli" if m.from_user.id in OWNERS else "Admin paneli", reply_markup=home_kb(m.from_user.id))

@adm.callback_query(F.data == "home")
async def _home(c): PEND.pop(c.from_user.id, None); await show(c, "Owner paneli" if c.from_user.id in OWNERS else "Admin paneli", home_kb(c.from_user.id))

@adm.callback_query(F.data == "cts")
async def _cts(c):
    if c.from_user.id not in OWNERS: return await c.answer("Faqat owner uchun", show_alert=True)
    await show(c, "Qaysi turdagi kontent?", KB(inline_keyboard=[[B(text="Rasmlar", callback_data="ct:img:0")],
               [B(text="Emojilar (rasm / premium)", callback_data="ct:emoji:0")], [B(text="UI ikonalar (emoji o'rniga)", callback_data="ct:ui:0")], [B(text="Ovul belgilari (setka / alohida)", callback_data="ct:emb:0")],
               [B(text="Ovoz effektlari", callback_data="ct:snd:0")], BACK]))

@adm.callback_query(F.data.startswith("ct:"))
async def _ct(c):
    if c.from_user.id not in OWNERS: return await c.answer("Faqat owner uchun", show_alert=True)
    _, kind, pg = c.data.split(":"); pg = int(pg); per = 8
    keys = ([k for k in SLOTS if k.startswith("ui_")] if kind == "ui" else [k for k in SLOTS if k.startswith("emb_")] if kind == "emb"
            else [k for k, (n, kd) in SLOTS.items() if kd == kind and not k.startswith(("ui_", "emb_"))])
    have = {r["key"] for r in allr("SELECT key FROM assets")}
    rows = [[B(text=SLOTS[k][0] + (" - bor" if k in have else ""), callback_data="slot:" + k)] for k in keys[pg * per:(pg + 1) * per]]
    nav = []
    if pg > 0: nav.append(B(text="Oldingi", callback_data=f"ct:{kind}:{pg - 1}"))
    if (pg + 1) * per < len(keys): nav.append(B(text="Keyingi", callback_data=f"ct:{kind}:{pg + 1}"))
    if nav: rows.append(nav)
    if kind == "emb": rows.insert(0, [B(text="📥 Ketma-ket yuklash (alohida-alohida)", callback_data="embulk")])
    rows.append([B(text="Orqaga", callback_data="cts")]); await show(c, "Slotni tanlang:", KB(inline_keyboard=rows))

@adm.callback_query(F.data.startswith("slot:"))
async def _slot(c):
    if c.from_user.id not in OWNERS: return await c.answer("Faqat owner uchun", show_alert=True)
    k = c.data[5:]; PEND[c.from_user.id] = {"m": "asset", "k": k}
    hint = {"img": "Rasmni yuboring (sifat uchun fayl/hujjat sifatida yuborgan ma'qul).",
            "emoji": "Rasm, stiker yoki premium emoji yuboring.", "snd": "Audio yoki ovozli xabar yuboring (mp3/ogg)."}[SLOTS[k][1]]
    await c.message.answer(f"<b>{SLOTS[k][0]}</b>\n{hint}" + (emb_hint() if k == "emb_sheet" else ""), reply_markup=KB(inline_keyboard=[BACK])); await c.answer()

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
    sql("INSERT OR REPLACE INTO assets(key,path,ext,emoji_id,ts,dflt) VALUES(?,?,?,?,?,0)", (key, str(path), ext, eid, int(time.time())))
    await push_assets()
    return "Saqlandi."

@adm.callback_query(F.data == "st")
async def _st(c):
    if c.from_user.id not in OWNERS: return await c.answer("Faqat owner uchun", show_alert=True)
    rows = [[B(text=f"{k} = {S(k)}", callback_data="set:" + k)] for k in DEF]; rows.append(BACK)
    await show(c, "Sozlamalar (o'zgartirish uchun bosing):", KB(inline_keyboard=rows))

@adm.callback_query(F.data.startswith("set:"))
async def _set(c):
    if c.from_user.id not in OWNERS: return await c.answer("Faqat owner uchun", show_alert=True)
    k = c.data[4:]; PEND[c.from_user.id] = {"m": "set", "k": k}
    await c.message.answer(f"{k} uchun yangi qiymat yuboring (hozir: {S(k)})"); await c.answer()

def ucard(uid):
    u = one("SELECT * FROM users WHERE id=?", (uid,))
    if not u: return "Topilmadi", KB(inline_keyboard=[BACK])
    now = time.time(); pro = f"faol ({int((u['pro_until'] - now) // 86400) + 1} kun qoldi)" if u["pro_until"] > now else "yo'q"
    fr = owned_frames(uid); adm_ = uid in OWNERS or bool(one("SELECT 1 FROM admins WHERE uid=?", (uid,)))
    t = (f"👤 ID <code>{uid}</code> " + (f"@{u['uname']}" if u["uname"] else "") + f"\nIsm: {html.escape(u['name'] or '')}\nBall: {u['points']} | O'yin: {u['games']} | G'alaba: {u['wins']}\n"
         f"👑 Pro: {pro} | Klan ID: {u['clan_id']}\n🖼 Ramkalar: {', '.join('№' + str(x) for x in fr) or 'yoq'}" + (f" (kiyilgan №{u['frame']})" if u["frame"] else "")
         + f"\n🛡 Admin: {'ha' if adm_ else 'yoq'} | 🚫 Ban: {'ha' if u['banned'] else 'yoq'}")
    rows = [[B(text="Pro +7", callback_data=f"ua:pro7:{uid}"), B(text="+30", callback_data=f"ua:pro30:{uid}"), B(text="+90", callback_data=f"ua:pro90:{uid}"), B(text="+365", callback_data=f"ua:pro365:{uid}")],
            [B(text="📅 Boshqa kun", callback_data=f"ua:prod:{uid}"), B(text="❌ Pro olish", callback_data=f"ua:pro0:{uid}")],
            [B(text="🖼 Ramka berish", callback_data=f"ua:fgl:{uid}"), B(text="🗑 Ramka olish", callback_data=f"ua:frl:{uid}")],
            [B(text="🚫 Ban / Unban", callback_data=f"ua:ban:{uid}"), B(text="✉️ Xabar yuborish", callback_data=f"ua:msg:{uid}")],
            [B(text="Ball qo'shish", callback_data=f"ua:pts:{uid}"), B(text="Klanga qo'shish", callback_data=f"ua:clan:{uid}")],
            [B(text="🛡 Adminlikni " + ("olib tashlash" if one("SELECT 1 FROM admins WHERE uid=?", (uid,)) else "berish"), callback_data=f"ua:adm:{uid}")], BACK]
    return t, KB(inline_keyboard=rows)

@adm.callback_query(F.data == "us")
async def _us(c):
    PEND.pop(c.from_user.id, None)
    await show(c, "👥 Foydalanuvchilar", KB(inline_keyboard=[[B(text="🔎 Qidirish (ID / ism / forward)", callback_data="usrch")], [B(text="🏆 Top 10", callback_data="top"), B(text="🆕 Yangilar", callback_data="ul:new:0")],
               [B(text="👑 Pro ro'yxati", callback_data="ul:pro:0"), B(text="🚫 Bloklanganlar", callback_data="ul:ban:0")], BACK]))

@adm.callback_query(F.data.startswith("ua:"))
async def _ua(c):
    _, a, uid = c.data.split(":"); uid = int(uid); own = c.from_user.id in OWNERS
    if a in ("pts", "clan", "adm") and not own: return await c.answer("Faqat owner uchun", show_alert=True)
    if a.startswith("pro") and a[3:].isdigit():
        d = int(a[3:]); u = one("SELECT pro_until FROM users WHERE id=?", (uid,))
        sql("UPDATE users SET pro_until=? WHERE id=?", (0 if d == 0 else max(int(time.time()), u["pro_until"]) + d * 86400, uid)); await refresh_me(uid)
    elif a == "ban":
        sql("UPDATE users SET banned=1-banned WHERE id=?", (uid,))
        if one("SELECT banned FROM users WHERE id=?", (uid,))["banned"] and uid in HUB: await send(uid, {"t": "banned"})
    elif a == "adm":
        if uid in OWNERS: return await c.answer("Bu owner", show_alert=True)
        if one("SELECT 1 FROM admins WHERE uid=?", (uid,)): sql("DELETE FROM admins WHERE uid=?", (uid,))
        else:
            sql("INSERT OR IGNORE INTO admins(uid,ts) VALUES(?,?)", (uid, int(time.time())))
            try: await bot.send_message(uid, "🛡 Siz admin qilindingiz. /admin buyrug'ini yuboring.")
            except Exception: pass
    elif a == "fgl":
        al = allr("SELECT num,price FROM frames ORDER BY num")
        return await show(c, "Qaysi ramka berilsin?" if al else "Ramkalar yo'q.", KB(inline_keyboard=[[B(text=f"🖼 №{r['num']} · {fr_price(r['price'])}", callback_data=f"fg:{uid}:{r['num']}")] for r in al] + [[B(text="◀️ Orqaga", callback_data=f"uc:{uid}")]]))
    elif a == "frl":
        fr = owned_frames(uid)
        return await show(c, "Qaysi ramka olib tashlansin?" if fr else "Ramkasi yo'q.", KB(inline_keyboard=[[B(text=f"🗑 №{n}", callback_data=f"fx:{uid}:{n}")] for n in fr] + [[B(text="◀️ Orqaga", callback_data=f"uc:{uid}")]]))
    else:
        PEND[c.from_user.id] = {"m": {"prod": "prod", "msg": "umsg"}.get(a, a), "uid": uid}
        await c.message.answer({"pts": "Qiymat yuboring (butun son, minus ham mumkin)", "clan": "Klan ID yuboring", "prod": "Necha kun Pro berilsin? (butun son)", "msg": "Yuboriladigan xabarni yozing (rasm/media ham mumkin):"}[a]); return await c.answer()
    t, kb = ucard(uid); await show(c, t, kb)

@adm.callback_query(F.data == "cl")
async def _cl(c):
    if c.from_user.id not in OWNERS: return await c.answer("Faqat owner uchun", show_alert=True)
    rows = [f"{r['id']}. {'⭐ ' if r['special'] else ''}{ESC(r['name'])} [{ESC(r['tag'])}] - {r['points']} ball, a'zo: {clan_count(r['id'])}, belgi №{r['emblem']}"
            for r in allr("SELECT * FROM clans ORDER BY points DESC LIMIT 40")]
    await show(c, "Ovullar (eng yaxshi 40 ta):\n" + ("\n".join(rows) or "yo'q"), KB(inline_keyboard=[
        [B(text="Yangi ovul", callback_data="cln"), B(text="Ovulni o'chirish", callback_data="cld")],
        [B(text="⭐ Rasmiy (maxsus teg)", callback_data="clsp"), B(text="➕ Ball qo'shish", callback_data="clpt")],
        [B(text="🖼 Ovul belgilari", callback_data="ct:emb:0")], BACK]))

@adm.callback_query(F.data.in_({"cln", "cld", "clsp", "clpt"}))
async def _cln(c):
    if c.from_user.id not in OWNERS: return await c.answer("Faqat owner uchun", show_alert=True)
    PEND[c.from_user.id] = {"m": {"cln": "newclan", "cld": "delclan", "clsp": "clsp", "clpt": "clpt"}[c.data]}
    await c.message.answer({"cln": "Format: Nom | TEG | ovul egasi ID", "cld": "O'chiriladigan ovul ID:", "clsp": "Rasmiy (maxsus teg) holatini almashtirish uchun ovul ID:",
                            "clpt": "Format: ovul ID | ball (minus ham mumkin)"}[c.data]); await c.answer()

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
    if c.from_user.id not in OWNERS: return await c.answer("Faqat owner uchun", show_alert=True)
    PEND[c.from_user.id] = {"m": "bc"}; await show(c, "E'lon xabarini yuboring (premium emoji va media saqlanadi):", KB(inline_keyboard=[BACK]))

@adm.callback_query(F.data == "bcgo")
async def _bcgo(c):
    if c.from_user.id not in OWNERS: return await c.answer("Faqat owner uchun", show_alert=True)
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
        if mo in EXTRA_MODES: return await pend_extra(m, p, mo, txt)
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
            sql("UPDATE users SET points=points+? WHERE id=?" if mo == "pts" else "UPDATE users SET clan_id=?,clan_role='' WHERE id=?", (v, p["uid"])); cb_flush(); PEND.pop(uid)
            t, kb = ucard(p["uid"]); return await m.answer(t, reply_markup=kb)
        if mo == "newclan":
            n, tag, ow = [x.strip() for x in txt.split("|")]; sql("INSERT INTO clans(name,tag,owner_id,created) VALUES(?,?,?,?)", (n, tag.upper(), int(ow), int(time.time())))
            sql("UPDATE users SET clan_id=(SELECT MAX(id) FROM clans),clan_role='',clan_ts=? WHERE id=?", (int(time.time()), int(ow))); cb_flush(); PEND.pop(uid)
            return await m.answer("Klan yaratildi.", reply_markup=KB(inline_keyboard=[[B(text="Klanlar", callback_data="cl")]]))
        if mo == "clsp":
            cid = int(txt); r_ = one("SELECT special FROM clans WHERE id=?", (cid,)); PEND.pop(uid)
            if not r_: return await m.answer("Ovul topilmadi.")
            sql("UPDATE clans SET special=? WHERE id=?", (0 if r_["special"] else 1, cid)); cb_flush()
            return await m.answer("Rasmiy: " + ("o'chirildi" if r_["special"] else "yoqildi ⭐"), reply_markup=KB(inline_keyboard=[[B(text="Ovullar", callback_data="cl")]]))
        if mo == "clpt":
            a, b = [x.strip() for x in txt.split("|")]; sql("UPDATE clans SET points=MAX(0,points+?) WHERE id=?", (int(b), int(a))); cb_flush(); PEND.pop(uid)
            return await m.answer("Saqlandi.", reply_markup=KB(inline_keyboard=[[B(text="Ovullar", callback_data="cl")]]))
        if mo == "delclan":
            clan_delete(int(txt)); PEND.pop(uid)
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
    if m.from_user.id not in OWNERS: return
    PEND[m.from_user.id] = {"m": "photo", "msg": m}
    await m.answer("Bu rasm qayerga qo'yilsin?", reply_markup=groups_kb())

@adm.callback_query(F.data == "pgback")
async def _pgback(c): await show(c, "Bu rasm qayerga qo'yilsin?", groups_kb())

@adm.callback_query(F.data.startswith("pg:"))
async def _pg(c):
    if c.from_user.id not in OWNERS: return await c.answer("Faqat owner uchun", show_alert=True)
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
    if c.from_user.id not in OWNERS: return await c.answer("Faqat owner uchun", show_alert=True)
    p = PEND.get(c.from_user.id); key = c.data[4:]
    if not p or p.get("m") != "photo" or key not in SLOTS: return await c.answer("Avval rasm yuboring", show_alert=True)
    try: res = await save_asset(key, p["msg"])
    except Exception as ex: res = "Xato: " + html.escape(str(ex))
    PEND.pop(c.from_user.id, None)
    await c.message.answer(f"<b>{SLOTS[key][0]}</b>: {res}", reply_markup=KB(inline_keyboard=[[B(text="Kontent", callback_data="cts")]])); await c.answer()

# ============================ RAMKALAR / PRO / ADMIN / TUGMALAR ============================
OWNF = F.func(lambda ev: bool(ev.from_user) and ev.from_user.id in OWNERS)
ESC = html.escape
def ok_url(u): return bool(re.match(r"^(https?://|tg://)\S+$", u or ""))
def fr_price(p): return "Bepul 🎁" if not p else f"{p} ⭐"
def fr_path(n):
    r = one("SELECT path FROM assets WHERE key=?", (f"frame_{n}",))
    return r["path"] if r and Path(r["path"]).exists() else None
def ensure_user(u):
    sql("INSERT OR IGNORE INTO users(id,name,created,free_voice) VALUES(?,?,?,?)", (u.id, u.full_name or "Oyinchi", int(time.time()), S("free_voice")))
    if u.username: sql("UPDATE users SET uname=? WHERE id=?", (u.username.lower(), u.id))
def uname_of(uid):
    u = one("SELECT name,uname FROM users WHERE id=?", (uid,))
    return (ESC(u["name"] or "?") + (f" @{u['uname']}" if u["uname"] else "")) if u else str(uid)
async def refresh_me(uid): await send(uid, {"t": "me", "me": me_info(uid)})
async def notify_owners(text, kb=None):
    for o in OWNERS:
        try: await bot.send_message(o, text, reply_markup=kb)
        except Exception: pass

_DM = {}
async def dm_name():
    v = S("dm_user").lstrip("@").strip()
    if v: return v
    if not _DM.get("u"):
        for o in OWNERS:
            try: _DM["u"] = (await bot.get_chat(o)).username or ""
            except Exception: pass
            if _DM.get("u"): break
    return _DM.get("u", "")

async def fr_buy_url(n):
    """Kanal tugmasi: 'lichka' rejimida egasining lichkasi (tayyor matn bilan), 'bot' rejimida bot ichidagi do'kon."""
    if S("sale_mode") == "dm":
        d = await dm_name()
        if d: return f"https://t.me/{d}?text=" + quote(f"Men №{n} ramkani olmoqchiman")
    return f"https://t.me/{BOT_USERNAME}?start=frame_{n}"

# ---------- foydalanuvchi tomoni: do'kon, ramka kartasi, ramkalarim ----------
async def frame_card(chat_id, uid, n):
    f = one("SELECT * FROM frames WHERE num=?", (n,)); p = fr_path(n)
    if not f or not p: return await bot.send_message(chat_id, "Bu ramka topilmadi.")
    own = one("SELECT 1 FROM user_frames WHERE uid=? AND num=?", (uid, n)); rows = []
    if own: rows.append([B(text="✅ Kiyish", callback_data=f"fe:{n}")])
    elif not f["price"]: rows.append([B(text="🎁 Bepul olish", callback_data=f"fget:{n}")])
    elif S("sale_mode") == "bot" or not await dm_name(): rows.append([B(text=f"⭐ {f['price']} Stars bilan olish", callback_data=f"fbuy:{n}")])
    else: rows.append([B(text="💬 Olish (egasiga yozish)", url=await fr_buy_url(n))])
    rows.append([B(text="🛍 Do'kon", callback_data="shop:0"), B(text="🖼 Ramkalarim", callback_data="mine")])
    await bot.send_photo(chat_id, FSInputFile(p), caption=f"🖼 <b>Ramka №{n}</b>\n💰 Narxi: {fr_price(f['price'])}" + ("\n✅ Sizda bor" if own else ""),
                         reply_markup=KB(inline_keyboard=rows))

def shop_kb(pg):
    al = allr("SELECT num,price FROM frames WHERE on_sale=1 ORDER BY num"); per = 8
    if not al: return "🛍 Hozircha sotuvda ramka yo'q.", KB(inline_keyboard=[[B(text="🖼 Ramkalarim", callback_data="mine")]])
    pg = max(0, min(pg, (len(al) - 1) // per))
    rows = [[B(text=f"🖼 №{r['num']} · {fr_price(r['price'])}", callback_data=f"fc:{r['num']}")] for r in al[pg * per:(pg + 1) * per]]
    nav = []
    if pg > 0: nav.append(B(text="◀️", callback_data=f"shop:{pg - 1}"))
    if (pg + 1) * per < len(al): nav.append(B(text="▶️", callback_data=f"shop:{pg + 1}"))
    if nav: rows.append(nav)
    rows.append([B(text="🖼 Ramkalarim", callback_data="mine")])
    return "🛍 <b>Ramka do'koni</b>\nProfil avatari uchun ramkani tanlang:", KB(inline_keyboard=rows)

def mine_kb(uid):
    own = owned_frames(uid); cur = (one("SELECT frame FROM users WHERE id=?", (uid,)) or {"frame": 0})["frame"]
    btns = [B(text=("✅ " if n == cur else "") + f"№{n}", callback_data=f"mfe:{n}") for n in own]
    rows = [btns[i:i + 3] for i in range(0, len(btns), 3)]
    rows.append([B(text="🚫 Yechish", callback_data="mfe:0"), B(text="🛍 Do'kon", callback_data="shop:0")])
    return ("🖼 <b>Ramkalarim</b>\nKiymoqchi bo'lganingizni tanlang:" if own else "🖼 Sizda hali ramka yo'q. Do'kondan oling!"), KB(inline_keyboard=rows)

@dp.callback_query(F.data.startswith("shop:"))
async def _shop(c):
    t, kb = shop_kb(int(c.data[5:]))
    try: await c.message.edit_text(t, reply_markup=kb)
    except Exception: await c.message.answer(t, reply_markup=kb)
    await c.answer()

@dp.callback_query(F.data.startswith("fc:"))
async def _fc(c): ensure_user(c.from_user); await frame_card(c.message.chat.id, c.from_user.id, int(c.data[3:])); await c.answer()

@dp.callback_query(F.data.startswith("fget:"))
async def _fget(c):
    ensure_user(c.from_user); n = int(c.data[5:]); f = one("SELECT * FROM frames WHERE num=?", (n,))
    if not f or f["price"]: return await c.answer("Bu ramka bepul emas", show_alert=True)
    grant_frame(c.from_user.id, n); await refresh_me(c.from_user.id)
    await c.message.answer(f"🎁 Ramka №{n} sizniki! Profilingizga kiyildi.", reply_markup=webkb()); await c.answer()

@dp.callback_query(F.data.startswith("fbuy:"))
async def _fbuy(c):
    ensure_user(c.from_user); n = int(c.data[5:]); f = one("SELECT * FROM frames WHERE num=?", (n,))
    if not f or not f["price"]: return await c.answer("Ramka topilmadi", show_alert=True)
    if one("SELECT 1 FROM user_frames WHERE uid=? AND num=?", (c.from_user.id, n)): return await c.answer("Sizda bu ramka bor", show_alert=True)
    await bot.send_invoice(c.from_user.id, title=f"Ramka №{n}", description="Profil avatari uchun ramka", payload=f"frame:{n}", currency="XTR",
                           prices=[LabeledPrice(label=f"Ramka №{n}", amount=f["price"])], provider_token="")
    await c.answer()

@dp.callback_query(F.data.startswith("fe:"))
async def _fe(c):
    n = int(c.data[3:])
    if n and not one("SELECT 1 FROM user_frames WHERE uid=? AND num=?", (c.from_user.id, n)): return await c.answer("Sizda bu ramka yo'q", show_alert=True)
    set_frame(c.from_user.id, n); await refresh_me(c.from_user.id); await c.answer("Kiyildi ✅" if n else "Yechildi")

@dp.callback_query(F.data == "mine")
async def _mine(c):
    ensure_user(c.from_user); t, kb = mine_kb(c.from_user.id); await c.message.answer(t, reply_markup=kb); await c.answer()

@dp.callback_query(F.data.startswith("mfe:"))
async def _mfe(c):
    n = int(c.data[4:])
    if n and not one("SELECT 1 FROM user_frames WHERE uid=? AND num=?", (c.from_user.id, n)): return await c.answer("Sizda bu ramka yo'q", show_alert=True)
    set_frame(c.from_user.id, n); await refresh_me(c.from_user.id); t, kb = mine_kb(c.from_user.id)
    try: await c.message.edit_text(t, reply_markup=kb)
    except Exception: pass
    await c.answer("Kiyildi ✅" if n else "Yechildi")

@dp.message(F.text.regexp(r"^Men №\s*(\d+)"))
async def want_frame(m):
    """Kanal tugmasi orqali kelgan: 'Men №N ramkani olmoqchiman' -> karta + egalarga tezkor 'Berish' tugmasi."""
    ensure_user(m.from_user); n = int(re.match(r"^Men №\s*(\d+)", m.text).group(1))
    await frame_card(m.chat.id, m.from_user.id, n)
    if one("SELECT 1 FROM frames WHERE num=?", (n,)):
        await notify_owners(f"🛒 {uname_of(m.from_user.id)} (ID <code>{m.from_user.id}</code>) №{n} ramkani olmoqchi.",
                            KB(inline_keyboard=[[B(text=f"🎁 №{n} ni berish", callback_data=f"ogu:{m.from_user.id}:{n}")],
                                                [B(text="👤 Profil", callback_data=f"uc:{m.from_user.id}")]]))

# ---------- owner: ramkalar ----------
def ofc_text(n):
    f = one("SELECT * FROM frames WHERE num=?", (n,)); k = one("SELECT COUNT(*) n FROM user_frames WHERE num=?", (n,))["n"]
    return f"🖼 <b>Ramka №{n}</b>\n💰 Narx: {fr_price(f['price'])}\n📦 Holat: {'sotuvda 🟢' if f['on_sale'] else 'sotuvda emas ⚪'}\n👑 Pro a'zolarga bepul: {'ha ✅' if f['pro'] else 'yoq'}\n🎯 Markaz: {str(f['hr']) + '%' if f['hr'] else 'aniqlanmagan'}\n👥 Egalari: {k}"

def ofc_kb(n):
    f = one("SELECT * FROM frames WHERE num=?", (n,))
    pr = lambda v: B(text=("✅ " if f["price"] == v else "") + ("Bepul" if v == 0 else f"{v}⭐"), callback_data=f"ops:{n}:{v}")
    return KB(inline_keyboard=[[B(text="📢 Kanalga sotuvga qo'yish", callback_data=f"ofp:{n}")],
        [pr(0), pr(10), pr(25), pr(50)], [pr(100), pr(250), pr(500), B(text="✏️ Boshqa", callback_data=f"ops:{n}:x")],
        [B(text="🟢 Sotuvda" if f["on_sale"] else "⚪ Sotuvda emas (bosing)", callback_data=f"osl:{n}")],
        [B(text="👑 Pro a'zolarga bepul: " + ("✅ ha" if f["pro"] else "yo'q"), callback_data=f"opr:{n}")],
        [B(text="🎁 Berish", callback_data=f"ogv:{n}"), B(text="🖼 Rasmni almashtirish", callback_data=f"oim:{n}")],
        [B(text="🗑 O'chirish", callback_data=f"odl:{n}")], [B(text="◀️ Ramkalar", callback_data="fl:0")]])

async def ofc_send(chat_id, n):
    p = fr_path(n)
    if p: await bot.send_photo(chat_id, FSInputFile(p), caption=ofc_text(n), reply_markup=ofc_kb(n))
    else: await bot.send_message(chat_id, ofc_text(n) + "\n(rasm topilmadi)", reply_markup=ofc_kb(n))

async def ofc_refresh(c, n):
    try: await c.message.edit_caption(caption=ofc_text(n), reply_markup=ofc_kb(n))
    except Exception: await ofc_send(c.message.chat.id, n)

# ============================ RASM QAYTA ISHLASH (ramka / belgi / emblema) ============================
# Owner rasmni oq fonda yoki shaffof PNG qilib yuborishi mumkin. Bot o'zi: fonni shaffof qiladi, ramka markazini
# (avatar o'rni) topadi va barcha ramkalarni bir xil kvadrat kanvasga keltiradi. Pillow yo'q bo'lsa rasm o'zgarishsiz saqlanadi.
FR_R = 0.58      # ramka markazi (teshik) kanvasning shu ulushini egallaydi (zarur bo'lsa kamayadi)
FR_MIN = 0.46    # undan kichik bo'lmaydi (juda katta bezaklar kesiladi)
FR_OUT = 512     # ramka PNG o'lchami
IC_OUT = 256     # belgi / emblema PNG o'lchami

def _white_thr(im):
    """Fon oqligi chegarasi: burchaklardagi eng och rangdan kelib chiqadi (JPEG shovqini uchun)."""
    w, h = im.size; v = []
    for x, y in ((1, 1), (w - 2, 1), (1, h - 2), (w - 2, h - 2)):
        px = im.getpixel((max(0, x), max(0, y)))
        v.append(min(px[:3]))
    return max(200, min(244, min(v) - 8))

def _empty_mask(rgba, thr):
    """L maska: 255 = 'bo'sh' piksel (shaffof yoki deyarli oq)."""
    r, g, b, a = rgba.split()
    mn = ImageChops.darker(ImageChops.darker(r, g), b)
    white = mn.point(lambda v: 255 if v >= thr else 0)
    opaque = a.point(lambda v: 255 if v >= 24 else 0)
    transp = ImageChops.invert(opaque)
    return ImageChops.lighter(transp, ImageChops.multiply(white, opaque))

def _label_regions(mask, step):
    """Bo'sh hududlarni (oq/shaffof) to'ldirib raqamlaydi. Qaytaradi: {label: (bbox, area)}. Markazga yaqin urug'lar birinchi."""
    w, h = mask.size; cx, cy = w / 2, h / 2
    seeds = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]
    grid = [(x, y) for y in range(step // 2, h, step) for x in range(step // 2, w, step)]
    grid.sort(key=lambda p: (p[0] - cx) ** 2 + (p[1] - cy) ** 2)
    out = {}; lab = 1
    for p in seeds + grid:
        if lab > 250: break
        if mask.getpixel(p) == 255:
            ImageDraw.floodfill(mask, p, lab); out[lab] = None; lab += 1
    hist = mask.histogram()
    for l in list(out):
        bb = mask.point(lambda v, l=l: 255 if v == l else 0).getbbox()
        out[l] = (bb, hist[l])
    return out

def _soft_alpha(a, erase):
    """erase (L, 255 = o'chirish) -> yumshoq chet bilan alpha."""
    e = erase.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.GaussianBlur(0.7))
    return ImageChops.multiply(a, ImageChops.invert(e))

def _load(src, maxside=900):
    im = Image.open(src); im = ImageOps.exif_transpose(im) if hasattr(ImageOps, "exif_transpose") else im
    im = im.convert("RGBA")
    if max(im.size) > maxside:
        k = maxside / max(im.size); im = im.resize((max(1, round(im.size[0] * k)), max(1, round(im.size[1] * k))), Image.LANCZOS)
    return im

def _bg_removable(im):
    """Burchaklar oq yoki shaffof bo'lsa fonni olib tashlash mumkin."""
    w, h = im.size
    for x, y in ((1, 1), (w - 2, 1), (1, h - 2), (w - 2, h - 2)):
        px = im.getpixel((x, y))
        if px[3] < 24: continue
        if min(px[:3]) < 200: return False
    return True

def proc_frame(src, dst):
    """Ramka: fon -> shaffof, markaz topiladi, kvadrat kanvas. Qaytaradi {'hr': markaz foizi (0 = aniqlanmadi), 'note': str}."""
    im = _load(src); w, h = im.size
    if not _bg_removable(im):
        sq = ImageOps.pad(im, (FR_OUT, FR_OUT), Image.LANCZOS, color=(0, 0, 0, 0)); sq.save(dst, "PNG")
        return {"hr": 0, "note": "fon oq/shaffof emas — o'zgarishsiz saqlandi (markaz aniqlanmadi)"}
    thr = _white_thr(im); mask = _empty_mask(im, thr)
    step = max(6, min(w, h) // 45); regs = _label_regions(mask, step)
    outside, hole, best = [], None, 0
    for l, (bb, area) in regs.items():
        if not bb: continue
        touch = bb[0] <= 1 or bb[1] <= 1 or bb[2] >= w - 1 or bb[3] >= h - 1
        if touch: outside.append(l)
        elif area > best: hole, best = l, area
    a = im.split()[3]
    ids = set(outside) | ({hole} if hole and best >= 0.015 * w * h else set())
    erase = mask.point(lambda v: 255 if v in ids else 0)
    a2 = _soft_alpha(a, erase); rgba = im.copy(); rgba.putalpha(a2)
    cb = a2.point(lambda v: 255 if v > 12 else 0).getbbox()
    if not cb: raise ValueError("rasm bo'sh")
    if not hole or best < 0.015 * w * h:
        sq = ImageOps.pad(rgba.crop(cb), (FR_OUT, FR_OUT), Image.LANCZOS, color=(0, 0, 0, 0)); sq.save(dst, "PNG")
        return {"hr": 0, "note": "ramka markazi (bo'sh doira) topilmadi — markaz oq/shaffof bo'lishi kerak"}
    hb = regs[hole][0]; hx, hy = (hb[0] + hb[2]) / 2, (hb[1] + hb[3]) / 2; d = ((hb[2] - hb[0]) + (hb[3] - hb[1])) / 2
    ext = max(hx - cb[0], cb[2] - hx, hy - cb[1], cb[3] - hy)
    C = max(d / FR_R, 2 * ext); C = min(C, d / FR_MIN)
    canvas = rgba.crop((round(hx - C / 2), round(hy - C / 2), round(hx + C / 2), round(hy + C / 2)))
    canvas = canvas.resize((FR_OUT, FR_OUT), Image.LANCZOS); canvas.save(dst, "PNG", optimize=True)
    return {"hr": int(round(100 * d / C)), "note": "shaffof fon, markaz aniqlandi"}

def proc_icon(src, dst, size=IC_OUT):
    """Belgi / emblema: tashqi oq fon shaffof, kontentga kesiladi, kvadratga keltiriladi."""
    im = _load(src, 1200)
    if _bg_removable(im):
        w, h = im.size; thr = _white_thr(im); mask = _empty_mask(im, thr)
        regs = _label_regions(mask, max(8, min(w, h) // 30)); ids = set()
        for l, (bb, area) in regs.items():
            if bb and (bb[0] <= 1 or bb[1] <= 1 or bb[2] >= w - 1 or bb[3] >= h - 1): ids.add(l)
        erase = mask.point(lambda v: 255 if v in ids else 0)
        im.putalpha(_soft_alpha(im.split()[3], erase))
    bb = im.split()[3].point(lambda v: 255 if v > 12 else 0).getbbox()
    if not bb: raise ValueError("rasm bo'sh")
    im = im.crop(bb)
    sq = ImageOps.pad(im, (size - 2 * 4, size - 2 * 4), Image.LANCZOS, color=(0, 0, 0, 0))
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0)); out.paste(sq, (4, 4), sq); out.save(dst, "PNG", optimize=True)
    return {"note": "shaffof fon, kvadratga keltirildi"}

def save_avatar_jpg(path, raw):
    """Foydalanuvchi avatari: 256x256 JPEG (markazdan kesiladi)."""
    if HAS_PIL:
        im = Image.open(io.BytesIO(raw)); im = ImageOps.exif_transpose(im).convert("RGB")
        ImageOps.fit(im, (256, 256), Image.LANCZOS).save(path, "JPEG", quality=88, optimize=True); return True
    if raw[:3] != b"\xff\xd8\xff": return False
    Path(path).write_bytes(raw); return True

def _img_fid(m):
    if m.photo: return m.photo[-1].file_id
    if m.document and (m.document.mime_type or "").startswith("image"): return m.document.file_id
    if m.sticker and not m.sticker.is_animated and not m.sticker.is_video: return m.sticker.file_id
    return None

async def put_img(key, m, mode):
    """Rasmni yuklab oladi, qayta ishlaydi (mode: 'frame' | 'icon') va assets ga yozadi. Qaytaradi info yoki None (rasm emas)."""
    fid = _img_fid(m)
    if not fid: return None
    adir = DATA / "assets"; adir.mkdir(parents=True, exist_ok=True); st = str(int(time.time() * 1000))
    tmp = adir / f"_in_{st}"; out = adir / f"_out_{st}.png"
    await bot.download(fid, destination=tmp)
    try:
        if HAS_PIL:
            info = await asyncio.to_thread(proc_frame if mode == "frame" else proc_icon, str(tmp), str(out)); ext = "png"
            final = adir / f"{key}.png"; os.replace(out, final)
        else:
            head = tmp.read_bytes()[:12]
            ext = "png" if head[:4] == b"\x89PNG" else "jpg" if head[:2] == b"\xff\xd8" else "webp" if head[8:12] == b"WEBP" else None
            if not ext: raise ValueError("PNG / JPG / WEBP rasm yuboring")
            final = adir / f"{key}.{ext}"; os.replace(tmp, final)
            info = {"hr": 0, "note": "Pillow o'rnatilmagan: rasm o'zgarishsiz saqlandi (requirements.txt ga Pillow qo'shing)"}
    finally:
        for p_ in (tmp, out):
            try: p_.unlink()
            except Exception: pass
    for old in adir.glob(key + ".*"):
        if old != final: old.unlink()
    sql("INSERT OR REPLACE INTO assets(key,path,ext,emoji_id,ts,dflt) VALUES(?,?,?,?,?,0)", (key, str(final), ext, None, int(time.time())))
    return info

async def save_frame(n, m):
    info = await put_img(f"frame_{n}", m, "frame")
    if info is None: return False
    sql("INSERT OR IGNORE INTO frames(num,price,on_sale,created) VALUES(?,?,?,?)", (n, 0, 0, int(time.time())))
    sql("UPDATE frames SET hr=? WHERE num=?", (info.get("hr", 0), n))
    await push_assets(); return True

def img_err(ex):
    t = str(ex)
    return "rasm o'qilmadi (PNG / JPG / WEBP yuboring)" if "cannot identify" in t or "image file" in t else html.escape(t)[:160]
_BL = []
def bulk_lock():
    if not _BL: _BL.append(asyncio.Lock())
    return _BL[0]
def done_kb(cb): return KB(inline_keyboard=[[B(text="✅ Tayyor", callback_data=cb)]])
async def bulk_item(m, kind):
    """Ketma-ket yuklash: har bir rasm keyingi bo'sh raqamga saqlanadi (albom yuborsa ham tartib buzilmaydi)."""
    cb = {"frame": "fdone", "badge": "bddone", "emb": "emdone"}[kind]
    if not _img_fid(m):
        return await m.answer("Rasm yuboring (PNG ni fayl/hujjat sifatida yuborgan ma'qul). Tugatgach «✅ Tayyor» ni bosing.", reply_markup=done_kb(cb))
    async with bulk_lock():
        try:
            if kind == "frame":
                n = (one("SELECT MAX(num) m FROM frames")["m"] or 0) + 1
                info = await put_img(f"frame_{n}", m, "frame")
                sql("INSERT INTO frames(num,price,on_sale,created,hr) VALUES(?,?,?,?,?)", (n, 0, 0, int(time.time()), info.get("hr", 0))); label = f"🖼 Ramka №{n}"
            elif kind == "badge":
                n = (one("SELECT MAX(num) m FROM badges")["m"] or 0) + 1
                info = await put_img(f"badge_{n}", m, "icon")
                sql("INSERT INTO badges(num,created) VALUES(?,?)", (n, int(time.time()))); label = f"🏅 Belgi №{n}"
            else:
                have = {r["key"] for r in allr("SELECT key FROM assets WHERE substr(key,1,4)='emb_'")}
                n = next((i for i in range(1, 41) if f"emb_{i}" not in have), 0)
                if not n: return await m.answer("Ovul belgilari to'ldi (40 ta). Almashtirish: Kontent → Ovul belgilari → kerakli raqam.", reply_markup=done_kb(cb))
                info = await put_img(f"emb_{n}", m, "icon")
                sql("INSERT OR REPLACE INTO settings(k,v) VALUES('emb_n',?)", (str(max(S("emb_n"), n)),)); label = f"⛺ Ovul belgisi №{n}"
        except Exception as ex:
            return await m.answer("❌ Saqlanmadi: " + img_err(ex), reply_markup=done_kb(cb))
    await push_assets()
    warn = "\n⚠️ Markaz topilmadi: ramka markazi oq/shaffof doira bo'lishi kerak. Eski usulda ko'rsatiladi." if kind == "frame" and not info.get("hr") else ""
    await m.answer(f"✅ {label} saqlandi ({info.get('note', '')}).{warn}\nYana rasm yuboring yoki «✅ Tayyor» ni bosing.", reply_markup=done_kb(cb))

@adm.callback_query(OWNF, F.data.startswith("fl:"))
async def _fl(c): await fl_show(c, int(c.data[3:]))

async def fl_show(c, pg):
    al = allr("SELECT * FROM frames ORDER BY num"); per = 8; pg = max(0, min(pg, max(0, (len(al) - 1) // per)))
    rows = [[B(text=f"{'🟢' if r['on_sale'] else '⚪'} №{r['num']} · {fr_price(r['price'])}", callback_data=f"ofc:{r['num']}")] for r in al[pg * per:(pg + 1) * per]]
    nav = []
    if pg > 0: nav.append(B(text="◀️", callback_data=f"fl:{pg - 1}"))
    if (pg + 1) * per < len(al): nav.append(B(text="▶️", callback_data=f"fl:{pg + 1}"))
    if nav: rows.append(nav)
    ch, dmu = S("sale_channel") or "yo'q", S("dm_user") or "avto"
    rows += [[B(text="➕ Yangi ramka", callback_data="fnew"), B(text="📥 Ketma-ket yuklash", callback_data="fbulk")],
             [B(text=f"📢 Sotuv kanali: {ch[:28]}", callback_data="set:sale_channel")],
             [B(text=f"💬 Lichka: {dmu}", callback_data="set:dm_user")],
             [B(text="🔁 Sotuv rejimi: " + ("Lichka (qo'lda)" if S("sale_mode") == "dm" else "Bot (Stars avtomatik)"), callback_data="fmode")], BACK]
    await show(c, f"🖼 <b>Ramkalar</b> ({len(al)} ta)\nSotuv rejimi: {'kanal tugmasi egasining lichkasiga olib boradi' if S('sale_mode') == 'dm' else 'kanal tugmasi botdagi to`lov oynasiga olib boradi'}", KB(inline_keyboard=rows))

@adm.callback_query(OWNF, F.data == "fbulk")
async def _fbulk(c):
    PEND[c.from_user.id] = {"m": "frbulk"}
    await c.message.answer("📥 <b>Ramkalarni ketma-ket yuboring</b>\nHar bir rasm keyingi raqamga saqlanadi (№1, №2...). Albom ham bo'ladi.\n"
                           "• PNG ni <b>fayl (hujjat)</b> sifatida yuboring.\n• Ramka markazi (avatar o'rni) oq yoki shaffof doira bo'lsin — bot fonni o'zi shaffof qiladi va avatarga moslaydi.\nTugatgach «✅ Tayyor».",
                           reply_markup=done_kb("fdone")); await c.answer()

@adm.callback_query(OWNF, F.data == "fdone")
async def _fdone(c): PEND.pop(c.from_user.id, None); await fl_show(c, 0)

@adm.callback_query(OWNF, F.data.startswith("opr:"))
async def _opr(c):
    n = int(c.data[4:]); sql("UPDATE frames SET pro=1-pro WHERE num=?", (n,)); await ofc_refresh(c, n); await c.answer("Saqlandi")

@adm.callback_query(OWNF, F.data == "fmode")
async def _fmode(c):
    sql("INSERT OR REPLACE INTO settings(k,v) VALUES('sale_mode',?)", ("bot" if S("sale_mode") == "dm" else "dm",)); await fl_show(c, 0)

@adm.callback_query(OWNF, F.data == "fnew")
async def _fnew(c):
    PEND[c.from_user.id] = {"m": "frnum"}; await c.message.answer("Ramka raqamini yuboring (masalan: 5)", reply_markup=KB(inline_keyboard=[BACK])); await c.answer()

@adm.callback_query(OWNF, F.data.startswith("ofc:"))
async def _ofc(c): await ofc_send(c.message.chat.id, int(c.data[4:])); await c.answer()

@adm.callback_query(OWNF, F.data.startswith("ops:"))
async def _ops(c):
    _, n, v = c.data.split(":"); n = int(n)
    if v == "x": PEND[c.from_user.id] = {"m": "frprice", "n": n}; await c.message.answer("Narxni Stars da yuboring (0 = bepul):"); return await c.answer()
    sql("UPDATE frames SET price=? WHERE num=?", (int(v), n)); await ofc_refresh(c, n); await c.answer("Saqlandi")

@adm.callback_query(OWNF, F.data.startswith("osl:"))
async def _osl(c):
    n = int(c.data[4:]); sql("UPDATE frames SET on_sale=1-on_sale WHERE num=?", (n,)); await ofc_refresh(c, n); await c.answer()

@adm.callback_query(OWNF, F.data.startswith("oim:"))
async def _oim(c):
    PEND[c.from_user.id] = {"m": "frimg", "n": int(c.data[4:])}; await c.message.answer("Yangi rasmni yuboring (PNG ni fayl sifatida yuborgan ma'qul)."); await c.answer()

@adm.callback_query(OWNF, F.data.startswith("ogv:"))
async def _ogv(c):
    PEND[c.from_user.id] = {"m": "give", "n": int(c.data[4:])}
    await c.message.answer("Kimga berilsin? ID, @username yuboring yoki foydalanuvchi xabarini forward qiling.", reply_markup=KB(inline_keyboard=[BACK])); await c.answer()

@adm.callback_query(OWNF, F.data.startswith("odl:"))
async def _odl(c):
    n = int(c.data[4:])
    await c.message.answer(f"№{n} ramka o'chirilsinmi? Egalaridan ham olinadi.", reply_markup=KB(inline_keyboard=[[B(text="✅ Ha, o'chirish", callback_data=f"odx:{n}"), B(text="Yo'q", callback_data="fl:0")]])); await c.answer()

@adm.callback_query(OWNF, F.data.startswith("odx:"))
async def _odx(c):
    n = int(c.data[4:])
    for r in allr("SELECT id FROM users WHERE frame=?", (n,)): set_frame(r["id"], 0)
    for old in (DATA / "assets").glob(f"frame_{n}.*"): old.unlink()
    sql("DELETE FROM assets WHERE key=?", (f"frame_{n}",)); sql("DELETE FROM user_frames WHERE num=?", (n,)); sql("DELETE FROM frames WHERE num=?", (n,))
    await push_assets(); await fl_show(c, 0)

@adm.callback_query(OWNF, F.data.startswith("ofp:"))
async def _ofp(c):
    n = int(c.data[4:]); f = one("SELECT * FROM frames WHERE num=?", (n,)); p = fr_path(n); ch = S("sale_channel").strip()
    if not f or not p: return await c.answer("Ramka topilmadi", show_alert=True)
    if not ch: return await c.answer("Avval sotuv kanalini sozlang: Ramkalar -> Sotuv kanali", show_alert=True)
    if ch.startswith("https://t.me/"): ch = "@" + ch.rstrip("/").split("/")[-1]
    url = await fr_buy_url(n)
    try:
        await bot.send_photo(ch, FSInputFile(p), caption=f"🖼 <b>Ramka №{n}</b>\n💰 Narxi: {fr_price(f['price'])}\n\n👇 Olish uchun tugmani bosing",
                             reply_markup=KB(inline_keyboard=[[B(text="🛒 Sotib olish" if f["price"] else "🎁 Olish", url=url)]]))
        sql("UPDATE frames SET on_sale=1 WHERE num=?", (n,)); await c.answer("Kanalga yuborildi ✅", show_alert=True); await ofc_refresh(c, n)
    except Exception as ex: await c.answer("Xato (bot kanalda admin bo'lsin): " + str(ex)[:140], show_alert=True)

async def give_notify(uid, n):
    try: await bot.send_message(uid, f"🎁 Sizga №{n} ramka berildi va profilingizga kiyildi!", reply_markup=webkb())
    except Exception: pass
    await refresh_me(uid)

@adm.callback_query(F.data.startswith("ogu:"))
async def _ogu(c):
    _, uid, n = c.data.split(":"); uid, n = int(uid), int(n)
    if not one("SELECT 1 FROM frames WHERE num=?", (n,)) or not one("SELECT 1 FROM users WHERE id=?", (uid,)): return await c.answer("Topilmadi", show_alert=True)
    grant_frame(uid, n); await give_notify(uid, n); await c.answer("Berildi ✅", show_alert=True)

# ---------- owner: belgilar (Pro a'zolar ism oldida ko'rsatadi) ----------
def bd_path(n):
    r = one("SELECT path FROM assets WHERE key=?", (f"badge_{n}",))
    return r["path"] if r and Path(r["path"]).exists() else None

def obc_kb(n):
    return KB(inline_keyboard=[[B(text="🖼 Rasmni almashtirish", callback_data=f"obi:{n}")], [B(text="🗑 O'chirish", callback_data=f"odb:{n}")], [B(text="◀️ Belgilar", callback_data="bl:0")]])

async def obc_send(chat_id, n):
    p = bd_path(n); k = one("SELECT COUNT(*) n FROM users WHERE badge=?", (n,))["n"]
    cap = f"🏅 <b>Belgi №{n}</b>\n👥 Kiyganlar: {k}"
    if p: await bot.send_photo(chat_id, FSInputFile(p), caption=cap, reply_markup=obc_kb(n))
    else: await bot.send_message(chat_id, cap + "\n(rasm topilmadi)", reply_markup=obc_kb(n))

async def bl_show(c, pg):
    al = allr("SELECT num FROM badges ORDER BY num"); per = 8; pg = max(0, min(pg, max(0, (len(al) - 1) // per)))
    rows = [[B(text=f"🏅 №{r['num']} · {one('SELECT COUNT(*) n FROM users WHERE badge=?', (r['num'],))['n']} ta", callback_data=f"obc:{r['num']}")] for r in al[pg * per:(pg + 1) * per]]
    nav = []
    if pg > 0: nav.append(B(text="◀️", callback_data=f"bl:{pg - 1}"))
    if (pg + 1) * per < len(al): nav.append(B(text="▶️", callback_data=f"bl:{pg + 1}"))
    if nav: rows.append(nav)
    rows += [[B(text="➕ Yangi belgi", callback_data="bnew"), B(text="📥 Ketma-ket yuklash", callback_data="bbulk")], BACK]
    await show(c, f"🏅 <b>Belgilar</b> ({len(al)} ta)\nPro a'zolar belgini Sozlamalar → Pro bo'limidan tanlaydi. Belgi ism oldida ko'rinadi.", KB(inline_keyboard=rows))

@adm.callback_query(OWNF, F.data.startswith("bl:"))
async def _bl(c): await bl_show(c, int(c.data[3:]))

@adm.callback_query(OWNF, F.data == "bnew")
async def _bnew(c):
    PEND[c.from_user.id] = {"m": "bdimg", "n": 0}
    await c.message.answer("Belgi rasmini yuboring (PNG ni fayl sifatida yuborgan ma'qul). Fon oq bo'lsa, bot uni shaffof qiladi.", reply_markup=KB(inline_keyboard=[BACK])); await c.answer()

@adm.callback_query(OWNF, F.data == "bbulk")
async def _bbulk(c):
    PEND[c.from_user.id] = {"m": "bdbulk"}
    await c.message.answer("📥 <b>Belgilarni ketma-ket yuboring</b>\nHar bir rasm keyingi raqamga saqlanadi. PNG ni fayl sifatida yuboring. Tugatgach «✅ Tayyor».", reply_markup=done_kb("bddone")); await c.answer()

@adm.callback_query(OWNF, F.data == "bddone")
async def _bddone(c): PEND.pop(c.from_user.id, None); await bl_show(c, 0)

@adm.callback_query(OWNF, F.data.startswith("obc:"))
async def _obc(c): await obc_send(c.message.chat.id, int(c.data[4:])); await c.answer()

@adm.callback_query(OWNF, F.data.startswith("obi:"))
async def _obi(c):
    PEND[c.from_user.id] = {"m": "bdimg", "n": int(c.data[4:])}; await c.message.answer("Yangi rasmni yuboring."); await c.answer()

@adm.callback_query(OWNF, F.data.startswith("odb:"))
async def _odb(c):
    n = int(c.data[4:])
    await c.message.answer(f"№{n} belgi o'chirilsinmi? Kiyganlardan ham olinadi.", reply_markup=KB(inline_keyboard=[[B(text="✅ Ha, o'chirish", callback_data=f"odbx:{n}"), B(text="Yo'q", callback_data="bl:0")]])); await c.answer()

@adm.callback_query(OWNF, F.data.startswith("odbx:"))
async def _odbx(c):
    n = int(c.data[5:]); users = [r["id"] for r in allr("SELECT id FROM users WHERE badge=?", (n,))]
    sql("UPDATE users SET badge=0 WHERE badge=?", (n,))
    for u_ in users: refresh_photo(u_)
    for old in (DATA / "assets").glob(f"badge_{n}.*"): old.unlink()
    sql("DELETE FROM assets WHERE key=?", (f"badge_{n}",)); sql("DELETE FROM badges WHERE num=?", (n,))
    await push_assets()
    for u_ in users:
        if u_ in HUB: await refresh_me(u_)
    await bl_show(c, 0)

@adm.callback_query(OWNF, F.data == "embulk")
async def _embulk(c):
    PEND[c.from_user.id] = {"m": "embulk"}
    await c.message.answer("📥 <b>Ovul belgilarini ketma-ket yuboring</b>\nHar bir rasm keyingi bo'sh raqamga (1–40) qo'yiladi va ro'yxatda darhol chiqadi. PNG ni fayl sifatida yuboring. Tugatgach «✅ Tayyor».", reply_markup=done_kb("emdone")); await c.answer()

@adm.callback_query(OWNF, F.data == "emdone")
async def _emdone(c):
    PEND.pop(c.from_user.id, None)
    await show(c, "✅ Ovul belgilari saqlandi.", KB(inline_keyboard=[[B(text="📂 Ovul belgilari ro'yxati", callback_data="ct:emb:0")], BACK]))

# ---------- foydalanuvchilar ----------
def find_users(m):
    fo = getattr(m, "forward_origin", None); su = getattr(fo, "sender_user", None) if fo else None
    if su: ensure_user(su); return [su.id]
    ff = getattr(m, "forward_from", None)
    if ff: ensure_user(ff); return [ff.id]
    t = (m.text or "").strip().lstrip("@")
    if not t: return []
    if t.isdigit(): return [int(t)]
    rows = allr("SELECT id FROM users WHERE uname=?", (t.lower(),)) or allr("SELECT id FROM users WHERE name LIKE ? LIMIT 8", (f"%{t}%",))
    return [r["id"] for r in rows]

def ulist_kb(ids, extra=None):
    rows = []
    for i in ids[:10]:
        u = one("SELECT name,points FROM users WHERE id=?", (i,))
        rows.append([B(text=(f"{(u['name'] or '?')[:22]} · {u['points']} · {i}" if u else f"ID {i}"), callback_data=(extra or "uc:") + str(i))])
    rows.append(BACK); return KB(inline_keyboard=rows)

@adm.callback_query(F.data == "usrch")
async def _usrch(c):
    PEND[c.from_user.id] = {"m": "uid"}
    await c.message.answer("Foydalanuvchini toping: ID, @username yoki ism yuboring, yoki uning xabarini forward qiling.", reply_markup=KB(inline_keyboard=[BACK])); await c.answer()

@adm.callback_query(F.data.startswith("uc:"))
async def _uc(c): t, kb = ucard(int(c.data[3:])); await show(c, t, kb)

@adm.callback_query(F.data == "top")
async def _top(c):
    ids = [r["id"] for r in allr("SELECT id FROM users ORDER BY points DESC LIMIT 10")]; await show(c, "🏆 Top 10 (ball bo'yicha):", ulist_kb(ids))

@adm.callback_query(F.data.startswith("ul:"))
async def _ul(c):
    _, kind, pg = c.data.split(":"); pg = int(pg); per = 10
    q = {"pro": ("WHERE pro_until>? ORDER BY pro_until DESC", (int(time.time()),), "👑 Pro foydalanuvchilar"),
         "ban": ("WHERE banned=1 ORDER BY id DESC", (), "🚫 Bloklanganlar"), "new": ("ORDER BY created DESC", (), "🆕 Oxirgi qo'shilganlar")}[kind]
    al = [r["id"] for r in allr(f"SELECT id FROM users {q[0]} LIMIT 200", q[1])]
    kb = ulist_kb(al[pg * per:(pg + 1) * per]); nav = []
    if pg > 0: nav.append(B(text="◀️", callback_data=f"ul:{kind}:{pg - 1}"))
    if (pg + 1) * per < len(al): nav.append(B(text="▶️", callback_data=f"ul:{kind}:{pg + 1}"))
    if nav: kb.inline_keyboard.insert(len(kb.inline_keyboard) - 1, nav)
    await show(c, f"{q[2]} ({len(al)})", kb)

@adm.callback_query(F.data.startswith("fg:"))
async def _fg(c):
    _, uid, n = c.data.split(":"); uid, n = int(uid), int(n); grant_frame(uid, n); await give_notify(uid, n); t, kb = ucard(uid); await show(c, t, kb)

@adm.callback_query(F.data.startswith("fx:"))
async def _fx(c):
    _, uid, n = c.data.split(":"); uid, n = int(uid), int(n)
    r = one("SELECT frame FROM users WHERE id=?", (uid,))
    if r and r["frame"] == n: set_frame(uid, 0)
    sql("DELETE FROM user_frames WHERE uid=? AND num=?", (uid, n)); await refresh_me(uid); t, kb = ucard(uid); await show(c, t, kb)

# ---------- owner: Pro boshqaruvi ----------
@adm.callback_query(OWNF, F.data == "pro")
async def _pro(c):
    free = S("pro_free"); act = one("SELECT COUNT(*) n FROM users WHERE pro_until>?", (int(time.time()),))["n"]
    rows = [[B(text="🔁 " + ("Pulik qilish (hozir BEPUL)" if free else "Bepul qilish (hozir PULIK)"), callback_data="prof")]]
    ms = list(PLANS); rows += [[B(text=f"{m} oy · {S('star_' + m)}⭐", callback_data=f"set:star_{m}") for m in ms[i:i + 2]] for i in range(0, len(ms), 2)]
    rows += [[B(text="🎁 Hammaga +7 kun", callback_data="pall:7"), B(text="🎁 Hammaga +30 kun", callback_data="pall:30")],
             [B(text="👑 Pro ro'yxati", callback_data="ul:pro:0"), B(text="👤 Odamga Pro berish", callback_data="usrch")], BACK]
    await show(c, f"👑 <b>Pro boshqaruvi</b>\nRejim: {'🟢 BEPUL: hamma foydalanuvchida Pro ochiq' if free else '💰 PULIK: Stars orqali sotiladi'}\nFaol Pro: {act} ta\nNarxni o'zgartirish uchun tugmani bosing:", KB(inline_keyboard=rows))

@adm.callback_query(OWNF, F.data == "prof")
async def _prof(c):
    sql("INSERT OR REPLACE INTO settings(k,v) VALUES('pro_free',?)", ("0" if S("pro_free") else "1",)); await _pro(c)

@adm.callback_query(OWNF, F.data.startswith("pall:"))
async def _pall(c):
    d = int(c.data[5:])
    await show(c, f"Barcha (bloklanmagan) foydalanuvchilarga +{d} kun Pro berilsinmi?", KB(inline_keyboard=[[B(text="✅ Ha", callback_data=f"palx:{d}"), B(text="Yo'q", callback_data="pro")]]))

@adm.callback_query(OWNF, F.data.startswith("palx:"))
async def _palx(c):
    d = int(c.data[5:]); n = sql("UPDATE users SET pro_until=MAX(pro_until,?)+? WHERE banned=0", (int(time.time()), d * 86400)).rowcount
    await show(c, f"✅ {n} ta foydalanuvchiga +{d} kun Pro berildi.", KB(inline_keyboard=[[B(text="👑 Pro", callback_data="pro")], BACK]))

# ---------- owner: adminlar ----------
@adm.callback_query(OWNF, F.data == "adl")
async def _adl(c):
    al = allr("SELECT uid FROM admins ORDER BY ts"); rows = [[B(text=f"🛡 {(one('SELECT name FROM users WHERE id=?', (a['uid'],)) or {'name': a['uid']})['name']} · {a['uid']} (olib tashlash)", callback_data=f"adr:{a['uid']}")] for a in al]
    rows += [[B(text="➕ Admin qo'shish", callback_data="ada")], [B(text="🔗 Taklif havolasi (1 marta, 24 soat)", callback_data="adi")], BACK]
    await show(c, "🛡 <b>Adminlar</b>\nOwnerlar: " + ", ".join(map(str, OWNERS)) + f"\nAdminlar: {len(al)} ta\n\nAdmin: foydalanuvchilar (Pro, ban, ramka berish), xonalar, dashboard. Kontent, sozlamalar, e'lon va to'lovlar faqat ownerga.", KB(inline_keyboard=rows))

@adm.callback_query(OWNF, F.data == "ada")
async def _ada(c):
    PEND[c.from_user.id] = {"m": "addadm"}; await c.message.answer("Admin qilinadigan odamning ID / @username sini yuboring yoki xabarini forward qiling.", reply_markup=KB(inline_keyboard=[BACK])); await c.answer()

@adm.callback_query(OWNF, F.data == "adi")
async def _adi(c):
    tok = os.urandom(5).hex(); sql("INSERT OR REPLACE INTO settings(k,v) VALUES(?,?)", ("inv_" + tok, str(int(time.time()) + 86400)))
    await c.message.answer(f"🔗 Bir martalik havola (24 soat):\nhttps://t.me/{BOT_USERNAME}?start=adm_{tok}\n\nShu havolani bosgan odam admin bo'ladi.", reply_markup=KB(inline_keyboard=[[B(text="🛡 Adminlar", callback_data="adl")]])); await c.answer()

@adm.callback_query(OWNF, F.data.startswith("adr:"))
async def _adr(c): sql("DELETE FROM admins WHERE uid=?", (int(c.data[4:]),)); await _adl(c)

# ---------- owner: start-menyu tugmalari konstruktori ----------
@adm.callback_query(OWNF, F.data == "bt")
async def _bt(c): await bt_show(c)

async def bt_show(c):
    rows = [[B(text="📢 Rasmiy kanal: " + (S("channel_url") or "yo'q")[:30], callback_data="set:channel_url")],
            [B(text="👥 Mafia Chat: " + (S("chat_url") or "yo'q")[:30], callback_data="set:chat_url")]]
    rows += [[B(text=f"🔘 {b['text']}", callback_data=f"bt:{b['id']}")] for b in allr("SELECT * FROM btns ORDER BY pos,id")]
    rows += [[B(text="➕ Yangi tugma", callback_data="btn")], BACK]
    await show(c, "🔘 <b>/start tugmalari</b>\nKanal va chat havolasini yoki o'zingizning tugmalaringizni (guruh, kanal, sayt) sozlang. Havolani o'chirish uchun '-' yuboring.", KB(inline_keyboard=rows))

@adm.callback_query(OWNF, F.data == "btn")
async def _btn(c):
    PEND[c.from_user.id] = {"m": "btn"}; await c.message.answer("Format:\nTugma matni | https://t.me/havola", reply_markup=KB(inline_keyboard=[BACK])); await c.answer()

@adm.callback_query(OWNF, F.data.startswith("bt:"))
async def _btc(c):
    b = one("SELECT * FROM btns WHERE id=?", (int(c.data[3:]),))
    if not b: return await c.answer("Topilmadi")
    i = b["id"]
    await show(c, f"🔘 <b>{ESC(b['text'])}</b>\n{ESC(b['url'])}", KB(inline_keyboard=[[B(text="✏️ Matn", callback_data=f"btt:{i}"), B(text="🔗 Havola", callback_data=f"btu:{i}")],
               [B(text="⬆️ Yuqoriga", callback_data=f"btup:{i}"), B(text="🗑 O'chirish", callback_data=f"btd:{i}")], [B(text="◀️ Tugmalar", callback_data="bt")]]))

@adm.callback_query(OWNF, F.data.regexp(r"^bt[tu]:"))
async def _bte(c):
    k, i = c.data.split(":"); PEND[c.from_user.id] = {"m": "btnt" if k == "btt" else "btnu", "id": int(i)}
    await c.message.answer("Yangi matn yuboring:" if k == "btt" else "Yangi havolani yuboring (https://...):"); await c.answer()

@adm.callback_query(OWNF, F.data.startswith("btup:"))
async def _btup(c):
    i = int(c.data[5:]); mn = one("SELECT COALESCE(MIN(pos),0) n FROM btns")["n"]; sql("UPDATE btns SET pos=? WHERE id=?", (mn - 1, i)); await bt_show(c)

@adm.callback_query(OWNF, F.data.startswith("btd:"))
async def _btd(c): sql("DELETE FROM btns WHERE id=?", (int(c.data[4:]),)); await bt_show(c)

# ---------- dashboard ----------
def dash_text(owner):
    n = lambda q, a=(): one(q, a)["n"]; now = int(time.time()); t0 = today() * 86400 - TZ
    s = (f"📊 <b>Dashboard</b>\n\n👥 Foydalanuvchilar: <b>{n('SELECT COUNT(*) n FROM users')}</b> (bugun +{n('SELECT COUNT(*) n FROM users WHERE created>=?', (t0,))}, 7 kun +{n('SELECT COUNT(*) n FROM users WHERE created>=?', (now - 7 * 86400,))})\n"
         f"🟢 Onlayn: {len(HUB)} | 🚪 Faol xonalar: {len(ROOMS)}\n"
         f"🎮 O'yinlar: {n('SELECT COUNT(*) n FROM games')} (24 soatda {n('SELECT COUNT(*) n FROM games WHERE ts>=?', (now - 86400,))})\n"
         f"👑 Pro: {n('SELECT COUNT(*) n FROM users WHERE pro_until>?', (now,))} | rejim: {'BEPUL' if S('pro_free') else 'PULIK'}\n"
         f"🚫 Bloklangan: {n('SELECT COUNT(*) n FROM users WHERE banned=1')} | 🏰 Klanlar: {n('SELECT COUNT(*) n FROM clans')}\n"
         f"🖼 Ramkalar: {n('SELECT COUNT(*) n FROM frames')} (berilgan {n('SELECT COUNT(*) n FROM user_frames')})\n🛡 Adminlar: {n('SELECT COUNT(*) n FROM admins')}")
    if owner:
        rv = lambda k: n("SELECT COALESCE(SUM(stars),0) n FROM payments WHERE kind=?", (k,))
        s += f"\n\n💰 Daromad: <b>{n('SELECT COALESCE(SUM(stars),0) n FROM payments')}⭐</b> (Pro {rv('pro')}⭐, Ramka {rv('frame')}⭐)"
    return s

@adm.callback_query(F.data == "dash")
async def _dash(c):
    rows = [[B(text="🔄 Yangilash", callback_data="dash"), B(text="🏆 Top 10", callback_data="top")]]
    if c.from_user.id in OWNERS: rows.append([B(text="💾 Zaxira (backup)", callback_data="bk")])
    rows.append(BACK); await show(c, dash_text(c.from_user.id in OWNERS), KB(inline_keyboard=rows))

@adm.callback_query(OWNF, F.data == "bk")
async def _bkb(c): await send_backup("tugma"); await c.answer("Zaxira yuborildi ✅", show_alert=True)

# ---------- matnli kiritishlar (PEND) ----------
EXTRA_MODES = {"uid", "frnum", "frimg", "frprice", "give", "addadm", "prod", "umsg", "btn", "btnt", "btnu", "frbulk", "bdbulk", "embulk", "bdimg"}
async def pend_extra(m, p, mo, txt):
    uid = m.from_user.id
    if mo == "frbulk": return await bulk_item(m, "frame")
    if mo == "bdbulk": return await bulk_item(m, "badge")
    if mo == "embulk": return await bulk_item(m, "emb")
    if mo == "bdimg":
        if not _img_fid(m): return await m.answer("Rasm yuboring (PNG / JPG / WEBP).")
        try:
            async with bulk_lock():
                n = p.get("n") or ((one("SELECT MAX(num) m FROM badges")["m"] or 0) + 1)
                await put_img(f"badge_{n}", m, "icon"); sql("INSERT OR IGNORE INTO badges(num,created) VALUES(?,?)", (n, int(time.time())))
        except Exception as ex: return await m.answer("❌ Saqlanmadi: " + img_err(ex))
        PEND.pop(uid, None); await push_assets(); return await obc_send(m.chat.id, n)
    if mo == "uid":
        ids = find_users(m); PEND.pop(uid, None)
        if not ids: return await m.answer("Topilmadi. ID, @username, ism yuboring yoki xabarini forward qiling.", reply_markup=KB(inline_keyboard=[[B(text="🔎 Qayta", callback_data="usrch")], BACK]))
        if len(ids) == 1: t, kb = ucard(ids[0]); return await m.answer(t, reply_markup=kb)
        return await m.answer("Topilganlar:", reply_markup=ulist_kb(ids))
    if mo == "frnum":
        if not txt.isdigit() or not 1 <= int(txt) <= 99999: return await m.answer("Faqat raqam yuboring (masalan: 5)")
        n = int(txt); PEND[uid] = {"m": "frimg", "n": n}
        return await m.answer(f"№{n} uchun ramka rasmini yuboring.\nShaffoflik saqlanishi uchun PNG ni fayl (hujjat) sifatida yuboring." + ("\n(Bu raqam bor: rasm almashtiriladi.)" if one("SELECT 1 FROM frames WHERE num=?", (n,)) else ""))
    if mo == "frimg":
        if not await save_frame(p["n"], m): return await m.answer("Rasm yuboring (PNG / JPG / WEBP).")
        PEND.pop(uid, None); return await ofc_send(m.chat.id, p["n"])
    if mo == "frprice":
        if not txt.isdigit(): return await m.answer("Butun son yuboring (0 = bepul)")
        sql("UPDATE frames SET price=? WHERE num=?", (int(txt), p["n"])); PEND.pop(uid, None); return await ofc_send(m.chat.id, p["n"])
    if mo == "give":
        ids = find_users(m)
        if not ids: return await m.answer("Topilmadi. Qayta urinib ko'ring.")
        if len(ids) > 1: return await m.answer("Aniqlang:", reply_markup=ulist_kb(ids, f"ogu_{p['n']}:"))
        if not one("SELECT 1 FROM users WHERE id=?", (ids[0],)): return await m.answer("Bu foydalanuvchi botda yo'q (avval /start bossin).")
        PEND.pop(uid, None); grant_frame(ids[0], p["n"]); await give_notify(ids[0], p["n"])
        return await m.answer(f"✅ №{p['n']} ramka berildi: {uname_of(ids[0])}", reply_markup=KB(inline_keyboard=[[B(text="🖼 Ramkalar", callback_data="fl:0")]]))
    if mo == "addadm":
        ids = find_users(m)
        if len(ids) != 1: return await m.answer("Aniq bitta odamni yuboring (ID yoki forward).")
        PEND.pop(uid, None); sql("INSERT OR IGNORE INTO admins(uid,ts) VALUES(?,?)", (ids[0], int(time.time())))
        try: await bot.send_message(ids[0], "🛡 Siz admin qilindingiz. /admin buyrug'ini yuboring.")
        except Exception: pass
        return await m.answer(f"✅ Admin: {uname_of(ids[0])}", reply_markup=KB(inline_keyboard=[[B(text="🛡 Adminlar", callback_data="adl")]]))
    if mo == "prod":
        if not txt.lstrip("-").isdigit(): return await m.answer("Butun son yuboring (kunlar)")
        u = one("SELECT pro_until FROM users WHERE id=?", (p["uid"],)); d = int(txt)
        sql("UPDATE users SET pro_until=? WHERE id=?", (max(int(time.time()), u["pro_until"]) + d * 86400 if d > 0 else 0, p["uid"])); PEND.pop(uid, None)
        t, kb = ucard(p["uid"]); return await m.answer(t, reply_markup=kb)
    if mo == "umsg":
        PEND.pop(uid, None)
        try: await bot.copy_message(p["uid"], m.chat.id, m.message_id); return await m.answer("✅ Yuborildi")
        except Exception as ex: return await m.answer("Yuborilmadi: " + ESC(str(ex)[:120]))
    if mo == "btn":
        a, _, b = txt.partition("|"); a, b = a.strip(), b.strip()
        if not a or not ok_url(b): return await m.answer("Format: Tugma matni | https://t.me/havola")
        sql("INSERT INTO btns(text,url,pos) VALUES(?,?,?)", (a[:40], b, one("SELECT COALESCE(MAX(pos),0)+1 n FROM btns")["n"])); PEND.pop(uid, None)
        return await m.answer("✅ Tugma qo'shildi", reply_markup=KB(inline_keyboard=[[B(text="🔘 Tugmalar", callback_data="bt")]]))
    if mo == "btnt":
        sql("UPDATE btns SET text=? WHERE id=?", (txt[:40], p["id"])); PEND.pop(uid, None); return await m.answer("✅ Saqlandi", reply_markup=KB(inline_keyboard=[[B(text="🔘 Tugmalar", callback_data="bt")]]))
    if mo == "btnu":
        if not ok_url(txt): return await m.answer("To'g'ri havola yuboring (https://...)")
        sql("UPDATE btns SET url=? WHERE id=?", (txt, p["id"])); PEND.pop(uid, None); return await m.answer("✅ Saqlandi", reply_markup=KB(inline_keyboard=[[B(text="🔘 Tugmalar", callback_data="bt")]]))

@adm.callback_query(F.data.regexp(r"^ogu_\d+:\d+$"))
async def _ogu2(c):
    a, uid = c.data.split(":"); n = int(a[4:]); uid = int(uid); PEND.pop(c.from_user.id, None)
    grant_frame(uid, n); await give_notify(uid, n); await c.answer("Berildi ✅", show_alert=True)


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
    seed_defaults()
    seed_roles()
    BOT_USERNAME = (await bot.get_me()).username
    if WEBAPP_URL:
        await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="🎮 O'ynash", web_app=WebAppInfo(url=WEBAPP_URL)))
    asyncio.create_task(backup_loop()); asyncio.create_task(send_backup("ishga tushdi")); asyncio.create_task(weekly_loop())
    srv = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=PORT, log_level="warning"))
    await asyncio.gather(srv.serve(), dp.start_polling(bot))

if __name__ == "__main__":
    asyncio.run(main())
