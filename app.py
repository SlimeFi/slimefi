"""SlimeFi - Jura Tempest Village Builder
Flask + SQLite backend. Virtual in-game economy only:
coins are NOT real money. Cash withdrawal / token value needs
separate funding, rules and security (future phase).
Income accrues from server timestamps, so it keeps building
even while the player is offline.
"""
import os
import json
import hmac
import hashlib
import sqlite3
import time
import threading
import secrets
import urllib.parse
from datetime import date
from flask import Flask, request, jsonify, send_from_directory, g

app = Flask(__name__, static_folder="static", static_url_path="/static")

# Bot token stays ONLY in a secure environment variable, never in code/chat.
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

def verify_init_data(init_data):
    """Telegram WebApp initData server-side verification (HMAC-SHA256).
    Returns the Telegram user id if the data is genuine, else None."""
    if not BOT_TOKEN or not init_data:
        return None
    try:
        pairs = dict(urllib.parse.parse_qsl(init_data, strict_parsing=True))
        received_hash = pairs.pop("hash", None)
        if not received_hash:
            return None
        check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, received_hash):
            return None
        user = json.loads(pairs.get("user", "{}"))
        return user.get("id")
    except Exception:
        return None

@app.before_request
def telegram_auth():
    """If the request carries Telegram initData, it MUST verify.
    Requests without initData are local browser tests (fallback user)."""
    if not request.path.startswith("/api/") or request.path == "/api/health":
        return None
    init_data = request.headers.get("X-Init-Data", "")
    if init_data:
        tg_id = verify_init_data(init_data)
        if tg_id is None:
            return jsonify({"ok": False, "msg": "Telegram verification failed"}), 401
        g.tg_user_id = f"tg{tg_id}"
    return None
DB_PATH = __file__.rsplit("/", 1)[0] + "/slimefi.db"
_lock = threading.Lock()

# ---------------- game config ----------------
SLIME_LEVELS = [
    (0, "Basic Slime"),
    (1000, "Fledgling Slime"),
    (6000, "Magically Enhanced"),
    (21000, "Crystalline Slime"),
    (61000, "Slime Royalty"),
    (161000, "Sentient Slime"),
    (411000, "Crystalline Guardian"),
    (1011000, "Slime Monarch"),
    (2511000, "Ascendant Entity"),
    (6511000, "Slime Sovereign"),
]

BUILDINGS = {
    "town_hall":      {"name": "Town Hall",      "resource": "coins",    "rate": 5,  "base": {"coins": 100, "wood": 20, "crystals": 0}},
    "goblin_hut":     {"name": "Goblin Hut",     "resource": "coins",    "rate": 12, "base": {"coins": 50,  "wood": 0,  "crystals": 0}},
    "goblin_workshop":{"name": "Goblin Workshop","resource": "wood",     "rate": 10, "base": {"coins": 60,  "wood": 0,  "crystals": 0}},
    "magic_ore_mine": {"name": "Magic Ore Mine", "resource": "crystals", "rate": 4,  "base": {"coins": 120, "wood": 15, "crystals": 0}},
    "dwarf_forge":    {"name": "Dwarf Forge",    "resource": None,       "rate": 0,  "base": {"coins": 250, "wood": 30, "crystals": 10}},
}
START_LEVELS = {"town_hall": 1, "goblin_hut": 1}
MAX_OFFLINE_HOURS = 12

QUESTS = [
    {"id": "collect1", "title": "Collect income 1 time",   "counter": "daily_collects", "target": 1, "reward": {"coins": 50}},
    {"id": "upgrade1", "title": "Upgrade any building",    "counter": "daily_upgrades", "target": 1, "reward": {"coins": 150}},
    {"id": "refine1",  "title": "Refine materials 1 time", "counter": "daily_refines",  "target": 1, "reward": {"coins": 80, "crystals": 5}},
]

RECIPES = {
    "craft_wood":    {"title": "Craft wood into coins",    "give": {"wood": 10},     "get": {"coins": 90}},
    "smelt_crystal": {"title": "Smelt crystals into coins","give": {"crystals": 10}, "get": {"coins": 120}},
}

