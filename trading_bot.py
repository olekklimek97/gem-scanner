#!/usr/bin/env python3
"""
🤖 SOLANA DEGEN TRADING BOT
=============================
Automatyczny bot tradingowy na Solanie.
Kupuje tokeny znalezione przez Liftoff Sniper, zarządza pozycjami.

Strategia:
  1. Scanner znajduje token z wysokim score
  2. Bot kupuje za ustaloną kwotę SOL
  3. Monitoruje cenę co 30s
  4. Take profit: przy +X% sprzedaje wkład, zostawia moonbag
  5. Stop loss: przy -X% sprzedaje wszystko

SETUP:
  1. pip install solana solders requests base58
  2. Stwórz NOWY wallet (NIGDY nie używaj głównego!)
  3. Wrzuć na niego niewielką ilość SOL (np. 0.5 SOL)
  4. Skopiuj private key i wklej do .env

Uruchomienie:
  python trading_bot.py                    # Tryb auto (kupuje z snipera)
  python trading_bot.py --buy <CA> 0.01    # Ręczny zakup za 0.01 SOL
  python trading_bot.py --sell <CA> 50     # Sprzedaj 50% pozycji
  python trading_bot.py --status           # Pokaż pozycje
  python trading_bot.py --dry-run          # Symulacja bez prawdziwych transakcji

⚠️  DISCLAIMER: To nie jest porada inwestycyjna.
    Bot operuje na REAL MONEY. Używaj tylko pieniędzy które możesz stracić.
"""

import requests
import json
import time
import sys
import os
import base64
import struct
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# Import safety module
try:
    from safety_module import (
        run_safety_check, run_full_safety_check, summarize_full_report,
        check_and_sweep, SAFETY_CONFIG,
        run_continuous_safety_checks, NEW_SAFETY_CONFIG,
    )
    SAFETY_AVAILABLE = True
except ImportError:
    SAFETY_AVAILABLE = False
    print("  ⚠️  safety_module.py not found — safety checks DISABLED")
    print("  Place safety_module.py in the same folder as trading_bot.py")


def _log_terminal_transition_if_needed(trade_log, position, status_before: str, reason: str):
    """Emit a structured trade-log event when a sell flips a position into a
    special terminal state ("rugged" from C-5 drained pool, "suspect" from the
    100x implausibility guard). The 24h summary at startup counts these.

    No-op if trade_log is None, if the status didn't actually transition into
    one of the special states, or if it was already in that state before."""
    if trade_log is None:
        return
    after = getattr(position, "status", "")
    if after not in ("rugged", "suspect"):
        return
    if status_before == after:
        # Already in this state — don't re-log on subsequent cycles.
        return
    try:
        action = "position_rugged" if after == "rugged" else "position_suspect"
        trade_log.log({
            "action": action,
            "token": getattr(position, "token_symbol", "?"),
            "token_address": getattr(position, "token_address", ""),
            "status_before": status_before,
            "status_after": after,
            "trigger_reason": reason,
            "buy_amount_sol": getattr(position, "buy_amount_sol", 0),
            "total_sold_sol": getattr(position, "total_sold_sol", 0),
        })
    except Exception as e:
        print(f"     ⚠️  Could not log terminal transition: {e}")


def _print_24h_summary(trade_log) -> None:
    """Print a one-line summary of safety-related refusals in the last 24h.

    Counts:
      - buy_refused events with sim_honeypot_unverified=True (C-4)
      - buy_refused events with sim_rugcheck_unavailable=True (H-4)
      - position_rugged events (C-5 drained-pool transitions)
      - position_suspect events (100x implausibility guard transitions)

    Safe to call before main loop start; no-op if trade_log is empty / unreadable.
    """
    try:
        events = trade_log.read_all()
    except Exception as e:
        print(f"  ⚠️  Could not read trade log for 24h summary: {e}")
        return

    if not events:
        print(f"  📊 Last 24h: trade log empty (fresh bot)")
        return

    cutoff_iso = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    recent = [e for e in events if isinstance(e, dict) and e.get("timestamp", "") >= cutoff_iso]

    hp_refused = sum(
        1 for e in recent
        if e.get("action") == "buy_refused" and e.get("sim_honeypot_unverified")
    )
    rc_refused = sum(
        1 for e in recent
        if e.get("action") == "buy_refused" and e.get("sim_rugcheck_unavailable")
    )
    rugged = sum(1 for e in recent if e.get("action") == "position_rugged")
    suspect = sum(1 for e in recent if e.get("action") == "position_suspect")
    buys = sum(1 for e in recent if e.get("action") == "buy")

    print(f"  📊 Last 24h: {buys} buys executed, "
          f"{hp_refused} refused (honeypot unverified), "
          f"{rc_refused} refused (rugcheck unavailable), "
          f"{rugged} positions marked rugged"
          + (f", {suspect} marked suspect" if suspect else ""))


def log_safety_event(trade_log, event: str, position, details: dict):
    """Record a safety event in trade_log.json for the dashboard."""
    try:
        trade_log.log({
            "action": "safety_event",
            "event": event,
            "token": getattr(position, "token_symbol", "?"),
            "token_address": getattr(position, "token_address", ""),
            "details": details,
        })
    except Exception as e:
        print(f"     ⚠️  Failed to log safety event: {e}")

# ─── KONFIGURACJA ───────────────────────────────────────────

CONFIG = {
    # ── Wallet ──
    # OPCJA 1: Private key bezpośrednio (base58)
    "private_key": os.environ.get("SOL_PRIVATE_KEY", ""),

    # OPCJA 2: Ścieżka do pliku z private key
    "private_key_file": "bot_wallet.key",

    # ── RPC ──
    # Domyślny publiczny RPC (wolny ale darmowy)
    # Dla lepszej szybkości użyj Helius/QuickNode (darmowy tier)
    "rpc_url": os.environ.get("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com"),

    # ── Jito MEV Protection ──
    # Wysyła transakcje przez Jito Block Engine zamiast publicznego mempoola
    # = ochrona przed sandwich attacks
    "use_jito": True,
    "jito_url": "https://frankfurt.mainnet.block-engine.jito.wtf/api/v1/transactions",
    "jito_tip_lamports": 10_000,       # 0.00001 SOL tip dla Jito (min 1000)
    "jito_tip_accounts": [             # Losowy wybór z oficjalnych tip accounts
        "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
        "HFqU5x63VTqvQss8hp11i4bVqkfRtQ7NmXwkiY8aq1Gs",
        "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY",
        "ADaUMid9yfUC5i9u4Xn1J33JFatd5Dn14t2LHLqYJ6J3",
        "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh",
        "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt",
        "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL",
        "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT",
    ],

    # ── Jupiter API ──
    "jupiter_quote_url": "https://quote-api.jup.ag/v6/quote",
    "jupiter_swap_url": "https://quote-api.jup.ag/v6/swap",

    # ── Trading Parameters ──
    "buy_amount_sol": 0.01,            # Ile SOL wydać na jeden zakup (~$1.50)
                                       # Reviewer: 0.003 jest za małe — fees zjadają 5-30%
    "max_positions": 10,               # Max otwartych pozycji
    "max_daily_buys": 15,              # Max zakupów dziennie

    # ── Take Profit Strategy (kaskadowy) ──
    "take_profit_pct": 30,             # TP1 trigger: +30% → recover invested SOL
    "moonbag_sell_pct": 0.12,          # Fraction of REMAINING tokens to sell on each cascade (12%)
    "early_cascade_interval": 0.30,    # Spacing between TPs before widening (30%)
    "late_cascade_interval": 0.50,     # Spacing between TPs after widening (50%)
    "cascade_widen_after_level": 2,    # After cascade_level reaches this, switch to late interval
                                       # Schedule: TP1 30%, TP2 60%, TP3 90%, TP4 140%, TP5 190%, ...
    "min_moonbag_value_sol": 0.005,    # Below this, fees > profit — hold instead of cascade-selling

    # Deprecated keys kept for back-compat with stale positions.json / show_status display:
    "cascade_interval_pct": 30,        # superseded by early_cascade_interval × 100
    "cascade_sell_pct": 25,             # superseded by moonbag_sell_pct × 100

    # ── Stop Loss ──
    "stop_loss_pct": -30,              # Sprzedaj wszystko przy -30%

    # ── Slippage ──
    "slippage_bps": 500,               # 5% slippage (reduced from 15% — Jito protects against sandwich)

    # ── Priority Fee ──
    "priority_fee_lamports": 100_000,  # 0.0001 SOL priority fee

    # ── Monitoring ──
    "price_check_interval": 30,        # Sprawdzaj ceny co 30s
    "min_score_to_buy": 70,            # Min score z snipera żeby kupił (70+ = STRONG)

    # ── Files ──
    "positions_file": "positions.json",
    "trade_log_file": "trade_log.json",

    # ── Safety ──
    "dry_run": True,                   # DOMYŚLNIE True — musisz wyłączyć jawnie: --live

    # ── Dry-run realism parameters (C-2, C-3) ──
    # Applied only in dry-run code paths. Live mode uses Jupiter's actual fees
    # baked into the quote and pays real on-chain costs.
    "jupiter_fee_pct": 0.0085,         # Jupiter's blended platform fee (0.85%)
    "network_fee_sol": 0.000115,       # base sig (5k) + priority (100k) + Jito tip (10k) in SOL

    # ── Drained pool guard (C-5) ──
    # Below this liquidity, the pool has effectively no counterparty. The
    # stale DexScreener priceNative cannot be honored on-chain. Dry-run
    # records 0 proceeds and marks position as "rugged" (vs "closed"/"dust").
    "drained_pool_threshold_usd": 500,

    # ── Fill simulation timing (H-1) ──
    # Real live: trigger-detection → quote → submit → confirm takes 10-40s,
    # during which memecoin price moves 5-15%. We sample twice with this
    # delay and use the worse price (lower for sells, higher for buys) to
    # model the submit→confirm window. Set to 0 in tests to skip the wait.
    "fill_delay_sec": 8,
}

# Solana constants
SOL_MINT = "So11111111111111111111111111111111111111111112"
LAMPORTS_PER_SOL = 1_000_000_000

# Quote-currency mints used by the dry-run pair resolver. Most new pump.fun
# tokens trade against USDC, not SOL — so an SOL-only filter would block them.
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
USD_STABLE_MINTS = frozenset({USDC_MINT, USDT_MINT})

# Reference pair used to convert USD-quoted prices → SOL-equivalent. This is
# the Raydium SOL/USDC pool on Solana — the canonical reference. The pair
# ADDRESS is hardcoded (it doesn't move); the PRICE is always fetched live.
SOL_USDC_REF_PAIR = "58oQChx4yWmvKdwLLZzBi4ChoCc2fqCUWBkwMihLYQo2"
_SOL_USD_CACHE = {"price_usd": 0.0, "expires_at": 0.0}
_SOL_USD_CACHE_TTL_SEC = 300  # 5 minutes — short enough to track market moves,
                              # long enough to avoid hammering DexScreener on
                              # every position cycle.


# ─── WALLET ─────────────────────────────────────────────────

