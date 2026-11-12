"""SlimeFi - Jura Tempest Village Builder
Flask + SQLite backend. Earning phase (added 2026-11-12 on the owner's
decision): coins convert to a rupee cash wallet funded by rewarded-ad
revenue; withdrawals are UPI requests the owner approves and pays
manually from the owner panel.
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
    """Identity must be proven: verified Telegram initData or the owner's
    secret key. Bare X-User-Id is trusted only in local dev (ALLOW_DEV_AUTH)."""
    if not request.path.startswith("/api/") or request.path == "/api/health":
        return None
    # Owner's personal panel link carries a secret key: it maps straight to
    # the owner's Telegram identity even when his Telegram client does not
    # pass WebApp initData (mod/external-browser opens).
    if OWNER_KEY and OWNER_ID:
        k = request.headers.get("X-Owner-Key", "")
        if k and hmac.compare_digest(k, OWNER_KEY):
            g.tg_user_id = OWNER_ID
            return None
    init_data = request.headers.get("X-Init-Data", "")
    if init_data:
        tg_id = verify_init_data(init_data)
        if tg_id is None:
            return jsonify({"ok": False, "msg": "Telegram verification failed"}), 401
        g.tg_user_id = f"tg{tg_id}"
        return None
    if not ALLOW_DEV_AUTH:
        return jsonify({"ok": False, "msg": "Open SlimeFi from its Telegram bot"}), 401
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

# ---------------- earning config ----------------
# Cash is tracked in paise (100 paise = 1 rupee) so money math stays exact.
COINS_PER_RUPEE = 1000        # 10,000 coins = Rs 10
CONVERT_MIN_COINS = 1000      # convert at least Rs 1 worth at a time
AD_REWARD_COINS = 100         # coins per rewarded ad watched
AD_DAILY_LIMIT = 20           # max rewarded ads per user per day
AD_COOLDOWN_SEC = 45          # gap between two ad claims
MIN_WITHDRAW_RUPEES = 100     # minimum UPI withdrawal request
PAYOUTS_ENABLED = os.environ.get("PAYOUTS_ENABLED", "0") == "1"  # UPI payout master switch (owner)
ALLOW_DEV_AUTH = os.environ.get("ALLOW_DEV_AUTH", "0") == "1"    # local testing only: trust X-User-Id
REFERRAL_REWARD_COINS = 500   # paid to the inviter when the referred user completes
                              # their first qualifying activity (ad / quest / daily /
                              # approved sponsored task) — never on join alone
OWNER_ID = os.environ.get("OWNER_ID", "")          # Telegram user key of the owner, e.g. "tg123456789"
OWNER_KEY = os.environ.get("OWNER_KEY", "")        # secret in the owner's personal panel link (startapp param)
ADSGRAM_BLOCK_ID = os.environ.get("ADSGRAM_BLOCK_ID", "")  # set when the AdsGram block is live

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
        CREATE TABLE IF NOT EXISTS withdrawals(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT,
            amount_paise INTEGER, upi_id TEXT, status TEXT DEFAULT 'pending',
            created_at REAL, decided_at REAL
        );
        CREATE TABLE IF NOT EXISTS seen_starts(
            tg_id TEXT PRIMARY KEY, username TEXT, first_name TEXT, ts REAL
        );
        CREATE TABLE IF NOT EXISTS tasks(
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, description TEXT,
            link TEXT, reward_coins INTEGER DEFAULT 0, active INTEGER DEFAULT 1,
            created_at REAL
        );
        CREATE TABLE IF NOT EXISTS task_submissions(
            id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER, user_id TEXT,
            proof TEXT, status TEXT DEFAULT 'pending', created_at REAL, decided_at REAL,
            UNIQUE(task_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS cash_ledger(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT,
            delta_paise INTEGER DEFAULT 0, kind TEXT, note TEXT, ts REAL
        );
        """)
        # Earning columns added after the first release: add them to old DBs too.
        cols = {r[1] for r in c.execute("PRAGMA table_info(users)")}
        for col, ddl in [("cash_paise", "ALTER TABLE users ADD COLUMN cash_paise INTEGER DEFAULT 0"),
                         ("ads_today", "ALTER TABLE users ADD COLUMN ads_today INTEGER DEFAULT 0"),
                         ("ads_date", "ALTER TABLE users ADD COLUMN ads_date TEXT"),
                         ("referred_qualified", "ALTER TABLE users ADD COLUMN referred_qualified INTEGER DEFAULT 0"),
                         ("last_ad_at", "ALTER TABLE users ADD COLUMN last_ad_at REAL DEFAULT 0")]:
            if col not in cols:
                c.execute(ddl)
        tcols = {r[1] for r in c.execute("PRAGMA table_info(tasks)")}
        for col, ddl in [("budget_coins", "ALTER TABLE tasks ADD COLUMN budget_coins INTEGER DEFAULT 0"),
                         ("spent_coins", "ALTER TABLE tasks ADD COLUMN spent_coins INTEGER DEFAULT 0"),
                         ("category", "ALTER TABLE tasks ADD COLUMN category TEXT DEFAULT 'telegram'")]:
            if col not in tcols:
                c.execute(ddl)

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
        "cash_paise": user["cash_paise"] or 0,
        "cash_rupees": round((user["cash_paise"] or 0) / 100, 2),
        "players": c.execute("SELECT COUNT(*) n FROM users WHERE user_id != 'owner'").fetchone()["n"],
        "pending_rewards_coins": c.execute(
            "SELECT COALESCE(SUM(t.reward_coins),0) s FROM task_submissions s "
            "JOIN tasks t ON t.id = s.task_id WHERE s.user_id=? AND s.status='pending'",
            (user["user_id"],)).fetchone()["s"],
        "is_owner": bool(OWNER_ID) and user["user_id"] == OWNER_ID,
        "earn": {
            "coins_per_rupee": COINS_PER_RUPEE,
            "convert_min_coins": CONVERT_MIN_COINS,
            "min_withdraw_rupees": MIN_WITHDRAW_RUPEES,
            "ad_reward_coins": AD_REWARD_COINS,
            "ad_daily_limit": AD_DAILY_LIMIT,
            "ads_today": (user["ads_today"] or 0) if user["ads_date"] == today else 0,
            "ad_cooldown_left": max(0, int(AD_COOLDOWN_SEC - (time.time() - (user["last_ad_at"] or 0)))),
            "ads_live": bool(ADSGRAM_BLOCK_ID),
            "referral_reward_coins": REFERRAL_REWARD_COINS,
            "payouts_enabled": PAYOUTS_ENABLED,
        },
        "my_withdrawals": [
            {"id": w["id"], "amount_rupees": round(w["amount_paise"] / 100, 2),
             "upi_id": w["upi_id"], "status": w["status"]}
            for w in c.execute("SELECT * FROM withdrawals WHERE user_id=? ORDER BY id DESC LIMIT 5",
                               (user["user_id"],))
        ],
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

