#!/usr/bin/env python3
"""
🚀 SOLANA LIFTOFF SNIPER
=========================
Ciągły monitoring tokenów Solana — łapie eksplozje w pierwszych minutach.
Skanuje co 3 minuty, wykrywa nagłe skoki volume i buy pressure.

Uruchomienie:
    python3 liftoff_sniper.py              # Ciągły monitoring
    python3 liftoff_sniper.py --once       # Jeden skan i koniec

Wymagania:
    pip install requests

Bez klucza API — korzysta z darmowego DexScreener API.
"""

import requests
import json
import time
import sys
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

# ─── KONFIGURACJA ───────────────────────────────────────────
CONFIG = {
    # Scan interval
    "scan_interval_seconds": 180,     # Co 3 minuty

    # LIFTOFF detection thresholds
    "min_volume_5m": 5_000,           # Min volume w 5 min ($5K = coś się dzieje)
    "min_volume_1h": 20_000,          # Min volume w 1h
    "min_buy_ratio": 0.65,            # Min buy/total ratio (65%+ = agresywne kupowanie)
    "min_txns_1h": 50,                # Min transakcji w 1h
    "max_fdv": 5_000_000,             # Max FDV — powyżej = za późno
    "min_fdv": 5_000,                 # Min FDV
    "min_liquidity": 2_000,           # Min płynność
    "max_pair_age_hours": 48,         # Max wiek pary
    "min_pair_age_hours": 2,          # Min wiek — 2h (większość rugów w pierwszych 1-2h)

    # Price momentum
    "min_price_change_5m": 5,         # Min +5% w 5 min
    "min_price_change_1h": 20,        # Min +20% w 1h

    # Alert cooldown — nie powtarzaj alertu dla tego samego tokena
    "alert_cooldown_minutes": 90,

    # Output
    "top_n": 5,
    "output_dir": "sniper_alerts",
    "log_file": "sniper_log.txt",
}

DEXSCREENER_BASE = "https://api.dexscreener.com"
HEADERS = {"Accept": "application/json", "User-Agent": "LiftoffSniper/1.0"}

# Track alerted tokens to avoid spam
alerted_tokens = {}  # token_addr -> last_alert_time
# Track previous scan data for delta detection
previous_scan = {}   # pair_addr -> {volume, txns, price}
scan_count = 0


# ─── UTILS ──────────────────────────────────────────────────

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')


def beep():
    """Cross-platform alert sound."""
    try:
        if os.name == 'nt':
            import winsound
            winsound.Beep(1000, 300)
            winsound.Beep(1500, 300)
            winsound.Beep(2000, 300)
        else:
            print('\a')
    except:
        print('\a')


def format_usd(val):
    if val >= 1_000_000:
        return f"${val/1_000_000:.1f}M"
    elif val >= 1_000:
        return f"${val/1_000:.1f}K"
    else:
        return f"${val:.0f}"


def format_time(dt):
    return dt.strftime("%H:%M:%S")


