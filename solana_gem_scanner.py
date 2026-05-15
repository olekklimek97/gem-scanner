#!/usr/bin/env python3
"""
🔍 SOLANA GEM SCANNER — Daily Digest Agent
============================================
Skanuje DexScreener API w poszukiwaniu tokenów Solana z potencjałem.
Filtruje, scoruje i wyrzuca Top 5 dnia.

Uruchomienie:
    python3 solana_gem_scanner.py

Wymagania:
    pip install requests tabulate

Bez klucza API — korzysta z darmowego DexScreener API (60 req/min).
"""

import requests
import json
import time
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from db import save_scan

# ─── KONFIGURACJA ───────────────────────────────────────────
CONFIG = {
    # Filtry podstawowe (poluzowane)
    "min_liquidity_usd": 2_000,       # Min płynność w USD
    "max_liquidity_usd": 2_000_000,   # Max — podwyższone
    "min_volume_24h": 3_000,          # Min volume 24h (niższy próg)
    "min_txns_24h": 30,               # Min transakcje 24h (niższy próg)
    "max_fdv": 10_000_000,            # Max fully diluted valuation
    "min_fdv": 5_000,                 # Min FDV — niższy próg
    "max_pair_age_hours": 168,        # Max wiek pary (7 dni)
    "min_pair_age_hours": 0.5,        # Min wiek — pół godziny

    # Scoring — buy/sell ratio
    "healthy_buy_sell_min": 0.6,      # Min stosunek buys/total (>60% = bullish)

    # Output
    "top_n": 10,                      # Ile tokenów w raporcie
    "output_dir": "reports",          # Folder na raporty
}

DEXSCREENER_BASE = "https://api.dexscreener.com"
HEADERS = {"Accept": "application/json", "User-Agent": "SolanaGemScanner/1.0"}


# ─── API CALLS ──────────────────────────────────────────────

def api_get(endpoint, params=None):
    """Safe API call with retry."""
    url = f"{DEXSCREENER_BASE}{endpoint}"
    for attempt in range(3):
        try:
            resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
            if resp.status_code == 429:
                print(f"  ⏳ Rate limit, czekam 10s...")
                time.sleep(10)
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            if attempt == 2:
                print(f"  ❌ Błąd API: {e}")
                return None
            time.sleep(2)
    return None


def get_trending_metas():
    """Pobierz trending narratives/metas."""
    print("📡 Pobieram trending narratives...")
    data = api_get("/metas/trending/v1")
    if not data:
        return []
    # Top 5 trendujących kategorii
    metas = []
    for m in data[:5]:
        metas.append({
            "name": m.get("name", "?"),
            "slug": m.get("slug", ""),
            "market_cap": m.get("marketCap", 0),
            "volume": m.get("volume", 0),
            "change_24h": m.get("marketCapChange", {}).get("h24", 0),
            "token_count": m.get("tokenCount", 0),
        })
    return metas


def get_boosted_tokens():
    """Pobierz tokeny z aktywnym boostem (płatna promocja = uwaga na shill)."""
    print("📡 Pobieram boosted tokens...")
    data = api_get("/token-boosts/top/v1")
    if not data:
        return set()
    # Zwróć set adresów boostowanych tokenów Solana
    boosted = set()
    for t in data:
        if t.get("chainId") == "solana":
            boosted.add(t.get("tokenAddress", "").lower())
    return boosted


def get_latest_profiles():
    """Pobierz najnowsze profile tokenów — tokeny które dodały info (website, socials)."""
    print("📡 Pobieram latest token profiles...")
    data = api_get("/token-profiles/latest/v1")
    if not data:
        return {}
    profiles = {}
    for p in data:
        if p.get("chainId") == "solana":
            addr = p.get("tokenAddress", "").lower()
            profiles[addr] = {
                "has_website": any(l.get("type") == "website" for l in (p.get("links") or [])),
                "has_twitter": any(l.get("type") == "twitter" for l in (p.get("links") or [])),
                "has_telegram": any(l.get("type") == "telegram" for l in (p.get("links") or [])),
                "description": p.get("description", ""),
                "original_address": p.get("tokenAddress", ""),
            }
    return profiles