# ---------------- db ----------------
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            user_id TEXT PRIMARY KEY,
            coins INTEGER DEFAULT 0, wood INTEGER DEFAULT 0, crystals INTEGER DEFAULT 0,
            pending_coins REAL DEFAULT 0, pending_wood REAL DEFAULT 0, pending_crystals REAL DEFAULT 0,
            lifetime_coins INTEGER DEFAULT 0,
            last_settle REAL, last_daily TEXT, streak INTEGER DEFAULT 0,
            referral_code TEXT, referred_by TEXT, invites INTEGER DEFAULT 0,
            quest_date TEXT, daily_collects INTEGER DEFAULT 0,
            daily_upgrades INTEGER DEFAULT 0, daily_refines INTEGER DEFAULT 0,
            created_at REAL
        );
        CREATE TABLE IF NOT EXISTS buildings(
            user_id TEXT, building_id TEXT, level INTEGER DEFAULT 0,
            PRIMARY KEY(user_id, building_id)
        );
        CREATE TABLE IF NOT EXISTS quest_claims(
            user_id TEXT, quest_id TEXT, day TEXT, PRIMARY KEY(user_id, quest_id, day)
        );
        CREATE TABLE IF NOT EXISTS ledger(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, kind TEXT,
            coins INTEGER DEFAULT 0, wood INTEGER DEFAULT 0, crystals INTEGER DEFAULT 0,
            note TEXT, ts REAL
        );
        """)

def get_user(c, user_id):
    row = c.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    if row:
        return row
    code = "SLIME-" + secrets.token_hex(3).upper()
    now = time.time()
    c.execute("INSERT INTO users(user_id,last_settle,referral_code,quest_date,created_at) VALUES(?,?,?,?,?)",
              (user_id, now, code, str(date.today()), now))
    for bid, lvl in START_LEVELS.items():
        c.execute("INSERT INTO buildings VALUES(?,?,?)", (user_id, bid, lvl))
    c.execute("INSERT INTO ledger(user_id,kind,note,ts) VALUES(?,?,?,?)",
              (user_id, "founding", "Village founded: Goblin Hut + Town Hall ready", now))
    return c.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()

def get_buildings(c, user_id):
    rows = {r["building_id"]: r["level"] for r in
            c.execute("SELECT * FROM buildings WHERE user_id=?", (user_id,))}
    return {bid: rows.get(bid, 0) for bid in BUILDINGS}

def settle(c, user):
    """Accrue production from timestamps (offline income), capped."""
    now = time.time()
    hours = min((now - user["last_settle"]) / 3600.0, MAX_OFFLINE_HOURS)
    if hours <= 0:
        return user
    levels = get_buildings(c, user["user_id"])
    add = {"coins": 0.0, "wood": 0.0, "crystals": 0.0}
    for bid, lvl in levels.items():
        cfg = BUILDINGS[bid]
        if cfg["resource"] and lvl > 0:
            add[cfg["resource"]] += cfg["rate"] * lvl * hours
    c.execute("""UPDATE users SET pending_coins=pending_coins+?, pending_wood=pending_wood+?,
                 pending_crystals=pending_crystals+?, last_settle=? WHERE user_id=?""",
              (add["coins"], add["wood"], add["crystals"], now, user["user_id"]))
    return c.execute("SELECT * FROM users WHERE user_id=?", (user["user_id"],)).fetchone()

def roll_day(c, user):
    today = str(date.today())
    if user["quest_date"] != today:
        c.execute("""UPDATE users SET quest_date=?, daily_collects=0, daily_upgrades=0,
                     daily_refines=0 WHERE user_id=?""", (today, user["user_id"]))
        return c.execute("SELECT * FROM users WHERE user_id=?", (user["user_id"],)).fetchone()
    return user

def slime_level(lifetime):
    lvl = 1
    for i, (need, _name) in enumerate(SLIME_LEVELS):
        if lifetime >= need:
            lvl = i + 1
    nxt = SLIME_LEVELS[lvl][0] if lvl < len(SLIME_LEVELS) else None
    return lvl, SLIME_LEVELS[lvl - 1][1], nxt

def upgrade_cost(bid, level):
    base = BUILDINGS[bid]["base"]
    f = 1.6 ** level
    return {k: int(v * f) for k, v in base.items()}

def state_payload(c, user):
    levels = get_buildings(c, user["user_id"])
    lvl, lname, nxt = slime_level(user["lifetime_coins"])
    today = str(date.today())
    quests = []
    for q in QUESTS:
        claimed = c.execute("SELECT 1 FROM quest_claims WHERE user_id=? AND quest_id=? AND day=?",
                            (user["user_id"], q["id"], today)).fetchone() is not None
        quests.append({**q, "progress": min(user[q["counter"]], q["target"]), "claimed": claimed})
    forge = levels["dwarf_forge"]
    buildings = []
    for bid, cfg in BUILDINGS.items():
        bl = levels[bid]
        maxed = bl >= 3 + forge * 2 and bid != "dwarf_forge"
        buildings.append({
            "id": bid, "name": cfg["name"], "level": bl,
            "resource": cfg["resource"], "rate_per_hour": cfg["rate"] * bl if cfg["resource"] else 0,
            "next_cost": upgrade_cost(bid, bl), "capped_by_forge": maxed,
        })
    return {
        "user_id": user["user_id"],
        "coins": user["coins"], "wood": user["wood"], "crystals": user["crystals"],
        "pending": {"coins": int(user["pending_coins"]), "wood": int(user["pending_wood"]),
                    "crystals": int(user["pending_crystals"])},
        "lifetime_coins": user["lifetime_coins"],
        "slime": {"level": lvl, "name": lname, "next_at": nxt},
        "streak": user["streak"],
        "daily_available": user["last_daily"] != today,
        "referral_code": user["referral_code"], "invites": user["invites"],
        "squad_boost_pct": min(user["invites"] * 5, 50),
        "buildings": buildings, "quests": quests,
        "recipes": [{"id": k, **v} for k, v in RECIPES.items()],
    }

def uid():
    if getattr(g, "tg_user_id", None):
        return g.tg_user_id
    data = request.get_json(silent=True) or {}
    return (request.headers.get("X-User-Id") or data.get("user_id")
            or request.args.get("user_id") or "owner")

def log(c, user_id, kind, d, note):
    c.execute("INSERT INTO ledger(user_id,kind,coins,wood,crystals,note,ts) VALUES(?,?,?,?,?,?,?)",
              (user_id, kind, d.get("coins", 0), d.get("wood", 0), d.get("crystals", 0), note, time.time()))

# ---------------- routes ----------------
@app.get("/api/health")
def health():
    return jsonify({"ok": True, "game": "SlimeFi", "ts": time.time()})

@app.get("/api/state")
def state():
    with _lock, db() as c:
        u = roll_day(c, settle(c, get_user(c, uid())))
        return jsonify(state_payload(c, u))

@app.post("/api/collect")
def collect():
    with _lock, db() as c:
        u = roll_day(c, settle(c, get_user(c, uid())))
        got = {"coins": int(u["pending_coins"]), "wood": int(u["pending_wood"]),
               "crystals": int(u["pending_crystals"])}
        if sum(got.values()) == 0:
            return jsonify({"ok": False, "msg": "Abhi collect karne ke liye kuch nahi hai. Buildings production kar rahi hain."})
        c.execute("""UPDATE users SET coins=coins+?, wood=wood+?, crystals=crystals+?,
                     lifetime_coins=lifetime_coins+?, pending_coins=0, pending_wood=0,
                     pending_crystals=0, daily_collects=daily_collects+1 WHERE user_id=?""",
                  (got["coins"], got["wood"], got["crystals"], got["coins"], u["user_id"]))
        log(c, u["user_id"], "collect", got, "Village income collected")
        u = c.execute("SELECT * FROM users WHERE user_id=?", (u["user_id"],)).fetchone()
        return jsonify({"ok": True, "got": got, "state": state_payload(c, u)})

@app.post("/api/upgrade")
def upgrade():
    data = request.get_json(silent=True) or {}
    bid = data.get("building_id")
    if bid not in BUILDINGS:
        return jsonify({"ok": False, "msg": "Unknown building"})
    with _lock, db() as c:
        u = roll_day(c, settle(c, get_user(c, uid())))
        levels = get_buildings(c, u["user_id"])
        bl = levels[bid]
        if bid != "dwarf_forge" and bl >= 3 + levels["dwarf_forge"] * 2:
            return jsonify({"ok": False, "msg": "Pehle Dwarf Forge upgrade karo, tabhi ye building aur upar jayegi."})
        cost = upgrade_cost(bid, bl)
        if u["coins"] < cost["coins"] or u["wood"] < cost["wood"] or u["crystals"] < cost["crystals"]:
            return jsonify({"ok": False, "msg": "Resources kam hain", "cost": cost})
        c.execute("INSERT INTO buildings VALUES(?,?,?) ON CONFLICT(user_id,building_id) DO UPDATE SET level=level+1",
                  (u["user_id"], bid, bl + 1))
        c.execute("""UPDATE users SET coins=coins-?, wood=wood-?, crystals=crystals-?,
                     daily_upgrades=daily_upgrades+1 WHERE user_id=?""",
                  (cost["coins"], cost["wood"], cost["crystals"], u["user_id"]))
        log(c, u["user_id"], "upgrade", {k: -v for k, v in cost.items()}, f"{BUILDINGS[bid]['name']} -> Lv {bl+1}")
        u = c.execute("SELECT * FROM users WHERE user_id=?", (u["user_id"],)).fetchone()
        return jsonify({"ok": True, "state": state_payload(c, u)})

@app.post("/api/refine")
def refine():
    data = request.get_json(silent=True) or {}
    rid = data.get("recipe_id")
    if rid not in RECIPES:
        return jsonify({"ok": False, "msg": "Unknown recipe"})
    r = RECIPES[rid]
    with _lock, db() as c:
        u = roll_day(c, settle(c, get_user(c, uid())))
        for k, v in r["give"].items():
            if u[k] < v:
                return jsonify({"ok": False, "msg": f"{k} kam hai, {v} chahiye"})
        sets = ", ".join([f"{k}={k}-?" for k in r["give"]] + [f"{k}={k}+?" for k in r["get"]])
        params = list(r["give"].values()) + list(r["get"].values())
        c.execute(f"UPDATE users SET {sets}, daily_refines=daily_refines+1, "
                  f"lifetime_coins=lifetime_coins+? WHERE user_id=?",
                  (*params, r["get"].get("coins", 0), u["user_id"]))
        log(c, u["user_id"], "refine", r["get"], r["title"])
        u = c.execute("SELECT * FROM users WHERE user_id=?", (u["user_id"],)).fetchone()
        return jsonify({"ok": True, "state": state_payload(c, u)})

@app.post("/api/daily")
def daily():
    today = str(date.today())
    with _lock, db() as c:
        u = roll_day(c, settle(c, get_user(c, uid())))
        if u["last_daily"] == today:
            return jsonify({"ok": False, "msg": "Aaj ka daily reward le liya hai. Kal phir aana."})
        streak = u["streak"] + 1
        reward = 100 + min(streak, 7) * 10
        c.execute("""UPDATE users SET coins=coins+?, lifetime_coins=lifetime_coins+?,
                     last_daily=?, streak=? WHERE user_id=?""",
                  (reward, reward, today, streak, u["user_id"]))
        log(c, u["user_id"], "daily", {"coins": reward}, f"Daily reward day {streak}")
        u = c.execute("SELECT * FROM users WHERE user_id=?", (u["user_id"],)).fetchone()
        return jsonify({"ok": True, "reward": reward, "state": state_payload(c, u)})

@app.post("/api/quest/claim")
def quest_claim():
    data = request.get_json(silent=True) or {}
    q = next((x for x in QUESTS if x["id"] == data.get("quest_id")), None)
    if not q:
        return jsonify({"ok": False, "msg": "Unknown quest"})
    today = str(date.today())
    with _lock, db() as c:
        u = roll_day(c, settle(c, get_user(c, uid())))
        if u[q["counter"]] < q["target"]:
            return jsonify({"ok": False, "msg": "Quest abhi complete nahi hai"})
        if c.execute("SELECT 1 FROM quest_claims WHERE user_id=? AND quest_id=? AND day=?",
                     (u["user_id"], q["id"], today)).fetchone():
            return jsonify({"ok": False, "msg": "Reward pehle hi le liya hai"})
        c.execute("INSERT INTO quest_claims VALUES(?,?,?)", (u["user_id"], q["id"], today))
        r = q["reward"]
        sets = ", ".join(f"{k}={k}+?" for k in r)
        c.execute(f"UPDATE users SET {sets}, lifetime_coins=lifetime_coins+? WHERE user_id=?",
                  (*r.values(), r.get("coins", 0), u["user_id"]))
        log(c, u["user_id"], "quest", r, q["title"])
        u = c.execute("SELECT * FROM users WHERE user_id=?", (u["user_id"],)).fetchone()
        return jsonify({"ok": True, "state": state_payload(c, u)})

@app.post("/api/referral/apply")
def referral_apply():
    data = request.get_json(silent=True) or {}
    code = (data.get("code") or "").strip().upper()
    with _lock, db() as c:
        u = get_user(c, uid())
        if u["referred_by"]:
            return jsonify({"ok": False, "msg": "Referral code pehle hi lag chuka hai"})
        inv = c.execute("SELECT * FROM users WHERE referral_code=?", (code,)).fetchone()
        if not inv or inv["user_id"] == u["user_id"]:
            return jsonify({"ok": False, "msg": "Code sahi nahi hai"})
        c.execute("UPDATE users SET referred_by=?, coins=coins+50, lifetime_coins=lifetime_coins+50 WHERE user_id=?",
                  (inv["user_id"], u["user_id"]))
        c.execute("UPDATE users SET invites=invites+1, coins=coins+100, lifetime_coins=lifetime_coins+100 WHERE user_id=?",
                  (inv["user_id"],))
        log(c, u["user_id"], "referral", {"coins": 50}, "Welcome bonus via referral")
        log(c, inv["user_id"], "referral", {"coins": 100}, "Friend joined via referral")
        u = c.execute("SELECT * FROM users WHERE user_id=?", (u["user_id"],)).fetchone()
        return jsonify({"ok": True, "state": state_payload(c, roll_day(c, settle(c, u)))})

@app.get("/")
def index():
    return send_from_directory("static", "index.html")

init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