def log(msg, file=None):
    """Print and optionally log to file."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    if file:
        try:
            with open(file, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except:
            pass


# ─── API ────────────────────────────────────────────────────

def api_get(endpoint, params=None):
    """Safe API call with retry."""
    url = f"{DEXSCREENER_BASE}{endpoint}"
    for attempt in range(3):
        try:
            resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
            if resp.status_code == 429:
                time.sleep(5)
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            if attempt == 2:
                return None
            time.sleep(2)
    return None


def get_boosted_tokens():
    """Get boosted token addresses (red flag tracking)."""
    data = api_get("/token-boosts/top/v1")
    if not data:
        return set()
    # Solana addresses are base58 — keep original case.
    return {t.get("tokenAddress", "")
            for t in data if t.get("chainId") == "solana"}


def get_profiles():
    """Get latest token profiles."""
    data = api_get("/token-profiles/latest/v1")
    if not data:
        return {}
    profiles = {}
    for p in data:
        if p.get("chainId") == "solana":
            addr = p.get("tokenAddress", "")
            links = p.get("links") or []
            profiles[addr] = {
                "has_website": any(l.get("type") == "website" for l in links),
                "has_twitter": any(l.get("type") == "twitter" for l in links),
                "has_telegram": any(l.get("type") == "telegram" for l in links),
            }
    return profiles


def get_trending_metas():
    """Get trending categories."""
    data = api_get("/metas/trending/v1")
    if not data:
        return []
    return [{"name": m.get("name", "?"), "slug": m.get("slug", "")}
            for m in data[:5]]


def get_meta_pairs(slug):
    """Get pairs from a category."""
    data = api_get(f"/metas/meta/v1/{slug}")
    if not data:
        return []
    return data.get("pairs", [])


def search_pairs(query):
    """Search for pairs."""
    data = api_get("/latest/dex/search", params={"q": query})
    if not data:
        return []
    return data.get("pairs", [])


# ─── LIFTOFF DETECTOR ───────────────────────────────────────

def detect_liftoff(pair, boosted, profiles):
    """
    Detect if a token is in early liftoff phase.
    Returns detection dict or None.
    """
    global previous_scan
    now = datetime.now(timezone.utc)
    c = CONFIG

    # Basic data
    chain = pair.get("chainId", "")
    if chain != "solana":
        return None

    base = pair.get("baseToken", {})
    token_addr = base.get("address", "")
    token_name = base.get("name", "?")
    token_symbol = base.get("symbol", "?")
    pair_addr = pair.get("pairAddress", "")

    liq = (pair.get("liquidity") or {}).get("usd", 0) or 0
    fdv = pair.get("fdv") or 0
    price_usd = float(pair.get("priceUsd") or 0)

    # Volume
    vol = pair.get("volume") or {}
    vol_5m = vol.get("m5", 0) or 0
    vol_1h = vol.get("h1", 0) or 0
    vol_6h = vol.get("h6", 0) or 0
    vol_24h = vol.get("h24", 0) or 0

    # Transactions
    txns = pair.get("txns") or {}
    txns_5m = txns.get("m5", {})
    txns_1h = txns.get("h1", {})
    txns_24h = txns.get("h24", {})

    buys_5m = txns_5m.get("buys", 0)
    sells_5m = txns_5m.get("sells", 0)
    total_5m = buys_5m + sells_5m

    buys_1h = txns_1h.get("buys", 0)
    sells_1h = txns_1h.get("sells", 0)
    total_1h = buys_1h + sells_1h

    buys_24h = txns_24h.get("buys", 0)
    sells_24h = txns_24h.get("sells", 0)
    total_24h = buys_24h + sells_24h

    # Price changes
    pc = pair.get("priceChange") or {}
    pc_5m = pc.get("m5", 0) or 0
    pc_1h = pc.get("h1", 0) or 0
    pc_6h = pc.get("h6", 0) or 0
    pc_24h = pc.get("h24", 0) or 0

    # Pair age
    created_at = pair.get("pairCreatedAt")
    if created_at:
        pair_age_hours = (now - datetime.fromtimestamp(created_at / 1000, tz=timezone.utc)).total_seconds() / 3600
    else:
        pair_age_hours = 999

    # ── HARD FILTERS ──
    if liq < c["min_liquidity"]:
        return None
    if fdv and (fdv > c["max_fdv"] or fdv < c["min_fdv"]):
        return None
    if pair_age_hours > c["max_pair_age_hours"]:
        return None
    if pair_age_hours < c["min_pair_age_hours"]:
        return None  # too fresh — first 1-2h is rug territory

    # ── LIFTOFF SIGNALS ──
    signals = []
    score = 0

    # Signal 1: Volume spike in 5 min
    if vol_5m >= c["min_volume_5m"]:
        signals.append(f"🔥 Vol 5m: {format_usd(vol_5m)}")
        score += 25
    elif vol_5m >= c["min_volume_5m"] * 0.5:
        score += 10

    # Signal 2: Volume spike in 1h
    if vol_1h >= c["min_volume_1h"]:
        signals.append(f"📊 Vol 1h: {format_usd(vol_1h)}")
        score += 20
    elif vol_1h >= c["min_volume_1h"] * 0.5:
        score += 8

    # Signal 3: Buy pressure 5m
    if total_5m > 10:
        buy_ratio_5m = buys_5m / total_5m
        if buy_ratio_5m >= 0.75:
            signals.append(f"🟢 Buys 5m: {buys_5m}/{total_5m} ({buy_ratio_5m:.0%})")
            score += 20
        elif buy_ratio_5m >= c["min_buy_ratio"]:
            score += 10

    # Signal 4: Buy pressure 1h
    if total_1h > c["min_txns_1h"]:
        buy_ratio_1h = buys_1h / total_1h
        if buy_ratio_1h >= 0.70:
            signals.append(f"🟢 Buys 1h: {buys_1h}/{total_1h} ({buy_ratio_1h:.0%})")
            score += 15
        elif buy_ratio_1h >= c["min_buy_ratio"]:
            score += 8

    # Signal 5: Price momentum 5m
    if pc_5m >= c["min_price_change_5m"]:
        signals.append(f"📈 +{pc_5m:.1f}% w 5m")
        score += 15
        if pc_5m >= 20:
            score += 10  # Bonus for mega pump

    # Signal 6: Price momentum 1h
    if pc_1h >= c["min_price_change_1h"]:
        signals.append(f"📈 +{pc_1h:.1f}% w 1h")
        score += 10

    # Signal 7: Volume acceleration (1h vol >> 6h vol / 6)
    if vol_6h > 0:
        hourly_avg_6h = vol_6h / 6
        if hourly_avg_6h > 0 and vol_1h > hourly_avg_6h * 3:
            accel = vol_1h / hourly_avg_6h
            signals.append(f"⚡ Volume accel: {accel:.1f}x vs avg")
            score += 15

    # Signal 8: Fresh pair bonus
    if pair_age_hours <= 2:
        signals.append(f"🆕 Brand new: {pair_age_hours:.0f}h old")
        score += 10
    elif pair_age_hours <= 6:
        score += 5

    # Signal 9: Profile exists (has website/twitter = more legit)
    profile = profiles.get(token_addr, {})
    if profile.get("has_twitter"):
        score += 5
    if profile.get("has_website"):
        score += 5

    # ── WARNINGS / PENALTIES ──
    warnings = []

    if token_addr in boosted:
        warnings.append("⚠️ BOOSTED")
        score -= 5

    if total_5m > 0 and sells_5m / total_5m > 0.6:
        warnings.append("🔴 Heavy selling 5m")
        score -= 15

    if pc_5m < -10:
        warnings.append("📉 Dumping 5m")
        score -= 10

    if liq < 5_000:
        warnings.append("💧 Micro liquidity")
        score -= 5

    # Volume/liq sanity — possible wash
    if liq > 0 and vol_1h / liq > 50:
        warnings.append("🔄 Possible wash trading")
        score -= 10

    score = max(0, min(100, score))

    # ── MIN SCORE TO ALERT ──
    if score < 40:
        return None

    # Signal strength
    if score >= 75:
        signal = "🚀 LIFTOFF"
    elif score >= 55:
        signal = "⚡ WARMING UP"
    else:
        signal = "👀 WATCHING"

    return {
        "token_name": token_name,
        "token_symbol": token_symbol,
        "token_address": base.get("address", ""),
        "pair_address": pair_addr,
        "dex": pair.get("dexId", "?"),
        "url": pair.get("url", ""),
        "price_usd": price_usd,
        "liquidity": liq,
        "fdv": fdv,
        "volume_5m": vol_5m,
        "volume_1h": vol_1h,
        "volume_24h": vol_24h,
        "buys_5m": buys_5m,
        "sells_5m": sells_5m,
        "buys_1h": buys_1h,
        "sells_1h": sells_1h,
        "buys_24h": buys_24h,
        "sells_24h": sells_24h,
        "price_change_5m": pc_5m,
        "price_change_1h": pc_1h,
        "price_change_6h": pc_6h,
        "price_change_24h": pc_24h,
        "pair_age_hours": round(pair_age_hours, 1),
        "score": score,
        "signal": signal,
        "signals": signals,
        "warnings": warnings,
    }


# ─── SCANNER ────────────────────────────────────────────────

ALERTED_RETENTION_HOURS = 24


def _prune_alerted_tokens():
    """Drop alerted_tokens entries older than ALERTED_RETENTION_HOURS to bound memory."""
    cutoff = datetime.now() - timedelta(hours=ALERTED_RETENTION_HOURS)
    stale = [addr for addr, ts in alerted_tokens.items() if ts < cutoff]
    for addr in stale:
        del alerted_tokens[addr]
    if stale:
        print(f"  🧹 Pruned {len(stale)} stale alert entries (>24h old). Active: {len(alerted_tokens)}")


def run_scan(boosted, profiles, metas):
    """Single scan cycle."""
    global scan_count
    scan_count += 1
    _prune_alerted_tokens()
    all_pairs = []

    # From trending metas
    for meta in metas[:4]:
        slug = meta["slug"]
        if slug:
            pairs = get_meta_pairs(slug)
            all_pairs.extend(pairs)
            time.sleep(0.5)

    # From search queries — focused on what's likely to pop
    queries = [
        "pump", "moon", "pepe", "doge", "bonk", "wif",
        "ai agent", "ai", "meme", "trump", "elon",
        "cat", "dog", "frog", "new", "sol",
    ]
    for q in queries:
        pairs = search_pairs(q)
        all_pairs.extend(pairs)
        time.sleep(0.5)

    # Deduplicate
    seen = set()
    unique = []
    for p in all_pairs:
        pa = p.get("pairAddress", "")
        if pa and pa not in seen:
            seen.add(pa)
            unique.append(p)

    # Detect liftoffs
    detections = []
    now = datetime.now()
    cooldown = timedelta(minutes=CONFIG["alert_cooldown_minutes"])

    for pair in unique:
        result = detect_liftoff(pair, boosted, profiles)
        if result:
            addr = result["token_address"]
            # Check cooldown (case-sensitive — Solana base58)
            if addr in alerted_tokens:
                if now - alerted_tokens[addr] < cooldown:
                    continue
            detections.append(result)
            alerted_tokens[addr] = now

    detections.sort(key=lambda x: x["score"], reverse=True)
    return detections[:CONFIG["top_n"]], len(unique)


def print_header():
    """Print scanner status header."""
    now = datetime.now()
    print("=" * 60)
    print(f"  🚀 SOLANA LIFTOFF SNIPER — Scan #{scan_count}")
    print(f"  📅 {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  🔄 Scanning every {CONFIG['scan_interval_seconds']}s")
    print(f"  📊 Alerting tokens cooldown: {CONFIG['alert_cooldown_minutes']} min")
    print("=" * 60)


def print_detection(det, idx):
    """Print a single liftoff detection."""
    log_file = CONFIG.get("log_file")

    print()
    print(f"{'━' * 58}")
    msg = f"  #{idx} | {det['signal']} | {det['token_name']} (${det['token_symbol']})"
    print(msg)
    print(f"  {'━' * 54}")
    print(f"  🎯 Score:        {det['score']}/100")
    if det['price_usd'] < 0.01:
        print(f"  💰 Price:        ${det['price_usd']:.8f}")
    else:
        print(f"  💰 Price:        ${det['price_usd']:.6f}")
    print(f"  💧 Liquidity:    {format_usd(det['liquidity'])}")
    print(f"  📊 FDV:          {format_usd(det['fdv'])}")
    print()
    print(f"  ⏱️  REAL-TIME MOMENTUM:")
    print(f"     Vol 5m: {format_usd(det['volume_5m'])}  |  Vol 1h: {format_usd(det['volume_1h'])}  |  Vol 24h: {format_usd(det['volume_24h'])}")
    print(f"     Buys/Sells 5m: {det['buys_5m']}/{det['sells_5m']}  |  1h: {det['buys_1h']}/{det['sells_1h']}")
    print(f"     Price: {det['price_change_5m']:+.1f}% (5m) | {det['price_change_1h']:+.1f}% (1h) | {det['price_change_24h']:+.1f}% (24h)")
    print(f"  ⏰ Age:          {det['pair_age_hours']:.0f}h  |  DEX: {det['dex']}")
    print()

    if det['signals']:
        print(f"  ✅ SIGNALS:")
        for s in det['signals']:
            print(f"     {s}")

    if det['warnings']:
        print(f"  ⚠️  WARNINGS:")
        for w in det['warnings']:
            print(f"     {w}")

    print(f"\n  🔗 {det['url']}")
    print(f"  📋 CA: {det['token_address']}")

    # Log alert
    if log_file:
        log(f"ALERT: {det['signal']} | {det['token_name']} (${det['token_symbol']}) | "
            f"Score: {det['score']} | FDV: {format_usd(det['fdv'])} | "
            f"Vol5m: {format_usd(det['volume_5m'])} | "
            f"Price 5m: {det['price_change_5m']:+.1f}% | "
            f"CA: {det['token_address']}", log_file)


def save_alert(detections):
    """Save alerts to JSON atomically (write to .tmp, then os.replace).
    Without atomicity the trading bot can read a half-written file and lose the alert."""
    output_dir = Path(CONFIG["output_dir"])
    output_dir.mkdir(exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    final_path = output_dir / f"alert_{date_str}.json"
    tmp_path = output_dir / f"alert_{date_str}.json.tmp"
    payload = {
        "scan_time": datetime.now(timezone.utc).isoformat(),
        "scan_number": scan_count,
        "detections": detections,
    }
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass  # fsync not supported on every platform/fs
    os.replace(str(tmp_path), str(final_path))


# ─── MAIN LOOP ──────────────────────────────────────────────

def main():
    single_run = "--once" in sys.argv

    print()
    print("  ╔═══════════════════════════════════════════╗")
    print("  ║   🚀 SOLANA LIFTOFF SNIPER               ║")
    print("  ║   Catching explosions in real-time        ║")
    print("  ╚═══════════════════════════════════════════╝")
    print()

    if single_run:
        print("  Mode: Single scan")
    else:
        print(f"  Mode: Continuous (every {CONFIG['scan_interval_seconds']}s)")
        print("  Press Ctrl+C to stop")
    print()

    # Initial data fetch
    log("Pobieram dane bazowe...")
    boosted = get_boosted_tokens()
    profiles = get_profiles()
    metas = get_trending_metas()
    log(f"Gotowe: {len(boosted)} boosted, {len(profiles)} profili, {len(metas)} trendów")
    print()

    # Ensure output dir
    Path(CONFIG["output_dir"]).mkdir(exist_ok=True)

    try:
        while True:
            print_header()

            detections, total_scanned = run_scan(boosted, profiles, metas)

            print(f"\n  📊 Przeskanowano {total_scanned} par")

            if detections:
                print(f"  🚨 WYKRYTO {len(detections)} LIFTOFF(ów)!")
                beep()
                for i, det in enumerate(detections, 1):
                    print_detection(det, i)
                save_alert(detections)
                print(f"\n  💾 Alerty zapisane w {CONFIG['output_dir']}/")
            else:
                print(f"  😴 Brak liftoffów w tym skanie. Monitoring kontynuowany...")

            print()
            print("─" * 58)
            print("  ⚠️  Max $5-10 per token. Większość pójdzie do zera.")
            print("  📖  Zawsze sprawdź ręcznie na DexScreener przed zakupem!")
            print("─" * 58)

            if single_run:
                break

            # Countdown to next scan
            interval = CONFIG["scan_interval_seconds"]
            print(f"\n  ⏳ Następny skan za {interval}s...", end="", flush=True)

            # Refresh base data every 10 scans
            if scan_count % 10 == 0:
                boosted = get_boosted_tokens()
                profiles = get_profiles()
                metas = get_trending_metas()

            for remaining in range(interval, 0, -1):
                time.sleep(1)
                if remaining % 30 == 0:
                    print(f"\r  ⏳ Następny skan za {remaining}s...   ", end="", flush=True)

            print()
            print()

    except KeyboardInterrupt:
        print("\n\n  👋 Sniper zatrzymany. Alerty zapisane w sniper_alerts/")
        print(f"  📊 Wykonano {scan_count} skanów")
        print()


if __name__ == "__main__":
    main()
