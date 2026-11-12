# SlimeFi - Jura Tempest Village Builder

Telegram Mini App game. Virtual in-game economy only: coins are NOT real
money. Cash withdrawal / token value needs separate funding, rules and
security (future phase, not in this codebase yet).

## Design (Phase 1)
- Dark Premium Gaming interface, Blue + Purple + Gold theme
- 2D illustrated village (Town hero), Telegram Mini App layout, mobile first

## Game system (Phase 2-4)
- Town Hall (village development), Goblin Hut (passive coins),
  Goblin Workshop (wood), Magic Ore Mine (crystals), Dwarf Forge (raises
  building upgrade limits)
- Slime Guardian: 10-level progression from lifetime coins
- Daily rewards, daily quests, refining, referral / squad boost (+5% per
  friend, max +50%)
- Offline income: production is calculated from server timestamps
  (capped at 12 hours), so coins accrue while the player is away

## Sections (Phase 3)
Town / Workshop / Quests / Alliance (bottom tabs)

## Tech (Phase 5)
- Python + Flask API, SQLite database (`slimefi.db`, auto-created)
- Telegram WebApp `initData` verified server-side (HMAC-SHA256) whenever
  the request carries it; bot token lives only in the `BOT_TOKEN`
  environment variable
- All coin changes are recorded in the `ledger` table

## Files
- `app.py` - game backend + API
- `bot.py` - Telegram bot: /start -> "Launch SlimeFi" Web App button
- `static/` - Mini App frontend (index.html, style.css, app.js, img/)
- `requirements.txt`, `.env.example`, `slimefi.service` (systemd unit)

## Run locally
```
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
./venv/bin/python app.py            # http://127.0.0.1:5000
```

## Run the bot (after deployment)
```
BOT_TOKEN=<token> WEBAPP_URL=https://your-public-url/ ./venv/bin/python bot.py
```

## Launch checklist (Phase 6)
1. Bot already created: @slimefi_bot (BotFather)
2. Put BOT_TOKEN in the host's environment variables (never in code/chat)
3. Deploy app.py to free hosting (Render / Koyeb), set WEBAPP_URL
4. Start bot.py on the host; test /start -> Launch SlimeFi
5. Test referral deep link: t.me/slimefi_bot?start=SLIME-XXXXXX
6. Public launch