def cash_log(c, user_id, delta_paise, kind, note):
    """Every rupee movement gets a cash_ledger row (owner's ledger rule:
    the cash balance is never edited without an audit entry)."""
    c.execute("INSERT INTO cash_ledger(user_id,delta_paise,kind,note,ts) VALUES(?,?,?,?,?)",
              (user_id, delta_paise, kind, note, time.time()))

def task_budget_left(t):
    """Budget coins still available on a task; None means unlimited."""
    if not t["budget_coins"]:
        return None
    return t["budget_coins"] - (t["spent_coins"] or 0)

def maybe_qualify_referral(c, user):
    """Referral rule (owner, 2026-11-12): the inviter earns REFERRAL_REWARD_COINS
    only when the referred user completes a first qualifying activity — never
    on join alone. Runs at most once per referred user."""
    if not user["referred_by"] or user["referred_qualified"]:
        return
    c.execute("UPDATE users SET referred_qualified=1 WHERE user_id=?", (user["user_id"],))
    inv = c.execute("SELECT * FROM users WHERE user_id=?", (user["referred_by"],)).fetchone()
    if inv:
        c.execute("UPDATE users SET coins=coins+?, lifetime_coins=lifetime_coins+? WHERE user_id=?",
                  (REFERRAL_REWARD_COINS, REFERRAL_REWARD_COINS, inv["user_id"]))
        log(c, inv["user_id"], "referral", {"coins": REFERRAL_REWARD_COINS},
            "Referral reward: dost ne pehli qualifying activity puri ki")

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
        maybe_qualify_referral(c, u)
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
        maybe_qualify_referral(c, u)
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
        # New rule: no instant coins. The inviter earns REFERRAL_REWARD_COINS
        # only when this user completes a first qualifying activity.
        c.execute("UPDATE users SET referred_by=? WHERE user_id=?",
                  (inv["user_id"], u["user_id"]))
        c.execute("UPDATE users SET invites=invites+1 WHERE user_id=?", (inv["user_id"],))
        log(c, u["user_id"], "referral", {}, "Referral code laga (reward qualifying activity par)")
        u = c.execute("SELECT * FROM users WHERE user_id=?", (u["user_id"],)).fetchone()
        return jsonify({"ok": True, "state": state_payload(c, roll_day(c, settle(c, u))),
                        "msg": "Referral lag gaya. Pehli activity puri karte hi dost ko reward milega."})