def get_pairs_for_tokens(token_addresses):
    """Pobierz pary dla listy adresów tokenów (max 30 na raz)."""
    all_pairs = []
    # API pozwala max 30 adresów na raz
    for i in range(0, len(token_addresses), 30):
        batch = token_addresses[i:i+30]
        addrs = ",".join(batch)
        print(f"📡 Pobieram pary dla {len(batch)} tokenów (batch {i//30 + 1})...")
        data = api_get(f"/token-pairs/v1/solana/{addrs}")
        if data and isinstance(data, list):
            all_pairs.extend(data)
        time.sleep(1)
    return all_pairs


def search_solana_tokens(query="SOL"):
    """Szukaj par na Solanie."""
    print(f"📡 Szukam par: '{query}'...")
    data = api_get("/latest/dex/search", params={"q": query})
    if not data:
        return []
    return data.get("pairs", [])


def get_meta_pairs(slug):
    """Pobierz pary z konkretnej kategorii/meta."""
    print(f"📡 Pobieram pary z kategorii: {slug}...")
    data = api_get(f"/metas/meta/v1/{slug}")
    if not data:
        return []
    return data.get("pairs", [])


# ─── SCORING ENGINE ─────────────────────────────────────────

def score_pair(pair, boosted_addrs, profiles):
    """
    Scoruje parę 0-100 na podstawie wielu kryteriów.
    Zwraca (score, breakdown) lub None jeśli nie przechodzi filtrów.
    """
    now = datetime.now(timezone.utc)
    breakdown = {}

    # ── Podstawowe dane ──
    chain = pair.get("chainId", "")
    if chain != "solana":
        return None

    base = pair.get("baseToken", {})
    token_addr = base.get("address", "").lower()
    token_name = base.get("name", "?")
    token_symbol = base.get("symbol", "?")

    liq = (pair.get("liquidity") or {}).get("usd", 0) or 0
    fdv = pair.get("fdv") or 0
    mc = pair.get("marketCap") or 0
    vol_24h = (pair.get("volume") or {}).get("h24", 0) or 0
    price_usd = float(pair.get("priceUsd") or 0)

    # Transakcje
    txns = pair.get("txns") or {}
    txns_24h = txns.get("h24", {})
    buys_24h = txns_24h.get("buys", 0)
    sells_24h = txns_24h.get("sells", 0)
    total_txns = buys_24h + sells_24h

    # Price change
    pc = pair.get("priceChange") or {}
    pc_5m = pc.get("m5", 0) or 0
    pc_1h = pc.get("h1", 0) or 0
    pc_6h = pc.get("h6", 0) or 0
    pc_24h = pc.get("h24", 0) or 0

    # Wiek pary
    created_at = pair.get("pairCreatedAt")
    if created_at:
        pair_age_hours = (now - datetime.fromtimestamp(created_at / 1000, tz=timezone.utc)).total_seconds() / 3600
    else:
        pair_age_hours = 999

    # ── FILTRY HARD (nie przechodzi = skip) ──
    c = CONFIG
    if liq < c["min_liquidity_usd"] or liq > c["max_liquidity_usd"]:
        return None
    if vol_24h < c["min_volume_24h"]:
        return None
    if total_txns < c["min_txns_24h"]:
        return None
    if fdv and (fdv > c["max_fdv"] or fdv < c["min_fdv"]):
        return None
    if pair_age_hours > c["max_pair_age_hours"] or pair_age_hours < c["min_pair_age_hours"]:
        return None

    # ── SCORING ──
    score = 0

    # 1. Volume/Liquidity ratio (max 20 pkt)
    # Wysoki vol relative do liq = dużo handlu = zainteresowanie
    vol_liq = vol_24h / liq if liq > 0 else 0
    if vol_liq >= 5:
        breakdown["vol/liq"] = 20
    elif vol_liq >= 2:
        breakdown["vol/liq"] = 15
    elif vol_liq >= 1:
        breakdown["vol/liq"] = 10
    else:
        breakdown["vol/liq"] = 5
    score += breakdown["vol/liq"]

    # 2. Buy/Sell ratio (max 20 pkt)
    if total_txns > 0:
        buy_ratio = buys_24h / total_txns
        if buy_ratio >= 0.65:
            breakdown["buy_pressure"] = 20
        elif buy_ratio >= 0.55:
            breakdown["buy_pressure"] = 15
        elif buy_ratio >= 0.45:
            breakdown["buy_pressure"] = 10
        else:
            breakdown["buy_pressure"] = 0  # Więcej sells niż buys = bearish
    else:
        breakdown["buy_pressure"] = 0
    score += breakdown["buy_pressure"]

    # 3. Price momentum (max 20 pkt)
    momentum = 0
    if pc_1h > 5:
        momentum += 7
    elif pc_1h > 0:
        momentum += 3
    if pc_6h > 10:
        momentum += 7
    elif pc_6h > 0:
        momentum += 3
    if pc_24h > 20:
        momentum += 6
    elif pc_24h > 0:
        momentum += 3
    breakdown["momentum"] = min(momentum, 20)
    score += breakdown["momentum"]

    # 4. Freshness — nowe pary dostają bonus (max 15 pkt)
    if pair_age_hours <= 6:
        breakdown["freshness"] = 15
    elif pair_age_hours <= 12:
        breakdown["freshness"] = 12
    elif pair_age_hours <= 24:
        breakdown["freshness"] = 10
    elif pair_age_hours <= 48:
        breakdown["freshness"] = 5
    else:
        breakdown["freshness"] = 2
    score += breakdown["freshness"]

    # 5. Profile completeness (max 10 pkt)
    profile = profiles.get(token_addr, {})
    prof_score = 0
    if profile.get("has_website"):
        prof_score += 4
    if profile.get("has_twitter"):
        prof_score += 4
    if profile.get("has_telegram"):
        prof_score += 2
    breakdown["profile"] = prof_score
    score += prof_score

    # 6. Transaction count bonus (max 10 pkt)
    if total_txns >= 1000:
        breakdown["activity"] = 10
    elif total_txns >= 500:
        breakdown["activity"] = 7
    elif total_txns >= 200:
        breakdown["activity"] = 5
    else:
        breakdown["activity"] = 2
    score += breakdown["activity"]

    # 7. Low FDV bonus — mniejsze = więcej upside (max 5 pkt)
    if fdv and fdv <= 100_000:
        breakdown["low_fdv"] = 5
    elif fdv and fdv <= 500_000:
        breakdown["low_fdv"] = 3
    else:
        breakdown["low_fdv"] = 1
    score += breakdown["low_fdv"]

    # 8. PENALTIES (ujemne punkty)
    penalties = 0

    # Dump penalty — jeśli cena spada mocno w krótkim czasie
    if pc_1h < -30:
        penalties -= 15
    elif pc_1h < -15:
        penalties -= 8

    # Boosted token penalty — płatna promocja = podejrzane
    if token_addr in boosted_addrs:
        penalties -= 5

    # Sell pressure penalty
    if total_txns > 0 and buys_24h / total_txns < 0.35:
        penalties -= 10

    breakdown["penalties"] = penalties
    score += penalties
    score = max(0, min(100, score))  # Clamp 0-100

    # ── SIGNAL STRENGTH ──
    # Dodatkowa etykieta
    if score >= 75:
        signal = "🟢 STRONG"
    elif score >= 55:
        signal = "🟡 MODERATE"
    elif score >= 40:
        signal = "🟠 WEAK"
    else:
        signal = "🔴 RISKY"

    # ── WARNINGS ──
    warnings = []
    if token_addr in boosted_addrs:
        warnings.append("⚠️ BOOSTED (paid promo)")
    if total_txns > 0 and buys_24h / total_txns < 0.4:
        warnings.append("🔴 Heavy selling")
    if pc_5m < -10:
        warnings.append("📉 Dumping last 5m")
    if pc_1h < -20:
        warnings.append("📉 Dumping last 1h")
    if liq < 5_000:
        warnings.append("💧 Micro liquidity (<$5K)")
    elif liq < 10_000:
        warnings.append("💧 Very low liquidity")
    if pair_age_hours < 2:
        warnings.append("🆕 Very new (<2h)")
    if fdv and fdv > 5_000_000:
        warnings.append("📊 High FDV (>$5M)")
    if vol_liq > 20:
        warnings.append("🔥 Extreme vol/liq ratio (possible wash trading)")

    return {
        "token_name": token_name,
        "token_symbol": token_symbol,
        "token_address": base.get("address", ""),
        "pair_address": pair.get("pairAddress", ""),
        "dex": pair.get("dexId", "?"),
        "url": pair.get("url", ""),
        "price_usd": price_usd,
        "liquidity": liq,
        "fdv": fdv,
        "market_cap": mc,
        "volume_24h": vol_24h,
        "buys_24h": buys_24h,
        "sells_24h": sells_24h,
        "price_change_1h": pc_1h,
        "price_change_6h": pc_6h,
        "price_change_24h": pc_24h,
        "pair_age_hours": round(pair_age_hours, 1),
        "score": score,
        "signal": signal,
        "breakdown": breakdown,
        "warnings": warnings,
    }


