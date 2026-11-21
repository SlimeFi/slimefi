"""SlimeFi v30 - Reward Earning Platform
Flask + SQLite backend. Coins are the canonical reward balance; displayed
rupee value is derived at 1,000 coins = Rs 1. UPI withdrawals reserve
coins immediately and are reviewed in the owner panel. Legacy village data
is retained in the database for safe migration but is no longer user-facing.
"""
import os
import json
import math
import hmac
import hashlib
import sqlite3
import time
import threading
import secrets
import urllib.parse
import urllib.request
import urllib.error
import re
from decimal import Decimal, InvalidOperation
from datetime import date, timedelta
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
        try:
            g.tg_username = user.get("username") or ""
            g.tg_first_name = ((user.get("first_name") or "") + " " +
                               (user.get("last_name") or "")).strip()
        except Exception:
            pass
        return user.get("id")
    except Exception:
        return None

@app.before_request
def telegram_auth():
    """Identity must be proven: verified Telegram initData or the owner's
    secret key. Bare X-User-Id is trusted only in local dev (ALLOW_DEV_AUTH)."""
    if not request.path.startswith("/api/") or request.path in ("/api/health", "/api/payouts", "/api/monetag/postback", "/api/rocket/state"):
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
    g.tg_user_id = request.headers.get("X-User-Id") or "owner"
    return None
@app.before_request
def ban_guard():
    """A banned (extra) account cannot perform actions. Owner routes stay
    reachable so the owner can review and unban; /api/state returns the
    notice itself."""
    if request.method != "POST":
        return None
    p = request.path
    if not p.startswith("/api/") or p.startswith("/api/owner/"):
        return None
    user_id = getattr(g, "tg_user_id", None)
    if not user_id:
        return None
    conn = db()
    try:
        row = conn.execute("SELECT banned FROM users WHERE user_id=?", (user_id,)).fetchone()
    finally:
        conn.close()
    if row and row["banned"]:
        return jsonify({"ok": False, "banned": True,
                        "msg": "MULTIPLE ACCOUNTS DETECTED — ye account band hai. Sirf apna main (pehla) account chalao."}), 403
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
DAILY_REWARDS = [50, 75, 100, 150, 200, 300, 500]  # 7-day streak bonus table
AD_REWARD_COINS = 250         # coins per rewarded ad watched (v83: 500 -> 250, owner 2026-11-19)
AD_DAILY_LIMIT = 20           # max rewarded ads per user per day
AD_COOLDOWN_SEC = 45          # gap between two ad claims
AD_MIN_WATCH_SEC = 8          # a real ad cannot finish faster than this
AD_MIN_OPEN_SEC = 15          # middle path: no 'done' signal -> token must be this old (ad stayed open)
AD_TOKEN_TTL_SEC = 300        # an ad-start token dies after 5 minutes
SPIN_FREE_PER_DAY = 1         # Spin Wheel: one free spin daily (owner, 2026-11-13)
SPIN_DAILY_LIMIT = 5          # v56: total spins per day = 1 free + 4 via ads (owner, 2026-11-15)
SPIN_SEGMENTS = [50, 500, 150, 300, 100, 250, 200, 100]  # wheel prizes in PAISE (₹0.50–₹5.00), credited to Available Balance
SPIN_WEIGHTS = [26, 4, 18, 10, 24, 12, 14, 20]           # relative chances (500 is rare)
MIN_WITHDRAW_RUPEES = 10      # minimum UPI withdrawal request (first withdrawal)
REPEAT_WITHDRAW_MIN_RUPEES = 50  # after any PAID withdrawal, later withdrawals need this minimum (owner, 2026-11-19)
PAYOUTS_ENABLED = os.environ.get("PAYOUTS_ENABLED", "0") == "1"  # UPI payout master switch (owner)
ALLOW_DEV_AUTH = os.environ.get("ALLOW_DEV_AUTH", "0") == "1"    # local testing only: trust X-User-Id
REFERRAL_REWARD_COINS = 5000   # paid to the inviter when the referred user completes
                              # their first qualifying activity (ad / quest / daily /
                              # approved sponsored task) — never on join alone
                              # (raised 1000 -> 5000 on owner order, 2026-11-18)
WELCOME_BONUS_COINS = 5000    # one-time bonus credited to every NEW account at
                              # creation (owner order, 2026-11-14; raised 1000 -> 5000
                              # = Rs5 on owner order, 2026-11-15); ledger kind
                              # "welcome_bonus"; NOT a referral qualifying activity