# ---------------- earning routes ----------------
@app.post("/api/ad/claim")
def ad_claim():
    """Reward for one watched ad. Daily cap + cooldown apply server-side,
    so the reward cannot be farmed by tapping. When the AdsGram block is
    connected, the frontend only calls this after a completed ad view."""
    today = str(date.today())
    with _lock, db() as c:
        u = roll_day(c, settle(c, get_user(c, uid())))
        watched = (u["ads_today"] or 0) if u["ads_date"] == today else 0
        if watched >= AD_DAILY_LIMIT:
            return jsonify({"ok": False, "msg": f"Aaj ke {AD_DAILY_LIMIT} ads ho gaye. Kal phir try karo."})
        left = AD_COOLDOWN_SEC - (time.time() - (u["last_ad_at"] or 0))
        if left > 0:
            return jsonify({"ok": False, "msg": f"Thoda ruko, agla ad {int(left)} sec me milega."})
        c.execute("""UPDATE users SET coins=coins+?, lifetime_coins=lifetime_coins+?,
                     ads_today=?, ads_date=?, last_ad_at=? WHERE user_id=?""",
                  (AD_REWARD_COINS, AD_REWARD_COINS, watched + 1, today, time.time(), u["user_id"]))
        log(c, u["user_id"], "ad", {"coins": AD_REWARD_COINS}, f"Rewarded ad #{watched + 1}")
        maybe_qualify_referral(c, u)
        u = c.execute("SELECT * FROM users WHERE user_id=?", (u["user_id"],)).fetchone()
        return jsonify({"ok": True, "reward": AD_REWARD_COINS, "state": state_payload(c, u)})