# ─── MAIN SCANNER ───────────────────────────────────────────

def run_scan():
    """Główna funkcja skanowania."""
    print("=" * 60)
    print("🔍 SOLANA GEM SCANNER — Daily Digest")
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    print()

    # 1. Pobierz dane pomocnicze
    boosted = get_boosted_tokens()
    profiles = get_latest_profiles()
    metas = get_trending_metas()
    time.sleep(1)

    # 2. Zbierz pary z różnych źródeł
    all_pairs = []

    # a) Z trendujących kategorii
    for meta in metas[:6]:
        slug = meta["slug"]
        if slug:
            pairs = get_meta_pairs(slug)
            all_pairs.extend(pairs)
            time.sleep(1)

    # b) NOWE: Pobierz pary dla tokenów ze świeżych profili
    # To są tokeny które niedawno dodały website/twitter = aktywne projekty
    profile_addrs = [v["original_address"] for v in profiles.values() if v.get("original_address")]
    if profile_addrs:
        profile_pairs = get_pairs_for_tokens(profile_addrs[:90])  # Max 3 batche po 30
        all_pairs.extend(profile_pairs)
        print(f"  → {len(profile_pairs)} par z profili tokenów")

    # c) NOWE: Pobierz pary dla boosted tokenów
    if boosted:
        boosted_list = list(boosted)[:30]
        boosted_pairs = get_pairs_for_tokens(boosted_list)
        all_pairs.extend(boosted_pairs)
        print(f"  → {len(boosted_pairs)} par z boosted tokenów")

    # d) Szukaj popularnych query — szerokie pokrycie
    search_queries = [
        # Memecoiny
        "pump", "moon", "pepe", "doge", "shib", "bonk", "wif",
        # Narratives
        "ai agent", "ai token", "depin", "rwa",
        # Trendy
        "meme", "cat", "dog", "frog",
        # Ogólne
        "new", "gem", "sol",
        # Knockoff/parody (trending)
        "trump", "elon", "biden",
    ]
    for q in search_queries:
        pairs = search_solana_tokens(q)
        all_pairs.extend(pairs)
        time.sleep(1)

    print(f"\n📊 Zebrano {len(all_pairs)} par do analizy...")

    # 3. Deduplikacja po pair address
    seen = set()
    unique_pairs = []
    for p in all_pairs:
        pa = p.get("pairAddress", "")
        if pa and pa not in seen:
            seen.add(pa)
            unique_pairs.append(p)

    print(f"📊 {len(unique_pairs)} unikalnych par po deduplikacji")

    # 4. Scoruj
    scored = []
    for pair in unique_pairs:
        result = score_pair(pair, boosted, profiles)
        if result:
            scored.append(result)

    print(f"📊 {len(scored)} par przeszło filtry")

    # 5. Sortuj i weź top N
    scored.sort(key=lambda x: x["score"], reverse=True)
    top = scored[:CONFIG["top_n"]]

    # 6. Wyświetl raport
    print_report(top, metas)

    # 7. Zapisz do pliku
    save_report(top, metas)

    # 8. Zapisz do SQLite
    save_scan(top)

    return top