class Wallet:
    """Simple wallet wrapper."""

    def __init__(self, private_key_b58: str):
        try:
            from solders.keypair import Keypair
            import base58
            self.keypair = Keypair.from_bytes(base58.b58decode(private_key_b58))
            self.public_key = str(self.keypair.pubkey())
            self.ready = True
        except ImportError:
            print("  ⚠️  Pakiety solana/solders nie zainstalowane.")
            print("  Uruchom: pip install solana solders base58")
            self.ready = False
        except Exception as e:
            print(f"  ❌ Błąd ładowania walleta: {e}")
            self.ready = False

    def sign_transaction(self, raw_tx_b64: str) -> Optional[str]:
        """Sign a base64-encoded transaction and return signed base64."""
        try:
            from solders.transaction import VersionedTransaction
            raw_bytes = base64.b64decode(raw_tx_b64)
            tx = VersionedTransaction.from_bytes(raw_bytes)
            signed_tx = VersionedTransaction(tx.message, [self.keypair])
            return base64.b64encode(bytes(signed_tx)).decode('utf-8')
        except Exception as e:
            print(f"  ❌ Błąd podpisywania tx: {e}")
            return None


def load_wallet() -> Optional[Wallet]:
    """Load wallet from config."""
    pk = CONFIG["private_key"]

    # Try env var first
    if pk:
        return Wallet(pk)

    # Try file
    pk_file = Path(CONFIG["private_key_file"])
    if pk_file.exists():
        pk = pk_file.read_text().strip()
        if pk:
            return Wallet(pk)

    return None


# ─── JUPITER SWAP ───────────────────────────────────────────

# Circuit breaker for Jupiter — avoids 10s timeouts on every pre-buy when DNS/network is broken.
_JUPITER_CB = {
    "consecutive_failures": 0,
    "open_until": 0.0,           # epoch seconds; if time.time() < this, breaker is OPEN (skip calls)
    "fail_threshold": 3,         # open the breaker after N consecutive failures
    "open_duration_sec": 60,     # how long to stay open before testing again
}


def _jupiter_breaker_open() -> bool:
    """Returns True if the breaker is currently OPEN (i.e. we should skip Jupiter)."""
    return time.time() < _JUPITER_CB["open_until"]


def _jupiter_record_failure():
    _JUPITER_CB["consecutive_failures"] += 1
    if _JUPITER_CB["consecutive_failures"] >= _JUPITER_CB["fail_threshold"]:
        _JUPITER_CB["open_until"] = time.time() + _JUPITER_CB["open_duration_sec"]
        print(f"  ⚡ Jupiter circuit breaker OPEN for {_JUPITER_CB['open_duration_sec']}s "
              f"after {_JUPITER_CB['consecutive_failures']} failures")


def _jupiter_record_success():
    if _JUPITER_CB["consecutive_failures"] > 0 or _JUPITER_CB["open_until"] > 0:
        _JUPITER_CB["consecutive_failures"] = 0
        _JUPITER_CB["open_until"] = 0.0


def get_quote(input_mint: str, output_mint: str, amount: int, slippage_bps: int = None) -> dict:
    """Get Jupiter swap quote.
    Short-circuits to None when the circuit breaker is open (no 10s timeout per call)."""
    if _jupiter_breaker_open():
        return None  # silent — breaker prints once when it opens

    if slippage_bps is None:
        slippage_bps = CONFIG["slippage_bps"]

    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": str(amount),
        "slippageBps": slippage_bps,
    }
    try:
        resp = requests.get(CONFIG["jupiter_quote_url"], params=params, timeout=15)
        resp.raise_for_status()
        _jupiter_record_success()
        return resp.json()
    except Exception as e:
        print(f"  ❌ Quote error: {e}")
        _jupiter_record_failure()
        return None


def get_swap_transaction(quote: dict, user_pubkey: str) -> str:
    """Get serialized swap transaction from Jupiter."""
    payload = {
        "quoteResponse": quote,
        "userPublicKey": user_pubkey,
        "wrapAndUnwrapSol": True,
        "prioritizationFeeLamports": CONFIG["priority_fee_lamports"],
    }
    try:
        resp = requests.post(CONFIG["jupiter_swap_url"], json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get("swapTransaction", "")
    except Exception as e:
        print(f"  ❌ Swap tx error: {e}")
        return ""


def send_transaction(signed_tx_b64: str) -> Optional[str]:
    """Send signed transaction — via Jito Block Engine if enabled, else standard RPC."""

    if CONFIG.get("use_jito", False):
        # ── JITO MEV-PROTECTED SEND ──
        # bundleOnly=true = single-tx bundle, skips public mempool
        jito_url = CONFIG["jito_url"] + "?bundleOnly=true"
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendTransaction",
            "params": [
                signed_tx_b64,
                {"encoding": "base64"}
            ]
        }
        try:
            resp = requests.post(jito_url, json=payload, timeout=30)
            data = resp.json()
            if "result" in data:
                print(f"     🛡️ Sent via Jito (MEV protected)")
                return data["result"]
            elif "error" in data:
                print(f"  ⚠️  Jito error: {data['error']} — falling back to standard RPC")
                # Fall through to standard RPC
            else:
                print(f"  ⚠️  Jito unexpected response — falling back to standard RPC")
        except Exception as e:
            print(f"  ⚠️  Jito failed ({e}) — falling back to standard RPC")

    # ── STANDARD RPC SEND (fallback) ──
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "sendTransaction",
        "params": [
            signed_tx_b64,
            {"encoding": "base64", "maxRetries": 3, "skipPreflight": True}
        ]
    }
    try:
        resp = requests.post(CONFIG["rpc_url"], json=payload, timeout=30)
        data = resp.json()
        if "result" in data:
            return data["result"]
        elif "error" in data:
            print(f"  ❌ TX error: {data['error']}")
            return None
    except Exception as e:
        print(f"  ❌ Send TX error: {e}")
        return None


def get_sol_balance(pubkey: str) -> float:
    """Get SOL balance."""
    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method": "getBalance",
        "params": [pubkey]
    }
    try:
        resp = requests.post(CONFIG["rpc_url"], json=payload, timeout=10)
        data = resp.json()
        lamports = data.get("result", {}).get("value", 0)
        return lamports / LAMPORTS_PER_SOL
    except:
        return 0.0


def get_token_balance(pubkey: str, mint: str) -> int:
    """Get token balance for a specific mint."""
    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method": "getTokenAccountsByOwner",
        "params": [
            pubkey,
            {"mint": mint},
            {"encoding": "jsonParsed"}
        ]
    }
    try:
        resp = requests.post(CONFIG["rpc_url"], json=payload, timeout=10)
        data = resp.json()
        accounts = data.get("result", {}).get("value", [])
        if accounts:
            info = accounts[0]["account"]["data"]["parsed"]["info"]["tokenAmount"]
            return int(info["amount"])
        return 0
    except:
        return 0


def get_token_price_sol(mint: str) -> float:
    """Get token price in SOL using Jupiter quote."""
    # Get price of 1M tokens worth in SOL
    quote = get_quote(mint, SOL_MINT, 1_000_000_000, slippage_bps=100)
    if quote:
        out_amount = int(quote.get("outAmount", 0))
        return out_amount / LAMPORTS_PER_SOL
    return 0.0


def get_dexscreener_price(token_address: str, pinned_pair_address: str = "") -> tuple:
    """Get token price from DexScreener. Returns (price_in_sol, price_in_usd, pair_address_used).

    CRITICAL: DexScreener `priceNative` is denominated in the QUOTE TOKEN of the pair
    (SOL for SOL/X pairs, USDC for USDC/X pairs, etc.). If we naively pick the
    highest-liquidity pair we may grab a USDC- or USDT-quoted pair and treat its
    priceNative (USD per token) as if it were SOL per token — producing PnL values
    that are off by ~150x or more.

    This function therefore restricts to SOL-quoted pairs only:
      - If `pinned_pair_address` is provided AND it's a SOL-quoted pair AND it still
        has liquidity → use it.
      - Otherwise pick the highest-liquidity SOL-quoted pair.
      - If no SOL-quoted pair exists, return (0, 0, "") rather than guessing.

    The pinned-pair behavior keeps PnL math consistent across cycles; the SOL-only
    filter eliminates the cross-quote bug that produced 374-billion-x phantom returns.
    """
    try:
        resp = requests.get(
            f"https://api.dexscreener.com/latest/dex/tokens/{token_address}",
            headers={"Accept": "application/json", "User-Agent": "DegenBot/1.0"},
            timeout=15,
        )
        resp.raise_for_status()
        pairs = resp.json().get("pairs") or []
        if not pairs:
            return 0.0, 0.0, ""

        # Restrict to SOL-quoted pairs (where priceNative is in SOL)
        sol_pairs = [p for p in pairs if (p.get("quoteToken") or {}).get("address") == SOL_MINT]
        if not sol_pairs:
            return 0.0, 0.0, ""

        chosen = None
        if pinned_pair_address:
            for p in sol_pairs:
                if p.get("pairAddress", "") == pinned_pair_address:
                    liq = (p.get("liquidity") or {}).get("usd", 0) or 0
                    if liq > 0:
                        chosen = p
                    break
        if chosen is None:
            chosen = max(sol_pairs, key=lambda p: (p.get("liquidity") or {}).get("usd", 0))

        # Final guard: if the chosen pair has zero liquidity, the priceNative on it
        # may be stale/garbage. Refuse to use it.
        chosen_liq = (chosen.get("liquidity") or {}).get("usd", 0) or 0
        if chosen_liq <= 0:
            return 0.0, 0.0, ""

        price_sol = float(chosen.get("priceNative") or 0)
        price_usd = float(chosen.get("priceUsd") or 0)
        return price_sol, price_usd, chosen.get("pairAddress", "")
    except Exception as e:
        print(f"     ⚠️  DexScreener price error: {e}")
    return 0.0, 0.0, ""


