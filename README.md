# Solana Degen Pipeline

A personal automation suite for high-risk Solana memecoin trading. Three standalone
Python processes that communicate via files on disk: a daily gem scanner that ranks
new tokens from DexScreener, a real-time liftoff sniper that detects explosive
momentum every 3 minutes, and an auto-trading bot that consumes sniper alerts and
manages positions with a cascading take-profit / stop-loss strategy. A local web
dashboard surfaces open positions, trade history, and past scans. **This is a
personal project — not packaged or supported for redistribution.**

## Requirements

- Python 3.9 or newer (verified on 3.14)
- `pip` for dependency installation
- Outbound HTTPS access to: `api.dexscreener.com`, `quote-api.jup.ag`,
  `api.rugcheck.xyz`, `api.mainnet-beta.solana.com` (or your custom RPC),
  `frankfurt.mainnet.block-engine.jito.wtf`

## Setup

```bash
# 1. Clone
git clone <your-repo-url> degen-pipeline
cd degen-pipeline

# 2. Create a virtualenv
python -m venv .venv
source .venv/bin/activate         # Linux/macOS
# .venv\Scripts\activate           # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env: paste your wallet private key (or leave SOL_PRIVATE_KEY empty
# and create bot_wallet.key with the base58 key on a single line)
# Set SOLANA_RPC_URL to a paid endpoint if you have one

# 5. Optional: generate a fresh throwaway wallet for dry-run testing
python -c "from solders.keypair import Keypair; import base58; kp=Keypair(); \
  print('Public:', kp.pubkey()); print('Private:', base58.b58encode(bytes(kp)).decode())"
```

## Running each component

Each script runs in its own terminal. They communicate through `sniper_alerts/`,
`positions.json`, and `trade_log.ndjson` — no sockets, no queue.

```bash
# Daily gem scanner — one-shot, writes report to reports/ and an entry to scanner_history.db
python solana_gem_scanner.py

# Real-time liftoff sniper — runs continuously, writes alert JSONs to sniper_alerts/
python liftoff_sniper.py
python liftoff_sniper.py --once   # single scan and exit

# Auto-trading bot
python trading_bot.py --dry-run            # default — no real transactions
python trading_bot.py --live               # real money mode
python trading_bot.py --status             # show current positions / wallet balance
python trading_bot.py --buy <CA> 0.01      # manual buy for 0.01 SOL
python trading_bot.py --sell <CA> 50       # manual sell 50% of position
python trading_bot.py --withdraw <addr>    # sweep SOL balance to a destination wallet

# Manual token safety check (uses the same 6-layer gate as the bot's pre-buy)
python safety_module.py <CA>              # legacy check
python safety_module.py <CA> --full       # full 6-layer check

# Local web dashboard — http://localhost:8420
python dashboard_local.py
```

### Recommended terminal layout for the full pipeline

```
[Terminal 1]  python liftoff_sniper.py
[Terminal 2]  python trading_bot.py --dry-run
[Terminal 3]  python dashboard_local.py     # browser opens automatically
```

Stop each component with Ctrl+C. All state is on disk, so safe to restart.

## Pre-live checklist

Before flipping `--live`, audit the open items tracked in conversation history:

- Live transaction confirmation against on-chain balance (do not trust the swap
  call's `outAmount` as proof of execution)
- File locking on `positions.json` / `trade_log.ndjson` (or migrate state to SQLite)
- Crash-recovery mid-sell: write a `pending_sell` marker before `send_transaction`
- Remove all `.lower()` calls on Solana token addresses (base58 is case-sensitive)
- Reconcile any historical positions whose `total_sold_sol` was clamped by the
  sanity cap — those are estimates, not real outcomes

## Files / layout

```
solana_gem_scanner.py   — daily scanner (DexScreener → reports/ + scanner_history.db)
liftoff_sniper.py       — continuous monitor (DexScreener → sniper_alerts/)
trading_bot.py          — auto-trader (sniper_alerts/ → Jupiter → positions.json)
safety_module.py        — 6-layer pre-buy safety gate + continuous safety checks
dashboard_local.py      — local web UI on :8420
db.py                   — SQLite helpers for scanner_history.db
.env.example            — environment variable template
requirements.txt        — pinned Python dependencies
```

## License / use

Personal project. No license granted, no support, no warranty. Trading crypto
with this code can and will lose money. The default `--dry-run` flag exists
for a reason — leave it on until you are sure of every line.