@app.post("/api/convert")
def convert():
    data = request.get_json(silent=True) or {}
    try:
        coins = int(data.get("coins") or 0)
    except (TypeError, ValueError):
        coins = 0
    if coins < CONVERT_MIN_COINS or coins % COINS_PER_RUPEE != 0:
        return jsonify({"ok": False, "msg": f"Convert {COINS_PER_RUPEE} coins ke hisab se hota hai, kam se kam {CONVERT_MIN_COINS} coins."})
    rupees_paise = (coins // COINS_PER_RUPEE) * 100
    with _lock, db() as c:
        u = roll_day(c, settle(c, get_user(c, uid())))
        if u["coins"] < coins:
            return jsonify({"ok": False, "msg": "Coins kam hain"})
        c.execute("UPDATE users SET coins=coins-?, cash_paise=cash_paise+? WHERE user_id=?",
                  (coins, rupees_paise, u["user_id"]))
        log(c, u["user_id"], "convert", {"coins": -coins}, f"Converted to Rs {rupees_paise // 100}")
        cash_log(c, u["user_id"], rupees_paise, "convert", f"{coins} coins converted to cash")
        u = c.execute("SELECT * FROM users WHERE user_id=?", (u["user_id"],)).fetchone()
        return jsonify({"ok": True, "state": state_payload(c, u),
                        "msg": f"{coins} coins cash me badal gaye"})

@app.post("/api/withdraw")
def withdraw():
    data = request.get_json(silent=True) or {}
    if not PAYOUTS_ENABLED:
        return jsonify({"ok": False, "msg": "Withdraw abhi band hai — jald shuru hoga"})
    upi = (data.get("upi_id") or "").strip()
    try:
        rupees = int(data.get("amount_rupees") or 0)
    except (TypeError, ValueError):
        rupees = 0
    if "@" not in upi or len(upi) < 5:
        return jsonify({"ok": False, "msg": "Sahi UPI ID dalo, jese naam@okhdfcbank"})
    if rupees < MIN_WITHDRAW_RUPEES:
        return jsonify({"ok": False, "msg": f"Minimum withdraw Rs {MIN_WITHDRAW_RUPEES} hai"})
    with _lock, db() as c:
        u = roll_day(c, settle(c, get_user(c, uid())))
        if (u["cash_paise"] or 0) < rupees * 100:
            return jsonify({"ok": False, "msg": "Cash balance kam hai"})
        c.execute("UPDATE users SET cash_paise=cash_paise-? WHERE user_id=?",
                  (rupees * 100, u["user_id"]))
        cur = c.execute("INSERT INTO withdrawals(user_id,amount_paise,upi_id,created_at) VALUES(?,?,?,?)",
                        (u["user_id"], rupees * 100, upi, time.time()))
        log(c, u["user_id"], "withdraw", {}, f"Withdraw request #{cur.lastrowid}: Rs {rupees} to UPI")
        cash_log(c, u["user_id"], -rupees * 100, "withdraw_hold", f"Withdraw #{cur.lastrowid} requested")
        u = c.execute("SELECT * FROM users WHERE user_id=?", (u["user_id"],)).fetchone()
        return jsonify({"ok": True, "state": state_payload(c, u),
                        "msg": f"Request bhej di. Rs {rupees} jald hi tumhare UPI par aayega."})

def _require_owner():
    return bool(OWNER_ID) and uid() == OWNER_ID

@app.get("/api/owner/stats")
def owner_stats():
    """Owner-only player counts for the Owner Panel."""
    if not OWNER_ID or uid() != OWNER_ID:
        return jsonify({"ok": False, "error": "Owner only"}), 403
    conn = db()
    total = conn.execute(
        "SELECT COUNT(*) c FROM users WHERE user_id != 'owner'").fetchone()["c"]
    ist = time.time() + 19800  # Asia/Kolkata offset, seconds
    day_start = (ist - (ist % 86400)) - 19800
    joined_today = conn.execute(
        "SELECT COUNT(*) c FROM users WHERE user_id != 'owner' AND created_at >= ?",
        (day_start,)).fetchone()["c"]
    starts = conn.execute("SELECT COUNT(*) c FROM seen_starts").fetchone()["c"]
    rows = conn.execute(
        "SELECT user_id, lifetime_coins, created_at, invites FROM users "
        "WHERE user_id != 'owner' ORDER BY created_at DESC LIMIT 10").fetchall()
    conn.close()
    return jsonify({"ok": True, "total_players": total, "joined_today": joined_today,
                    "bot_starts": starts, "recent": [dict(r) for r in rows]})


@app.get("/api/owner/withdrawals")
def owner_withdrawals():
    if not _require_owner():
        return jsonify({"ok": False, "msg": "Owner only"}), 403
    with _lock, db() as c:
        rows = [{"id": w["id"], "user_id": w["user_id"],
                 "amount_rupees": round(w["amount_paise"] / 100, 2),
                 "upi_id": w["upi_id"], "status": w["status"]}
                for w in c.execute("SELECT * FROM withdrawals ORDER BY (status='pending') DESC, id DESC LIMIT 50")]
        totals = c.execute("SELECT COUNT(*) n, COALESCE(SUM(amount_paise),0) s FROM withdrawals WHERE status='pending'").fetchone()
        return jsonify({"ok": True, "withdrawals": rows,
                        "pending_count": totals["n"], "pending_rupees": round(totals["s"] / 100, 2)})

@app.post("/api/owner/withdrawals/decide")
def owner_decide():
    if not _require_owner():
        return jsonify({"ok": False, "msg": "Owner only"}), 403
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    if action not in ("paid", "reject"):
        return jsonify({"ok": False, "msg": "Unknown action"})
    with _lock, db() as c:
        w = c.execute("SELECT * FROM withdrawals WHERE id=?", (data.get("id"),)).fetchone()
        if not w or w["status"] != "pending":
            return jsonify({"ok": False, "msg": "Request pending nahi hai"})
        if action == "reject":
            c.execute("UPDATE users SET cash_paise=cash_paise+? WHERE user_id=?",
                      (w["amount_paise"], w["user_id"]))
            log(c, w["user_id"], "withdraw_refund", {}, f"Withdraw #{w['id']} rejected, refunded")
            cash_log(c, w["user_id"], w["amount_paise"], "withdraw_refund", f"Withdraw #{w['id']} rejected, refunded")
        else:
            cash_log(c, w["user_id"], 0, "withdraw_paid", f"Withdraw #{w['id']} paid out to UPI")
        c.execute("UPDATE withdrawals SET status=?, decided_at=? WHERE id=?",
                  ("paid" if action == "paid" else "rejected", time.time(), w["id"]))
        return jsonify({"ok": True, "msg": "Ho gaya"})

# ---------------- sponsored tasks (Task Center) ----------------
@app.get("/api/tasks")
def tasks_list():
    """Active sponsored tasks + this user's submission status per task.
    Flow (owner's plan): complete -> submit proof -> admin checks ->
    reward credited to the ledger -> eligible for withdrawal."""
    with _lock, db() as c:
        u = get_user(c, uid())
        subs = {s["task_id"]: s["status"] for s in
                c.execute("SELECT * FROM task_submissions WHERE user_id=?", (u["user_id"],))}
        rows = []
        for t in c.execute("SELECT * FROM tasks WHERE active=1 ORDER BY id DESC"):
            left = task_budget_left(t)
            if left is not None and left < t["reward_coins"] and t["id"] not in subs:
                continue  # campaign budget over: no new workers on this task
            rows.append({"id": t["id"], "title": t["title"], "description": t["description"],
                         "link": t["link"], "reward_coins": t["reward_coins"],
                         "category": t["category"] or "telegram",
                         "budget_left": left, "my_status": subs.get(t["id"])})
        return jsonify({"ok": True, "tasks": rows})

@app.post("/api/tasks/submit")
def tasks_submit():
    data = request.get_json(silent=True) or {}
    proof = (data.get("proof") or "").strip()
    if len(proof) < 3:
        return jsonify({"ok": False, "msg": "Proof dalo — screenshot link, username ya kaam ki detail"})
    with _lock, db() as c:
        u = get_user(c, uid())
        t = c.execute("SELECT * FROM tasks WHERE id=? AND active=1", (data.get("task_id"),)).fetchone()
        if not t:
            return jsonify({"ok": False, "msg": "Task nahi mila"})
        left = task_budget_left(t)
        if left is not None and left < t["reward_coins"]:
            return jsonify({"ok": False, "msg": "Is task ka campaign budget khatam ho gaya hai"})
        old = c.execute("SELECT * FROM task_submissions WHERE task_id=? AND user_id=?",
                        (t["id"], u["user_id"])).fetchone()
        if old and old["status"] != "rejected":
            return jsonify({"ok": False, "msg": "Is task ka proof pehle se submit hai (duplicate nahi chalega)"})
        if old:  # a rejected proof may be submitted once more, corrected
            c.execute("UPDATE task_submissions SET proof=?, status='pending', created_at=?, decided_at=NULL WHERE id=?",
                      (proof, time.time(), old["id"]))
        else:
            c.execute("INSERT INTO task_submissions(task_id,user_id,proof,created_at) VALUES(?,?,?,?)",
                      (t["id"], u["user_id"], proof, time.time()))
        return jsonify({"ok": True, "msg": "Proof submit ho gaya. Admin check ke baad reward milega."})

@app.post("/api/owner/tasks")
def owner_tasks_create():
    if not _require_owner():
        return jsonify({"ok": False, "msg": "Owner only"}), 403
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    try:
        reward = int(data.get("reward_coins") or 0)
    except (TypeError, ValueError):
        reward = 0
    try:
        budget = int(data.get("budget_coins") or 0)
    except (TypeError, ValueError):
        budget = 0
    category = (data.get("category") or "telegram").strip().lower()
    if category not in ("telegram", "twitter", "other"):
        category = "telegram"
    if not title or reward <= 0:
        return jsonify({"ok": False, "msg": "Task ka title aur reward (coins) dalo"})
    with _lock, db() as c:
        cur = c.execute("INSERT INTO tasks(title,description,link,reward_coins,budget_coins,category,created_at) VALUES(?,?,?,?,?,?,?)",
                        (title, (data.get("description") or "").strip(),
                         (data.get("link") or "").strip(), reward, budget, category, time.time()))
        return jsonify({"ok": True, "id": cur.lastrowid, "msg": "Task ban gaya"})

@app.get("/api/owner/submissions")
def owner_submissions():
    if not _require_owner():
        return jsonify({"ok": False, "msg": "Owner only"}), 403
    with _lock, db() as c:
        subs = [{"id": s["id"], "task_id": s["task_id"], "task_title": s["title"],
                 "user_id": s["user_id"], "proof": s["proof"], "status": s["status"],
                 "reward_coins": s["reward_coins"]}
                for s in c.execute(
                    "SELECT s.*, t.title, t.reward_coins FROM task_submissions s "
                    "JOIN tasks t ON t.id = s.task_id "
                    "ORDER BY (s.status='pending') DESC, s.id DESC LIMIT 50")]
        tasks = [{"id": t["id"], "title": t["title"], "reward_coins": t["reward_coins"],
                  "budget_coins": t["budget_coins"], "spent_coins": t["spent_coins"],
                  "category": t["category"] or "telegram", "active": t["active"]}
                 for t in c.execute("SELECT * FROM tasks ORDER BY id DESC LIMIT 20")]
        return jsonify({"ok": True, "submissions": subs, "tasks": tasks})

@app.post("/api/owner/submissions/decide")
def owner_submissions_decide():
    if not _require_owner():
        return jsonify({"ok": False, "msg": "Owner only"}), 403
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    if action not in ("approve", "reject"):
        return jsonify({"ok": False, "msg": "Unknown action"})
    with _lock, db() as c:
        s = c.execute("SELECT s.*, t.reward_coins, t.title, t.budget_coins, t.spent_coins FROM task_submissions s "
                      "JOIN tasks t ON t.id = s.task_id WHERE s.id=?", (data.get("id"),)).fetchone()
        if not s or s["status"] != "pending":
            return jsonify({"ok": False, "msg": "Submission pending nahi hai"})
        if action == "approve":
            if s["budget_coins"] and (s["spent_coins"] or 0) + s["reward_coins"] > s["budget_coins"]:
                return jsonify({"ok": False, "msg": "Campaign budget khatam — ye approve nahi ho sakta"})
            if s["budget_coins"]:
                c.execute("UPDATE tasks SET spent_coins=spent_coins+? WHERE id=?",
                          (s["reward_coins"], s["task_id"]))
            c.execute("UPDATE users SET coins=coins+?, lifetime_coins=lifetime_coins+? WHERE user_id=?",
                      (s["reward_coins"], s["reward_coins"], s["user_id"]))
            log(c, s["user_id"], "task", {"coins": s["reward_coins"]},
                f"Sponsored task approved: {s['title']}")
            submitter = c.execute("SELECT * FROM users WHERE user_id=?", (s["user_id"],)).fetchone()
            maybe_qualify_referral(c, submitter)
        c.execute("UPDATE task_submissions SET status=?, decided_at=? WHERE id=?",
                  ("approved" if action == "approve" else "rejected", time.time(), s["id"]))
        return jsonify({"ok": True, "msg": "Ho gaya"})

@app.get("/")
def index():
    return send_from_directory("static", "index.html")


WEBAPP_URL = os.environ.get("WEBAPP_URL", "")


@app.post("/webhook")
def telegram_webhook():
    """Telegram bot webhook: answers /start with the Launch button.
    The reply is returned as the webhook response itself, so the server
    needs no outbound call to Telegram for this."""
    upd = request.get_json(silent=True) or {}
    msg = upd.get("message") or {}
    # Record who pressed /start (the id comes from Telegram's own servers in
    # the update, so it cannot be faked by a webview): used to identify the
    # owner's real Telegram id for OWNER_ID.
    try:
        frm = msg.get("from") or {}
        if frm.get("id"):
            with db() as c:
                c.execute("INSERT OR REPLACE INTO seen_starts VALUES(?,?,?,?)",
                          (f"tg{frm['id']}", frm.get("username") or "",
                           frm.get("first_name") or "", time.time()))
    except Exception:
        pass
    text = (msg.get("text") or "").strip()
    chat_id = (msg.get("chat") or {}).get("id")
    if not chat_id or not text.startswith("/start"):
        return jsonify({"ok": True})
    url = WEBAPP_URL
    parts = text.split(maxsplit=1)
    if len(parts) > 1 and parts[1].startswith("SLIME-"):
        url = WEBAPP_URL + ("&" if "?" in WEBAPP_URL else "?") + "ref=" + parts[1]
    return jsonify({
        "method": "sendMessage",
        "chat_id": chat_id,
        "text": ("Welcome to SlimeFi - Jura Tempest Village Builder.\n"
                 "Apna gaon banao, buildings upgrade karo, coins kamao "
                 "aur apne Slime ko Level 10 Slime Sovereign tak le jao."),
        "reply_markup": {"inline_keyboard": [[
            {"text": "Launch SlimeFi", "web_app": {"url": url}}
        ]]},
    })

init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), threaded=True)
