# Gem Scanner — Solana Token Discovery & Trading Automation

> Autonomous AI-built pipeline that scans Solana for emerging tokens, scores
> them with a 6-layer safety system, and simulates trades with a cascading
> TP/SL strategy.

A portfolio project exploring what it actually takes to ship an autonomous
trading system end-to-end: ingestion → signal scoring → safety gating →
execution → monitoring → web UI → deployment. Built iteratively with
Claude Code as the primary development tool, currently running on AWS in
**dry-run mode** (no real money is moved).

---

## Architecture overview

Three independent Python services plus a Next.js dashboard, deployed on a
single AWS EC2 instance. Services don't talk over sockets or queues —
they communicate by writing JSON / NDJSON / SQLite to disk, atomically.
The Flask backend exposes that on-disk state as a JSON API which the
Next.js frontend consumes.

```
   DexScreener  ·  RugCheck  ·  Solana RPC  ·  Jupiter  ·  Jito
                              │
                              ▼
   ┌───────────────────────────────────────────────────┐
   │  liftoff_sniper.py       (3-min scan loop)        │
   │     scores momentum → writes sniper_alerts/*.json │
   └───────────────────────────────────────────────────┘
                              │
                              ▼
   ┌───────────────────────────────────────────────────┐
   │  trading_bot.py          (15-30s cycle)           │
   │     polls sniper_alerts/                          │
   │       → safety_module.py  (6-layer pre-buy gate)  │
   │       → Jupiter quote + swap                      │
   │       → Jito block-engine submit (MEV protection) │
   │     manages cascading TP/SL on open positions     │
   └───────────────────────────────────────────────────┘
                              │
                              ▼
       positions.json  ·  trade_log.ndjson  ·  scanner_history.db
                              │
                              ▼
   ┌───────────────────────────────────────────────────┐
   │  dashboard_local.py      (Flask, port 8420)       │
   │     /api/positions  /api/trades  /api/system-status│
   │     /api/metrics-summary  /api/history            │
   └───────────────────────────────────────────────────┘
                              │
                              ▼
   ┌───────────────────────────────────────────────────┐
   │  dashboard-web/          (Next.js 14, port 3000)  │
   │     SWR + Recharts, 30s auto-refresh              │
   └───────────────────────────────────────────────────┘
```

---

## Tech stack

**Backend**
- Python 3.10+ (deployed on 3.12), stdlib `http.server` for the API surface,
  `sqlite3` for scan history, `requests` for outbound HTTP.
- Domain libraries: `solana`, `solders`, `base58` for transaction signing.

**Frontend**
- Next.js 14 (App Router) · TypeScript (strict) · React 18
- Tailwind CSS · SWR (30-second auto-refresh) · Recharts
- Custom design system with three Google Fonts (Space Grotesk / Fraunces /
  JetBrains Mono) loaded via `next/font` (no external link tags).

**Infrastructure**
- AWS EC2 t3.micro (Ubuntu 24.04)
- systemd unit files with auto-restart for each of the three services
- `cron` job for nightly state backups (14-day retention)
- SSH tunneling for dashboard access (no public ports exposed)