def _get_sol_usd_price() -> float:
    """Cached SOL/USD reference from the Raydium SOL/USDC pool. 5-min TTL.

    Returns the cached value if fresh. On expiry, refreshes; on fetch failure,
    falls back to the stale cached value (last known good) rather than 0 so a
    transient DexScreener blip doesn't break USDC-quoted price conversions.
    Returns 0.0 only if we've *never* successfully fetched.
    """
    now = time.time()
    if _SOL_USD_CACHE["expires_at"] > now and _SOL_USD_CACHE["price_usd"] > 0:
        return _SOL_USD_CACHE["price_usd"]
    try:
        resp = requests.get(
            f"https://api.dexscreener.com/latest/dex/pairs/solana/{SOL_USDC_REF_PAIR}",
            headers={"Accept": "application/json", "User-Agent": "DegenBot/1.0"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json() or {}
        # The /pairs/ endpoint returns either {"pair": {...}} (older shape)
        # or {"pairs": [{...}]} (newer shape). Accept both.
        pair = data.get("pair") or (data.get("pairs") or [None])[0]
        if pair:
            price_usd = float(pair.get("priceUsd") or 0)
            if price_usd > 0:
                _SOL_USD_CACHE["price_usd"] = price_usd
                _SOL_USD_CACHE["expires_at"] = now + _SOL_USD_CACHE_TTL_SEC
                return price_usd
    except Exception as e:
        print(f"     ⚠️  SOL/USD reference fetch error: {e}")
    # Fall back to whatever we last had (possibly 0 on first failure)
    return _SOL_USD_CACHE["price_usd"]


def _resolve_dry_run_pair(token_address: str, pinned_pair_address: str = "") -> dict:
    """Internal helper: look up the dry-run pair and return SOL-equivalent price
    plus the metadata callers need for logging (quote symbol, dex id, ref price).

    Handles BOTH SOL-quoted and USDC/USDT-quoted pairs. Most new pump.fun tokens
    only have a USDC pool at launch, so the previous SOL-only filter was
    rejecting every buy in dry-run.

    Returns a dict with these keys (zeros on failure):
        price_sol      — SOL per token (converted from USD if needed)
        price_usd      — USD per token (from DexScreener directly)
        pair_address   — the pair actually used
        quote_symbol   — "SOL", "USDC", "USDT", … (for log lines)
        dex_id         — "raydium", "orca", "meteora", … (for log lines)
        sol_usd_ref    — the SOL/USD price used for conversion (0 for SOL-quoted)
    """
    info = {
        "price_sol": 0.0, "price_usd": 0.0, "pair_address": "",
        "quote_symbol": "", "dex_id": "", "sol_usd_ref": 0.0,
        # C-1: chosen pair's liquidity (USD) — used by slippage model.
        # Zero on failure paths or when no chosen pair.
        "liquidity_usd": 0.0,
    }
    try:
        resp = requests.get(
            f"https://api.dexscreener.com/latest/dex/tokens/{token_address}",
            headers={"Accept": "application/json", "User-Agent": "DegenBot/1.0"},
            timeout=15,
        )
        resp.raise_for_status()
        pairs = (resp.json() or {}).get("pairs") or []
    except Exception as e:
        print(f"     ⚠️  DexScreener fetch error: {e}")
        return info

    if not pairs:
        return info

    # Solana chain only, with non-zero liquidity (dead pairs report stale prices)
    live_pairs = [
        p for p in pairs
        if p.get("chainId") == "solana"
        and ((p.get("liquidity") or {}).get("usd", 0) or 0) > 0
    ]
    if not live_pairs:
        return info

    # Prefer the pinned pair if it's still live — keeps PnL math consistent
    # across cycles. Otherwise pick the deepest liquidity.
    chosen = None
    if pinned_pair_address:
        for p in live_pairs:
            if p.get("pairAddress", "") == pinned_pair_address:
                chosen = p
                break
    if chosen is None:
        chosen = max(live_pairs, key=lambda p: (p.get("liquidity") or {}).get("usd", 0))

    quote_addr = (chosen.get("quoteToken") or {}).get("address", "")
    info["pair_address"] = chosen.get("pairAddress", "")
    info["quote_symbol"] = (chosen.get("quoteToken") or {}).get("symbol", "?")
    info["dex_id"] = chosen.get("dexId", "?")
    info["price_usd"] = float(chosen.get("priceUsd") or 0)
    info["liquidity_usd"] = float((chosen.get("liquidity") or {}).get("usd", 0) or 0)
    price_native = float(chosen.get("priceNative") or 0)

    if quote_addr == SOL_MINT:
        # SOL-quoted: priceNative IS SOL per token; no conversion needed.
        if price_native > 0:
            info["price_sol"] = price_native
        return info

    # Non-SOL quote (USDC/USDT, and as a fallback any other quote where DexScreener
    # gives us a non-zero priceUsd we can trust). Convert via the SOL/USDC ref.
    if info["price_usd"] <= 0:
        return info
    sol_usd = _get_sol_usd_price()
    info["sol_usd_ref"] = sol_usd
    if sol_usd <= 0:
        print(f"     ⚠️  SOL/USD reference unavailable; cannot convert "
              f"{info['quote_symbol']}-quoted price for {token_address[:8]}...")
        return info
    if quote_addr not in USD_STABLE_MINTS:
        # Exotic quote — log it so we know what was used. Still safe to convert
        # via priceUsd since DexScreener has already done its own conversion.
        print(f"     ℹ️  Non-stable quote {info['quote_symbol']} for "
              f"{token_address[:8]}...; using priceUsd → SOL via ref")
    info["price_sol"] = info["price_usd"] / sol_usd
    return info


def _worst_of_two_price(token_address: str, pinned_pair: str, leg_type: str) -> dict:
    """H-1: Sample price twice with a configurable delay between, return the
    worse of the two as a price_info dict (same shape as _resolve_dry_run_pair).

    `leg_type="sell"`: use the LOWER price_sol (worse outcome for seller).
    `leg_type="buy"`:  use the HIGHER price_sol (worse outcome for buyer).

    Models the submit→confirm window where the price you observed at trigger
    has already moved by the time the swap lands. Real-life delta in this
    window is 5-15% on volatile memecoins; we don't fake the delta, we just
    sample twice and pick the worse one, letting natural market movement
    over `fill_delay_sec` provide the realism.

    If `fill_delay_sec=0` (test mode), still samples twice (the second call
    may hit a cached or different value); behavior is deterministic against
    mocks. Both samples' `liquidity_usd` are kept on the chosen one.

    On price-lookup failure (price_sol <= 0), returns the first sample
    unchanged — the caller still sees the failure and applies its existing
    branches (drained pool / no usable price / etc.).
    """
    sample1 = _resolve_dry_run_pair(token_address, pinned_pair)
    delay = max(0, CONFIG.get("fill_delay_sec", 0))
    if delay > 0:
        time.sleep(delay)
    sample2 = _resolve_dry_run_pair(token_address, pinned_pair)

    # If either sample failed, defer to whichever is valid — but prefer the
    # one with a usable price so downstream logic gets the best diagnostic.
    if sample1["price_sol"] <= 0 and sample2["price_sol"] <= 0:
        return sample1
    if sample1["price_sol"] <= 0:
        return sample2
    if sample2["price_sol"] <= 0:
        return sample1

    if leg_type == "sell":
        worse = sample1 if sample1["price_sol"] <= sample2["price_sol"] else sample2
        better = sample2 if worse is sample1 else sample1
    elif leg_type == "buy":
        worse = sample1 if sample1["price_sol"] >= sample2["price_sol"] else sample2
        better = sample2 if worse is sample1 else sample1
    else:
        return sample1  # unknown leg — just return first sample

    # Tag with a diagnostic so the caller's log line can show both samples.
    worse["_fill_sim_chosen"] = worse["price_sol"]
    worse["_fill_sim_other"] = better["price_sol"]
    worse["_fill_sim_leg"] = leg_type
    return worse


def _estimate_slippage_pct(trade_value_usd: float, liquidity_usd: float) -> float:
    """C-1: Crude price-impact model based on share of pool consumed.

    Model:
      pool_share = trade_value_usd / max(liquidity_usd, $1)
      slippage   = min(0.20, pool_share * 2.0)

    Rationale (constant-product AMM, x*y=k):
      A trade that consumes fraction f of a pool moves price by ~f / (1-f).
      For small f this is ~f; we apply a 2× multiplier to bound at 20 % since
      DexScreener's `liquidity.usd` is one-sided (the SOL/USDC side); a 50 %
      share of that == roughly 25 % of total pool depth on an AMM. The hard
      20 % cap reflects that beyond a certain point Jupiter simply won't route.

    Returns: slippage as a decimal (0.05 == 5 % price impact).
    """
    if liquidity_usd <= 0:
        # Unknown liquidity — refuse to estimate. Caller decides whether to
        # block or proceed; we don't want to silently apply 0 %.
        return 0.0
    pool_share = trade_value_usd / max(liquidity_usd, 1.0)
    return min(0.20, pool_share * 2.0)


def _apply_dry_run_costs(sol_amount: float, leg_type: str) -> tuple:
    """C-2 + C-3: Apply realistic transaction costs to a dry-run SOL amount.

    `leg_type`: "buy" or "sell".

    Live mode pays:
      - Jupiter's blended platform fee (~0.85%) on every swap
      - Solana base sig fee + priority fee + Jito tip per tx
        (default total ~0.000115 SOL per transaction)

    For a BUY: the user spends `sol_amount` gross but the swap routes only
    `gross × (1 - jup_pct)` worth of SOL into tokens, AND must pay the network
    fee on top. Net buying power = sol_amount × (1 - jup_pct) - network_fee.

    For a SELL: pool yields `sol_amount` gross; Jupiter takes its cut and we
    pay network fee. Net received = sol_amount × (1 - jup_pct) - network_fee.

    Returns (net_amount, fees_dict) where fees_dict captures the components for
    logging.
    """
    jup_pct = CONFIG["jupiter_fee_pct"]
    net_fee = CONFIG["network_fee_sol"]
    jup_drag = sol_amount * jup_pct
    net = sol_amount - jup_drag - net_fee
    net = max(0.0, net)  # never go negative — real txs would just fail
    return net, {
        "leg": leg_type,
        "gross_sol": round(sol_amount, 9),
        "jupiter_fee_sol": round(jup_drag, 9),
        "network_fee_sol": net_fee,
        "net_sol": round(net, 9),
    }


def get_dry_run_price(token_address: str, pinned_pair_address: str = "") -> tuple:
    """Backward-compatible wrapper around _resolve_dry_run_pair().

    Returns (price_sol, price_usd, pair_address_used) — same 3-tuple shape as
    get_dexscreener_price() so existing callers (e.g. execute_sell) keep working.
    For richer metadata (quote_symbol, dex_id, sol_usd_ref) used in execute_buy's
    log line, call _resolve_dry_run_pair() directly.
    """
    info = _resolve_dry_run_pair(token_address, pinned_pair_address)
    return info["price_sol"], info["price_usd"], info["pair_address"]


# ─── POSITION MANAGEMENT ────────────────────────────────────

class Position:
    """Represents an open position."""
    def __init__(self, data: dict):
        self.token_address = data["token_address"]
        self.token_symbol = data.get("token_symbol", "?")
        self.token_name = data.get("token_name", "?")
        self.buy_amount_sol = data["buy_amount_sol"]
        self.buy_amount_tokens = data["buy_amount_tokens"]
        self.buy_price_sol = data.get("buy_price_sol", 0)
        self.buy_time = data.get("buy_time", datetime.now(timezone.utc).isoformat())
        self.buy_tx = data.get("buy_tx", "")
        self.status = data.get("status", "open")  # open, partial, closed, stopped
        self.tokens_remaining = data.get("tokens_remaining", data["buy_amount_tokens"])
        self.total_sold_sol = data.get("total_sold_sol", 0)
        self.sells = data.get("sells", [])
        self.score = data.get("score", 0)
        self.url = data.get("url", "")
        self.cascade_level = data.get("cascade_level", 0)  # 0=no TP yet, 1=wkład recovered, 2+=cascades
        self.peak_pnl_pct = data.get("peak_pnl_pct", 0)   # Track highest PnL for cascade triggers

        # ── Cascade pricing reference ──
        # Per-token entry price used for cascade threshold comparisons.
        # If absent in data (e.g. stale positions.json), derive from buy_amount_sol/buy_amount_tokens.
        bppt = data.get("buy_price_per_token")
        if bppt is None or bppt <= 0:
            bppt = (self.buy_amount_sol / self.buy_amount_tokens) if self.buy_amount_tokens > 0 else 0
        self.buy_price_per_token = bppt

        # Pinned DexScreener pair address — keeps PnL math consistent if a higher-liq pair pops up
        self.pair_address = data.get("pair_address", "")

        # ── Safety baselines (continuous monitoring) ──
        self.liquidity_at_buy = data.get("liquidity_at_buy", 0)
        self.holder_snapshot = data.get("holder_snapshot", [])
        self.last_mint_check_time = data.get("last_mint_check_time", 0)
        self.last_holder_check_time = data.get("last_holder_check_time", 0)

    def to_dict(self):
        return {
            "token_address": self.token_address,
            "token_symbol": self.token_symbol,
            "token_name": self.token_name,
            "buy_amount_sol": self.buy_amount_sol,
            "buy_amount_tokens": self.buy_amount_tokens,
            "buy_price_sol": self.buy_price_sol,
            "buy_time": self.buy_time,
            "buy_tx": self.buy_tx,
            "status": self.status,
            "tokens_remaining": self.tokens_remaining,
            "total_sold_sol": self.total_sold_sol,
            "sells": self.sells,
            "score": self.score,
            "url": self.url,
            "cascade_level": self.cascade_level,
            "peak_pnl_pct": self.peak_pnl_pct,
            "buy_price_per_token": self.buy_price_per_token,
            "pair_address": self.pair_address,
            "liquidity_at_buy": self.liquidity_at_buy,
            "holder_snapshot": self.holder_snapshot,
            "last_mint_check_time": self.last_mint_check_time,
            "last_holder_check_time": self.last_holder_check_time,
        }


class PositionManager:
    """Manages all open positions."""

    def __init__(self, filepath: str):
        self.filepath = Path(filepath)
        self.positions: list[Position] = []
        self.load()

    def load(self):
        if self.filepath.exists():
            try:
                data = json.loads(self.filepath.read_text(encoding="utf-8"))
                self.positions = [Position(p) for p in data]
            except:
                self.positions = []

    # Position lifecycle: once in a terminal state the bot stops monitoring it.
    # Disk becomes authoritative for these so external edits (e.g. data corrections
    # for corrupt sells) aren't clobbered by the bot's stale in-memory copy on the
    # next save triggered by some OTHER position's activity.
    _TERMINAL_STATES = frozenset({"closed", "dust", "rugged", "stopped", "suspect"})

    def save(self):
        """Atomic save with read-modify-write merge:
        - For mutable positions (open/partial): in-memory state wins (the bot owns the truth).
        - For positions transitioning TO terminal in this save (mem terminal, disk still mutable
          or absent): in-memory state wins (we need to persist the transition).
        - For positions that were ALREADY terminal on disk (mem terminal AND disk terminal):
          disk wins. This preserves manual corrections and prevents the bot from rewriting a
          position it's no longer managing.

        Without this read-modify-write, two bugs occur:
        1. Manual fixes to positions.json get clobbered (the trigger for this fix).
        2. If two bot instances run concurrently, both write their full in-memory state and
           the loser silently loses everything. Read-modify-write narrows the race window
           and ensures terminal positions converge."""
        on_disk_by_addr = {}
        if self.filepath.exists():
            try:
                disk_data = json.loads(self.filepath.read_text(encoding="utf-8"))
                if isinstance(disk_data, list):
                    on_disk_by_addr = {
                        p.get("token_address"): p
                        for p in disk_data
                        if isinstance(p, dict) and p.get("token_address")
                    }
            except Exception:
                # Corrupt disk file — fall through and overwrite with in-memory state
                on_disk_by_addr = {}

        merged = []
        seen_addrs = set()
        for pos in self.positions:
            seen_addrs.add(pos.token_address)
            in_mem = pos.to_dict()
            disk_record = on_disk_by_addr.get(pos.token_address)
            if (pos.status in self._TERMINAL_STATES
                    and disk_record is not None
                    and disk_record.get("status") in self._TERMINAL_STATES):
                # Both terminal — disk is authoritative
                merged.append(disk_record)
            else:
                merged.append(in_mem)

        # Defensive: keep any disk-only records that vanished from memory (shouldn't happen,
        # but if it did we'd lose history without this)
        for addr, disk_record in on_disk_by_addr.items():
            if addr not in seen_addrs:
                merged.append(disk_record)

        tmp_path = self.filepath.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(str(tmp_path), str(self.filepath))  # Atomic on most OS

    def add(self, position: Position):
        self.positions.append(position)
        self.save()

    def get_open(self) -> list[Position]:
        return [p for p in self.positions if p.status in ("open", "partial")]

    def get_by_address(self, addr: str) -> Optional[Position]:
        # CASE-SENSITIVE: Solana addresses are base58, case matters.
        for p in self.positions:
            if p.token_address == addr:
                return p
        return None

    @property
    def open_count(self):
        """Only count positions where investment is still at risk (not yet recovered)."""
        return len([p for p in self.positions
                    if p.status in ("open", "partial") and p.cascade_level == 0])

    @property
    def moonbag_count(self):
        """Count moonbag positions (investment recovered, riding free)."""
        return len([p for p in self.positions
                    if p.status in ("open", "partial") and p.cascade_level >= 1])


# ─── TRADE LOG ──────────────────────────────────────────────

class TradeLog:
    """Append-only NDJSON trade log (one JSON object per line).
    Avoids the O(n^2) write pattern of rewriting the entire file on every event,
    which became a real problem once continuous safety checks started logging warnings.

    Backward compatible with the old format (a JSON array): if `trade_log.json`
    is detected as a single JSON array, it is migrated to NDJSON on first init.
    """

    def __init__(self, filepath: str):
        self.filepath = Path(filepath)
        if self.filepath.exists():
            self._migrate_if_legacy()

    def _migrate_if_legacy(self):
        """If file starts with '[', it's the old JSON-array format → rewrite as NDJSON."""
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                head = f.read(1)
            if head != "[":
                return  # already NDJSON or empty
            data = json.loads(self.filepath.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                return
            tmp = self.filepath.with_suffix(".migrate.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                for item in data:
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")
            os.replace(str(tmp), str(self.filepath))
            print(f"  📝 Migrated {len(data)} trade-log entries from JSON array → NDJSON")
        except Exception as e:
            print(f"  ⚠️  trade_log migration skipped: {e}")

    def log(self, trade: dict):
        trade["timestamp"] = datetime.now(timezone.utc).isoformat()
        # Append-only: O(1) per write.
        with open(self.filepath, "a", encoding="utf-8") as f:
            f.write(json.dumps(trade, ensure_ascii=False) + "\n")

    def read_all(self) -> list:
        """Read all events. Used by --status display and dashboard fallback."""
        if not self.filepath.exists():
            return []
        events = []
        with open(self.filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # skip corrupt lines
        return events

    def compact(self, keep_last: int = 5000):
        """Optional cleanup: keep only the most recent `keep_last` events."""
        events = self.read_all()
        if len(events) <= keep_last:
            return
        kept = events[-keep_last:]
        tmp = self.filepath.with_suffix(".compact.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for ev in kept:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        os.replace(str(tmp), str(self.filepath))
        print(f"  🗜️  Compacted trade log: kept last {keep_last} of {len(events)} events")


# ─── TRADING LOGIC ──────────────────────────────────────────

def execute_buy(wallet: Wallet, token_address: str, amount_sol: float,
                token_name: str = "?", token_symbol: str = "?",
                score: int = 0, url: str = "",
                trade_log: Optional["TradeLog"] = None) -> Optional[Position]:
    """Buy a token.

    `trade_log` (optional): when provided, refusals are logged as
    `buy_refused` events with the safety_module's diagnostic tags
    (sim_honeypot_unverified, sim_rugcheck_unavailable, …) so post-hoc
    analysis can count failure modes."""
    amount_lamports = int(amount_sol * LAMPORTS_PER_SOL)

    print(f"\n  🛒 BUYING {token_name} (${token_symbol})")
    print(f"     Amount: {amount_sol} SOL")
    print(f"     CA: {token_address}")

    # ── SAFETY CHECK (pre-buy, FULL 6-layer gate) ──
    initial_holder_snapshot = []
    if SAFETY_AVAILABLE:
        # C-4: pass dry-run flag so safety module can apply conservative posture
        # when Jupiter is unreachable (honeypot inconclusive → block in dry-run).
        full_report = run_full_safety_check(token_address, is_dry_run=CONFIG["dry_run"])
        print(summarize_full_report(full_report))
        initial_holder_snapshot = full_report.get("holder_snapshot", [])

        if not full_report["passed"]:
            if full_report.get("sim_honeypot_unverified"):
                print(f"     ❌ Buy refused: honeypot check unavailable "
                      f"(Jupiter breaker open) — conservative dry-run posture")
            if full_report.get("sim_rugcheck_unavailable"):
                print(f"     ❌ Buy refused: RugCheck unavailable "
                      f"— conservative dry-run posture (H-4)")
            print(f"     ❌ SAFETY CHECK FAILED — blocking: {full_report['blocking_reasons']}")
            # Structured refusal log so the 24h summary / dashboards can count.
            if trade_log is not None:
                try:
                    trade_log.log({
                        "action": "buy_refused",
                        "token": token_symbol,
                        "token_address": token_address,
                        "amount_sol_proposed": amount_sol,
                        "score": score,
                        "reasons": full_report["blocking_reasons"],
                        "sim_honeypot_unverified": bool(full_report.get("sim_honeypot_unverified")),
                        "sim_rugcheck_unavailable": bool(full_report.get("sim_rugcheck_unavailable")),
                    })
                except Exception as e:
                    print(f"     ⚠️  Could not log buy_refused event: {e}")
            return None
        print(f"     ✅ Full safety check passed (score: {full_report['score']}/100)")
    else:
        print(f"     ⚠️  Safety module not available — buying without checks")

    # ── Capture liquidity baseline from DexScreener ──
    liquidity_at_buy = 0
    try:
        ds = requests.get(
            f"https://api.dexscreener.com/latest/dex/tokens/{token_address}",
            headers={"Accept": "application/json", "User-Agent": "DegenBot/1.0"},
            timeout=10,
        )
        pairs = (ds.json() or {}).get("pairs") or []
        if pairs:
            best = max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd", 0))
            liquidity_at_buy = float((best.get("liquidity") or {}).get("usd", 0) or 0)
            print(f"     💧 Liquidity baseline: ${liquidity_at_buy:.0f}")
    except Exception as e:
        print(f"     ⚠️  Could not capture liquidity baseline: {e}")

    now_epoch = time.time()

    if CONFIG["dry_run"]:
        # ── DRY-RUN PRICE SOURCE ──
        # DexScreener handles both SOL- and USDC-quoted pairs (most pump.fun
        # tokens are USDC-quoted at launch). _resolve_dry_run_pair returns rich
        # metadata so we can log quote currency + DEX. Jupiter stays for live
        # execution and for the safety honeypot check.
        # H-1: sample twice with fill_delay_sec between to model the
        # submit→confirm window. For buys we pick the HIGHER (worse) price.
        price_info = _worst_of_two_price(token_address, "", leg_type="buy")
        ds_price_sol = price_info["price_sol"]
        ds_price_usd = price_info["price_usd"]
        pinned_pair = price_info["pair_address"]
        quote_symbol = price_info["quote_symbol"] or "?"
        dex_id = price_info["dex_id"] or "?"
        sol_usd_ref = price_info["sol_usd_ref"]

        if ds_price_sol <= 0:
            print(f"     ❌ DRY-RUN BUY ABORTED: no usable DexScreener price for "
                  f"{token_address[:8]}... — skipping (no useless 0-token position created)")
            return None

        # H-1 diagnostic — visible only when the two samples differ.
        if "_fill_sim_other" in price_info and price_info["_fill_sim_chosen"] != price_info["_fill_sim_other"]:
            print(f"     ⏱️  Fill simulation: price1={price_info['_fill_sim_other']:.10f}, "
                  f"price2={price_info['_fill_sim_chosen']:.10f}, "
                  f"using HIGHER (buy worse-of-2)")

        # C-2 + C-3: deduct Jupiter fee and network fees from the buy budget
        # before computing how many tokens we actually receive.
        net_budget_sol, buy_fees = _apply_dry_run_costs(amount_sol, leg_type="buy")
        if net_budget_sol <= 0:
            print(f"     ❌ DRY-RUN BUY ABORTED: fees ({buy_fees['jupiter_fee_sol']} jup + "
                  f"{buy_fees['network_fee_sol']} net) consumed entire budget "
                  f"{amount_sol} SOL — buy too small")
            return None

        # C-1: apply slippage on the buy. The trade value (USD) is the SOL
        # spent × SOL/USD reference (or the equivalent priceUsd × tokens).
        # We estimate slippage from pool depth and reduce sim_tokens accordingly.
        pool_liq_usd = price_info.get("liquidity_usd", 0.0)
        # Convert net_budget_sol → USD via the chosen pair's price.
        # SOL-quoted: price_usd ≈ SOL price × price_native; we already have
        # ds_price_usd directly and don't need to round-trip.
        if ds_price_sol > 0 and ds_price_usd > 0:
            trade_usd = net_budget_sol * (ds_price_usd / ds_price_sol)
        else:
            trade_usd = 0.0
        slippage = _estimate_slippage_pct(trade_usd, pool_liq_usd)
        # Buyer pays the slippage too: effective price ≈ spot × (1 + slippage),
        # so tokens received ≈ net_budget / (spot × (1 + slippage)).
        effective_buy_price = ds_price_sol * (1 + slippage)
        sim_tokens = int(net_budget_sol / effective_buy_price)
        if sim_tokens <= 0:
            print(f"     ❌ DRY-RUN BUY ABORTED: computed 0 tokens for "
                  f"net budget {net_budget_sol:.6f} SOL @ {effective_buy_price:.10f} SOL/token "
                  f"(after {slippage*100:.2f}% slippage; quote: {quote_symbol} via {dex_id}) — skipping")
            return None

        sim_price_sol = effective_buy_price  # record what we actually paid per token
        print(f"     💸 Fees: -{buy_fees['jupiter_fee_sol']:.6f} Jupiter (0.85%), "
              f"-{buy_fees['network_fee_sol']:.6f} network → "
              f"net buy budget {net_budget_sol:.6f} SOL")
        print(f"     📉 Slippage: {slippage*100:.2f}% "
              f"(${trade_usd:.2f} into ${pool_liq_usd:,.0f} pool = "
              f"{(trade_usd/max(pool_liq_usd,1))*100:.3f}% share)")

        # Headline log line — exposes quote currency so we can see at a glance
        # whether the simulated buy used the SOL pool or a USDC conversion.
        if sol_usd_ref > 0:
            quote_suffix = (f"(quote: {quote_symbol} via {dex_id}, "
                            f"SOL/USDC ref: ${sol_usd_ref:.2f})")
        else:
            quote_suffix = f"(quote: {quote_symbol} via {dex_id})"
        print(f"     🧪 DRY-RUN BUY: {token_name} (CA: {token_address[:8]}...) — "
              f"simulated {sim_tokens:,} tokens at ${ds_price_usd:.8f} per token "
              f"{quote_suffix}")

        pos = Position({
            "token_address": token_address,
            "token_symbol": token_symbol,
            "token_name": token_name,
            "buy_amount_sol": amount_sol,
            "buy_amount_tokens": sim_tokens,
            "buy_price_sol": sim_price_sol,
            "buy_tx": "DRY_RUN",
            "status": "open",
            "tokens_remaining": sim_tokens,
            "score": score,
            "url": url,
            "pair_address": pinned_pair,
            "liquidity_at_buy": liquidity_at_buy,
            "holder_snapshot": initial_holder_snapshot,
            "last_mint_check_time": now_epoch,
            "last_holder_check_time": now_epoch,
        })
        return pos

    # 1. Get quote
    print(f"     📊 Pobieram quote...")
    quote = get_quote(SOL_MINT, token_address, amount_lamports)
    if not quote:
        print(f"     ❌ Nie udało się pobrać quote")
        return None

    out_amount = int(quote.get("outAmount", 0))
    print(f"     📊 Otrzymam ~{out_amount:,} tokenów")

    # 2. Get swap transaction
    print(f"     🔄 Przygotowuję transakcję...")
    swap_tx = get_swap_transaction(quote, wallet.public_key)
    if not swap_tx:
        print(f"     ❌ Nie udało się przygotować transakcji")
        return None

    # 3. Sign
    print(f"     ✍️  Podpisuję...")
    signed = wallet.sign_transaction(swap_tx)
    if not signed:
        return None

    # 4. Send
    print(f"     📤 Wysyłam transakcję...")
    signature = send_transaction(signed)
    if not signature:
        return None

    print(f"     ✅ TX: {signature}")
    print(f"     🔗 https://solscan.io/tx/{signature}")

    # Wait a bit for confirmation
    time.sleep(3)

    # Get actual token balance
    actual_tokens = get_token_balance(wallet.public_key, token_address)

    # Pin the DexScreener pair for consistent price tracking across cycles
    _, _, pinned_pair = get_dexscreener_price(token_address)

    pos = Position({
        "token_address": token_address,
        "token_symbol": token_symbol,
        "token_name": token_name,
        "buy_amount_sol": amount_sol,
        "buy_amount_tokens": actual_tokens if actual_tokens > 0 else out_amount,
        "buy_price_sol": amount_sol / out_amount * LAMPORTS_PER_SOL if out_amount > 0 else 0,
        "buy_tx": signature,
        "status": "open",
        "tokens_remaining": actual_tokens if actual_tokens > 0 else out_amount,
        "score": score,
        "url": url,
        "pair_address": pinned_pair,
        "liquidity_at_buy": liquidity_at_buy,
        "holder_snapshot": initial_holder_snapshot,
        "last_mint_check_time": now_epoch,
        "last_holder_check_time": now_epoch,
    })
    return pos


def execute_sell(wallet: Wallet, position: Position, sell_pct: float,
                 reason: str = "",
                 trade_log: Optional["TradeLog"] = None) -> bool:
    """Sell a percentage of position. Falls back to selling everything as 'dust'
    if the requested amount is too small to actually move tokens or is below the
    fee-recovery threshold.

    `trade_log` (optional): when provided, terminal-status transitions to
    "rugged" (C-5 drained pool) or "suspect" (100x implausibility guard) are
    logged as separate structured events so the 24h summary can count them."""
    # Snapshot status BEFORE sell so we can detect transitions even though the
    # caller logs its own action event afterward.
    status_before = position.status
    requested = int(position.tokens_remaining * (sell_pct / 100))
    tokens_to_sell = requested

    # Estimate current value to decide between "dust → sell all" vs "ensure progress"
    dust_close = False
    est_value_sol = 0
    pinned = getattr(position, "pair_address", "") or ""
    if position.tokens_remaining > 0:
        # Use DexScreener (pinned pair) for the value estimate
        price_sol, _, _ = get_dexscreener_price(position.token_address, pinned)
        if price_sol > 0:
            est_value_sol = position.tokens_remaining * price_sol

    # H-5 FIX: handle the silent-fail case where tokens_to_sell rounds to 0
    if tokens_to_sell <= 0 and position.tokens_remaining > 0 and sell_pct > 0:
        if est_value_sol > 0 and est_value_sol < 0.001:
            # Dust position: close it out completely
            tokens_to_sell = position.tokens_remaining
            dust_close = True
            reason = f"DUST close ({reason})"
            print(f"     🧹 Position is dust ({est_value_sol:.6f} SOL) — selling all remaining")
        else:
            # Round-down killed our sell; force at least 1 token to ensure progress
            tokens_to_sell = max(1, requested)
            print(f"     🔧 Rounded sell ({requested}) → forcing {tokens_to_sell} token(s) to ensure cascade progress")

    if tokens_to_sell <= 0:
        # Truly nothing to sell (tokens_remaining itself is 0 or sell_pct is 0)
        print(f"     ⚠️  Brak tokenów do sprzedania (remaining={position.tokens_remaining}, pct={sell_pct})")
        return False

    print(f"\n  📤 SELLING {sell_pct:.0f}% of {position.token_name} (${position.token_symbol})")
    print(f"     Tokens: {tokens_to_sell:,} / {position.tokens_remaining:,}")
    print(f"     Reason: {reason}")

    if CONFIG["dry_run"]:
        print(f"     🧪 DRY RUN — symulacja sprzedaży")
        # Use the dry-run price source (DexScreener) — Jupiter is unreliable
        # on the AWS host. Re-fetch each time so cascade decisions reflect the
        # current price, not the price at sell-trigger evaluation time.
        sim_sol_out = 0
        # H-1: sample twice with fill_delay_sec between, use the WORSE price
        # (lower for sells) to model submit→confirm latency.
        price_info_sell = _worst_of_two_price(position.token_address, pinned, leg_type="sell")
        price_sol = price_info_sell["price_sol"]
        price_usd = price_info_sell["price_usd"]
        pool_liq_usd = price_info_sell.get("liquidity_usd", 0.0)
        if "_fill_sim_other" in price_info_sell and \
                price_info_sell["_fill_sim_chosen"] != price_info_sell["_fill_sim_other"]:
            print(f"     ⏱️  Fill simulation: price1={price_info_sell['_fill_sim_other']:.10f}, "
                  f"price2={price_info_sell['_fill_sim_chosen']:.10f}, "
                  f"using LOWER (sell worse-of-2)")

        # ── C-5: DRAINED POOL REFUSAL ──
        # When current pool liquidity is below the threshold, the pool is
        # effectively unsellable. DexScreener's last priceNative is stale and
        # cannot be honored on-chain. Real outcome: 0 SOL.
        # This replaces the prior 10x-cap + 100x-implausibility hacks for the
        # drained-pool case; the 100x sanity check below still catches OTHER
        # bad-data cases (wrong-quote pair, decimals mismatch, etc.).
        drained_threshold = CONFIG.get("drained_pool_threshold_usd", 500)
        is_drained = price_sol > 0 and pool_liq_usd > 0 and pool_liq_usd < drained_threshold
        # Also treat "no price data at all on emergency-sell" as drained:
        # DexScreener returned nothing usable AND we're in an emergency.
        is_drained_no_data = (price_sol <= 0) and ("EMERGENCY" in reason or "Liquidity dropped" in reason)
        if is_drained or is_drained_no_data:
            baseline = getattr(position, "liquidity_at_buy", 0) or 0
            print(f"     💀 DRAINED POOL: liquidity ${pool_liq_usd:,.0f} "
                  f"(baseline at buy: ${baseline:,.0f}, threshold: ${drained_threshold}) — "
                  f"refusing to record exit proceeds (real outcome: 0 SOL)")
            sim_sol_out = 0
            position.status = "rugged"
            # Skip slippage / fee / 100x-cap blocks entirely; nothing to compute.
        elif price_sol > 0:
            # C-1: slippage applied to the spot value BEFORE fees.
            gross_at_spot = tokens_to_sell * price_sol
            if price_sol > 0 and price_usd > 0:
                trade_usd = gross_at_spot * (price_usd / price_sol)
            else:
                trade_usd = 0.0
            slippage = _estimate_slippage_pct(trade_usd, pool_liq_usd)
            # Seller eats the slippage: effective price ≈ spot × (1 - slippage)
            gross_after_slippage = gross_at_spot * (1 - slippage)
            # C-2 + C-3: deduct Jupiter fee and network fees from the gross.
            sim_sol_out, sell_fees = _apply_dry_run_costs(gross_after_slippage, leg_type="sell")
            print(f"     📉 Slippage: {slippage*100:.2f}% "
                  f"(${trade_usd:.2f} into ${pool_liq_usd:,.0f} pool) → "
                  f"{gross_at_spot:.6f} → {gross_after_slippage:.6f} SOL")
            print(f"     💸 Fees: -{sell_fees['jupiter_fee_sol']:.6f} Jupiter, "
                  f"-{sell_fees['network_fee_sol']:.6f} network → "
                  f"net {sim_sol_out:.6f} SOL")

        # SANITY CHECK: any single sell yielding more than 100x the original buy_amount is
        # almost certainly a data bug (wrong-quote pair, stale dead-pair price, decimals
        # mismatch). Flag, log loudly, and DO NOT record the bogus value.
        # NOTE: drained-pool case is now handled BEFORE this — the C-5 block sets
        # sim_sol_out=0 and status="rugged" already. This guard remains for other
        # implausibility sources (decimals, wrong-quote pair, etc.).
        max_plausible = position.buy_amount_sol * 100.0
        if sim_sol_out > max_plausible and max_plausible > 0:
            print(f"     🛑 Implausible sell value {sim_sol_out:.4f} SOL "
                  f"(>100x buy_amount {position.buy_amount_sol} SOL) — likely bad price data. "
                  f"Recording 0 received and marking position as 'suspect'.")
            sim_sol_out = 0
            position.status = "suspect"
        print(f"     📊 Simulated: ~{sim_sol_out:.6f} SOL received")
        position.tokens_remaining -= tokens_to_sell
        position.total_sold_sol += sim_sol_out
        # Preserve special terminal statuses set above (rugged from C-5,
        # suspect from the 100x guard) — only assign closed/dust if the
        # status is still "open" or "partial".
        if position.tokens_remaining <= 0 or dust_close:
            if position.status not in ("rugged", "suspect"):
                position.status = "dust" if dust_close else "closed"
            position.tokens_remaining = 0
        else:
            # Preserve special terminal statuses here too — a partial sell
            # that triggered the 100x guard or drained-pool guard should NOT
            # be reclassified back to "partial".
            if position.status not in ("rugged", "suspect"):
                position.status = "partial"
        position.sells.append({
            "pct": sell_pct, "tokens": tokens_to_sell,
            "sol_received": sim_sol_out, "reason": reason,
            "tx": "DRY_RUN",
            "time": datetime.now(timezone.utc).isoformat(),
        })
        _log_terminal_transition_if_needed(trade_log, position, status_before, reason)
        return True

    # 1. Quote
    quote = get_quote(position.token_address, SOL_MINT, tokens_to_sell)
    if not quote:
        print(f"     ❌ Nie udało się pobrać quote sell")
        return False

    sol_out = int(quote.get("outAmount", 0)) / LAMPORTS_PER_SOL
    print(f"     📊 Otrzymam ~{sol_out:.4f} SOL")

    # SANITY CHECK (live): if Jupiter quote claims >100x the original buy, refuse to send.
    # Either the route is malicious/wrong or there's a decoding bug. Never trade on data we don't trust.
    # 100x is already an exceptional outcome for a single cascade slice; anything larger is a smell.
    if sol_out > position.buy_amount_sol * 100.0 and position.buy_amount_sol > 0:
        print(f"     🛑 ABORTING SELL: quote {sol_out:.4f} SOL > 100x buy_amount "
              f"({position.buy_amount_sol} SOL). Refusing to trade on suspect quote.")
        return False

    # 2. Swap tx
    swap_tx = get_swap_transaction(quote, wallet.public_key)
    if not swap_tx:
        return False

    # 3. Sign + send
    signed = wallet.sign_transaction(swap_tx)
    if not signed:
        return False

    signature = send_transaction(signed)
    if not signature:
        return False

    print(f"     ✅ SOLD! TX: {signature}")
    print(f"     💰 Received: {sol_out:.4f} SOL")

    # Update position
    position.tokens_remaining -= tokens_to_sell
    position.total_sold_sol += sol_out
    if position.tokens_remaining <= 0 or dust_close:
        position.status = "dust" if dust_close else "closed"
        position.tokens_remaining = 0
    else:
        position.status = "partial"

    position.sells.append({
        "pct": sell_pct, "tokens": tokens_to_sell,
        "sol_received": sol_out, "reason": reason,
        "tx": signature,
        "time": datetime.now(timezone.utc).isoformat(),
    })
    _log_terminal_transition_if_needed(trade_log, position, status_before, reason)
    return True


# ─── POSITION MONITOR ───────────────────────────────────────

def _next_cascade_threshold(c: dict, current_level: int) -> float:
    """Compute the PnL% threshold that triggers the NEXT cascade,
    given that `current_level` TPs have already fired.

    Schedule (with defaults):
      level 0 → TP1 fires at +30% (recover investment)
      level 1 → TP2 fires at +60%  (+30% interval — early)
      level 2 → TP3 fires at +90%  (+30% interval — early)
      level 3 → TP4 fires at +140% (+50% interval — late)
      level 4 → TP5 fires at +190% (+50% interval — late)
      level N≥3 → next TP fires +50% above previous

    The interval used to compute TP_(i+1) depends on the level we are
    transitioning FROM (i). If i <= cascade_widen_after_level, use the
    early interval; otherwise the late one.
    """
    threshold = c["take_profit_pct"]  # TP1 anchor (percentage, e.g. 30)
    widen_after = c.get("cascade_widen_after_level", 2)
    early = c.get("early_cascade_interval", 0.30) * 100
    late = c.get("late_cascade_interval", 0.50) * 100

    # Walk from TP1 up to TP(current_level+1), adding the right interval each step.
    # When computing TP_(i+1), the relevant transition is FROM level i.
    for from_level in range(1, current_level + 1):
        threshold += early if from_level <= widen_after else late
    return threshold


def check_positions(wallet: Wallet, pm: PositionManager, trade_log: TradeLog):
    """Check all open positions and execute TP/SL."""
    open_positions = pm.get_open()
    if not open_positions:
        return

    c = CONFIG
    print(f"\n  📊 Sprawdzam {len(open_positions)} pozycji...")

    for pos in open_positions:
        # Get current value of remaining tokens in SOL
        if pos.tokens_remaining <= 0:
            pos.status = "closed"
            pm.save()
            continue

        # ── CONTINUOUS SAFETY CHECKS (per cycle) ──
        if SAFETY_AVAILABLE:
            try:
                now_epoch = time.time()
                mint_interval = NEW_SAFETY_CONFIG["mint_recheck_interval_sec"]
                do_mint = (now_epoch - getattr(pos, "last_mint_check_time", 0)) >= mint_interval
                cont = run_continuous_safety_checks(
                    pos.token_address,
                    baseline_liquidity_usd=getattr(pos, "liquidity_at_buy", 0),
                    prev_holder_snapshot=getattr(pos, "holder_snapshot", []),
                    do_mint_recheck=do_mint,
                    rpc_url=CONFIG["rpc_url"],
                )

                # Persist updated holder snapshot + mint check timestamp
                if cont["new_holder_snapshot"]:
                    pos.holder_snapshot = cont["new_holder_snapshot"]
                    pos.last_holder_check_time = now_epoch
                if do_mint and cont.get("mint") is not None:
                    pos.last_mint_check_time = now_epoch

                # Log warnings
                for w in cont.get("warnings", []):
                    print(f"     {w}")
                    log_safety_event(trade_log, "warning", pos, {"message": w})

                # Emergency sell trigger
                if cont["emergency_sell"]:
                    reasons = " | ".join(cont["emergency_reasons"])
                    print(f"\n  🚨 EMERGENCY SAFETY TRIGGER for {pos.token_symbol}: {reasons}")
                    log_safety_event(trade_log, "emergency_sell_trigger", pos, {
                        "reasons": cont["emergency_reasons"],
                        "liquidity": cont.get("liquidity"),
                        "mint": cont.get("mint"),
                    })
                    if execute_sell(wallet, pos, 100, f"EMERGENCY: {reasons[:80]}",
                                    trade_log=trade_log):
                        trade_log.log({
                            "action": "emergency_sell",
                            "token": pos.token_symbol,
                            "reasons": cont["emergency_reasons"],
                        })
                        pm.save()
                    continue  # Skip TP/SL evaluation — position is closing
            except Exception as e:
                print(f"     ⚠️  Continuous safety check error for {pos.token_symbol}: {e}")

        current_value_sol = 0
        if CONFIG["dry_run"]:
            # Dry-run: use DexScreener price on the pinned pair (consistent across cycles).
            # If pinned pair died, get_dexscreener_price falls back to highest-liq pair.
            pinned = getattr(pos, "pair_address", "") or ""
            price_sol, _, used_pair = get_dexscreener_price(pos.token_address, pinned)
            if price_sol <= 0:
                print(f"     ⚠️  {pos.token_name}: no price data, skipping")
                pm.save()  # persist any safety-check timestamps
                continue
            # If we fell back to a different pair (pinned died), update the pin
            if pinned and used_pair and used_pair != pinned:
                print(f"     🔁 {pos.token_name}: pinned pair {pinned[:8]}... dead, switched to {used_pair[:8]}...")
                pos.pair_address = used_pair
            current_value_sol = pos.tokens_remaining * price_sol
        else:
            quote = get_quote(pos.token_address, SOL_MINT, pos.tokens_remaining, slippage_bps=100)
            if not quote:
                pm.save()  # persist any safety-check timestamps
                continue
            current_value_sol = int(quote.get("outAmount", 0)) / LAMPORTS_PER_SOL

        # PnL on the *remaining bag* against original investment (used for stop-loss + display only).
        pnl_pct = ((current_value_sol - pos.buy_amount_sol) / pos.buy_amount_sol * 100) if pos.buy_amount_sol > 0 else 0

        # ── DUST AUTO-CLOSE (moonbags only) ──
        # A moonbag that decayed below 0.001 SOL is worth less than fees. Close it so it stops
        # cluttering the dashboard and gets monitored in `closed/dust` view instead.
        DUST_THRESHOLD_SOL = 0.001
        if pos.cascade_level > 0 and 0 < current_value_sol < DUST_THRESHOLD_SOL:
            print(f"     🧹 {pos.token_name}: moonbag value {current_value_sol:.6f} SOL < {DUST_THRESHOLD_SOL} — auto-close as dust")
            pos.status = "dust"
            pos.tokens_remaining = 0
            trade_log.log({
                "action": "dust_close",
                "token": pos.token_symbol,
                "token_address": pos.token_address,
                "value_at_close_sol": current_value_sol,
                "cascade_level": pos.cascade_level,
                "reason": "moonbag dust",
            })
            pm.save()
            continue  # nothing more to evaluate for this position

        # Per-token price change since buy (used for cascade thresholds).
        # This is the right metric: "TP2 at +60%" must mean price is 60% above buy, regardless of how
        # many tokens we already sold.
        if pos.tokens_remaining > 0 and pos.buy_price_per_token > 0:
            current_price_per_token = current_value_sol / pos.tokens_remaining
            price_change_pct = (current_price_per_token - pos.buy_price_per_token) / pos.buy_price_per_token * 100
        else:
            current_price_per_token = 0
            price_change_pct = pnl_pct  # fallback when we have no per-token reference

        # Display
        emoji = "🟢" if pnl_pct >= 0 else "🔴"
        print(f"     {emoji} {pos.token_name} (${pos.token_symbol}): "
              f"price {price_change_pct:+.1f}% | bag PnL {pnl_pct:+.1f}% | "
              f"Value: {current_value_sol:.4f} SOL | Paid: {pos.buy_amount_sol:.4f} SOL")

        # ── CASCADING TAKE PROFIT (price-based thresholds) ──

        # Level 0: First TP — recover investment when PRICE is up >= take_profit_pct.
        if price_change_pct >= c["take_profit_pct"] and pos.cascade_level == 0:
            # Sell enough of the remaining bag to recover the original SOL investment.
            tokens_for_investment = pos.tokens_remaining * (pos.buy_amount_sol / current_value_sol)
            sell_pct = min(95, (tokens_for_investment / pos.tokens_remaining) * 100)

            print(f"     🎯 TAKE PROFIT #1! Price +{price_change_pct:.0f}% — selling {sell_pct:.0f}% to recover investment")
            if execute_sell(wallet, pos, sell_pct,
                            f"TP1: recover investment at price +{price_change_pct:.0f}%",
                            trade_log=trade_log):
                pos.cascade_level = 1
                pos.peak_pnl_pct = max(pos.peak_pnl_pct, pnl_pct)
                trade_log.log({
                    "action": "cascade_tp", "level": 1,
                    "token": pos.token_symbol,
                    "price_change_pct": price_change_pct,
                    "pnl_pct": pnl_pct,
                    "sell_pct": sell_pct,
                })
                pm.save()

        # Level 1+: Cascade based on PRICE change schedule (30% early, 50% late, 12% of remaining).
        elif pos.cascade_level >= 1 and pos.tokens_remaining > 0:
            next_cascade_pct = _next_cascade_threshold(c, pos.cascade_level)

            if price_change_pct >= next_cascade_pct:
                min_val = c.get("min_moonbag_value_sol", 0.005)
                if current_value_sol < min_val:
                    print(f"     💤 Moonbag value ({current_value_sol:.4f} SOL) below min ({min_val} SOL) — holding")
                else:
                    sell_pct = c["moonbag_sell_pct"] * 100  # fraction → percentage
                    next_level = pos.cascade_level + 1
                    print(f"     🚀 CASCADE #{next_level}! Price +{price_change_pct:.0f}% (threshold +{next_cascade_pct:.0f}%) — selling {sell_pct:.0f}% of remaining")
                    if execute_sell(wallet, pos, sell_pct,
                                    f"Cascade #{next_level} at price +{price_change_pct:.0f}%",
                                    trade_log=trade_log):
                        pos.cascade_level = next_level
                        pos.peak_pnl_pct = max(pos.peak_pnl_pct, pnl_pct)
                        trade_log.log({
                            "action": "cascade_tp", "level": next_level,
                            "token": pos.token_symbol,
                            "price_change_pct": price_change_pct,
                            "pnl_pct": pnl_pct,
                            "sell_pct": sell_pct,
                            "threshold_pct": next_cascade_pct,
                        })
                        pm.save()

        # ── STOP LOSS (still PnL-based, only active before TP1) ──
        if pnl_pct <= c["stop_loss_pct"] and pos.cascade_level == 0:
            print(f"     🛑 STOP LOSS! Bag PnL {pnl_pct:.0f}% — selling everything")
            if execute_sell(wallet, pos, 100, f"SL at PnL {pnl_pct:.0f}%",
                            trade_log=trade_log):
                trade_log.log({
                    "action": "stop_loss", "token": pos.token_symbol,
                    "pnl_pct": pnl_pct,
                    "price_change_pct": price_change_pct,
                })
                pm.save()

        # After investment recovered (cascade_level >= 1), NO stop loss
        # Moonbag rides to 0 or moon — it's free money

        time.sleep(1)  # Rate limit


# ─── SNIPER INTEGRATION ─────────────────────────────────────

def compute_cycle_interval(open_count: int) -> int:
    """Choose cycle interval based on active position count.

    0 positions:    30s  — only checking for alerts, no need to spin fast
    1-5 positions:  15s  — faster TP/SL reaction while load is manageable
    6+ positions:   30s  — back off to avoid Jupiter/DexScreener rate limits
    """
    if open_count == 0:
        return 30
    if open_count <= 5:
        return 15
    return 30


def _save_processed_alerts(processed_file: Path, processed: set):
    """Atomically persist the processed-alerts marker file."""
    tmp = processed_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(sorted(processed)), encoding="utf-8")
    os.replace(str(tmp), str(processed_file))


def process_sniper_alerts(wallet: Wallet, pm: PositionManager, trade_log: TradeLog):
    """Check for new sniper alerts and auto-buy."""
    alert_dir = Path("sniper_alerts")
    if not alert_dir.exists():
        return

    processed_file = Path("processed_alerts.json")
    processed = set()
    if processed_file.exists():
        try:
            processed = set(json.loads(processed_file.read_text()))
        except Exception:
            pass

    for alert_file in sorted(alert_dir.glob("alert_*.json")):
        fname = alert_file.name
        # Skip in-flight writes from the sniper (atomic-write .tmp companions).
        if fname.endswith(".tmp"):
            continue
        if fname in processed:
            continue

        try:
            data = json.loads(alert_file.read_text(encoding="utf-8"))
        except Exception as e:
            # Don't mark as processed — most likely a partial write; retry next cycle.
            print(f"     ⚠️  Could not parse {fname} ({e}); will retry next cycle")
            continue

        try:
            detections = data.get("detections", [])

            for det in detections:
                token_addr = det.get("token_address", "")
                score = det.get("score", 0)
                signal = det.get("signal", "")

                # Skip if already have position (case-sensitive — Solana base58)
                existing = pm.get_by_address(token_addr)
                if existing:
                    print(f"     ⏭️  Skipping duplicate {det.get('token_symbol', '?')} "
                          f"({token_addr[:8]}...) — already have position "
                          f"(status={existing.status}, new alert score={score})")
                    trade_log.log({
                        "action": "skipped_duplicate",
                        "token": det.get("token_symbol", "?"),
                        "token_address": token_addr,
                        "existing_status": existing.status,
                        "existing_cascade_level": existing.cascade_level,
                        "new_alert_score": score,
                        "signal": signal,
                    })
                    continue

                # Skip if too many ACTIVE positions (moonbags don't count)
                if pm.open_count >= CONFIG["max_positions"]:
                    print(f"     ⚠️  Max aktywnych pozycji ({CONFIG['max_positions']}). Moonbagi: {pm.moonbag_count}. Pomijam.")
                    continue

                # Skip if score too low
                if score < CONFIG["min_score_to_buy"]:
                    continue

                # BUY!
                pos = execute_buy(
                    wallet, token_addr, CONFIG["buy_amount_sol"],
                    token_name=det.get("token_name", "?"),
                    token_symbol=det.get("token_symbol", "?"),
                    score=score,
                    url=det.get("url", ""),
                    trade_log=trade_log,
                )

                if pos:
                    pm.add(pos)
                    trade_log.log({
                        "action": "buy", "token": pos.token_symbol,
                        "amount_sol": pos.buy_amount_sol,
                        "score": score, "signal": signal,
                    })
                    print(f"     ✅ Pozycja otwarta!")

                time.sleep(2)

            # Mark processed only AFTER successful processing of this file.
            processed.add(fname)
            # Persist the marker per-file so a mid-loop crash doesn't lose progress.
            _save_processed_alerts(processed_file, processed)
        except Exception as e:
            # Iteration over detections failed for some other reason (e.g. RPC error).
            # Don't mark as processed — retry next cycle.
            print(f"     ❌ Error processing {fname}: {e} — will retry next cycle")


# ─── MAINTENANCE: PRUNE 0-TOKEN POSITIONS ───────────────────

def cleanup_zero_positions(pm: PositionManager):
    """Remove positions with buy_amount_tokens == 0 from positions.json.

    These rows are artifacts of the pre-fix dry-run path: Jupiter timeouts
    on the AWS host produced quote=None → sim_tokens=0 → instant
    dust-close, leaving useless entries that pollute the dashboard. With
    the DexScreener-first dry-run path this no longer happens, but the
    historical entries need a one-shot clean-up.

    Interactive: prints the count and prompts y/N before deleting.
    """
    zero_positions = [p for p in pm.positions if getattr(p, "buy_amount_tokens", 0) == 0]
    count = len(zero_positions)

    if count == 0:
        print(f"\n  ✅ No zero-token positions found ({len(pm.positions)} total). Nothing to clean up.")
        return

    print(f"\n  🧹 Found {count} positions with buy_amount_tokens=0 "
          f"(out of {len(pm.positions)} total).")
    # Show a sample so the user knows what's about to go
    for p in zero_positions[:5]:
        print(f"     · {p.token_name} (${p.token_symbol})  "
              f"status={p.status}  buy={p.buy_amount_sol} SOL  "
              f"address={p.token_address[:8]}...")
    if count > 5:
        print(f"     · …and {count - 5} more")

    try:
        answer = input(f"\n  This will remove {count} positions with no recorded "
                       f"token amounts. Continue? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\n  Aborted.")
        return

    if answer not in ("y", "yes"):
        print("  Aborted. No changes made.")
        return

    # Filter and persist. We bypass pm.save() here because its read-modify-write
    # merge will defensively re-add disk records that vanished from memory —
    # which is the right behavior for the runtime bot (don't lose history from a
    # stale in-memory snapshot) but the wrong behavior for an intentional delete.
    # Write the filtered list directly with the same atomic-tmp + os.replace
    # pattern the rest of the codebase uses.
    kept = [p for p in pm.positions if getattr(p, "buy_amount_tokens", 0) != 0]
    removed = len(pm.positions) - len(kept)
    pm.positions = kept

    payload = [p.to_dict() for p in kept]
    tmp_path = pm.filepath.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(str(tmp_path), str(pm.filepath))

    print(f"  ✅ Removed {removed} zero-token positions. {len(kept)} positions remain.")


# ─── STATUS DISPLAY ─────────────────────────────────────────

def show_status(wallet: Optional[Wallet], pm: PositionManager):
    """Display current status."""
    print()
    print("  ╔═══════════════════════════════════════════╗")
    print("  ║   🤖 DEGEN BOT — Status                  ║")
    print("  ╚═══════════════════════════════════════════╝")
    print()

    if wallet and wallet.ready:
        balance = get_sol_balance(wallet.public_key)
        print(f"  💳 Wallet: {wallet.public_key[:8]}...{wallet.public_key[-6:]}")
        print(f"  💰 Balance: {balance:.4f} SOL")
    else:
        print(f"  ⚠️  Wallet nie skonfigurowany")

    open_pos = pm.get_open()
    active = [p for p in open_pos if p.cascade_level == 0]
    moonbags = [p for p in open_pos if p.cascade_level >= 1]
    closed = [p for p in pm.positions if p.status in ("closed", "stopped")]

    print(f"\n  📊 Pozycje: {len(active)} active (at risk) | {len(moonbags)} moonbags (free) | {len(closed)} closed")
    print(f"  🎯 Strategy: TP +{CONFIG['take_profit_pct']}% → recover investment → cascade {CONFIG['cascade_sell_pct']}% every +{CONFIG['cascade_interval_pct']}% | SL {CONFIG['stop_loss_pct']}%")
    print(f"  💵 Buy size: {CONFIG['buy_amount_sol']} SOL per token")
    print(f"  📊 Min score: {CONFIG['min_score_to_buy']}+")
    print(f"  {'🧪 DRY RUN MODE' if CONFIG['dry_run'] else '🔴 LIVE MODE — Real money!'}")
    print()

    if open_pos:
        print("  ── Open Positions ──")
        for p in open_pos:
            age = ""
            try:
                dt = datetime.fromisoformat(p.buy_time.replace('Z', '+00:00'))
                hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
                age = f"{hours:.0f}h"
            except:
                pass

            pnl_label = ""
            if p.total_sold_sol > 0:
                pnl_label = f" | Sold: {p.total_sold_sol:.4f} SOL"

            print(f"     {'🟢' if p.status == 'open' else '🟡'} {p.token_name} (${p.token_symbol}) "
                  f"| Bought: {p.buy_amount_sol} SOL | Score: {p.score} "
                  f"| Age: {age} | Status: {p.status} | Cascade: {p.cascade_level}{pnl_label}")
        print()

    if closed:
        print("  ── Closed Positions ──")
        total_invested = 0
        total_returned = 0
        for p in closed:
            total_invested += p.buy_amount_sol
            total_returned += p.total_sold_sol
            pnl = p.total_sold_sol - p.buy_amount_sol
            emoji = "🟢" if pnl >= 0 else "🔴"
            print(f"     {emoji} {p.token_name} | In: {p.buy_amount_sol} SOL | Out: {p.total_sold_sol:.4f} SOL | PnL: {pnl:+.4f} SOL")

        total_pnl = total_returned - total_invested
        print(f"\n     📊 Total: Invested {total_invested:.4f} SOL | Returned {total_returned:.4f} SOL | PnL: {total_pnl:+.4f} SOL")
        print()


# ─── MAIN ───────────────────────────────────────────────────

def main():
    args = sys.argv[1:]

    # Parse flags
    if "--dry-run" in args:
        CONFIG["dry_run"] = True
        args = [a for a in args if a != "--dry-run"]

    if "--live" in args:
        CONFIG["dry_run"] = False
        args = [a for a in args if a != "--live"]

    pm = PositionManager(CONFIG["positions_file"])
    trade_log = TradeLog(CONFIG["trade_log_file"])
    wallet = load_wallet()

    # ── STATUS ──
    if "--status" in args:
        show_status(wallet, pm)
        return

    # ── CLEANUP ZERO-TOKEN POSITIONS ──
    # Maintenance command: prunes positions.json of entries created by the
    # pre-fix dry-run path (Jupiter timeouts → buy_amount_tokens=0 → instant
    # dust-close → useless rows polluting the dashboard).
    if "--cleanup-zero-positions" in args:
        cleanup_zero_positions(pm)
        return

    # ── MANUAL BUY ──
    if "--buy" in args:
        idx = args.index("--buy")
        if idx + 2 >= len(args):
            print("  Usage: --buy <CA> <amount_sol>")
            return
        token_addr = args[idx + 1]
        amount_sol = float(args[idx + 2])

        if not wallet or not wallet.ready:
            print("  ❌ Wallet nie skonfigurowany!")
            return

        pos = execute_buy(wallet, token_addr, amount_sol, trade_log=trade_log)
        if pos:
            pm.add(pos)
            trade_log.log({"action": "manual_buy", "token": pos.token_symbol, "amount_sol": amount_sol})
            print("  ✅ Zakup wykonany!")
        return

    # ── MANUAL SELL ──
    if "--sell" in args:
        idx = args.index("--sell")
        if idx + 2 >= len(args):
            print("  Usage: --sell <CA> <percentage>")
            return
        token_addr = args[idx + 1]
        sell_pct = float(args[idx + 2])

        if not wallet or not wallet.ready:
            print("  ❌ Wallet nie skonfigurowany!")
            return

        pos = pm.get_by_address(token_addr)
        if not pos:
            print(f"  ❌ Nie znaleziono pozycji dla {token_addr}")
            return

        if execute_sell(wallet, pos, sell_pct, "Manual sell", trade_log=trade_log):
            pm.save()
            trade_log.log({"action": "manual_sell", "token": pos.token_symbol, "sell_pct": sell_pct})
            print("  ✅ Sprzedaż wykonana!")
        return

    # ── WITHDRAW SOL ──
    if "--withdraw" in args:
        idx = args.index("--withdraw")
        if idx + 1 >= len(args):
            print("  Usage: --withdraw <TWOJ_ADRES_PHANTOM>")
            print("  Wyśle cały SOL (minus fee) na podany adres.")
            return

        dest_address = args[idx + 1]

        if not wallet or not wallet.ready:
            print("  ❌ Wallet nie skonfigurowany!")
            return

        balance = get_sol_balance(wallet.public_key)
        # Zostaw 0.002 SOL na fee
        send_amount = balance - 0.002
        if send_amount <= 0:
            print(f"  ❌ Za mało SOL. Balance: {balance:.4f} SOL")
            return

        print(f"\n  💸 WITHDRAW")
        print(f"     From:   {wallet.public_key}")
        print(f"     To:     {dest_address}")
        print(f"     Amount: {send_amount:.4f} SOL (keeping 0.002 for fee)")
        print(f"     Balance: {balance:.4f} SOL")

        if CONFIG["dry_run"]:
            print(f"     🧪 DRY RUN — nie wysłano")
            return

        try:
            from solders.system_program import TransferParams, transfer
            from solders.transaction import Transaction
            from solders.pubkey import Pubkey
            from solders.hash import Hash
            import base58

            # Get recent blockhash
            bh_resp = requests.post(CONFIG["rpc_url"], json={
                "jsonrpc": "2.0", "id": 1,
                "method": "getLatestBlockhash",
                "params": [{"commitment": "finalized"}]
            }, timeout=10).json()

            blockhash_str = bh_resp["result"]["value"]["blockhash"]
            blockhash = Hash.from_string(blockhash_str)

            # Build transfer instruction
            ix = transfer(TransferParams(
                from_pubkey=wallet.keypair.pubkey(),
                to_pubkey=Pubkey.from_string(dest_address),
                lamports=int(send_amount * LAMPORTS_PER_SOL),
            ))

            # Build and sign transaction
            tx = Transaction.new_signed_with_payer(
                [ix], wallet.keypair.pubkey(), [wallet.keypair], blockhash
            )

            # Send
            tx_b64 = base64.b64encode(bytes(tx)).decode('utf-8')
            signature = send_transaction(tx_b64)

            if signature:
                print(f"     ✅ Wysłano! TX: {signature}")
                print(f"     🔗 https://solscan.io/tx/{signature}")
                trade_log.log({
                    "action": "withdraw", "amount_sol": send_amount,
                    "to": dest_address, "tx": signature,
                })
            else:
                print(f"     ❌ Transakcja nie powiodła się")
        except ImportError:
            print(f"     ❌ Potrzebujesz: pip install solana solders base58")
        except Exception as e:
            print(f"     ❌ Błąd: {e}")
        return

    # ── AUTO MODE ──
    print()
    print("  ╔═══════════════════════════════════════════╗")
    print("  ║   🤖 SOLANA DEGEN TRADING BOT            ║")
    print("  ║   Auto-buy from Liftoff Sniper           ║")
    print("  ╚═══════════════════════════════════════════╝")
    print()

    if CONFIG["dry_run"]:
        print("  🧪 DRY RUN MODE — żadne prawdziwe transakcje nie zostaną wykonane")
    else:
        print("  🔴 LIVE MODE — bot będzie wykonywał prawdziwe transakcje!")
        print("  ⚠️  Upewnij się że wallet ma niewielką ilość SOL")

    if not wallet or not wallet.ready:
        if not CONFIG["dry_run"]:
            print("\n  ❌ Wallet nie skonfigurowany!")
            print("  Ustaw zmienną środowiskową SOL_PRIVATE_KEY")
            print("  lub stwórz plik bot_wallet.key z private key (base58)")
            print("\n  Aby wygenerować nowy wallet:")
            print("    python -c \"from solders.keypair import Keypair; import base58; kp=Keypair(); print('Public:', kp.pubkey()); print('Private:', base58.b58encode(bytes(kp)).decode())\"")
            return
        else:
            print("  ℹ️  Wallet nie skonfigurowany — dry-run bez portfela")
            wallet = None

    if wallet and wallet.ready:
        show_status(wallet, pm)

    # 24h diagnostic summary — surfaces honeypot-unverified / rugcheck-unavailable
    # refusals and drained-pool ruggings so the operator can see API health at a glance.
    _print_24h_summary(trade_log)

    print(f"  🚀 Startuje auto-mode. Ctrl+C aby zatrzymać.")
    print(f"  📂 Czekam na alerty z Liftoff Sniper w sniper_alerts/")
    print()

    try:
        cycle = 0
        while True:
            cycle += 1
            now = datetime.now().strftime("%H:%M:%S")
            print(f"  [{now}] Cykl #{cycle}")

            # 1. Process new sniper alerts
            process_sniper_alerts(wallet, pm, trade_log)

            # 2. Check existing positions for TP/SL
            check_positions(wallet, pm, trade_log)

            # 3. Auto-sweep profits to cold wallet (every 10 cycles)
            if SAFETY_AVAILABLE and wallet and cycle % 10 == 0:
                try:
                    sig = check_and_sweep(wallet.public_key, wallet.keypair, CONFIG["rpc_url"])
                    if sig:
                        trade_log.log({"action": "auto_sweep", "tx": sig})
                except Exception as e:
                    print(f"  ⚠️  Sweep error: {e}")

            # Wait — interval scales with open-position count.
            open_count = len(pm.get_open())
            interval = compute_cycle_interval(open_count)
            print(f"  ⏳ Następny check za {interval}s (open positions: {open_count})...\n")
            time.sleep(interval)

    except KeyboardInterrupt:
        print(f"\n\n  👋 Bot zatrzymany.")
        if wallet and wallet.ready:
            show_status(wallet, pm)


if __name__ == "__main__":
    main()