def format_usd(val):
    if val >= 1_000_000:
        return f"${val/1_000_000:.1f}M"
    elif val >= 1_000:
        return f"${val/1_000:.1f}K"
    else:
        return f"${val:.0f}"


def print_report(top, metas):
    """Wyświetl ładny raport w terminalu."""
    print()
    print("=" * 60)
    print("🏆 TOP GEMS OF THE DAY")
    print("=" * 60)

    if not top:
        print("\n  😔 Brak tokenów spełniających kryteria.")
        print("  Spróbuj poluzować filtry w CONFIG.")
        return

    # Trending metas
    if metas:
        print("\n🔥 TRENDING NARRATIVES:")
        for m in metas[:3]:
            change = m["change_24h"]
            arrow = "🟢" if change > 0 else "🔴"
            print(f"   {arrow} {m['name']} — MC: {format_usd(m['market_cap'])} | 24h: {change:+.1f}%")

    print()

    for i, t in enumerate(top, 1):
        print(f"{'─' * 58}")
        print(f"  #{i} | {t['token_name']} (${t['token_symbol']})")
        print(f"  {'─' * 54}")
        print(f"  🎯 Score:        {t['score']}/100  {t['signal']}")
        print(f"  💰 Price:        ${t['price_usd']:.8f}" if t['price_usd'] < 0.01 else f"  💰 Price:        ${t['price_usd']:.4f}")
        print(f"  💧 Liquidity:    {format_usd(t['liquidity'])}")
        print(f"  📊 FDV:          {format_usd(t['fdv'])}")
        print(f"  📈 Volume 24h:   {format_usd(t['volume_24h'])}")
        print(f"  🛒 Buys/Sells:   {t['buys_24h']}/{t['sells_24h']}")
        print(f"  📊 Price 1h/6h/24h: {t['price_change_1h']:+.1f}% / {t['price_change_6h']:+.1f}% / {t['price_change_24h']:+.1f}%")
        print(f"  ⏰ Age:          {t['pair_age_hours']:.0f}h")
        print(f"  🏦 DEX:          {t['dex']}")
        print(f"  🔗 {t['url']}")

        # Score breakdown
        bd = t['breakdown']
        print(f"  📋 Breakdown:    V/L:{bd.get('vol/liq',0)} Buy:{bd.get('buy_pressure',0)} Mom:{bd.get('momentum',0)} Fresh:{bd.get('freshness',0)} Prof:{bd.get('profile',0)} Act:{bd.get('activity',0)} FDV:{bd.get('low_fdv',0)} Pen:{bd.get('penalties',0)}")

        if t['warnings']:
            print(f"  ⚠️  {' | '.join(t['warnings'])}")

        print(f"  📋 CA: {t['token_address']}")
        print()

    print("─" * 58)
    print("⚠️  DISCLAIMER: To nie jest porada inwestycyjna!")
    print("    Max $5-10 per token. Większość pójdzie do zera.")
    print("    Zawsze rób własny research przed zakupem.")
    print("─" * 58)