**External APIs**
- [DexScreener](https://docs.dexscreener.com/) — market data, free tier
- [Jupiter](https://station.jup.ag/docs/apis/swap-api) — DEX quotes + swaps
- [RugCheck](https://api.rugcheck.xyz) — token safety scores + holders
- Solana RPC (mainnet-beta or paid endpoint)
- [Jito Block Engine](https://jito-labs.gitbook.io/mev/) — MEV-protected tx submission

**Dev tools**
- Claude Code (primary development tool; this entire project is the result of
  iterative pairing with Claude Sonnet 4.5)
- Git + GitHub for source control and PR-based review
- KeePass for credential storage; Docker for an N8N alerting sidecar

---

## Key features

- **Real-time momentum detection** — sniper scans every 3 minutes across
  DexScreener trending categories and themed search queries (`pump`, `bonk`,
  `ai agent`, …), scoring each pair against 9 signals (5m/1h volume spike,
  buy pressure, price momentum, volume acceleration, freshness, social
  profile).
- **6-layer safety gate** wrapped around every potential buy:
  1. Honeypot simulation (multi-amount Jupiter quote round-trip)
  2. Mint and freeze authority revocation check (live RPC re-check on each
     bot cycle, with emergency-sell trigger on restore)
  3. LP burn / lock verification with **short-lock rejection** (LP that
     unlocks in &lt;30 days is treated as a delayed rug)
  4. Top-10 + top-20 holder concentration limits
  5. Deployer reputation — detects serial ruggers from RugCheck history (3+
     rugged tokens in 30 days → block)
  6. Continuous liquidity monitoring per open position (>50% drop = emergency
     sell)
- **Cascading take-profit** — TP1 at +30% sells just enough to recover the
  original SOL investment (turning the rest into a risk-free moonbag), then
  sells 12% of remaining tokens at each +30% step (widening to +50% steps
  after cascade level 2). Stop-loss is only armed pre-TP1.
- **Dynamic bot cycle** — interval adjusts to load: 30 s when idle, 15 s
  with 1-5 open positions, 30 s with 6+ to avoid Jupiter/DexScreener rate
  limits.
- **Atomic file-based IPC** — every state write goes to `*.tmp` followed by
  `os.replace()` so a mid-write crash never leaves a half-written
  `positions.json`. Read-modify-write merges prevent races when the
  trading bot and a manual `--sell` command touch the file concurrently.
- **Adversarial code-review workflow** — when Claude proposes a change, the
  follow-up turn asks a fresh Claude instance to red-team it. Several
  shipped fixes (case-sensitive Solana address comparison, duplicate-buy
  prevention, dust-position auto-close) came from that loop.
- **Web dashboard with auto-refresh** — Next.js frontend fetches via SWR
  every 30 s; the trades panel auto-scrolls when a genuinely new event lands
  (compared against the last-known head timestamp, not just any refresh).
- **Daily backups via cron** — `backup_data.sh` snapshots `positions.json`,
  `trade_log.ndjson`, `processed_alerts.json`, and `scanner_history.db`
  into `backups/YYYY-MM-DD/` with 14-day retention.

---

## Deployment (AWS)

```
┌─ t3.micro · Ubuntu 24.04 ──────────────────────────────┐
│                                                        │
│  /home/ubuntu/gem-scanner/   (git pull from GitHub)    │
│                                                        │
│   systemd units                                        │
│     gem-sniper.service     (liftoff_sniper.py)         │
│     gem-bot.service        (trading_bot.py --dry-run)  │
│     gem-dashboard.service  (dashboard_local.py)        │
│       Restart=always, RestartSec=10                    │
│                                                        │
│   cron                                                 │
│     0 3 * * *  /home/ubuntu/gem-scanner/backup_data.sh │
│                                                        │
│   Dashboard reached via SSH tunnel from local machine: │
│     ssh -L 8420:localhost:8420 ubuntu@<ip>             │
└────────────────────────────────────────────────────────┘
```

No public ports. Code updates land via `git pull` after merging a PR on the
public GitHub repo. systemd restarts handle process crashes; the
file-based state model means restarts are always safe.

---

## Local development

```bash
# Clone
git clone https://github.com/olekklimek97/gem-scanner.git
cd gem-scanner

# ─── Backend ────────────────────────────────────────────
python -m venv .venv
.venv\Scripts\activate         # Windows
source .venv/bin/activate      # Linux / macOS
pip install -r requirements.txt
cp .env.example .env            # then edit: SOL_PRIVATE_KEY, SOLANA_RPC_URL

# In three separate terminals:
python liftoff_sniper.py
python trading_bot.py --dry-run
python dashboard_local.py       # Flask backend on :8420

# ─── Frontend (new terminal) ───────────────────────────
cd dashboard-web
npm install
npm run dev                     # Next.js on :3000
```

Then open **http://localhost:3000**. The legacy HTML dashboard remains
available at `http://localhost:8420/` if you want to compare.

### CLI tools

```bash
python solana_gem_scanner.py            # one-shot daily scan
python trading_bot.py --status          # show open positions
python trading_bot.py --buy <CA> 0.01   # manual buy
python trading_bot.py --sell <CA> 50    # sell 50%
python safety_module.py <CA> --full     # run the 6-layer gate manually
```

---

## What I learned

- **Designing atomic file-based IPC** between independent Python processes
  — `*.tmp` + `os.replace()`, read-modify-write merges, and graceful
  recovery from partial writes — turned out to be a surprisingly rich
  problem once concurrent edits (bot cycle + manual CLI command) entered
  the picture.
- **Production deployment pipelines** that go beyond a single script:
  laptop → GitHub PR → server `git pull` → systemd restart → cron backups
  → SSH-tunneled monitoring.
- **Iterative development with Claude Code**, including using a fresh
  Claude instance to adversarially review the previous one's diff. Several
  real bugs were caught this way that wouldn't have surfaced from
  self-review.
- **Bridging Python REST APIs to a modern TypeScript frontend** — CORS
  preflight, strict TS hooks over SWR, schema co-design between Flask
  endpoint and `types/index.ts`.
- **Trading-bot architecture** — keeping discovery, safety, execution,
  and monitoring as separable concerns so each can be reasoned about,
  tested, and replaced independently.

---

## Project status

- **Mode:** `--dry-run` (the default). Trades are simulated against live
  market data; no SOL leaves the wallet.
- **Pre-live blockers tracked** (intentional, not bugs):
  - Live transaction confirmation against on-chain balance (currently
    trusts Jupiter's `outAmount` as proof of execution)
  - File locking on `positions.json` / `trade_log.ndjson` for hardened
    concurrent access (or a SQLite migration)
  - Crash-recovery mid-sell: write a `pending_sell` marker before
    `send_transaction` so reconciliation knows what was in flight
  - Reconcile historical positions whose `total_sold_sol` was clamped by
    the implausibility sanity cap — those are estimates, not real outcomes
- **Dashboard:** functional but deliberately minimal. The current panels
  (hero metrics, system health, positions, trades, scan-history chart) are
  the obvious extension points — adding per-position drill-down,
  configurable score thresholds, or a backtest view would all be
  reasonable next steps.

---

## Screenshots

_Coming soon — captures of the Next.js dashboard, the CLI status output,
and the AWS systemd service status._

---

## License / notes

Personal project built as a portfolio piece. Code is shared openly for
inspection and learning. Trading crypto is risky — this project is in
**dry-run mode** and is **not financial advice**. If you fork it and flip
`--live`, you own the consequences.
