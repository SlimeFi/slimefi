"""SlimeFi Telegram bot (@slimefi_bot).
/start -> welcome message + "Launch SlimeFi" Web App button.
Token comes ONLY from the BOT_TOKEN environment variable (never in code).
Run:  BOT_TOKEN=... WEBAPP_URL=https://your-host/ python3 bot.py
Uses only the Python standard library (long polling).
"""
import os
import json
import time
import urllib.request
import urllib.parse

TOKEN = os.environ.get("BOT_TOKEN", "")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "")
API = f"https://api.telegram.org/bot{TOKEN}/"


def call(method, **params):
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(API + method, data=data)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def main():
    if not TOKEN:
        raise SystemExit("BOT_TOKEN environment variable is not set")
    if not WEBAPP_URL:
        raise SystemExit("WEBAPP_URL environment variable is not set")
    print("SlimeFi bot polling...")
    offset = 0
    while True:
        try:
            res = call("getUpdates", offset=offset, timeout=50)
            for upd in res.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                text = (msg.get("text") or "").strip()
                chat_id = (msg.get("chat") or {}).get("id")
                if not chat_id:
                    continue
                if text.startswith("/start"):
                    parts = text.split(maxsplit=1)
                    url = WEBAPP_URL
                    if len(parts) > 1 and parts[1].startswith("SLIME-"):
                        url = WEBAPP_URL + ("&" if "?" in WEBAPP_URL else "?") + "ref=" + parts[1]
                    keyboard = json.dumps({"inline_keyboard": [[
                        {"text": "Launch SlimeFi", "web_app": {"url": url}}
                    ]]})
                    call("sendMessage", chat_id=chat_id,
                         text=("Welcome to SlimeFi - Jura Tempest Village Builder.\n"
                               "Apna gaon banao, buildings upgrade karo, coins kamao "
                               "aur apne Slime ko Level 10 Slime Sovereign tak le jao."),
                         reply_markup=keyboard)
        except Exception as e:
            print("poll error:", e)
            time.sleep(3)


if __name__ == "__main__":
    main()