OWNER_ID = os.environ.get("OWNER_ID", "")          # Telegram user key of the owner, e.g. "tg123456789"
OWNER_KEY = os.environ.get("OWNER_KEY", "")        # secret in the owner's personal panel link (startapp param)
ADSGRAM_BLOCK_ID = os.environ.get("ADSGRAM_BLOCK_ID", "")  # set when the AdsGram block is live
MONETAG_POSTBACK_SECRET = os.environ.get("MONETAG_POSTBACK_SECRET", "")  # long random string, set in Monetag postback URL + env
MONETAG_ZONE_ID = os.environ.get("MONETAG_ZONE_ID", "12248440")

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
            created_at REAL, decided_at REAL,
            holder_name TEXT DEFAULT '', bank_name TEXT DEFAULT '', txn_id TEXT DEFAULT '',
            utr TEXT DEFAULT '', payment_date TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS seen_starts(
            tg_id TEXT PRIMARY KEY, username TEXT, first_name TEXT, ts REAL
        );
        CREATE TABLE IF NOT EXISTS promo_codes(
            code TEXT PRIMARY KEY, coins INTEGER, max_uses INTEGER DEFAULT 0,
            used_count INTEGER DEFAULT 0, active INTEGER DEFAULT 1, created_at REAL);
        CREATE TABLE IF NOT EXISTS promo_redemptions(
            code TEXT, user_id TEXT, created_at REAL, UNIQUE(code, user_id));
        CREATE TABLE IF NOT EXISTS referral_reviews(
            id INTEGER PRIMARY KEY AUTOINCREMENT, referrer_id TEXT, referred_id TEXT UNIQUE,
            status TEXT DEFAULT 'pending', created_at REAL, decided_at REAL
        );
        CREATE TABLE IF NOT EXISTS ad_sessions(
            token TEXT PRIMARY KEY, user_id TEXT, created_at REAL, used INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS ad_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, event TEXT, detail TEXT, ts REAL
        );
        CREATE TABLE IF NOT EXISTS monetag_postbacks(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ymid TEXT, event_type TEXT,
            reward_event_type TEXT, zone_id TEXT, estimated_price TEXT,
            telegram_id TEXT, credited INTEGER DEFAULT 0, ts REAL,
            UNIQUE(ymid, event_type)
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
        CREATE TABLE IF NOT EXISTS support_tickets(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
            category TEXT NOT NULL, message TEXT NOT NULL, status TEXT DEFAULT 'open',
            reply TEXT DEFAULT '', reply_at REAL,
            created_at REAL NOT NULL, updated_at REAL
        );
        CREATE TABLE IF NOT EXISTS warnings(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
            message TEXT NOT NULL, ts REAL NOT NULL, seen INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS game_progress(
            user_id TEXT PRIMARY KEY, xp INTEGER NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS game_daily_claims(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
            game_id TEXT NOT NULL, game_date TEXT NOT NULL, xp INTEGER NOT NULL,
            created_at REAL NOT NULL, UNIQUE(user_id, game_id, game_date)
        );
        CREATE TABLE IF NOT EXISTS rocket_rounds(
            round_id INTEGER PRIMARY KEY, start_ts REAL, fly_start REAL,
            crash_point REAL, crash_ts REAL, end_ts REAL
        );
        CREATE TABLE IF NOT EXISTS rocket_bets(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
            round_id INTEGER NOT NULL, amount_coins INTEGER NOT NULL,
            amount_paise INTEGER DEFAULT 0,
            avail_used_coins INTEGER DEFAULT 0, win_used_paise INTEGER DEFAULT 0,
            cashed_out INTEGER DEFAULT 0, cashout_mult REAL,
            win_coins INTEGER DEFAULT 0, win_paise INTEGER DEFAULT 0,
            created_at REAL, cashed_at REAL,
            UNIQUE(user_id, round_id)
        );
        """)
        # Earning columns added after the first release: add them to old DBs too.
        cols = {r[1] for r in c.execute("PRAGMA table_info(users)")}
        for col, ddl in [("cash_paise", "ALTER TABLE users ADD COLUMN cash_paise INTEGER DEFAULT 0"),
                         ("ads_today", "ALTER TABLE users ADD COLUMN ads_today INTEGER DEFAULT 0"),
                         ("ads_date", "ALTER TABLE users ADD COLUMN ads_date TEXT"),
                         ("referred_qualified", "ALTER TABLE users ADD COLUMN referred_qualified INTEGER DEFAULT 0"),
                         ("last_ad_at", "ALTER TABLE users ADD COLUMN last_ad_at REAL DEFAULT 0"),
                         ("username", "ALTER TABLE users ADD COLUMN username TEXT DEFAULT ''"),
                         ("first_name", "ALTER TABLE users ADD COLUMN first_name TEXT DEFAULT ''"),
                         ("ip_fp", "ALTER TABLE users ADD COLUMN ip_fp TEXT DEFAULT ''"),
                         ("banned", "ALTER TABLE users ADD COLUMN banned INTEGER DEFAULT 0"),
                         ("ban_reason", "ALTER TABLE users ADD COLUMN ban_reason TEXT DEFAULT ''"),
                         ("ban_override", "ALTER TABLE users ADD COLUMN ban_override INTEGER DEFAULT 0"),
                         ("suspicious", "ALTER TABLE users ADD COLUMN suspicious INTEGER DEFAULT 0"),
                         ("suspicious_reason", "ALTER TABLE users ADD COLUMN suspicious_reason TEXT DEFAULT ''"),
                         ("spins_today", "ALTER TABLE users ADD COLUMN spins_today INTEGER DEFAULT 0"),
                         ("spins_date", "ALTER TABLE users ADD COLUMN spins_date TEXT DEFAULT ''"),
                         ("spin_credits", "ALTER TABLE users ADD COLUMN spin_credits INTEGER DEFAULT 0"),
                         ("avail_coins", "ALTER TABLE users ADD COLUMN avail_coins INTEGER DEFAULT 0"),
                         ("winning_paise", "ALTER TABLE users ADD COLUMN winning_paise INTEGER DEFAULT 0")]:
            if col not in cols:
                c.execute(ddl)
        tcols = {r[1] for r in c.execute("PRAGMA table_info(tasks)")}
        for col, ddl in [("budget_coins", "ALTER TABLE tasks ADD COLUMN budget_coins INTEGER DEFAULT 0"),
                         ("spent_coins", "ALTER TABLE tasks ADD COLUMN spent_coins INTEGER DEFAULT 0"),
                         ("category", "ALTER TABLE tasks ADD COLUMN category TEXT DEFAULT 'telegram'"),
                         ("verify_mode", "ALTER TABLE tasks ADD COLUMN verify_mode TEXT DEFAULT 'proof'")]:
            if col not in tcols:
                c.execute(ddl)
        scols = {r[1] for r in c.execute("PRAGMA table_info(support_tickets)")}
        for col, ddl in [("reply", "ALTER TABLE support_tickets ADD COLUMN reply TEXT DEFAULT ''"),
                         ("reply_at", "ALTER TABLE support_tickets ADD COLUMN reply_at REAL")]:
            if col not in scols:
                c.execute(ddl)
        wcols = {r[1] for r in c.execute("PRAGMA table_info(withdrawals)")}
        for col, ddl in [("holder_name", "ALTER TABLE withdrawals ADD COLUMN holder_name TEXT DEFAULT ''"),
                         ("bank_name", "ALTER TABLE withdrawals ADD COLUMN bank_name TEXT DEFAULT ''"),
                         ("txn_id", "ALTER TABLE withdrawals ADD COLUMN txn_id TEXT DEFAULT ''"),
                         ("utr", "ALTER TABLE withdrawals ADD COLUMN utr TEXT DEFAULT ''"),
                         ("payment_date", "ALTER TABLE withdrawals ADD COLUMN payment_date TEXT DEFAULT ''")]:
            if col not in wcols:
                c.execute(ddl)
        scols = {r[1] for r in c.execute("PRAGMA table_info(ad_sessions)")}
        if "ymid" not in scols:
            c.execute("ALTER TABLE ad_sessions ADD COLUMN ymid TEXT DEFAULT ''")
        rbcols = {r[1] for r in c.execute("PRAGMA table_info(rocket_bets)")}
        if "amount_paise" not in rbcols:
            c.execute("ALTER TABLE rocket_bets ADD COLUMN amount_paise INTEGER DEFAULT 0")
            # purani coin-bets ko paise me badlo (10 coins = 1 paisa)
            c.execute("UPDATE rocket_bets SET amount_paise=amount_coins/10 WHERE amount_paise=0")
        for col in ("avail_used_coins", "win_used_paise"):
            if col not in rbcols:
                c.execute(f"ALTER TABLE rocket_bets ADD COLUMN {col} INTEGER DEFAULT 0")
        # v30 migration: old cash wallet was created by manually converting coins.
        # Move that value back into coins once, preserving total user value.
        for row in c.execute("SELECT user_id, cash_paise FROM users WHERE COALESCE(cash_paise,0)>0").fetchall():
            restored = int(row["cash_paise"]) * 10  # 10 coins per paisa at 1,000 coins = Rs 1
            c.execute("UPDATE users SET coins=coins+?, cash_paise=0 WHERE user_id=?", (restored, row["user_id"]))
            c.execute("INSERT INTO ledger(user_id,kind,coins,wood,crystals,note,ts) VALUES(?,?,?,?,?,?,?)",
                      (row["user_id"], "wallet_migration", restored, 0, 0,
                       "v30 wallet migration: legacy cash converted back to canonical coins", time.time()))

def get_user(c, user_id):
    row = c.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    if row:
        uname = getattr(g, "tg_username", "")
        fname = getattr(g, "tg_first_name", "")
        new_uname = uname if uname and row["username"] != uname else row["username"]
        new_fname = fname if fname and row["first_name"] != fname else row["first_name"]
        if new_uname != row["username"] or new_fname != row["first_name"]:
            c.execute("UPDATE users SET username=?, first_name=? WHERE user_id=?",
                      (new_uname, new_fname, user_id))
            row = c.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        return row
    code = "SLIME-" + secrets.token_hex(3).upper()
    now = time.time()
    c.execute("INSERT INTO users(user_id,last_settle,referral_code,quest_date,created_at) VALUES(?,?,?,?,?)",
              (user_id, now, code, str(date.today()), now))
    _uname = getattr(g, "tg_username", "")
    _fname = getattr(g, "tg_first_name", "")
    if _uname or _fname:
        c.execute("UPDATE users SET username=?, first_name=? WHERE user_id=?",
                  (_uname, _fname, user_id))
    for bid, lvl in START_LEVELS.items():
        c.execute("INSERT INTO buildings VALUES(?,?,?)", (user_id, bid, lvl))
    c.execute("INSERT INTO ledger(user_id,kind,note,ts) VALUES(?,?,?,?)",
              (user_id, "founding", "Village founded: Goblin Hut + Town Hall ready", now))
    if WELCOME_BONUS_COINS > 0:
        c.execute("UPDATE users SET coins=coins+?, lifetime_coins=lifetime_coins+? WHERE user_id=?",
                  (WELCOME_BONUS_COINS, WELCOME_BONUS_COINS, user_id))
        c.execute("INSERT INTO ledger(user_id,kind,coins,wood,crystals,note,ts) VALUES(?,?,?,?,?,?,?)",
                  (user_id, "welcome_bonus", WELCOME_BONUS_COINS, 0, 0,
                   "New user welcome bonus", now))
    g.just_created = True   # v53: this request created the account -> the
                            # /api/state payload flags it so the client shows
                            # the one-time welcome popup to NEW users only
    return c.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()

def display_name(c, user_id):
    """Best human label for a user: Telegram first name (falling back to the
    /start record in seen_starts), combined with @username when both exist;
    '' when neither is known."""
    r = c.execute(
        "SELECT COALESCE(NULLIF(first_name,''), "
        "(SELECT s.first_name FROM seen_starts s WHERE s.tg_id=users.user_id), '') fn, "
        "COALESCE(NULLIF(username,''), "
        "(SELECT s.username FROM seen_starts s WHERE s.tg_id=users.user_id), '') un "
        "FROM users WHERE user_id=?", (user_id,)).fetchone()
    if not r:
        return ""
    if r["fn"] and r["un"]:
        return r["fn"] + " (@" + r["un"] + ")"
    return r["fn"] or (("@" + r["un"]) if r["un"] else "")


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

def _spins_used_today(c, user_id):
    """v56: total spins taken today (free + ad-credit), counted from the
    ledger's 'spin' prize rows so the daily cap needs no extra column."""
    start = time.mktime(date.today().timetuple())
    return c.execute("SELECT COUNT(*) n FROM ledger WHERE user_id=? AND kind='spin' AND ts>=?",
                     (user_id, start)).fetchone()["n"]


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

def _daily_cycle_day(user):
    """Which day of the 7-day cycle today's claim belongs to (1..7)."""
    today = str(date.today())
    yday = str(date.today() - timedelta(days=1))
    if user["last_daily"] == today:
        ns = user["streak"] or 1
    elif user["last_daily"] == yday:
        ns = (user["streak"] or 0) + 1
    else:
        ns = 1
    return (ns - 1) % 7 + 1

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
    warn = c.execute(
        "SELECT id,message,ts FROM warnings WHERE user_id=? AND seen=0 "
        "ORDER BY id DESC LIMIT 1", (user["user_id"],)).fetchone()
    return {
        "user_id": user["user_id"],
        "just_created": bool(getattr(g, "just_created", False)),
        "warning": ({"id": warn["id"], "message": warn["message"], "ts": warn["ts"]} if warn else None),
        "welcome_bonus_coins": WELCOME_BONUS_COINS,
        "coins": user["coins"], "wood": user["wood"], "crystals": user["crystals"],
        "avail_coins": int(user["avail_coins"] or 0),
        "winning_paise": int(user["winning_paise"] or 0),
        "winning_rupees": round((user["winning_paise"] or 0) / 100, 2),
        "pending": {"coins": int(user["pending_coins"]), "wood": int(user["pending_wood"]),
                    "crystals": int(user["pending_crystals"])},
        "lifetime_coins": user["lifetime_coins"],
        "slime": {"level": lvl, "name": lname, "next_at": nxt},
        "streak": user["streak"],
        "daily_available": user["last_daily"] != today,
        "daily_table": DAILY_REWARDS,
        "daily_cycle_day": _daily_cycle_day(user),
        "spin": {
            "free_left": max(0, SPIN_FREE_PER_DAY - ((user["spins_today"] or 0) if user["spins_date"] == today else 0)),
            "credits": user["spin_credits"] or 0,
            "used_today": _spins_used_today(c, user["user_id"]),
            "daily_limit": SPIN_DAILY_LIMIT,
            "segments": SPIN_SEGMENTS,
            "free_per_day": SPIN_FREE_PER_DAY,
        },
        "referral_code": user["referral_code"], "invites": user["invites"],
        "has_referrer": bool(user["referred_by"]),
        "my_referrals": [
            {"label": ("@" + r["uname"]) if r["uname"] else "Player " + r["user_id"][-4:],
             "q": r["referred_qualified"] or 0}
            for r in c.execute(
                "SELECT u.user_id, u.referred_qualified, COALESCE(NULLIF(u.username,''), "
                "(SELECT s.username FROM seen_starts s WHERE s.tg_id = u.user_id), '') AS uname "
                "FROM users u WHERE u.referred_by=? ORDER BY u.created_at DESC LIMIT 20",
                (user["user_id"],))
        ],
        "squad_boost_pct": min(user["invites"] * 5, 50),
        "buildings": buildings, "quests": quests,
        "recipes": [{"id": k, **v} for k, v in RECIPES.items()],
        "cash_paise": int(user["coins"] or 0) // 10,
        "cash_rupees": round((user["coins"] or 0) / COINS_PER_RUPEE, 2),
        "coin_value_rupees": round((user["coins"] or 0) / COINS_PER_RUPEE, 2),
        "players": c.execute("SELECT COUNT(*) n FROM users WHERE user_id != 'owner'").fetchone()["n"],
        "pending_rewards_coins": c.execute(
            "SELECT COALESCE(SUM(t.reward_coins),0) s FROM task_submissions s "
            "JOIN tasks t ON t.id = s.task_id WHERE s.user_id=? AND s.status='pending'",
            (user["user_id"],)).fetchone()["s"],
        "is_owner": bool(OWNER_ID) and user["user_id"] == OWNER_ID,
        "earn": {
            "coins_per_rupee": COINS_PER_RUPEE,
            "convert_min_coins": CONVERT_MIN_COINS,
            "min_withdraw_rupees": _min_withdraw_for(c, user["user_id"]),
            "ad_reward_coins": AD_REWARD_COINS,
            "ad_daily_limit": AD_DAILY_LIMIT,
            "ads_today": (user["ads_today"] or 0) if user["ads_date"] == today else 0,
            "ad_cooldown_left": max(0, int(AD_COOLDOWN_SEC - (time.time() - (user["last_ad_at"] or 0)))),
            "ads_live": bool(ADSGRAM_BLOCK_ID),
            "referral_reward_coins": REFERRAL_REWARD_COINS,
            "payouts_enabled": PAYOUTS_ENABLED,
        },
        "leaderboard": [
            {"label": ("@" + r["uname"]) if r["uname"] else "Player " + r["user_id"][-4:],
             "coins": r["lifetime_coins"], "level": slime_level(r["lifetime_coins"])[0]}
            for r in c.execute(
                "SELECT u.user_id, u.lifetime_coins, COALESCE(NULLIF(u.username,''), "
                "(SELECT s.username FROM seen_starts s WHERE s.tg_id = u.user_id), '') AS uname "
                "FROM users u WHERE u.user_id != 'owner' "
                "ORDER BY u.lifetime_coins DESC, u.created_at ASC LIMIT 10")
        ],
        "recent_payouts": [
            {"amount_rupees": round(w["amount_paise"] / 100, 2), "upi": mask_upi(w["upi_id"]),
             "txn_id": w["txn_id"] or "", "utr": w["utr"] or "",
             "payment_date": w["payment_date"] or "",
             "ts": w["decided_at"] or w["created_at"]}
            for w in c.execute("SELECT * FROM withdrawals WHERE status='paid' ORDER BY id DESC LIMIT 10")
        ],
        "payouts_paid_count": c.execute(
            "SELECT COUNT(*) n FROM withdrawals WHERE status='paid'").fetchone()["n"],
        "payouts_paid_rupees": round(c.execute(
            "SELECT COALESCE(SUM(amount_paise),0) s FROM withdrawals WHERE status='paid'"
        ).fetchone()["s"] / 100, 2),
        "my_withdrawals": [
            {"id": w["id"], "amount_rupees": round(w["amount_paise"] / 100, 2),
             "upi_id": w["upi_id"], "status": w["status"],
             "holder_name": w["holder_name"] or "", "bank_name": w["bank_name"] or "",
             "txn_id": w["txn_id"] or "", "utr": w["utr"] or "",
             "payment_date": w["payment_date"] or ""}
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

def adlog(c, user_id, event, detail=""):
    """Diagnostic trail for ad starts/claims (owner-only readout), so a
    client whose reward never arrives can be traced server-side."""
    try:
        c.execute("INSERT INTO ad_log(user_id,event,detail,ts) VALUES(?,?,?,?)",
                  (user_id, event, detail, time.time()))
    except Exception:
        pass

def cash_log(c, user_id, delta_paise, kind, note):
    """Every rupee movement gets a cash_ledger row (owner's ledger rule:
    the cash balance is never edited without an audit entry)."""
    c.execute("INSERT INTO cash_ledger(user_id,delta_paise,kind,note,ts) VALUES(?,?,?,?,?)",
              (user_id, delta_paise, kind, note, time.time()))

def mask_upi(upi):
    """Public payout proof must never expose a full UPI id."""
    local, _, domain = (upi or "").partition("@")
    head = local[:2] if len(local) > 2 else local[:1]
    return f"{head}***@{domain}" if domain else "***"

def task_budget_left(t):
    """Budget coins still available on a task; None means unlimited."""
    if not t["budget_coins"]:
        return None
    return t["budget_coins"] - (t["spent_coins"] or 0)

def _client_fp():
    """Keyed fingerprint of the client network. The raw IP is NEVER stored —
    only an HMAC of it, which can link accounts from one network without
    keeping anyone's address."""
    xff = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    ip = xff or (request.remote_addr or "")
    if not ip:
        return ""
    return hmac.new((BOT_TOKEN or "slimefi").encode(), ip.encode(),
                    hashlib.sha256).hexdigest()[:32]

def _detect_multi_account(c, user):
    """Referral Security rule (owner, revised 2026-11-12): a shared network
    (IP) alone NEVER makes anyone guilty. A newer account arriving from the
    same network as an older one is only FLAGGED suspicious for the owner's
    review — it is not banned and nothing is blocked. The owner is exempt."""
    if user["user_id"] == "owner" or (OWNER_ID and user["user_id"] == OWNER_ID):
        return
    if user["ban_override"]:
        return  # the owner personally cleared this account; never auto-ban it again
    fp = _client_fp()
    if not fp:
        return
    if user["ip_fp"] != fp:
        c.execute("UPDATE users SET ip_fp=? WHERE user_id=?", (fp, user["user_id"]))
    other = c.execute(
        "SELECT user_id, created_at FROM users WHERE ip_fp=? AND user_id != ? AND banned=0 "
        "ORDER BY created_at ASC LIMIT 1", (fp, user["user_id"])).fetchone()
    if other and (user["created_at"] or 0) > (other["created_at"] or 0):
        c.execute("UPDATE users SET suspicious=1, suspicious_reason='Same network (IP) as an older account — sirf network match hai, jaanch baki hai' WHERE user_id=?",
                  (user["user_id"],))

def _pay_referral(c, user, inv):
    c.execute("UPDATE users SET referred_qualified=1 WHERE user_id=?", (user["user_id"],))
    c.execute("UPDATE users SET coins=coins+?, lifetime_coins=lifetime_coins+? WHERE user_id=?",
              (REFERRAL_REWARD_COINS, REFERRAL_REWARD_COINS, inv["user_id"]))
    log(c, inv["user_id"], "referral", {"coins": REFERRAL_REWARD_COINS},
        "Referral reward: dost ne pehli qualifying activity puri ki")

def maybe_qualify_referral(c, user):
    """Referral rule (owner, 2026-11-12): the inviter earns REFERRAL_REWARD_COINS
    only when the referred user completes a first qualifying activity — never
    on join alone. Runs at most once per referred user. If inviter and
    referred share one network, the reward is HELD for the owner's review
    (referral_reviews) instead of being paid automatically."""
    if not user["referred_by"] or user["referred_qualified"]:
        return
    inv = c.execute("SELECT * FROM users WHERE user_id=?", (user["referred_by"],)).fetchone()
    if not inv:
        c.execute("UPDATE users SET referred_qualified=1 WHERE user_id=?", (user["user_id"],))
        return
    if user["ip_fp"] and inv["ip_fp"] and user["ip_fp"] == inv["ip_fp"]:
        c.execute("UPDATE users SET referred_qualified=2 WHERE user_id=?", (user["user_id"],))
        c.execute("INSERT OR IGNORE INTO referral_reviews(referrer_id,referred_id,status,created_at) "
                  "VALUES(?,?,'pending',?)", (inv["user_id"], user["user_id"], time.time()))
        return
    _pay_referral(c, user, inv)

# ---------------- routes ----------------


# ---------------- Games Center (XP-only launch phase) ----------------
GAME_XP = {"puzzle": 20, "quiz": 25}

@app.get("/api/games/state")
def games_state():
    user_id = uid()
    today = str(date.today())
    with db() as c:
        row = c.execute("SELECT xp FROM game_progress WHERE user_id=?", (user_id,)).fetchone()
        xp = int(row["xp"] if row else 0)
        claims = c.execute("SELECT game_id FROM game_daily_claims WHERE user_id=? AND game_date=?", (user_id, today)).fetchall()
    claimed = [r["game_id"] for r in claims]
    return jsonify({"ok": True, "xp": xp, "level": 1 + xp // 100, "claimed_today": claimed,
                    "games": [{"id":"puzzle", "xp":GAME_XP["puzzle"]}, {"id":"quiz", "xp":GAME_XP["quiz"]}]})

@app.post("/api/games/complete")
def games_complete():
    data = request.get_json(silent=True) or {}
    game_id = str(data.get("game_id") or "")
    if game_id not in GAME_XP:
        return jsonify({"ok": False, "msg": "Game valid nahi hai"}), 400
    user_id = uid()
    today = str(date.today())
    award = GAME_XP[game_id]
    with _lock, db() as c:
        try:
            c.execute("INSERT INTO game_daily_claims(user_id,game_id,game_date,xp,created_at) VALUES(?,?,?,?,?)",
                      (user_id, game_id, today, award, time.time()))
        except sqlite3.IntegrityError:
            row = c.execute("SELECT xp FROM game_progress WHERE user_id=?", (user_id,)).fetchone()
            return jsonify({"ok": False, "msg": "Aaj ka reward pehle hi claim ho chuka hai", "xp": int(row["xp"] if row else 0)})
        c.execute("INSERT INTO game_progress(user_id,xp,updated_at) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET xp=xp+excluded.xp, updated_at=excluded.updated_at",
                  (user_id, award, time.time()))
        xp = c.execute("SELECT xp FROM game_progress WHERE user_id=?", (user_id,)).fetchone()["xp"]
    return jsonify({"ok": True, "xp_awarded": award, "xp": int(xp), "level": 1 + int(xp)//100,
                    "msg": f"+{award} XP added! Coins balance change nahi hua."})

@app.get("/api/health")
def health():
    return jsonify({"ok": True, "game": "SlimeFi", "ts": time.time()})

# ---------------- Slime Rocket LIVE rounds (v70) ----------------
# Ek hi round pure server par chalta hai — sab phones par plane ek hi time
# par, ek hi tarah udta hai aur ek hi point par crash hota hai. Crash point
# owner ki table se (2026-11-18): 1.0x par crash nahi (min 1.01), 1.1–1.7
# purane rate, aage nayi list, 50x tak kul 100%. Crash point round pehle se
# HMAC-seeded hai — client ko crash se pehle kabhi nahi bheja jata.
ROCKET_GROWTH = 0.11          # multiplier = e^(0.11 * seconds), client se match (v74: slow — 1.5x ~4s, 2x ~6s)
ROCKET_BET_SEC = 6.0          # flight se pehle betting / countdown samay
ROCKET_RESULT_SEC = 4.0       # crash ke baad result pause
ROCKET_TABLE = [
    (1.10, 0.20), (1.20, 0.35), (1.30, 0.50), (1.40, 0.52), (1.50, 0.55),
    (1.70, 0.70), (2.00, 0.78), (2.30, 0.79), (2.80, 0.83), (3.00, 0.85),
    (4.00, 0.88), (4.50, 0.90), (5.00, 0.92), (6.00, 0.93), (7.00, 0.94),
    (10.00, 0.95), (13.00, 0.96), (15.00, 0.97), (20.00, 0.98), (50.00, 1.00),
]

def _rocket_crash_point(round_id):
    salt = MONETAG_POSTBACK_SECRET or OWNER_KEY or "slimefi-rocket-dev"
    digest = hmac.new(salt.encode(), ("rocket:%d" % round_id).encode(), hashlib.sha256).digest()
    u = int.from_bytes(digest[:8], "big") / float(1 << 64)
    prev_x, prev_p = 1.00, 0.0
    for x, p in ROCKET_TABLE:
        if u < p:
            c = prev_x + (u - prev_p) / (p - prev_p) * (x - prev_x)
            return min(max(c, 1.01), 50.0)
        prev_x, prev_p = x, p
    return 50.0

def _rocket_new_round(c, round_id, start_ts):
    cp = _rocket_crash_point(round_id)
    fly_start = start_ts + ROCKET_BET_SEC
    crash_ts = fly_start + math.log(cp) / ROCKET_GROWTH
    end_ts = crash_ts + ROCKET_RESULT_SEC
    c.execute("INSERT INTO rocket_rounds(round_id, start_ts, fly_start, crash_point, crash_ts, end_ts) VALUES(?,?,?,?,?,?)",
              (round_id, start_ts, fly_start, cp, crash_ts, end_ts))
    return {"round_id": round_id, "start_ts": start_ts, "fly_start": fly_start,
            "crash_point": cp, "crash_ts": crash_ts, "end_ts": end_ts}

def _rocket_current(c, now):
    row = c.execute("SELECT * FROM rocket_rounds ORDER BY round_id DESC LIMIT 1").fetchone()
    if row is None:
        return _rocket_new_round(c, 1, now)
    cur = dict(row)
    if now < cur["end_ts"]:
        return cur
    if now - cur["end_ts"] > 300:   # lambi khamoshi — chain abhi se phir shuru
        return _rocket_new_round(c, cur["round_id"] + 1, now)
    nxt_id, nxt_start = cur["round_id"], cur["end_ts"]
    while True:
        nxt = _rocket_new_round(c, nxt_id + 1, nxt_start)
        if now < nxt["end_ts"]:
            return nxt
        nxt_id, nxt_start = nxt["round_id"], nxt["end_ts"]

@app.get("/api/rocket/state")
def rocket_state():
    now = time.time()
    with _lock, db() as c:
        cur = _rocket_current(c, now)
        hist = [round(r["crash_point"], 2) for r in c.execute(
            "SELECT crash_point FROM rocket_rounds WHERE crash_ts<=? ORDER BY round_id DESC LIMIT 12",
            (now,)).fetchall()]
    if now < cur["fly_start"]:
        phase = "betting"
    elif now < cur["crash_ts"]:
        phase = "flying"
    else:
        phase = "crashed"
    out = {"ok": True, "server_now": now, "round_id": cur["round_id"], "phase": phase,
           "round_start": cur["start_ts"], "fly_start": cur["fly_start"],
           "growth": ROCKET_GROWTH, "bet_sec": ROCKET_BET_SEC, "history": hist,
           "live_bets": [
               {"name": (("@" + r["username"]) if r["username"] else ("Player " + (r["user_id"] or "")[-4:])),
                "amount_paise": int(r["amount_paise"] or 0),
                "cashed_out": int(r["cashed_out"] or 0),
                "cashout_mult": round(r["cashout_mult"], 2) if r["cashout_mult"] else None,
                "win_paise": int(r["win_paise"] or 0)}
               for r in c.execute(
                   "SELECT b.amount_paise, b.cashed_out, b.cashout_mult, b.win_paise, "
                   "b.user_id, u.username FROM rocket_bets b LEFT JOIN users u "
                   "ON u.user_id=b.user_id WHERE b.round_id=? ORDER BY b.created_at DESC LIMIT 8",
                   (cur["round_id"],)).fetchall()]}
    if phase == "crashed":
        out["crash_point"] = round(cur["crash_point"], 2)
        out["round_end"] = cur["end_ts"]
    return jsonify(out)

@app.post("/api/rocket/bet")
def rocket_bet():
    """Rocket bet — ₹ me (amount_paise me bhejo). Stake betting phase me hi
    lagti hai. Paisa pehle Available Balance (avail_coins) se kat-ta hai;
    Available khatam hone par baki Winning Balance (winning_paise) se.
    Gaon wale coins se Rocket ka koi lena-dena nahi — coins sirf Convert se
    ₹ (Available Balance) me badle jate hain. Jeet (cash out par) Winning
    Balance (winning_paise) me jati hai."""
    data = request.get_json(silent=True) or {}
    try:
        amt_paise = int(data.get("amount_paise") or 0)
    except (TypeError, ValueError):
        amt_paise = 0
    try:
        round_id = int(data.get("round_id") or 0)
    except (TypeError, ValueError):
        round_id = 0
    if amt_paise < 1:
        return jsonify({"ok": False, "msg": "Bet kam se kam ₹0.01 ki hoti hai."})
    amt_coins = amt_paise * 10  # 10 coins = 1 paisa (1000 coins = Rs 1)
    now = time.time()
    with _lock, db() as c:
        u = roll_day(c, get_user(c, uid()))
        cur = _rocket_current(c, now)
        if round_id != cur["round_id"] or now >= cur["fly_start"]:
            return jsonify({"ok": False, "msg": "Is round me bet band ho gayi — agle round ka wait karo."})
        old = c.execute("SELECT * FROM rocket_bets WHERE user_id=? AND round_id=?",
                        (u["user_id"], round_id)).fetchone()
        if old:
            return jsonify({"ok": True, "already": True,
                            "amount_paise": int(old["amount_paise"] or 0),
                            "amount_coins": old["amount_coins"],
                            "state": state_payload(c, u)})
        # Waterfall: pehle Available Balance, phir Winning Balance.
        # avail_use ko 10 ke multiple par rakho taaki paise ka hisaab exact rahe.
        avail = int(u["avail_coins"] or 0)
        win_p = int(u["winning_paise"] or 0)
        if amt_paise > avail // 10 + win_p:
            return jsonify({"ok": False, "msg": "Available + Winning Balance me itne paise nahi hain. Pehle Convert Coins se coins ko ₹ me badlo."})
        avail_use = min(avail // 10 * 10, amt_coins)
        win_use = (amt_coins - avail_use) // 10
        c.execute("UPDATE users SET avail_coins=avail_coins-?, winning_paise=winning_paise-? WHERE user_id=?",
                  (avail_use, win_use, u["user_id"]))
        c.execute("INSERT INTO rocket_bets(user_id,round_id,amount_coins,amount_paise,avail_used_coins,win_used_paise,created_at) VALUES(?,?,?,?,?,?,?)",
                  (u["user_id"], round_id, amt_coins, amt_paise, avail_use, win_use, now))
        log(c, u["user_id"], "rocket_bet", {"coins": 0},
            f"Rocket round #{round_id}: ₹{amt_paise/100:.2f} ki bet (Available ₹{avail_use/1000:.2f} + Winning ₹{win_use/100:.2f})")
        u = c.execute("SELECT * FROM users WHERE user_id=?", (u["user_id"],)).fetchone()
        return jsonify({"ok": True, "amount_paise": amt_paise, "amount_coins": amt_coins,
                        "state": state_payload(c, u)})

@app.post("/api/rocket/cancel")
def rocket_cancel():
    """Bet wapas lo — sirf betting phase me, plane udne se pehle.
    Stake usi hisaab se wapas jata hai jis se kata tha (Available + Winning)."""
    data = request.get_json(silent=True) or {}
    try:
        round_id = int(data.get("round_id") or 0)
    except (TypeError, ValueError):
        round_id = 0
    now = time.time()
    with _lock, db() as c:
        u = roll_day(c, get_user(c, uid()))
        cur = _rocket_current(c, now)
        if round_id != cur["round_id"] or now >= cur["fly_start"]:
            return jsonify({"ok": False, "msg": "Ab cancel nahi ho sakta — plane ud chuka hai."})
        row = c.execute("SELECT * FROM rocket_bets WHERE user_id=? AND round_id=?",
                        (u["user_id"], round_id)).fetchone()
        if not row:
            return jsonify({"ok": True, "cancelled": False, "state": state_payload(c, u)})
        amt_paise = int(row["amount_paise"] or 0)
        amt_coins = int(row["amount_coins"] or amt_paise * 10)
        avail_use = int(row["avail_used_coins"] or 0)
        win_use = int(row["win_used_paise"] or 0)
        # Purani rows (split nahi likha) — sab Available Balance me wapas.
        if avail_use == 0 and win_use == 0 and amt_coins > 0:
            avail_use = amt_coins
        c.execute("UPDATE users SET avail_coins=avail_coins+?, winning_paise=winning_paise+? WHERE user_id=?",
                  (avail_use, win_use, u["user_id"]))
        c.execute("DELETE FROM rocket_bets WHERE id=?", (row["id"],))
        log(c, u["user_id"], "rocket_cancel", {"coins": 0},
            f"Rocket round #{round_id}: ₹{amt_paise/100:.2f} ki bet cancel — refund (Available ₹{avail_use/1000:.2f} + Winning ₹{win_use/100:.2f})")
        u = c.execute("SELECT * FROM users WHERE user_id=?", (u["user_id"],)).fetchone()
        return jsonify({"ok": True, "cancelled": True, "refunded_paise": amt_paise,
                        "refunded_coins": amt_coins, "state": state_payload(c, u)})

@app.post("/api/rocket/cashout")
def rocket_cashout():
    """Rocket cash out: jeet Winning Balance (winning_paise) me jati hai.
    Bet ₹ me lagi thi — jeet = bet × multiplier (paise me, 10 coins = 1 paisa).
    Server crash point se pehle hi cash out maanta hai — late request reject hoti hai."""
    data = request.get_json(silent=True) or {}
    try:
        round_id = int(data.get("round_id") or 0)
    except (TypeError, ValueError):
        round_id = 0
    now = time.time()
    with _lock, db() as c:
        u = roll_day(c, get_user(c, uid()))
        row = c.execute("SELECT * FROM rocket_bets WHERE user_id=? AND round_id=?",
                        (u["user_id"], round_id)).fetchone()
        if not row:
            return jsonify({"ok": False, "msg": "Is round me tumhari koi bet nahi hai."})
        if row["cashed_out"]:
            return jsonify({"ok": True, "already": True,
                            "mult": row["cashout_mult"], "win_coins": row["win_coins"],
                            "win_paise": row["win_paise"],
                            "win_rupees": round((row["win_paise"] or 0) / 100, 2),
                            "state": state_payload(c, u)})
        cur = _rocket_current(c, now)
        if round_id != cur["round_id"]:
            return jsonify({"ok": False, "late": True, "msg": "Round khatam ho gaya — bet haar gayi."})
        if now < cur["fly_start"]:
            return jsonify({"ok": False, "msg": "Plane abhi uda nahi — thoda wait karo."})
        if now >= cur["crash_ts"]:
            return jsonify({"ok": False, "late": True, "msg": "Der ho gayi — plane crash ho chuka hai."})
        mult = math.exp(ROCKET_GROWTH * (now - cur["fly_start"]))
        mult = min(mult, cur["crash_point"])
        mult = round(mult, 2)  # dikhaya gaya multiplier hi payout wala — 2 decimal par fix
        bet_paise = int(row["amount_paise"] or 0)
        win_paise = int(bet_paise * mult)
        win_coins = win_paise * 10
        c.execute("UPDATE users SET winning_paise=winning_paise+? WHERE user_id=?",
                  (win_paise, u["user_id"]))
        c.execute("UPDATE rocket_bets SET cashed_out=1, cashout_mult=?, win_coins=?, win_paise=?, cashed_at=? WHERE id=?",
                  (round(mult, 2), win_coins, win_paise, now, row["id"]))
        log(c, u["user_id"], "rocket_win", {"coins": 0},
            f"Rocket round #{round_id}: ₹{bet_paise/100:.2f} @ {mult:.2f}x = ₹{win_paise/100:.2f} Winning Balance me")
        c.execute("INSERT INTO cash_ledger(user_id,delta_paise,kind,note,ts) VALUES(?,?,?,?,?)",
                  (u["user_id"], win_paise, "rocket_win",
                   f"Rocket round #{round_id} cash out @ {mult:.2f}x", now))
        u = c.execute("SELECT * FROM users WHERE user_id=?", (u["user_id"],)).fetchone()
        return jsonify({"ok": True, "mult": round(mult, 2), "win_coins": win_coins,
                        "win_paise": win_paise, "win_rupees": round(win_paise / 100, 2),
                        "state": state_payload(c, u)})

@app.get("/api/rocket/mybet")
def rocket_mybet():
    """Is round me meri bet kya hai (page reload ke baad wapas lane ke liye)."""
    try:
        round_id = int(request.args.get("round_id") or 0)
    except (TypeError, ValueError):
        round_id = 0
    with _lock, db() as c:
        u = roll_day(c, get_user(c, uid()))
        row = c.execute("SELECT * FROM rocket_bets WHERE user_id=? AND round_id=?",
                        (u["user_id"], round_id)).fetchone()
        if not row:
            return jsonify({"ok": True, "bet": None, "state": state_payload(c, u)})
        return jsonify({"ok": True, "bet": {
            "round_id": row["round_id"],
            "amount_paise": int(row["amount_paise"] or 0),
            "amount_coins": row["amount_coins"],
            "cashed_out": bool(row["cashed_out"]), "cashout_mult": row["cashout_mult"],
            "win_coins": row["win_coins"], "win_paise": row["win_paise"]},
            "state": state_payload(c, u)})

@app.get("/api/state")
def state():
    with _lock, db() as c:
        u = roll_day(c, get_user(c, uid()))
        _detect_multi_account(c, u)
        u = c.execute("SELECT * FROM users WHERE user_id=?", (u["user_id"],)).fetchone() or u
        if u["banned"]:
            return jsonify({"ok": True, "banned": True, "ban_notice":
                            "MULTIPLE ACCOUNTS DETECTED\n\n"
                            "Tumhare ek se zyada SlimeFi accounts mile hain. Niyam ke hisaab se "
                            "sirf pehla (main) account chal sakta hai — ye extra account band kar "
                            "diya gaya hai.\n\nAgar ye galti se hua hai, to official channel par "
                            "support se baat karo."})
        return jsonify(state_payload(c, u))

@app.post("/api/collect")
def collect():
    return jsonify({"ok": False, "msg": "Village Game v30 se hata diya gaya hai."}), 410

@app.post("/api/upgrade")
def upgrade():
    return jsonify({"ok": False, "msg": "Village Game v30 se hata diya gaya hai."}), 410

@app.post("/api/refine")
def refine():
    return jsonify({"ok": False, "msg": "Village Game v30 se hata diya gaya hai."}), 410

@app.post("/api/daily")
def daily():
    today = str(date.today())
    with _lock, db() as c:
        u = roll_day(c, get_user(c, uid()))
        if u["last_daily"] == today:
            return jsonify({"ok": False, "msg": "Aaj ka bonus le liya hai. Kal phir aana."})
        yday = str(date.today() - timedelta(days=1))
        streak = (u["streak"] or 0) + 1 if u["last_daily"] == yday else 1
        cycle_day = (streak - 1) % 7 + 1
        reward = DAILY_REWARDS[cycle_day - 1]
        c.execute("""UPDATE users SET coins=coins+?, lifetime_coins=lifetime_coins+?,
                     last_daily=?, streak=? WHERE user_id=?""",
                  (reward, reward, today, streak, u["user_id"]))
        log(c, u["user_id"], "daily", {"coins": reward},
            f"Daily bonus day {cycle_day} (streak {streak})")
        maybe_qualify_referral(c, u)
        u = c.execute("SELECT * FROM users WHERE user_id=?", (u["user_id"],)).fetchone()
        return jsonify({"ok": True, "reward": reward,
                        "msg": f"Day {cycle_day} bonus mila: {reward} coins! Streak: {streak} din",
                        "state": state_payload(c, u)})

@app.post("/api/quest/claim")
def quest_claim():
    return jsonify({"ok": False, "msg": "Village Game v30 se hata diya gaya hai."}), 410

@app.post("/api/referral/apply")
def referral_apply():
    data = request.get_json(silent=True) or {}
    code = (data.get("code") or "").strip().upper()
    with _lock, db() as c:
        u = get_user(c, uid())
        if u["referred_by"]:
            return jsonify({"ok": False, "msg": "Referral code pehle hi lag chuka hai"})
        if time.time() - (u["created_at"] or 0) > 48 * 3600:
            return jsonify({"ok": False, "msg": "Referral code sirf account banne ke 48 ghante ke andar lag sakta hai — tumhara samay nikal gaya"})
        inv = c.execute("SELECT * FROM users WHERE referral_code=?", (code,)).fetchone()
        if not inv or inv["user_id"] == u["user_id"]:
            return jsonify({"ok": False, "msg": "Code sahi nahi hai"})
        if (inv["created_at"] or 0) > (u["created_at"] or 0):
            return jsonify({"ok": False, "msg": "Ye code tumse naye account ka hai — referral code sirf pehle bane (purane) account ka lag sakta hai"})
        # New rule: no instant coins. The inviter earns REFERRAL_REWARD_COINS
        # only when this user completes a first qualifying activity.
        c.execute("UPDATE users SET referred_by=? WHERE user_id=?",
                  (inv["user_id"], u["user_id"]))
        c.execute("UPDATE users SET invites=invites+1 WHERE user_id=?", (inv["user_id"],))
        log(c, u["user_id"], "referral", {}, "Referral code laga (reward qualifying activity par)")
        u = c.execute("SELECT * FROM users WHERE user_id=?", (u["user_id"],)).fetchone()
        return jsonify({"ok": True, "state": state_payload(c, roll_day(c, u)),
                        "msg": "Referral lag gaya. Pehli activity puri karte hi dost ko reward milega."})

@app.post("/api/clientlog")
def clientlog():
    """Capability beacon from the client (diagnostics): records whether
    the Monetag SDK function exists in this user's webview, so ad-load
    failures on a specific phone can be traced from the server side."""
    data = request.get_json(silent=True) or {}
    with _lock, db() as c:
        u = get_user(c, uid())
        mt = 1 if data.get("monetag") else 0
        ua = (data.get("ua") or "")[:90]
        adlog(c, u["user_id"], "client_check", f"monetag={mt} ua={ua}")
        return jsonify({"ok": True})

# ---------------- earning routes ----------------
@app.post("/api/ad/start")
def ad_start():
    """Issue a one-time token BEFORE an ad is shown. The reward claim must
    carry this exact token: it works once, only for its owner, only after
    a minimum watch time and only within a few minutes — so a bare API
    call, a duplicate claim or a replayed old request earns nothing."""
    today = str(date.today())
    data = request.get_json(silent=True) or {}
    with _lock, db() as c:
        u = roll_day(c, get_user(c, uid()))
        if not ADSGRAM_BLOCK_ID:
            adlog(c, u["user_id"], "start_reject", "no_block")
            return jsonify({"ok": False, "msg": "Ads abhi live nahi hain"})
        watched = (u["ads_today"] or 0) if u["ads_date"] == today else 0
        if watched >= AD_DAILY_LIMIT:
            adlog(c, u["user_id"], "start_reject", "cap")
            return jsonify({"ok": False, "msg": f"Aaj ke {AD_DAILY_LIMIT} ads ho gaye. Kal phir try karo."})
        if (data.get("purpose") == "spin"
                and _spins_used_today(c, u["user_id"]) + (u["spin_credits"] or 0) >= SPIN_DAILY_LIMIT):
            adlog(c, u["user_id"], "start_reject", "spin_cap")
            return jsonify({"ok": False, "msg": f"Aaj ke {SPIN_DAILY_LIMIT} spin pure ho gaye. Kal phir try karo."})
        left = AD_COOLDOWN_SEC - (time.time() - (u["last_ad_at"] or 0))
        if left > 0:
            adlog(c, u["user_id"], "start_reject", f"cooldown {int(left)}")
            return jsonify({"ok": False, "msg": f"Thoda ruko, agla ad {int(left)} sec me milega."})
        c.execute("DELETE FROM ad_sessions WHERE created_at < ?", (time.time() - 3600,))
        token = secrets.token_urlsafe(24)
        # Monetag S2S: the Mini App generates a unique ymid per ad show and
        # passes it here in parallel with calling the SDK, so the later
        # server postback can be matched to this exact session/user.
        ymid = (data.get("ymid") or "").strip()
        if ymid and (len(ymid) > 64 or not re.fullmatch(r"[A-Za-z0-9._-]+", ymid)):
            ymid = ""
        if ymid and c.execute("SELECT 1 FROM ad_sessions WHERE ymid=?", (ymid,)).fetchone():
            ymid = ""
        c.execute("INSERT INTO ad_sessions(token,user_id,created_at,used,ymid) VALUES(?,?,?,0,?)",
                  (token, u["user_id"], time.time(), ymid))
        adlog(c, u["user_id"], "start", f"v={data.get('v')} ymid={ymid}")
        return jsonify({"ok": True, "token": token})

@app.post("/api/ad/claim")
def ad_claim():
    """Claim endpoint for one ad session. A valid one-time token from
    /api/ad/start is mandatory: no token, someone else's token, a reused
    (duplicate/replayed) token, a too-fast claim or an expired token
    earns nothing. Since v34, watch-ad COINS are credited only by the
    Monetag valued postback (owner decision: "sirf valued ad par coin
    do") — a non-spin claim here validates + logs and returns
    credited:false without burning the token. Spin claims
    (purpose="spin") are unchanged: they pay +1 spin credit here."""
    data = request.get_json(silent=True) or {}
    token = (data.get("token") or "").strip()
    today = str(date.today())
    with _lock, db() as c:
        u = roll_day(c, get_user(c, uid()))
        row = c.execute("SELECT * FROM ad_sessions WHERE token=?", (token,)).fetchone() if token else None
        if not row or row["user_id"] != u["user_id"]:
            adlog(c, u["user_id"], "claim_reject", "bad_token")
            return jsonify({"ok": False, "msg": "Reward link sahi nahi hai — naya ad dekh kar try karo"})
        if row["used"]:
            adlog(c, u["user_id"], "claim_reject", "used")
            u2 = c.execute("SELECT * FROM users WHERE user_id=?", (u["user_id"],)).fetchone()
            return jsonify({"ok": False, "msg": "Is ad ka reward pehle hi mil chuka hai",
                            "state": state_payload(c, u2)})
        age = time.time() - (row["created_at"] or 0)
        min_age = AD_MIN_WATCH_SEC if data.get("done") else AD_MIN_OPEN_SEC
        if age < min_age:
            adlog(c, u["user_id"], "claim_reject", f"young age={int(age)} min={min_age} done={1 if data.get('done') else 0}")
            return jsonify({"ok": False, "msg": "Ad pura dekho — itni jaldi reward nahi milta"})
        if age > AD_TOKEN_TTL_SEC:
            adlog(c, u["user_id"], "claim_reject", "expired")
            return jsonify({"ok": False, "msg": "Reward link expire ho gaya — naya ad dekho"})
        watched = (u["ads_today"] or 0) if u["ads_date"] == today else 0
        if watched >= AD_DAILY_LIMIT:
            adlog(c, u["user_id"], "claim_reject", "cap")
            return jsonify({"ok": False, "msg": f"Aaj ke {AD_DAILY_LIMIT} ads ho gaye. Kal phir try karo."})
        left = AD_COOLDOWN_SEC - (time.time() - (u["last_ad_at"] or 0))
        if left > 0:
            adlog(c, u["user_id"], "claim_reject", f"cooldown {int(left)}")
            return jsonify({"ok": False, "msg": f"Thoda ruko, agla ad {int(left)} sec me milega."})
        spin_mode = data.get("purpose") == "spin"
        if not spin_mode:
            # v34 (owner decision, 2026-11-13: "Sirf valued ad par coin
            # do"): a client claim NEVER credits coins for a watch-ad.
            # The only coin path is the Monetag valued postback, which
            # shares this session row — so the token is deliberately NOT
            # burned here and ads_today is untouched; a valued postback
            # that arrives after this claim can still pay exactly once.
            adlog(c, u["user_id"], "claim_nocredit",
                  f"done={1 if data.get('done') else 0} age={int(age)}")
            return jsonify({"ok": True, "credited": False, "reward": 0,
                            "spin_credit": False,
                            "state": state_payload(c, u)})
        cur = c.execute("UPDATE ad_sessions SET used=1 WHERE token=? AND used=0", (token,))
        if cur.rowcount != 1:
            return jsonify({"ok": False, "msg": "Is ad ka reward pehle hi mil chuka hai"})
        # v56 backstop: never grant a spin credit past the daily total.
        if _spins_used_today(c, u["user_id"]) + (u["spin_credits"] or 0) >= SPIN_DAILY_LIMIT:
            adlog(c, u["user_id"], "claim_reject", "spin_cap")
            return jsonify({"ok": False, "msg": f"Aaj ke {SPIN_DAILY_LIMIT} spin pure ho gaye. Kal phir try karo."})
        # Spin page ad: the reward is +1 spin credit, not coins.
        c.execute("""UPDATE users SET spin_credits=spin_credits+1,
                     ads_today=?, ads_date=?, last_ad_at=? WHERE user_id=?""",
                  (watched + 1, today, time.time(), u["user_id"]))
        log(c, u["user_id"], "ad_spin", {"spin": 1}, f"Rewarded ad #{watched + 1} -> +1 spin")
        maybe_qualify_referral(c, u)
        u = c.execute("SELECT * FROM users WHERE user_id=?", (u["user_id"],)).fetchone()
        adlog(c, u["user_id"], "claim_ok", f"done={1 if data.get('done') else 0} age={int(age)} spin=1")
        return jsonify({"ok": True, "reward": 0,
                        "spin_credit": True, "state": state_payload(c, u)})

@app.post("/api/spin")
def spin():
    """Spin Wheel (owner, 2026-11-13): 1 free spin daily; more spins come
    from rewarded ads (spin credits via /api/ad/claim purpose=spin). The
    SERVER picks the prize with fixed weights — the client only animates
    to the segment index it is given, so prizes cannot be faked."""
    today = str(date.today())
    with _lock, db() as c:
        u = roll_day(c, get_user(c, uid()))
        spins_today = (u["spins_today"] or 0) if u["spins_date"] == today else 0
        credits = u["spin_credits"] or 0
        if _spins_used_today(c, u["user_id"]) >= SPIN_DAILY_LIMIT:
            return jsonify({"ok": False,
                            "msg": f"Aaj ke {SPIN_DAILY_LIMIT} spin pure ho gaye — kal phir milege"})
        if credits > 0:
            c.execute("UPDATE users SET spin_credits=spin_credits-1 WHERE user_id=?",
                      (u["user_id"],))
        elif spins_today < SPIN_FREE_PER_DAY:
            c.execute("UPDATE users SET spins_today=?, spins_date=? WHERE user_id=?",
                      (spins_today + 1, today, u["user_id"]))
        else:
            return jsonify({"ok": False,
                            "msg": "Aaj ka free spin ho gaya — aur spin ke liye ad dekho"})
        pick = secrets.randbelow(sum(SPIN_WEIGHTS))
        idx, acc = 0, 0
        for i, w in enumerate(SPIN_WEIGHTS):
            acc += w
            if pick < acc:
                idx = i
                break
        prize = SPIN_SEGMENTS[idx]  # paise
        avail_units = prize * 10  # avail_coins is coin-units (1000 = ₹1), so 1 paisa = 10 units
        c.execute("UPDATE users SET avail_coins=avail_coins+? WHERE user_id=?",
                  (avail_units, u["user_id"]))
        log(c, u["user_id"], "spin", {"avail_coins": avail_units},
            f"Spin Wheel prize ₹{prize / 100:.2f} → Available Balance")
        maybe_qualify_referral(c, u)
        u = c.execute("SELECT * FROM users WHERE user_id=?", (u["user_id"],)).fetchone()
        return jsonify({"ok": True, "prize": prize, "index": idx,
                        "state": state_payload(c, u)})

@app.get("/api/monetag/postback")
def monetag_postback():
    """Monetag server-to-server reward postback (GET, from Monetag's servers,
    no Telegram initData). Secret in the postback URL (stored only in the
    Monetag dashboard) proves the request came from Monetag. A 'valued'
    impression credits the session matched by ymid exactly once; the same
    ad_sessions.used flag as /api/ad/claim makes client-claim and postback
    mutually exclusive, so double-crediting is impossible. Duplicate
    postbacks (Monetag does not dedupe) are absorbed by UNIQUE(ymid,
    event_type). Non-valued events and clicks are logged only, with
    estimated_price kept for revenue accounting."""
    if not MONETAG_POSTBACK_SECRET:
        return jsonify({"ok": False, "msg": "postback disabled"}), 503
    provided = request.args.get("secret", "")
    if not provided or not hmac.compare_digest(provided, MONETAG_POSTBACK_SECRET):
        return jsonify({"ok": False}), 403
    ymid = (request.args.get("ymid") or "").strip()
    event_type = request.args.get("event_type") or ""
    reward = request.args.get("reward_event_type") or ""
    zone_id = request.args.get("zone_id") or ""
    est = request.args.get("estimated_price") or ""
    tg_id = request.args.get("telegram_id") or ""
    today = str(date.today())
    with _lock, db() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO monetag_postbacks(ymid,event_type,reward_event_type,zone_id,estimated_price,telegram_id,ts,credited) VALUES(?,?,?,?,?,?,?,0)",
            (ymid, event_type, reward, zone_id, est, tg_id, time.time()))
        row = c.execute("SELECT * FROM ad_sessions WHERE ymid=?", (ymid,)).fetchone() if ymid else None
        who = row["user_id"] if row else "monetag"
        if cur.rowcount != 1:
            adlog(c, who, "postback_dup", f"ymid={ymid} ev={event_type}")
            return jsonify({"ok": True, "dup": True})
        if not row:
            adlog(c, "monetag", "postback_unknown_ymid", f"ymid={ymid} ev={event_type} reward={reward} est={est}")
            return jsonify({"ok": True, "unknown": True})
        if event_type != "impression" or reward != "valued" or (zone_id and zone_id != MONETAG_ZONE_ID):
            adlog(c, who, "postback_nocredit", f"ymid={ymid} ev={event_type} reward={reward} zone={zone_id} est={est}")
            return jsonify({"ok": True})
        if row["used"]:
            adlog(c, who, "postback_already_claimed", f"ymid={ymid} est={est}")
            return jsonify({"ok": True, "already": True})
        age = time.time() - (row["created_at"] or 0)
        if age < AD_MIN_WATCH_SEC:
            # A valued impression postback can legitimately arrive within
            # a second of the start: Monetag sends it when the ad is
            # SERVED, not when it finishes playing. The valued flag is
            # Monetag's own paid decision on a secret-gated server call,
            # so a young age is NOT a fraud signal on this path — log it
            # and credit normally. (The old code returned 503 asking for
            # a retry, but the retry was then swallowed as a duplicate,
            # so real valued rewards were lost. Bug found 2026-11-14
            # from the owner's live adlog: valued postbacks at age 0-1s.)
            adlog(c, who, "postback_fast", f"ymid={ymid} age={int(age)}")
        u = roll_day(c, settle(c, get_user(c, who)))
        if not u:
            adlog(c, "monetag", "postback_unknown_user", f"ymid={ymid}")
            return jsonify({"ok": True, "unknown": True})
        watched = (u["ads_today"] or 0) if u["ads_date"] == today else 0
        if watched >= AD_DAILY_LIMIT:
            adlog(c, who, "postback_skip", f"ymid={ymid} cap est={est}")
            return jsonify({"ok": True, "skip": "cap"})
        left = AD_COOLDOWN_SEC - (time.time() - (u["last_ad_at"] or 0))
        if left > 0:
            adlog(c, who, "postback_skip", f"ymid={ymid} cooldown={int(left)} est={est}")
            # Remove the dedupe row so Monetag's retry (after the
            # cooldown) is processed fresh instead of eaten as a dup.
            c.execute("DELETE FROM monetag_postbacks WHERE ymid=? AND event_type=?",
                      (ymid, event_type))
            return jsonify({"ok": False, "retry": True}), 503
        cur2 = c.execute("UPDATE ad_sessions SET used=1 WHERE token=? AND used=0", (row["token"],))
        if cur2.rowcount != 1:
            return jsonify({"ok": True, "already": True})
        c.execute("""UPDATE users SET coins=coins+?, lifetime_coins=lifetime_coins+?,
                     ads_today=?, ads_date=?, last_ad_at=? WHERE user_id=?""",
                  (AD_REWARD_COINS, AD_REWARD_COINS, watched + 1, today, time.time(), who))
        log(c, who, "ad_monetag_postback", {"coins": AD_REWARD_COINS}, f"Monetag postback ymid={ymid} est={est}")
        c.execute("UPDATE monetag_postbacks SET credited=1 WHERE ymid=? AND event_type=?", (ymid, event_type))
        maybe_qualify_referral(c, u)
        adlog(c, who, "postback_credit", f"ymid={ymid} est={est} age={int(age)} tg={tg_id}")
        return jsonify({"ok": True, "credited": AD_REWARD_COINS})

@app.post("/api/convert")
def convert():
    """v72: Coins -> Available Balance (two-balance wallet: coins convert karne
    par Available Balance me jate hain; Winning Balance alag rehta hai)."""
    data = request.get_json(silent=True) or {}
    try:
        amt = int(data.get("coins") or 0)
    except (TypeError, ValueError):
        amt = 0
    if amt < 1:
        return jsonify({"ok": False, "msg": "Sahi amount dalo — kam se kam 1 coin."})
    with _lock, db() as c:
        u = get_user(c, uid())
        if amt > (u["coins"] or 0):
            return jsonify({"ok": False, "msg": "Itne coins nahi hain tumhare paas."})
        c.execute("UPDATE users SET coins=coins-?, avail_coins=avail_coins+? WHERE user_id=?",
                  (amt, amt, u["user_id"]))
        log(c, u["user_id"], "convert", {"coins": -amt},
            f"{amt} coins Available Balance me convert hue")
        u = c.execute("SELECT * FROM users WHERE user_id=?", (u["user_id"],)).fetchone()
        return jsonify({"ok": True, "converted": amt,
                        "msg": f"{amt} coins = ₹{amt / COINS_PER_RUPEE:.2f} Available Balance me aa gaye ✅",
                        "state": state_payload(c, u)})

def _min_withdraw_for(c, user_id):
    """Per-user withdraw minimum: Rs 10 for the first withdrawal; once the
    user has any PAID withdrawal, Rs 50 for every later one (owner, 2026-11-19)."""
    row = c.execute("SELECT COUNT(*) n FROM withdrawals WHERE user_id=? AND status='paid'",
                    (user_id,)).fetchone()
    return REPEAT_WITHDRAW_MIN_RUPEES if (row and row["n"] > 0) else MIN_WITHDRAW_RUPEES


@app.post("/api/withdraw")
def withdraw():
    data = request.get_json(silent=True) or {}
    if not PAYOUTS_ENABLED:
        return jsonify({"ok": False, "msg": "Withdraw abhi band hai — payout approval ke baad shuru hoga"})
    upi = (data.get("upi_id") or "").strip()
    holder = (data.get("holder_name") or "").strip()
    try:
        amount = Decimal(str(data.get("amount_rupees") or "0")).quantize(Decimal("0.01"))
        amount_paise = int(amount * 100)
    except (InvalidOperation, TypeError, ValueError):
        amount_paise = 0
    if "@" not in upi or len(upi) < 5:
        return jsonify({"ok": False, "msg": "Sahi UPI ID dalo, jaise naam@okhdfcbank"})
    if len(holder) < 3:
        return jsonify({"ok": False, "msg": "Account holder ka poora naam dalo"})
    with _lock, db() as c:
        u = roll_day(c, get_user(c, uid()))
        min_wd = _min_withdraw_for(c, u["user_id"])
        if amount_paise < min_wd * 100:
            return jsonify({"ok": False, "msg": f"Minimum withdraw Rs {min_wd} hai"})
        # Withdraw sirf Winning Balance se hota hai — Available Balance aur
        # gaon wale coins se nahi.
        if int(u["winning_paise"] or 0) < amount_paise:
            return jsonify({"ok": False, "msg": "Winning Balance me itne paise nahi hain."})
        # Reserve winning paise and create request in one locked DB transaction.
        c.execute("UPDATE users SET winning_paise=winning_paise-? WHERE user_id=?", (amount_paise, u["user_id"]))
        cur = c.execute("INSERT INTO withdrawals(user_id,amount_paise,upi_id,created_at,holder_name) VALUES(?,?,?,?,?)",
                        (u["user_id"], amount_paise, upi, time.time(), holder))
        log(c, u["user_id"], "withdraw_hold", {"coins": 0},
            f"Withdraw #{cur.lastrowid} reserved ₹{amount} from Winning Balance")
        c.execute("INSERT INTO cash_ledger(user_id,delta_paise,kind,note,ts) VALUES(?,?,?,?,?)",
                  (u["user_id"], 0, "withdraw_hold", f"Withdraw #{cur.lastrowid} reserved", time.time()))
        fresh = c.execute("SELECT * FROM users WHERE user_id=?", (u["user_id"],)).fetchone()
        return jsonify({"ok": True, "state": state_payload(c, fresh),
                        "msg": f"₹{amount} ki request submit ho gayi. Winning Balance se ₹{amount} reserve kiye gaye hain."})


def _require_owner():
    return bool(OWNER_ID) and uid() == OWNER_ID

@app.get("/api/history")
def user_history():
    with db() as c:
        user_id = uid()
        rows = c.execute("SELECT kind,coins,note,ts FROM ledger WHERE user_id=? ORDER BY id DESC LIMIT 30",
                         (user_id,)).fetchall()
        withdrawals = c.execute("SELECT id,amount_paise,status,created_at,decided_at FROM withdrawals WHERE user_id=? ORDER BY id DESC LIMIT 15",
                                (user_id,)).fetchall()
        items = [{"type": r["kind"], "coins": r["coins"], "note": r["note"], "ts": r["ts"]} for r in rows]
        items += [{"type": "withdrawal_" + r["status"], "coins": -int(r["amount_paise"]) * 10,
                   "note": f"Withdrawal #{r['id']} · ₹{r['amount_paise']/100:.2f} · {r['status']}",
                   "ts": r["decided_at"] or r["created_at"]} for r in withdrawals]
        items.sort(key=lambda x: x["ts"] or 0, reverse=True)
        return jsonify({"ok": True, "history": items[:40]})

@app.get("/api/support/tickets")
def support_tickets_list():
    with db() as c:
        rows = c.execute("SELECT id,category,message,status,reply,reply_at,created_at,updated_at FROM support_tickets WHERE user_id=? ORDER BY id DESC LIMIT 20",
                         (uid(),)).fetchall()
        return jsonify({"ok": True, "tickets": [dict(r) for r in rows]})

@app.post("/api/support/tickets")
def support_ticket_create():
    data = request.get_json(silent=True) or {}
    category = (data.get("category") or "general").strip()[:40]
    message = (data.get("message") or "").strip()
    if len(message) < 10 or len(message) > 1200:
        return jsonify({"ok": False, "msg": "Problem 10 se 1200 characters me likho"})
    with _lock, db() as c:
        cur = c.execute("INSERT INTO support_tickets(user_id,category,message,created_at,updated_at) VALUES(?,?,?,?,?)",
                        (uid(), category, message, time.time(), time.time()))
        return jsonify({"ok": True, "ticket_id": cur.lastrowid, "msg": f"Support ticket #{cur.lastrowid} submit ho gaya"})

@app.get("/api/owner/support/tickets")
def owner_support_tickets():
    if not _require_owner():
        return jsonify({"ok": False, "msg": "Owner only"}), 403
    with db() as c:
        rows = c.execute("SELECT id,user_id,category,message,status,reply,reply_at,created_at FROM support_tickets ORDER BY id DESC LIMIT 100").fetchall()
        tickets = []
        for r in rows:
            d = dict(r)
            d["dname"] = display_name(c, r["user_id"])
            tickets.append(d)
        return jsonify({"ok": True, "tickets": tickets})

@app.post("/api/owner/support/tickets/close")
def owner_support_ticket_close():
    if not _require_owner():
        return jsonify({"ok": False, "msg": "Owner only"}), 403
    data = request.get_json(silent=True) or {}
    with _lock, db() as c:
        cur = c.execute("UPDATE support_tickets SET status='closed',updated_at=? WHERE id=? AND status='open'",
                        (time.time(), data.get("id")))
        if not cur.rowcount:
            return jsonify({"ok": False, "msg": "Open ticket nahi mila"})
        return jsonify({"ok": True, "msg": "Ticket close ho gaya"})

@app.post("/api/owner/support/tickets/reply")
def owner_support_ticket_reply():
    if not _require_owner():
        return jsonify({"ok": False, "msg": "Owner only"}), 403
    data = request.get_json(silent=True) or {}
    reply = (data.get("reply") or "").strip()
    if len(reply) < 1 or len(reply) > 1200:
        return jsonify({"ok": False, "msg": "Reply 1 se 1200 characters me likho"})
    with _lock, db() as c:
        cur = c.execute("UPDATE support_tickets SET reply=?,reply_at=?,updated_at=? WHERE id=? AND status='open'",
                        (reply, time.time(), time.time(), data.get("id")))
        if not cur.rowcount:
            return jsonify({"ok": False, "msg": "Open ticket nahi mila"})
        return jsonify({"ok": True, "msg": "Reply bhej diya gaya"})

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
        "SELECT u.user_id, u.lifetime_coins, u.created_at, u.invites, "
        "COALESCE(NULLIF(u.username,''), "
        "(SELECT s.username FROM seen_starts s WHERE s.tg_id = u.user_id), '') AS uname, "
        "COALESCE(NULLIF(u.first_name,''), "
        "(SELECT s.first_name FROM seen_starts s WHERE s.tg_id = u.user_id), '') AS dname "
        "FROM users u WHERE u.user_id != 'owner' ORDER BY u.created_at DESC LIMIT 10").fetchall()
    players = conn.execute(
        "SELECT u.user_id, u.coins, u.lifetime_coins, "
        "COALESCE(NULLIF(u.username,''), "
        "(SELECT s.username FROM seen_starts s WHERE s.tg_id = u.user_id), '') AS uname, "
        "COALESCE(NULLIF(u.first_name,''), "
        "(SELECT s.first_name FROM seen_starts s WHERE s.tg_id = u.user_id), '') AS dname "
        "FROM users u WHERE u.user_id != 'owner' ORDER BY u.created_at DESC LIMIT 50").fetchall()
    promos = conn.execute(
        "SELECT code, coins, max_uses, used_count, active FROM promo_codes "
        "ORDER BY created_at DESC LIMIT 20").fetchall()
    total_coin_balance = conn.execute("SELECT COALESCE(SUM(coins),0) s FROM users").fetchone()["s"]
    open_tickets = conn.execute("SELECT COUNT(*) n FROM support_tickets WHERE status='open'").fetchone()["n"]
    conn.close()
    return jsonify({"ok": True, "total_players": total, "joined_today": joined_today,
                    "bot_starts": starts, "total_coin_balance": total_coin_balance,
                    "estimated_coin_value_rupees": round(total_coin_balance / COINS_PER_RUPEE, 2),
                    "open_support_tickets": open_tickets,
                    "recent": [dict(r) for r in rows],
                    "players": [dict(r) for r in players],
                    "promos": [dict(r) for r in promos]})


@app.get("/api/owner/adlog")
def owner_adlog():
    """Owner-only: recent ad start/claim attempts, for diagnosing
    clients whose ad reward never arrives."""
    if not OWNER_ID or uid() != OWNER_ID:
        return jsonify({"ok": False, "error": "Owner only"}), 403
    conn = db()
    rows = conn.execute(
        "SELECT user_id, event, detail, ts FROM ad_log ORDER BY id DESC LIMIT 60").fetchall()
    return jsonify({"ok": True, "log": [dict(r) for r in rows]})

@app.get("/api/owner/withdrawals")
def owner_withdrawals():
    if not _require_owner():
        return jsonify({"ok": False, "msg": "Owner only"}), 403
    with _lock, db() as c:
        rows = [{"id": w["id"], "user_id": w["user_id"],
                 "amount_rupees": round(w["amount_paise"] / 100, 2),
                 "upi_id": w["upi_id"], "status": w["status"],
                 "holder_name": w["holder_name"] or "", "bank_name": w["bank_name"] or "",
                 "txn_id": w["txn_id"] or "", "utr": w["utr"] or "",
                 "payment_date": w["payment_date"] or ""}
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
            # Withdraw Winning Balance se reserve hua tha — wahi wapas jata hai.
            c.execute("UPDATE users SET winning_paise=winning_paise+? WHERE user_id=?", (int(w["amount_paise"]), w["user_id"]))
            log(c, w["user_id"], "withdraw_refund", {"coins": 0},
                f"Withdraw #{w['id']} rejected; ₹{int(w['amount_paise'])/100:.2f} Winning Balance me wapas")
        else:
            txn = (data.get("txn_id") or "").strip()
            utr = (data.get("utr") or "").strip()
            pdate = (data.get("payment_date") or "").strip()
            if len(txn) < 6 and len(utr) < 6:
                return jsonify({"ok": False, "msg": "Transaction ID ya UTR dalo — yahi payment ka proof hai, bina iske Paid nahi hoga"})
            if not pdate:
                pdate = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 19800))
            c.execute("UPDATE withdrawals SET txn_id=?, utr=?, payment_date=? WHERE id=?",
                      (txn, utr, pdate, w["id"]))
            cash_log(c, w["user_id"], 0, "withdraw_paid", f"Withdraw #{w['id']} paid out to UPI, txn {txn} utr {utr} on {pdate}")
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
        _ensure_channel_modes(c)
        rows = []
        for t in c.execute("SELECT * FROM tasks WHERE active=1 ORDER BY id DESC"):
            left = task_budget_left(t)
            if left is not None and left < t["reward_coins"] and t["id"] not in subs:
                continue  # campaign budget over: no new workers on this task
            rows.append({"id": t["id"], "title": t["title"], "description": t["description"],
                         "link": t["link"], "reward_coins": t["reward_coins"],
                         "category": t["category"] or "telegram",
                         "verify_mode": t["verify_mode"] or "proof",
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

def _ensure_channel_modes(c):
    """The official-channel task is verified by bot membership, never proof."""
    c.execute("UPDATE tasks SET verify_mode='channel' "
              "WHERE link LIKE '%t.me/SlimeFi_official%' AND COALESCE(verify_mode,'proof') != 'channel'")

def _channel_ref(link):
    m = re.search(r"t\.me/([A-Za-z0-9_]+)", link or "")
    return "@" + m.group(1) if m else None

def _tg_get_chat_member(chat_ref, tg_id_num):
    """Ask Telegram (via the bot) whether a user is in a channel. The bot
    must be a member/admin of that channel for this to work."""
    if not BOT_TOKEN:
        return None
    try:
        data = urllib.parse.urlencode({"chat_id": chat_ref, "user_id": tg_id_num}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getChatMember", data=data)
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception:
        return None

@app.post("/api/promo/redeem")
def promo_redeem():
    """User redeems a promo code (from the Daily Bonus page): one use per
    user, capped by the code's max_uses, credited with a ledger entry."""
    code = ((request.get_json(silent=True) or {}).get("code") or "").strip().upper()
    if not code:
        return jsonify({"ok": False, "msg": "Promo code dalo"})
    with _lock, db() as c:
        user = get_user(c, uid())
        p = c.execute("SELECT * FROM promo_codes WHERE code=?", (code,)).fetchone()
        if not p or not p["active"]:
            return jsonify({"ok": False, "msg": "Galat promo code hai"})
        if p["max_uses"] and (p["used_count"] or 0) >= p["max_uses"]:
            return jsonify({"ok": False, "msg": "Is promo code ki limit khatam ho gayi"})
        done = c.execute("SELECT 1 FROM promo_redemptions WHERE code=? AND user_id=?",
                         (code, user["user_id"])).fetchone()
        if done:
            return jsonify({"ok": False, "msg": "Tum ye code pehle hi use kar chuke ho"})
        c.execute("INSERT INTO promo_redemptions(code,user_id,created_at) VALUES(?,?,?)",
                  (code, user["user_id"], time.time()))
        c.execute("UPDATE promo_codes SET used_count=used_count+1 WHERE code=?", (code,))
        c.execute("UPDATE users SET coins=coins+?, lifetime_coins=lifetime_coins+? WHERE user_id=?",
                  (p["coins"], p["coins"], user["user_id"]))
        log(c, user["user_id"], "promo", {"coins": p["coins"]}, f"Promo code: {code}")
        fresh = c.execute("SELECT * FROM users WHERE user_id=?", (user["user_id"],)).fetchone()
        maybe_qualify_referral(c, fresh)
        return jsonify({"ok": True, "msg": f"Promo lag gaya! {p['coins']} coins mile",
                        "state": state_payload(c, fresh)})

@app.post("/api/tasks/<int:task_id>/channel-check")
def task_channel_check(task_id):
    """Channel-join task: no proof. The bot verifies membership itself and
    the reward is credited at once, under the same budget/ledger rules."""
    with _lock, db() as c:
        _ensure_channel_modes(c)
        user = get_user(c, uid())
        t = c.execute("SELECT * FROM tasks WHERE id=? AND active=1", (task_id,)).fetchone()
        if not t:
            return jsonify({"ok": False, "msg": "Task nahi mila"})
        if (t["verify_mode"] or "proof") != "channel":
            return jsonify({"ok": False, "msg": "Is task ke liye proof bhejo"})
        ref = _channel_ref(t["link"])
        if not ref:
            return jsonify({"ok": False, "msg": "Channel link set nahi hai"})
        sub = c.execute("SELECT * FROM task_submissions WHERE task_id=? AND user_id=?",
                        (task_id, user["user_id"])).fetchone()
        if sub and sub["status"] == "approved":
            return jsonify({"ok": True, "msg": "Is task ka reward pehle hi mil chuka hai"})
        if not user["user_id"].startswith("tg"):
            return jsonify({"ok": False, "msg": "Ye check sirf Telegram account par hota hai"})
        resp = _tg_get_chat_member(ref, int(user["user_id"][2:]))
        if not resp or not resp.get("ok"):
            return jsonify({"ok": False, "msg": "Check nahi ho paya — pehle bot ko channel me admin bana kar add karo, fir try karo"})
        result = resp.get("result") or {}
        st = result.get("status")
        member = st in ("member", "administrator", "creator") or (st == "restricted" and result.get("is_member"))
        if not member:
            return jsonify({"ok": False, "msg": "Tum channel me nahi mile — pehle channel join karo, fir check karo"})
        if t["budget_coins"] and (t["spent_coins"] or 0) + t["reward_coins"] > t["budget_coins"]:
            return jsonify({"ok": False, "msg": "Is task ka budget khatam ho gaya hai"})
        now = time.time()
        if sub:
            c.execute("UPDATE task_submissions SET status='approved', proof='channel auto-check', decided_at=? WHERE id=?",
                      (now, sub["id"]))
        else:
            c.execute("INSERT INTO task_submissions(task_id,user_id,proof,status,created_at,decided_at) VALUES(?,?,?,'approved',?,?)",
                      (task_id, user["user_id"], "channel auto-check", now, now))
        if t["budget_coins"]:
            c.execute("UPDATE tasks SET spent_coins=spent_coins+? WHERE id=?",
                      (t["reward_coins"], task_id))
        c.execute("UPDATE users SET coins=coins+?, lifetime_coins=lifetime_coins+? WHERE user_id=?",
                  (t["reward_coins"], t["reward_coins"], user["user_id"]))
        log(c, user["user_id"], "task", {"coins": t["reward_coins"]},
            f"Channel task auto-approved: {t['title']}")
        fresh = c.execute("SELECT * FROM users WHERE user_id=?", (user["user_id"],)).fetchone()
        maybe_qualify_referral(c, fresh)
        return jsonify({"ok": True,
                        "msg": f"Channel join confirm ho gaya — {t['reward_coins']} coins mil gaye!",
                        "state": state_payload(c, fresh)})

@app.post("/api/tasks/<int:task_id>/claim")
def task_claim(task_id):
    """Direct-complete task: the owner removed proof submission. One tap on
    Complete credits the reward at once — once per user, under the same
    campaign-budget and ledger rules as an approved proof used to follow."""
    with _lock, db() as c:
        _ensure_channel_modes(c)
        user = get_user(c, uid())
        t = c.execute("SELECT * FROM tasks WHERE id=? AND active=1", (task_id,)).fetchone()
        if not t:
            return jsonify({"ok": False, "msg": "Task nahi mila"})
        if (t["verify_mode"] or "proof") == "channel":
            return jsonify({"ok": False, "msg": "Ye channel task hai — pehle channel join karo, fir Check Karo dabao"})
        sub = c.execute("SELECT * FROM task_submissions WHERE task_id=? AND user_id=?",
                        (task_id, user["user_id"])).fetchone()
        if sub and sub["status"] == "approved":
            return jsonify({"ok": True, "msg": "Is task ka reward pehle hi mil chuka hai"})
        if sub and sub["status"] == "pending":
            return jsonify({"ok": False, "msg": "Is task ka purana proof admin ke paas hai — owner se baat karo"})
        if t["budget_coins"] and (t["spent_coins"] or 0) + t["reward_coins"] > t["budget_coins"]:
            return jsonify({"ok": False, "msg": "Is task ka budget khatam ho gaya hai"})
        now = time.time()
        if sub:
            c.execute("UPDATE task_submissions SET status='approved', proof='direct complete', decided_at=? WHERE id=?",
                      (now, sub["id"]))
        else:
            c.execute("INSERT INTO task_submissions(task_id,user_id,proof,status,created_at,decided_at) VALUES(?,?,?,'approved',?,?)",
                      (task_id, user["user_id"], "direct complete", now, now))
        if t["budget_coins"]:
            c.execute("UPDATE tasks SET spent_coins=spent_coins+? WHERE id=?",
                      (t["reward_coins"], task_id))
        c.execute("UPDATE users SET coins=coins+?, lifetime_coins=lifetime_coins+? WHERE user_id=?",
                  (t["reward_coins"], t["reward_coins"], user["user_id"]))
        log(c, user["user_id"], "task", {"coins": t["reward_coins"]},
            f"Task complete (direct): {t['title']}")
        fresh = c.execute("SELECT * FROM users WHERE user_id=?", (user["user_id"],)).fetchone()
        maybe_qualify_referral(c, fresh)
        return jsonify({"ok": True,
                        "msg": f"Task complete — {t['reward_coins']} coins mil gaye!",
                        "state": state_payload(c, fresh)})

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
    verify_mode = (data.get("verify_mode") or "proof").strip().lower()
    if verify_mode not in ("proof", "channel"):
        verify_mode = "proof"
    if not title or reward <= 0:
        return jsonify({"ok": False, "msg": "Task ka title aur reward (coins) dalo"})
    with _lock, db() as c:
        cur = c.execute("INSERT INTO tasks(title,description,link,reward_coins,budget_coins,category,verify_mode,created_at) VALUES(?,?,?,?,?,?,?,?)",
                        (title, (data.get("description") or "").strip(),
                         (data.get("link") or "").strip(), reward, budget, category, verify_mode, time.time()))
        return jsonify({"ok": True, "id": cur.lastrowid, "msg": "Task ban gaya"})

@app.get("/api/owner/submissions")
def owner_submissions():
    if not _require_owner():
        return jsonify({"ok": False, "msg": "Owner only"}), 403
    with _lock, db() as c:
        subs = [{"id": s["id"], "task_id": s["task_id"], "task_title": s["title"],
                 "user_id": s["user_id"], "dname": display_name(c, s["user_id"]),
                 "proof": s["proof"], "status": s["status"],
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

@app.get("/api/owner/referrals")
def owner_referrals():
    if not _require_owner():
        return jsonify({"ok": False, "msg": "Owner only"}), 403
    with db() as c:
        def uname_of(c, user_id):
            lbl = display_name(c, user_id)
            return lbl if lbl else user_id
        reviews = []
        for r in c.execute("SELECT * FROM referral_reviews WHERE status='pending' ORDER BY created_at DESC LIMIT 20"):
            reviews.append({"id": r["id"], "referrer": uname_of(c, r["referrer_id"]),
                            "referred": uname_of(c, r["referred_id"]),
                            "reward_coins": REFERRAL_REWARD_COINS, "created_at": r["created_at"]})
        banned = []
        for r in c.execute("SELECT * FROM users WHERE banned=1 ORDER BY created_at DESC LIMIT 20"):
            banned.append({"user_id": r["user_id"], "label": uname_of(c, r["user_id"]),
                           "reason": r["ban_reason"], "lifetime_coins": r["lifetime_coins"],
                           "created_at": r["created_at"]})
        suspicious = []
        for r in c.execute("SELECT * FROM users WHERE suspicious=1 AND banned=0 ORDER BY created_at DESC LIMIT 20"):
            suspicious.append({"user_id": r["user_id"], "label": uname_of(c, r["user_id"]),
                               "reason": r["suspicious_reason"], "lifetime_coins": r["lifetime_coins"],
                               "created_at": r["created_at"]})
        return jsonify({"ok": True, "reviews": reviews, "banned": banned, "suspicious": suspicious})

@app.post("/api/owner/referrals/decide")
def owner_referrals_decide():
    if not _require_owner():
        return jsonify({"ok": False, "msg": "Owner only"}), 403
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    if action not in ("approve", "reject"):
        return jsonify({"ok": False, "msg": "Unknown action"})
    with _lock, db() as c:
        r = c.execute("SELECT * FROM referral_reviews WHERE id=?", (data.get("id"),)).fetchone()
        if not r or r["status"] != "pending":
            return jsonify({"ok": False, "msg": "Review pending nahi hai"})
        referred = c.execute("SELECT * FROM users WHERE user_id=?", (r["referred_id"],)).fetchone()
        inv = c.execute("SELECT * FROM users WHERE user_id=?", (r["referrer_id"],)).fetchone()
        if action == "approve":
            if referred and inv:
                _pay_referral(c, referred, inv)
            c.execute("UPDATE referral_reviews SET status='approved', decided_at=? WHERE id=?",
                      (time.time(), r["id"]))
            return jsonify({"ok": True, "msg": "Approve ho gaya — reward de diya"})
        c.execute("UPDATE users SET referred_qualified=3 WHERE user_id=?", (r["referred_id"],))
        c.execute("UPDATE referral_reviews SET status='rejected', decided_at=? WHERE id=?",
                  (time.time(), r["id"]))
        return jsonify({"ok": True, "msg": "Reject ho gaya — reward nahi milega"})

@app.post("/api/owner/promos")
def owner_promo_create():
    if not _require_owner():
        return jsonify({"ok": False, "msg": "Owner only"}), 403
    d = request.get_json(silent=True) or {}
    code = (d.get("code") or "").strip().upper()
    try:
        coins = int(d.get("coins") or 0)
        max_uses = int(d.get("max_uses") or 0)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "msg": "Coins / limit sahi dalo"})
    if not code or len(code) > 32 or coins <= 0:
        return jsonify({"ok": False, "msg": "Code aur coins sahi dalo"})
    with _lock, db() as c:
        if c.execute("SELECT 1 FROM promo_codes WHERE code=?", (code,)).fetchone():
            return jsonify({"ok": False, "msg": "Ye code pehle se hai"})
        c.execute("INSERT INTO promo_codes(code,coins,max_uses,created_at) VALUES(?,?,?,?)",
                  (code, coins, max_uses, time.time()))
        return jsonify({"ok": True, "msg": f"Promo code {code} ban gaya — {coins} coins, "
                                           f"{max_uses if max_uses else 'unlimited'} log use kar sakte hain"})

@app.post("/api/owner/promos/deactivate")
def owner_promo_deactivate():
    if not _require_owner():
        return jsonify({"ok": False, "msg": "Owner only"}), 403
    code = ((request.get_json(silent=True) or {}).get("code") or "").strip().upper()
    with _lock, db() as c:
        c.execute("UPDATE promo_codes SET active=0 WHERE code=?", (code,))
        return jsonify({"ok": True, "msg": f"Promo {code} band kar diya"})

@app.post("/api/owner/grant")
def owner_grant():
    """Owner gifts coins to a player from the Admin Panel. By the owner's
    order the gift counts in BOTH the spendable balance and lifetime coins
    (so the player's level and leaderboard position rise too), and the gift
    is written to the ledger."""
    if not _require_owner():
        return jsonify({"ok": False, "msg": "Owner only"}), 403
    data = request.get_json(silent=True) or {}
    try:
        amount = int(data.get("coins") or 0)
    except (TypeError, ValueError):
        amount = 0
    if amount <= 0 or amount > 1000000:
        return jsonify({"ok": False, "msg": "Coins ki sahi matra dalo (1 se 10,00,000 tak)"})
    target = (data.get("user_id") or "").strip()
    with _lock, db() as c:
        u = c.execute("SELECT * FROM users WHERE user_id=?", (target,)).fetchone()
        if not u or target == "owner":
            return jsonify({"ok": False, "msg": "Player nahi mila"})
        c.execute("UPDATE users SET coins=coins+?, lifetime_coins=lifetime_coins+? WHERE user_id=?",
                  (amount, amount, target))
        log(c, target, "owner_grant", {"coins": amount}, "Owner ne coins diye (Admin Panel se)")
        fresh = c.execute("SELECT coins FROM users WHERE user_id=?", (target,)).fetchone()
        return jsonify({"ok": True, "balance": fresh["coins"],
                        "msg": f"{amount} coins de diye. Naya balance: {fresh['coins']} coins"})

@app.post("/api/owner/reduce")
def owner_reduce():
    """Owner cuts coins from a player (correction / abuse recovery). The cut
    applies to both the spendable balance and lifetime coins, never below
    zero, and is written to the ledger."""
    if not _require_owner():
        return jsonify({"ok": False, "msg": "Owner only"}), 403
    data = request.get_json(silent=True) or {}
    try:
        amount = int(data.get("coins") or 0)
    except (TypeError, ValueError):
        amount = 0
    if amount <= 0 or amount > 1000000:
        return jsonify({"ok": False, "msg": "Coins ki sahi matra dalo (1 se 10,00,000 tak)"})
    target = (data.get("user_id") or "").strip()
    with _lock, db() as c:
        u = c.execute("SELECT * FROM users WHERE user_id=?", (target,)).fetchone()
        if not u or target == "owner":
            return jsonify({"ok": False, "msg": "Player nahi mila"})
        new_coins = max(0, (u["coins"] or 0) - amount)
        new_life = max(0, (u["lifetime_coins"] or 0) - amount)
        cut = (u["coins"] or 0) - new_coins
        c.execute("UPDATE users SET coins=?, lifetime_coins=? WHERE user_id=?",
                  (new_coins, new_life, target))
        log(c, target, "owner_reduce", {"coins": -cut}, "Owner ne coins kaate (Admin Panel se)")
        return jsonify({"ok": True, "balance": new_coins,
                        "msg": f"{cut} coins kaat liye. Naya balance: {new_coins} coins"})

@app.get("/api/owner/activity")
def owner_activity():
    """v55: ONE player's full activity for the Admin Panel — coin history
    (ledger + withdrawals) and ad events together, newest first, so the
    owner no longer has to dig through everyone's mixed stream."""
    if not _require_owner():
        return jsonify({"ok": False, "msg": "Owner only"}), 403
    target = (request.args.get("user_id") or "").strip()
    with db() as c:
        u = c.execute("SELECT * FROM users WHERE user_id=?", (target,)).fetchone()
        if not u:
            return jsonify({"ok": False, "msg": "Player nahi mila"})
        items = []
        for r in c.execute("SELECT kind,coins,note,ts FROM ledger WHERE user_id=? ORDER BY id DESC LIMIT 40", (target,)):
            items.append({"type": r["kind"], "coins": r["coins"], "note": r["note"] or "", "ts": r["ts"], "src": "coin"})
        for r in c.execute("SELECT id,amount_paise,status,created_at,decided_at FROM withdrawals WHERE user_id=? ORDER BY id DESC LIMIT 10", (target,)):
            items.append({"type": "withdrawal_" + r["status"], "coins": -int(r["amount_paise"]) * 10,
                          "note": f"Withdrawal #{r['id']} · ₹{r['amount_paise']/100:.2f} · {r['status']}",
                          "ts": r["decided_at"] or r["created_at"], "src": "coin"})
        for r in c.execute("SELECT event,detail,ts FROM ad_log WHERE user_id=? AND event!='client_check' ORDER BY id DESC LIMIT 40", (target,)):
            items.append({"type": "ad_" + r["event"], "coins": 0, "note": r["detail"] or "", "ts": r["ts"], "src": "ad"})
        items.sort(key=lambda x: x["ts"] or 0, reverse=True)
        warns = [{"id": w["id"], "message": w["message"], "ts": w["ts"], "seen": w["seen"]}
                 for w in c.execute("SELECT id,message,ts,seen FROM warnings WHERE user_id=? ORDER BY id DESC LIMIT 10", (target,))]
        return jsonify({"ok": True,
                        "player": {"user_id": u["user_id"], "dname": display_name(c, target),
                                   "coins": u["coins"], "lifetime_coins": u["lifetime_coins"],
                                   "ads_today": (u["ads_today"] or 0) if u["ads_date"] == str(date.today()) else 0,
                                   "banned": bool(u["banned"]), "ban_reason": u["ban_reason"] or "",
                                   "created_at": u["created_at"], "invites": u["invites"] or 0},
                        "activity": items[:60], "warnings": warns})


@app.post("/api/owner/ban")
def owner_ban():
    """v55: manual ban from the Admin Panel (before this only unban
    existed, so the owner could not ban anyone from the panel)."""
    if not _require_owner():
        return jsonify({"ok": False, "msg": "Owner only"}), 403
    data = request.get_json(silent=True) or {}
    target = (data.get("user_id") or "").strip()
    with _lock, db() as c:
        u = c.execute("SELECT * FROM users WHERE user_id=?", (target,)).fetchone()
        if not u or target == "owner":
            return jsonify({"ok": False, "msg": "Player nahi mila"})
        c.execute("UPDATE users SET banned=1, ban_reason=? WHERE user_id=?",
                  ("Owner ne ban kiya (Admin Panel se)", target))
        return jsonify({"ok": True, "msg": "Player ban ho gaya"})


@app.post("/api/owner/warn")
def owner_warn():
    """v55: owner sends a warning to one player; it pops up in that
    player's app on their next open until they tap 'Samajh Gaya'."""
    if not _require_owner():
        return jsonify({"ok": False, "msg": "Owner only"}), 403
    data = request.get_json(silent=True) or {}
    target = (data.get("user_id") or "").strip()
    message = (data.get("message") or "").strip()[:300]
    if not message:
        return jsonify({"ok": False, "msg": "Warning ka message likho"})
    with _lock, db() as c:
        u = c.execute("SELECT * FROM users WHERE user_id=?", (target,)).fetchone()
        if not u or target == "owner":
            return jsonify({"ok": False, "msg": "Player nahi mila"})
        c.execute("INSERT INTO warnings(user_id,message,ts,seen) VALUES(?,?,?,0)",
                  (target, message, time.time()))
        return jsonify({"ok": True, "msg": "Warning bhej di — user ko app me dikhegi"})


def _tg_send_message(tg_num, text):
    """Send one DM via the bot API. Returns 'ok', 'blocked' (403 — the user
    blocked the bot or never started it) or 'fail'."""
    if not BOT_TOKEN:
        return "fail"
    try:
        data = urllib.parse.urlencode({"chat_id": tg_num, "text": text}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data=data)
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.loads(r.read())
        return "ok" if resp.get("ok") else "fail"
    except urllib.error.HTTPError as e:
        return "blocked" if e.code == 403 else "fail"
    except Exception:
        return "fail"


@app.post("/api/owner/broadcast")
def owner_broadcast():
    """v78: owner broadcasts one message to everyone who ever started the
    bot (seen_starts). test=1 sends only to the owner. Sends are throttled
    (~50ms apart) and the reply counts ok/blocked/fail per recipient."""
    if not _require_owner():
        return jsonify({"ok": False, "msg": "Owner only"}), 403
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()[:1000]
    test = bool(data.get("test"))
    if not text:
        return jsonify({"ok": False, "msg": "Pehle message likho"})
    if not BOT_TOKEN:
        return jsonify({"ok": False, "msg": "Bot token set nahi hai — broadcast nahi ho sakta"})
    if test:
        num = OWNER_ID[2:] if (OWNER_ID or "").startswith("tg") else (OWNER_ID or "")
        recipients = [num] if num else []
    else:
        with db() as c:
            rows = c.execute("SELECT tg_id FROM seen_starts ORDER BY ts").fetchall()
        recipients = []
        for r in rows:
            t = r["tg_id"] or ""
            n = t[2:] if t.startswith("tg") else t
            if n:
                recipients.append(n)
    sent = blocked = failed = 0
    for i, num in enumerate(recipients):
        if i:
            time.sleep(0.05)
        res = _tg_send_message(num, text)
        if res == "ok":
            sent += 1
        elif res == "blocked":
            blocked += 1
        else:
            failed += 1
    total = len(recipients)
    if test:
        msg = ("Test message tumhe bhej diya gaya" if sent
               else "Test message nahi gaya — bot ke saath apni chat check karo")
    else:
        msg = f"Broadcast ho gaya: {sent}/{total} ko gaya"
        if blocked:
            msg += f", {blocked} ne bot band/block kiya hai"
        if failed:
            msg += f", {failed} fail"
    return jsonify({"ok": True, "test": test, "total": total, "sent": sent,
                    "blocked": blocked, "failed": failed, "msg": msg})


@app.post("/api/warning/seen")
def warning_seen():
    data = request.get_json(silent=True) or {}
    try:
        wid = int(data.get("id") or 0)
    except (TypeError, ValueError):
        wid = 0
    with _lock, db() as c:
        c.execute("UPDATE warnings SET seen=1 WHERE id=? AND user_id=?", (wid, uid()))
        return jsonify({"ok": True})


@app.post("/api/owner/unban")
def owner_unban():
    if not _require_owner():
        return jsonify({"ok": False, "msg": "Owner only"}), 403
    data = request.get_json(silent=True) or {}
    with _lock, db() as c:
        c.execute("UPDATE users SET banned=0, ban_reason='', ban_override=1 WHERE user_id=?",
                  (data.get("user_id") or "",))
        return jsonify({"ok": True, "msg": "Account unban ho gaya"})

@app.get("/")
def index():
    return send_from_directory("static", "index.html")

@app.get("/payouts")
def payouts_page():
    return send_from_directory("static", "payouts.html")

@app.get("/api/payouts")
def payouts_public():
    """Public payout proofs (AdsGram clause 8): every PAID withdrawal with
    amount, date, holder name, masked UPI and transaction id. No login —
    anyone (users, reviewers) can verify payouts really happened."""
    with db() as c:
        rows = [{"amount_rupees": round(w["amount_paise"] / 100, 2),
                 "upi": mask_upi(w["upi_id"]), "holder_name": w["holder_name"] or "",
                 "txn_id": w["txn_id"] or "", "utr": w["utr"] or "",
                 "payment_date": w["payment_date"] or "",
                 "ts": w["decided_at"] or w["created_at"]}
                for w in c.execute(
                    "SELECT * FROM withdrawals WHERE status='paid' ORDER BY id DESC LIMIT 50")]
        tot = c.execute("SELECT COUNT(*) n, COALESCE(SUM(amount_paise),0) s "
                        "FROM withdrawals WHERE status='paid'").fetchone()
        return jsonify({"ok": True, "payouts": rows, "count": tot["n"],
                        "total_rupees": round(tot["s"] / 100, 2)})


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
        "text": ("\U0001F7E2 Welcome to SlimeFi — Reward Earning Platform!\n\n"
                 "\U0001F4B0 Watch Ads aur available Tasks se Coins kamao\n"
                 "\U0001F465 Friends invite karo — qualifying activity par referral reward\n"
                 "\U0001F381 Daily Bonus aur special campaigns check karo\n"
                 "\U0001FA99 1,000 Coins = \u20B91 (Wallet me automatic value)\n"
                 "\U0001F4B3 Eligible hone par UPI withdrawal request bhejo\n\n"
                 "\U0001F447 Start karne ke liye neeche button dabao."),
        "reply_markup": {"inline_keyboard": [[
            {"text": "\U0001F680 Launch SlimeFi", "web_app": {"url": url}}
        ]]},
    })

init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), threaded=True)