def save_report(top, metas):
    """Zapisz raport do JSON i TXT."""
    output_dir = Path(CONFIG["output_dir"])
    output_dir.mkdir(exist_ok=True)

    date_str = datetime.now().strftime("%Y-%m-%d_%H%M")

    # JSON
    report = {
        "scan_date": datetime.now(timezone.utc).isoformat(),
        "config": CONFIG,
        "trending_metas": metas,
        "top_tokens": top,
    }

    json_path = output_dir / f"scan_{date_str}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    # Readable TXT
    txt_path = output_dir / f"scan_{date_str}.txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(f"SOLANA GEM SCANNER — {date_str}\n")
        f.write("=" * 50 + "\n\n")

        if metas:
            f.write("TRENDING NARRATIVES:\n")
            for m in metas[:3]:
                f.write(f"  • {m['name']} — MC: {format_usd(m['market_cap'])} | 24h: {m['change_24h']:+.1f}%\n")
            f.write("\n")

        for i, t in enumerate(top, 1):
            f.write(f"#{i} {t['token_name']} (${t['token_symbol']}) — Score: {t['score']}/100\n")
            f.write(f"   Price: ${t['price_usd']:.8f}\n")
            f.write(f"   Liq: {format_usd(t['liquidity'])} | FDV: {format_usd(t['fdv'])} | Vol: {format_usd(t['volume_24h'])}\n")
            f.write(f"   Buys/Sells: {t['buys_24h']}/{t['sells_24h']} | Age: {t['pair_age_hours']:.0f}h\n")
            f.write(f"   1h/6h/24h: {t['price_change_1h']:+.1f}% / {t['price_change_6h']:+.1f}% / {t['price_change_24h']:+.1f}%\n")
            f.write(f"   {t['url']}\n")
            f.write(f"   CA: {t['token_address']}\n")
            if t['warnings']:
                f.write(f"   ⚠️  {' | '.join(t['warnings'])}\n")
            f.write("\n")

        f.write("\n⚠️  Nie jest to porada inwestycyjna. DYOR.\n")

    print(f"\n💾 Raport zapisany:")
    print(f"   📄 {json_path}")
    print(f"   📄 {txt_path}")


# ─── ENTRY POINT ─────────────────────────────────────────────

if __name__ == "__main__":
    run_scan()