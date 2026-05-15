#!/usr/bin/env python3
"""
🛡️ SAFETY MODULE — Token Safety Checks
=========================================
Implementuje 6 krytycznych punktów z product review:
1. Honeypot detection (simulated sell via Jupiter quote)
2. Mint authority / freeze authority check
3. LP lock / burn check
4. Top holder concentration
5. Auto-sweep zysków na cold wallet
6. Comprehensive pre-buy safety gate

Używany przez trading_bot.py przed każdym zakupem.

Wymaga: pip install requests
Opcjonalnie: rugcheck (pip install rugcheck) dla pełnych raportów
"""

import requests
import json
import time
from typing import Optional
from dataclasses import dataclass, field

# ─── CONFIG ─────────────────────────────────────────────────

RUGCHECK_API = "https://api.rugcheck.xyz"
JUPITER_QUOTE_URL = "https://quote-api.jup.ag/v6/quote"
SOL_MINT = "So11111111111111111111111111111111111111111112"

# Jupiter circuit breaker (mirrors trading_bot but module-local to avoid coupling)
_JUPITER_CB = {
    "consecutive_failures": 0,
    "open_until": 0.0,
    "fail_threshold": 3,
    "open_duration_sec": 60,
}


def _jup_breaker_open() -> bool:
    return time.time() < _JUPITER_CB["open_until"]


def _jup_failure():
    _JUPITER_CB["consecutive_failures"] += 1
    if _JUPITER_CB["consecutive_failures"] >= _JUPITER_CB["fail_threshold"]:
        _JUPITER_CB["open_until"] = time.time() + _JUPITER_CB["open_duration_sec"]


def _jup_success():
    if _JUPITER_CB["consecutive_failures"] > 0 or _JUPITER_CB["open_until"] > 0:
        _JUPITER_CB["consecutive_failures"] = 0
        _JUPITER_CB["open_until"] = 0.0

# Safety thresholds
SAFETY_CONFIG = {
    "max_top10_holder_pct": 25,       # Max % supply held by top 10 (excluding LP/burn)
    "require_mint_revoked": True,      # Mint authority must be revoked
    "require_freeze_revoked": True,    # Freeze authority must be revoked
    "min_lp_burned_pct": 50,           # Min % LP burned/locked
    "honeypot_max_sell_tax_pct": 15,   # Max sell tax detected via simulated swap
    "min_rugcheck_score": 300,         # Min RugCheck score (Good=700+, some risk=300-700)

    # Auto-sweep
    "auto_sweep_enabled": True,
    "auto_sweep_threshold_sol": 0.5,   # Sweep profits above this to cold wallet
    "cold_wallet_address": "",         # SET THIS to your Phantom/cold wallet address
}


# ─── DATA CLASSES ────────────────────────────────────────────

@dataclass
class SafetyReport:
    """Result of all safety checks for a token."""
    token_address: str
    passed: bool = False
    score: int = 0  # 0-100 safety score

    # Individual checks
    honeypot_safe: Optional[bool] = None
    honeypot_sell_tax_pct: float = 0.0
    honeypot_detail: str = ""

    mint_revoked: Optional[bool] = None
    freeze_revoked: Optional[bool] = None

    lp_burned_pct: float = 0.0
    lp_locked: Optional[bool] = None

    top10_holder_pct: float = 0.0
    holder_count: int = 0

    rugcheck_score: int = 0
    rugcheck_risks: list = field(default_factory=list)

    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"  🛡️ SAFETY REPORT: {self.token_address[:12]}..."]
        lines.append(f"  {'✅ PASSED' if self.passed else '❌ FAILED'} (score: {self.score}/100)")
        lines.append(f"  ")

        checks = [
            (self.honeypot_safe, f"Honeypot: {'SAFE' if self.honeypot_safe else 'DANGER'} (sell tax: {self.honeypot_sell_tax_pct:.1f}%)", self.honeypot_detail),
            (self.mint_revoked, f"Mint authority: {'REVOKED ✓' if self.mint_revoked else 'ACTIVE ⚠️'}", ""),
            (self.freeze_revoked, f"Freeze authority: {'REVOKED ✓' if self.freeze_revoked else 'ACTIVE ⚠️'}", ""),
            (self.lp_burned_pct >= SAFETY_CONFIG["min_lp_burned_pct"], f"LP burned: {self.lp_burned_pct:.0f}%", ""),
            (self.top10_holder_pct <= SAFETY_CONFIG["max_top10_holder_pct"], f"Top 10 holders: {self.top10_holder_pct:.1f}%", ""),
        ]

        for ok, msg, detail in checks:
            emoji = "✅" if ok else "❌" if ok is False else "❓"
            lines.append(f"     {emoji} {msg}")
            if detail:
                lines.append(f"        {detail}")

        if self.rugcheck_risks:
            lines.append(f"  ⚠️  RugCheck risks: {', '.join(self.rugcheck_risks[:3])}")

        if self.errors:
            lines.append(f"  ❌ Errors: {', '.join(self.errors)}")

        return "\n".join(lines)


# ─── CHECK 1: HONEYPOT DETECTION ────────────────────────────

def check_honeypot(token_address: str) -> dict:
    """
    Detect honeypot by simulating a sell via Jupiter quote.
    If sell quote fails or shows extreme price impact → likely honeypot.
    Returns safe=None if Jupiter is unreachable (circuit breaker open).
    """
    result = {
        "safe": None,
        "sell_tax_pct": 0.0,
        "detail": "",
    }

    if _jup_breaker_open():
        result["detail"] = "Jupiter circuit breaker open — honeypot check skipped"
        return result

    try:
        # Step 1: Get buy quote (SOL → Token) for a small amount
        buy_amount = 1_000_000  # 0.001 SOL in lamports
        buy_quote = requests.get(JUPITER_QUOTE_URL, params={
            "inputMint": SOL_MINT,
            "outputMint": token_address,
            "amount": str(buy_amount),
            "slippageBps": 5000,
        }, timeout=10).json()

        if "error" in buy_quote or not buy_quote.get("outAmount"):
            result["safe"] = False
            result["detail"] = "Cannot get buy quote — token may not be tradeable"
            _jup_success()  # API responded, just no route — don't trip breaker
            return result

        tokens_received = int(buy_quote["outAmount"])

        # Step 2: Simulate immediate sell of those tokens
        time.sleep(0.5)
        sell_quote = requests.get(JUPITER_QUOTE_URL, params={
            "inputMint": token_address,
            "outputMint": SOL_MINT,
            "amount": str(tokens_received),
            "slippageBps": 5000,
        }, timeout=10).json()

        if "error" in sell_quote or not sell_quote.get("outAmount"):
            result["safe"] = False
            result["detail"] = "Cannot get sell quote — likely HONEYPOT (selling blocked)"
            return result

        sol_returned = int(sell_quote["outAmount"])

        # Step 3: Calculate effective sell tax
        # Perfect round-trip would return buy_amount
        # Difference = fees + price impact + any sell tax
        loss_pct = (1 - sol_returned / buy_amount) * 100
        result["sell_tax_pct"] = loss_pct

        if loss_pct > 90:
            result["safe"] = False
            result["detail"] = f"Sell returns only {100-loss_pct:.0f}% — almost certainly HONEYPOT"
        elif loss_pct > SAFETY_CONFIG["honeypot_max_sell_tax_pct"]:
            result["safe"] = False
            result["detail"] = f"Sell tax {loss_pct:.1f}% exceeds max {SAFETY_CONFIG['honeypot_max_sell_tax_pct']}%"
        else:
            result["safe"] = True
            result["detail"] = f"Round-trip loss {loss_pct:.1f}% (includes DEX fees + price impact)"

    except requests.exceptions.Timeout:
        result["detail"] = "Jupiter API timeout"
        result["safe"] = None
        _jup_failure()
    except Exception as e:
        result["detail"] = f"Error: {str(e)[:80]}"
        result["safe"] = None
        _jup_failure()
    else:
        _jup_success()

    return result


# ─── CHECK 2-4: RUGCHECK API ────────────────────────────────

def check_rugcheck(token_address: str) -> dict:
    """
    Get comprehensive safety report from RugCheck API.
    Checks: mint authority, freeze authority, LP lock, holder concentration.
    """
    result = {
        "mint_revoked": None,
        "freeze_revoked": None,
        "lp_burned_pct": 0.0,
        "lp_locked": None,
        "top10_holder_pct": 0.0,
        "holder_count": 0,
        "score": 0,
        "risks": [],
        "error": None,
    }

    try:
        # Try report/summary first (lighter)
        resp = requests.get(
            f"{RUGCHECK_API}/v1/tokens/{token_address}/report/summary",
            timeout=15,
            headers={"Accept": "application/json"},
        )

        if resp.status_code == 200:
            data = resp.json()
            _parse_rugcheck_report(data, result)
            return result

        # Fallback: full report
        resp = requests.get(
            f"{RUGCHECK_API}/v1/tokens/{token_address}/report",
            timeout=15,
            headers={"Accept": "application/json"},
        )

        if resp.status_code == 200:
            data = resp.json()
            _parse_rugcheck_report(data, result)
        else:
            result["error"] = f"RugCheck API returned {resp.status_code}"

    except requests.exceptions.Timeout:
        result["error"] = "RugCheck API timeout"
    except Exception as e:
        result["error"] = f"RugCheck error: {str(e)[:80]}"

    return result


def _parse_rugcheck_report(data: dict, result: dict):
    """Parse RugCheck API response into our result format."""

    # Score
    result["score"] = data.get("score", 0)

    # Risks
    risks = data.get("risks", [])
    if isinstance(risks, list):
        for r in risks:
            if isinstance(r, dict):
                name = r.get("name", "")
                level = r.get("level", "")
                if name:
                    result["risks"].append(f"{name} ({level})")
            elif isinstance(r, str):
                result["risks"].append(r)

    # Token meta / authorities
    token_meta = data.get("tokenMeta", data.get("token", {}))
    if isinstance(token_meta, dict):
        # Mint authority
        mint_auth = token_meta.get("mintAuthority")
        if mint_auth is None or mint_auth == "" or mint_auth == "null":
            result["mint_revoked"] = True
        else:
            result["mint_revoked"] = False

        # Freeze authority
        freeze_auth = token_meta.get("freezeAuthority")
        if freeze_auth is None or freeze_auth == "" or freeze_auth == "null":
            result["freeze_revoked"] = True
        else:
            result["freeze_revoked"] = False

    # LP info
    markets = data.get("markets", [])
    if isinstance(markets, list):
        for market in markets:
            if isinstance(market, dict):
                lp = market.get("lp", {})
                if isinstance(lp, dict):
                    burned_pct = lp.get("lpBurnPct", lp.get("burnPct", 0))
                    if burned_pct:
                        result["lp_burned_pct"] = max(result["lp_burned_pct"], float(burned_pct))
                    locked_pct = lp.get("lpLockedPct", lp.get("lockedPct", 0))
                    if locked_pct and float(locked_pct) > 50:
                        result["lp_locked"] = True

    # Top holders
    top_holders = data.get("topHolders", [])
    if isinstance(top_holders, list):
        total_pct = 0
        count = 0
        for h in top_holders[:10]:
            if isinstance(h, dict):
                pct = h.get("pct", h.get("percentage", 0))
                # Skip known LP/burn addresses
                addr = h.get("address", "").lower()
                is_system = any(x in addr for x in [
                    "1111111111111",  # Burn address
                    "5q544fkrfoe",    # Raydium LP
                ])
                owner = (h.get("owner", "") or "").lower()
                is_lp = "raydium" in owner or "orca" in owner or "burn" in owner

                if not is_system and not is_lp:
                    total_pct += float(pct)
                    count += 1

        result["top10_holder_pct"] = total_pct
        result["holder_count"] = len(top_holders)

    # Also check for specific risk flags in the data
    if data.get("rugged"):
        result["risks"].append("TOKEN_RUGGED")
    if data.get("freezeAuthority") or (token_meta and token_meta.get("freezeAuthority")):
        if result["freeze_revoked"] is None:
            result["freeze_revoked"] = False


# ─── COMBINED SAFETY GATE ───────────────────────────────────

def run_safety_check(token_address: str) -> SafetyReport:
    """
    Run all safety checks. Returns SafetyReport with pass/fail.
    This is the main function called by trading_bot.py before buying.
    """
    report = SafetyReport(token_address=token_address)
    score = 0

    print(f"  🛡️ Running safety check on {token_address[:12]}...")

    # 1. Honeypot check (simulated sell)
    print(f"     🔍 Checking honeypot...")
    hp = check_honeypot(token_address)
    report.honeypot_safe = hp["safe"]
    report.honeypot_sell_tax_pct = hp["sell_tax_pct"]
    report.honeypot_detail = hp["detail"]

    if hp["safe"] is True:
        score += 30
    elif hp["safe"] is False:
        score -= 50  # Honeypot is instant fail
    else:
        report.warnings.append("Honeypot check inconclusive")

    time.sleep(0.5)

    # 2-4. RugCheck (mint, freeze, LP, holders)
    print(f"     🔍 Checking RugCheck...")
    rc = check_rugcheck(token_address)

    if rc["error"]:
        report.errors.append(rc["error"])
    else:
        # Mint authority
        report.mint_revoked = rc["mint_revoked"]
        if rc["mint_revoked"] is True:
            score += 15
        elif rc["mint_revoked"] is False:
            score -= 30  # Active mint = can print tokens
            report.warnings.append("MINT AUTHORITY ACTIVE — dev can print tokens")

        # Freeze authority
        report.freeze_revoked = rc["freeze_revoked"]
        if rc["freeze_revoked"] is True:
            score += 10
        elif rc["freeze_revoked"] is False:
            score -= 25  # Active freeze = can freeze your wallet
            report.warnings.append("FREEZE AUTHORITY ACTIVE — dev can freeze your tokens")

        # LP burned/locked
        report.lp_burned_pct = rc["lp_burned_pct"]
        report.lp_locked = rc["lp_locked"]
        if rc["lp_burned_pct"] >= 90:
            score += 15
        elif rc["lp_burned_pct"] >= SAFETY_CONFIG["min_lp_burned_pct"]:
            score += 10
        elif rc["lp_locked"]:
            score += 8
        else:
            score -= 10
            report.warnings.append(f"LP only {rc['lp_burned_pct']:.0f}% burned, not locked")

        # Top holders
        report.top10_holder_pct = rc["top10_holder_pct"]
        report.holder_count = rc["holder_count"]
        if rc["top10_holder_pct"] <= 15:
            score += 15
        elif rc["top10_holder_pct"] <= SAFETY_CONFIG["max_top10_holder_pct"]:
            score += 10
        elif rc["top10_holder_pct"] <= 40:
            score += 0
            report.warnings.append(f"High holder concentration: {rc['top10_holder_pct']:.0f}%")
        else:
            score -= 20
            report.warnings.append(f"EXTREME holder concentration: {rc['top10_holder_pct']:.0f}%")

        # RugCheck score
        report.rugcheck_score = rc["score"]
        report.rugcheck_risks = rc["risks"]
        if rc["score"] >= 700:
            score += 15
        elif rc["score"] >= SAFETY_CONFIG["min_rugcheck_score"]:
            score += 5

    # Normalize score 0-100
    report.score = max(0, min(100, score))

    # PASS/FAIL decision
    # Hard failures (any of these = instant FAIL):
    hard_fail = False
    if report.honeypot_safe is False:
        hard_fail = True
    if report.mint_revoked is False and SAFETY_CONFIG["require_mint_revoked"]:
        hard_fail = True
    if report.freeze_revoked is False and SAFETY_CONFIG["require_freeze_revoked"]:
        hard_fail = True
    if report.top10_holder_pct > SAFETY_CONFIG["max_top10_holder_pct"]:
        hard_fail = True

    report.passed = not hard_fail and report.score >= 30

    return report


# ─── NEW SAFETY LAYERS (continuous + advanced pre-buy) ──────

DEXSCREENER_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens/"
DEFAULT_RPC_URL = "https://api.mainnet-beta.solana.com"
SYSTEM_PROGRAM = "11111111111111111111111111111111"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"

# Well-known burn / dead addresses
DEAD_ADDRESSES = {
    "1nc1nerator11111111111111111111111111111111",  # Solana incinerator
    "11111111111111111111111111111111",
    "1111111111111111111111111111111111",
    "deadDEAD000000000000000000000000000000000000",
    "burnburnburnburnburnburnburnburnburnburnburn",
}

# Per-process cache so we don't hammer external APIs from the monitoring loop.
# Each entry: {key: (value, expiry_epoch)}. Successful lookups: 1h TTL. Failures: 60s TTL.
_safety_cache = {
    "deployer": {},          # token_addr -> (result_dict, expiry_epoch)
    "lp_verification": {},
}
_CACHE_TTL_SUCCESS_SEC = 3600   # 1 hour for ok=True lookups
_CACHE_TTL_FAILURE_SEC = 60     # 60 seconds for ok=False (so we don't pin a transient failure)


def _cache_get(bucket: str, key: str):
    """Return cached value if not expired, else None."""
    entry = _safety_cache.get(bucket, {}).get(key)
    if not entry:
        return None
    value, expiry = entry
    if time.time() >= expiry:
        # Expired — drop it
        try:
            del _safety_cache[bucket][key]
        except KeyError:
            pass
        return None
    return value


def _cache_set(bucket: str, key: str, value: dict):
    """Store with TTL based on value['ok'] (success → 1h, failure → 60s)."""
    ttl = _CACHE_TTL_SUCCESS_SEC if value.get("ok", True) else _CACHE_TTL_FAILURE_SEC
    _safety_cache.setdefault(bucket, {})[key] = (value, time.time() + ttl)

# Threshold config for new layers
NEW_SAFETY_CONFIG = {
    "liquidity_drop_emergency_pct": 50,    # >50% drop = emergency sell
    "liquidity_drop_warning_pct": 30,
    "holder_dump_warning_pct": 20,          # single holder dumping >20% supply
    "honeypot_impact_diff_max_pct": 30,    # >30% impact diff between sell sizes = suspect
    "deployer_max_recent_tokens": 5,        # >5 tokens in 7 days = serial deployer
    "mint_recheck_interval_sec": 300,       # re-check mint authority every 5 min
}


def _get_dexscreener_pair(token_address: str) -> Optional[dict]:
    """Fetch the most-liquid DexScreener pair for a token."""
    try:
        r = requests.get(
            f"{DEXSCREENER_TOKEN_URL}{token_address}",
            headers={"Accept": "application/json", "User-Agent": "DegenBot-Safety/1.0"},
            timeout=10,
        )
        r.raise_for_status()
        pairs = (r.json() or {}).get("pairs") or []
        if not pairs:
            return None
        return max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd", 0))
    except Exception:
        return None


def _rugcheck_report(token_address: str) -> Optional[dict]:
    """Fetch full RugCheck report. Returns None on failure."""
    try:
        r = requests.get(
            f"{RUGCHECK_API}/v1/tokens/{token_address}/report",
            headers={"Accept": "application/json"},
            timeout=15,
        )
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


# ─── LAYER 1: REAL-TIME LIQUIDITY MONITORING ────────────────

def monitor_liquidity(token_address: str, baseline_liquidity_usd: float) -> dict:
    """Compare current liquidity vs baseline at buy time.
    Returns emergency_sell=True if liquidity dropped >50%."""
    result = {
        "layer": "liquidity_monitor",
        "ok": True,
        "emergency_sell": False,
        "warning": False,
        "baseline_liquidity_usd": baseline_liquidity_usd,
        "current_liquidity_usd": 0,
        "drop_pct": 0,
        "message": "",
    }
    pair = _get_dexscreener_pair(token_address)
    if not pair:
        result["ok"] = False
        result["message"] = "DexScreener data unavailable"
        return result

    current_liq = float((pair.get("liquidity") or {}).get("usd", 0) or 0)
    result["current_liquidity_usd"] = current_liq

    if baseline_liquidity_usd > 0:
        drop_pct = (baseline_liquidity_usd - current_liq) / baseline_liquidity_usd * 100
        result["drop_pct"] = round(drop_pct, 2)
        emergency_t = NEW_SAFETY_CONFIG["liquidity_drop_emergency_pct"]
        warn_t = NEW_SAFETY_CONFIG["liquidity_drop_warning_pct"]
        if drop_pct >= emergency_t:
            result["emergency_sell"] = True
            result["message"] = f"🚨 Liquidity dropped {drop_pct:.1f}% (${baseline_liquidity_usd:.0f} → ${current_liq:.0f}) — EMERGENCY"
        elif drop_pct >= warn_t:
            result["warning"] = True
            result["message"] = f"⚠️ Liquidity dropped {drop_pct:.1f}%"
        else:
            result["message"] = f"OK: liquidity Δ{-drop_pct:+.1f}% (${current_liq:.0f})"
    else:
        result["message"] = f"No baseline; current liq ${current_liq:.0f}"
    return result


# ─── LAYER 2: HOLDER CONCENTRATION TRACKING ────────────────

def _take_holder_snapshot(token_address: str) -> list:
    """Return list of top-10 {address, pct} from RugCheck."""
    data = _rugcheck_report(token_address)
    if not data:
        return []
    holders = data.get("topHolders") or []
    return [
        {"address": (h.get("address") or "")[:44], "pct": float(h.get("pct", 0) or 0)}
        for h in holders[:10]
        if isinstance(h, dict)
    ]


def check_holder_changes(token_address: str, previous_snapshot: Optional[list] = None) -> dict:
    """Compare current holder snapshot with previous. Flag if any holder dumped >20%.
    Always returns the new snapshot so caller can store it."""
    result = {
        "layer": "holder_changes",
        "ok": True,
        "warning": False,
        "snapshot": [],
        "large_dumps": [],
        "message": "",
    }
    current = _take_holder_snapshot(token_address)
    result["snapshot"] = current
    if not current:
        result["ok"] = False
        result["message"] = "Holder data unavailable"
        return result

    if not previous_snapshot:
        result["message"] = f"Baseline recorded ({len(current)} holders)"
        return result

    prev_map = {h["address"]: h["pct"] for h in previous_snapshot if isinstance(h, dict)}
    threshold = NEW_SAFETY_CONFIG["holder_dump_warning_pct"]
    for h in current:
        prev = prev_map.get(h["address"], 0)
        drop = prev - h["pct"]
        if drop >= threshold:
            result["large_dumps"].append({
                "address": h["address"],
                "previous_pct": prev,
                "current_pct": h["pct"],
                "drop_pct": round(drop, 2),
            })
    # Also detect holders that disappeared entirely
    current_addrs = {h["address"] for h in current}
    for prev_addr, prev_pct in prev_map.items():
        if prev_addr not in current_addrs and prev_pct >= threshold:
            result["large_dumps"].append({
                "address": prev_addr,
                "previous_pct": prev_pct,
                "current_pct": 0,
                "drop_pct": prev_pct,
            })

    if result["large_dumps"]:
        result["warning"] = True
        top = max(result["large_dumps"], key=lambda d: d["drop_pct"])
        result["message"] = f"⚠️ Holder {top['address'][:8]}... dumped {top['drop_pct']:.1f}% supply"
    else:
        result["message"] = "OK: no major holder dumps"
    return result


# ─── LAYER 3: MINT AUTHORITY RE-CHECK ──────────────────────

def recheck_mint_authority(token_address: str, rpc_url: str = DEFAULT_RPC_URL) -> dict:
    """Verify mint AND freeze authority are still revoked. Trigger emergency on restore."""
    result = {
        "layer": "mint_recheck",
        "ok": True,
        "emergency_sell": False,
        "mint_authority": None,
        "freeze_authority": None,
        "message": "",
    }
    try:
        payload = {
            "jsonrpc": "2.0", "id": 1,
            "method": "getAccountInfo",
            "params": [token_address, {"encoding": "jsonParsed"}],
        }
        r = requests.post(rpc_url, json=payload, timeout=10)
        value = (r.json().get("result") or {}).get("value")
        if not value:
            result["ok"] = False
            result["message"] = "RPC returned no account info"
            return result
        info = ((value.get("data") or {}).get("parsed") or {}).get("info") or {}
        mint_auth = info.get("mintAuthority")
        freeze_auth = info.get("freezeAuthority")
        result["mint_authority"] = mint_auth
        result["freeze_authority"] = freeze_auth

        if mint_auth not in (None, "", "null"):
            result["emergency_sell"] = True
            result["message"] = f"🚨 MINT AUTHORITY ACTIVE: {str(mint_auth)[:20]}... — EMERGENCY SELL"
        elif freeze_auth not in (None, "", "null"):
            result["emergency_sell"] = True
            result["message"] = f"🚨 FREEZE AUTHORITY ACTIVE: {str(freeze_auth)[:20]}... — EMERGENCY SELL"
        else:
            result["message"] = "OK: mint & freeze still revoked"
    except Exception as e:
        result["ok"] = False
        result["message"] = f"Mint recheck error: {str(e)[:80]}"
    return result


# ─── LAYER 4: LP BURN / DEAD-ADDRESS VERIFICATION ──────────

def _is_dead_address(addr: str) -> bool:
    if not addr:
        return False
    if addr in DEAD_ADDRESSES:
        return True
    # Heuristic: pure '1's address (Solana null-like)
    if addr.startswith("1111111111") and len(addr) >= 32:
        return True
    # Common burn patterns
    al = addr.lower()
    if "burn" in al or "dead" in al or "incinerator" in al:
        return True
    return False


def verify_lp_dead_address(token_address: str, rpc_url: str = DEFAULT_RPC_URL) -> dict:
    """Verify top LP holder is on a real burn address (not a contract that could unlock)."""
    result = {
        "layer": "lp_verification",
        "ok": True,
        "lp_safe": False,
        "lp_holder": None,
        "holder_type": "unknown",
        "message": "",
    }
    cached = _cache_get("lp_verification", token_address)
    if cached is not None:
        return cached

    data = _rugcheck_report(token_address)
    if not data:
        result["ok"] = False
        result["message"] = "RugCheck unavailable"
        _cache_set("lp_verification", token_address, result)
        return result

    markets = data.get("markets") or []
    if not markets:
        result["message"] = "No LP market data"
        _cache_set("lp_verification", token_address, result)
        return result

    # Find any pair's LP holders
    lp_holders = []
    for m in markets:
        if isinstance(m, dict):
            holders = ((m.get("lp") or {}).get("holders")) or []
            if holders:
                lp_holders = holders
                break

    if not lp_holders:
        result["message"] = "No LP holder data"
        _cache_set("lp_verification", token_address, result)
        return result

    top = lp_holders[0] if isinstance(lp_holders[0], dict) else {}
    addr = top.get("address", "") or top.get("owner", "")
    result["lp_holder"] = addr

    if _is_dead_address(addr):
        result["lp_safe"] = True
        result["holder_type"] = "burn_address"
        result["message"] = f"✅ LP on dead address {addr[:20]}..."
    else:
        # Classify wallet vs contract via account info
        try:
            payload = {
                "jsonrpc": "2.0", "id": 1,
                "method": "getAccountInfo",
                "params": [addr, {"encoding": "base64"}],
            }
            r = requests.post(rpc_url, json=payload, timeout=10)
            value = (r.json().get("result") or {}).get("value")
            if value:
                owner = value.get("owner", "")
                executable = value.get("executable", False)
                if executable:
                    result["holder_type"] = "program"
                    result["message"] = f"⚠️ LP held by executable program {addr[:20]}... — verify lock"
                elif owner == SYSTEM_PROGRAM:
                    result["holder_type"] = "wallet"
                    result["message"] = f"⚠️ LP held by wallet {addr[:20]}... — unlockable"
                elif owner == TOKEN_PROGRAM:
                    result["holder_type"] = "token_account"
                    result["message"] = f"⚠️ LP in token account {addr[:20]}... — verify owner"
                else:
                    result["holder_type"] = f"owned_by:{owner[:12]}"
                    result["message"] = f"⚠️ LP holder owned by {owner[:20]}... — verify lock"
            else:
                result["message"] = f"⚠️ LP holder {addr[:20]}... — no account info"
        except Exception as e:
            result["ok"] = False
            result["message"] = f"LP classification error: {str(e)[:60]}"

    _cache_set("lp_verification", token_address, result)
    return result


# ─── LAYER 5: MULTI-AMOUNT HONEYPOT SIMULATION ─────────────

def honeypot_check_multi_amount(token_address: str, total_tokens_simulated: int = 1_000_000_000) -> dict:
    """Test sell quotes at 10/50/100% of position. Flag if any fail or impact diff >30%.
    Gracefully degrades when Jupiter is unreachable (returns ok=False, not a hard fail)."""
    result = {
        "layer": "honeypot_multi_amount",
        "ok": True,
        "passed": False,
        "tests": [],
        "max_impact_diff_pct": 0,
        "message": "",
    }
    test_pcts = [10, 50, 100]
    network_failures = 0

    # Short-circuit if Jupiter is known-down — saves 30s of timeouts
    if _jup_breaker_open():
        result["ok"] = False
        result["message"] = "Jupiter circuit breaker open — honeypot multi-amount skipped (not blocking)"
        return result

    for pct in test_pcts:
        amount = max(1, int(total_tokens_simulated * pct / 100))
        test = {
            "pct": pct, "amount": amount,
            "success": False, "out_amount": 0,
            "price_impact_pct": 0, "error": "",
        }
        try:
            r = requests.get(JUPITER_QUOTE_URL, params={
                "inputMint": token_address,
                "outputMint": SOL_MINT,
                "amount": str(amount),
                "slippageBps": 500,
            }, timeout=10)
            if r.status_code == 200:
                q = r.json()
                if "error" not in q and q.get("outAmount"):
                    test["success"] = True
                    test["out_amount"] = int(q["outAmount"])
                    test["price_impact_pct"] = float(q.get("priceImpactPct", 0)) * 100
                    _jup_success()
                else:
                    test["error"] = q.get("error", "no outAmount")
                    _jup_success()  # API responded
            else:
                test["error"] = f"HTTP {r.status_code}"
                _jup_failure()
        except Exception as e:
            test["error"] = f"network: {str(e)[:60]}"
            network_failures += 1
            _jup_failure()
        result["tests"].append(test)
        time.sleep(0.3)

    # If all tests failed at the network layer → Jupiter unreachable; cannot determine
    if network_failures == len(test_pcts):
        result["ok"] = False
        result["message"] = "Jupiter unreachable — honeypot status UNKNOWN (not blocking)"
        return result

    # Any non-network failure (rejection) is a likely honeypot
    rejected = [t for t in result["tests"] if not t["success"] and not t["error"].startswith("network")]
    if rejected:
        pcts = [t["pct"] for t in rejected]
        result["message"] = f"🚨 HONEYPOT: sell rejected at {pcts}%"
        return result

    # Compare price impacts across successful tests
    impacts = [t["price_impact_pct"] for t in result["tests"] if t["success"]]
    if len(impacts) >= 2:
        diff = max(impacts) - min(impacts)
        result["max_impact_diff_pct"] = round(diff, 2)
        if diff > NEW_SAFETY_CONFIG["honeypot_impact_diff_max_pct"]:
            result["message"] = f"🚨 Suspicious impact spread {diff:.1f}% between sell sizes — possible partial honeypot"
            return result

    result["passed"] = True
    result["message"] = "✅ Sells OK at 10/50/100% with consistent impact"
    return result


# ─── LAYER 6: DEPLOYER HISTORY CHECK ───────────────────────

def check_deployer(token_address: str) -> dict:
    """Look up token deployer and count their recent deployments. Flag serial deployers."""
    cached = _cache_get("deployer", token_address)
    if cached is not None:
        return cached

    result = {
        "layer": "deployer_history",
        "ok": True,
        "warning": False,
        "deployer": None,
        "recent_deployments": 0,
        "message": "",
    }
    data = _rugcheck_report(token_address)
    if not data:
        result["ok"] = False
        result["message"] = "RugCheck unavailable"
        _cache_set("deployer", token_address, result)
        return result

    # RugCheck schema varies; try multiple keys
    deployer = (
        data.get("creator")
        or data.get("deployer")
        or (data.get("tokenMeta") or {}).get("updateAuthority")
        or (data.get("token") or {}).get("updateAuthority")
    )
    result["deployer"] = deployer

    # Recent deployments list (if RugCheck returns it)
    history = data.get("creatorTokens") or data.get("deployerTokens") or []
    if isinstance(history, list):
        count = len(history)
    else:
        count = 0
    result["recent_deployments"] = count

    threshold = NEW_SAFETY_CONFIG["deployer_max_recent_tokens"]
    if not deployer:
        result["message"] = "Deployer unknown"
    elif count > threshold:
        result["warning"] = True
        result["message"] = f"⚠️ Serial deployer: {count} tokens by {deployer[:20]}..."
    else:
        result["message"] = f"OK: deployer has {count} known tokens"

    _cache_set("deployer", token_address, result)
    return result


# ─── UPGRADED PRE-BUY GATE ─────────────────────────────────

def run_full_safety_check(token_address: str) -> dict:
    """Run legacy SafetyReport + all new layers. Returns detailed dict report."""
    print(f"  🛡️ Running FULL safety check on {token_address[:12]}...")

    legacy = run_safety_check(token_address)
    legacy_dict = {
        "passed": legacy.passed,
        "score": legacy.score,
        "honeypot_safe": legacy.honeypot_safe,
        "mint_revoked": legacy.mint_revoked,
        "freeze_revoked": legacy.freeze_revoked,
        "lp_burned_pct": legacy.lp_burned_pct,
        "top10_holder_pct": legacy.top10_holder_pct,
        "rugcheck_score": legacy.rugcheck_score,
        "rugcheck_risks": legacy.rugcheck_risks,
        "warnings": legacy.warnings,
        "errors": legacy.errors,
        "summary": legacy.summary(),
    }

    print(f"     🔍 Layer 4: LP dead-address verification...")
    lp = verify_lp_dead_address(token_address)
    time.sleep(0.3)
    print(f"     🔍 Layer 5: multi-amount honeypot test...")
    hp = honeypot_check_multi_amount(token_address)
    time.sleep(0.3)
    print(f"     🔍 Layer 6: deployer history...")
    dp = check_deployer(token_address)
    time.sleep(0.3)
    print(f"     🔍 Recording holder baseline...")
    hb = check_holder_changes(token_address)

    report = {
        "token_address": token_address,
        "timestamp": time.time(),
        "layers": {
            "legacy_safety": legacy_dict,
            "lp_verification": lp,
            "honeypot_multi_amount": hp,
            "deployer_history": dp,
            "holders_baseline": hb,
        },
        "holder_snapshot": hb.get("snapshot", []),
        "passed": True,
        "score": legacy.score,
        "blocking_reasons": [],
    }

    # Aggregate pass/fail
    if not legacy.passed:
        report["passed"] = False
        report["blocking_reasons"].append("legacy_safety_failed")

    # LP: not strictly blocking but penalize unsafe LP
    if lp.get("ok") and not lp.get("lp_safe"):
        report["score"] = max(0, report["score"] - 10)
        # Don't block (many legit tokens use locks, not burns), just penalize

    # Honeypot multi-amount: block only if we got a real rejection
    if hp.get("ok") and not hp.get("passed") and hp.get("tests"):
        # Either a rejection or excessive impact diff was the cause
        rejected = [t for t in hp["tests"] if not t["success"] and not (t.get("error", "").startswith("network"))]
        if rejected or hp.get("max_impact_diff_pct", 0) > NEW_SAFETY_CONFIG["honeypot_impact_diff_max_pct"]:
            report["passed"] = False
            report["blocking_reasons"].append("multi_amount_honeypot")

    # Deployer: warning only
    if dp.get("warning"):
        report["score"] = max(0, report["score"] - 5)

    return report


def summarize_full_report(report: dict) -> str:
    """Human-readable summary of run_full_safety_check output."""
    lines = []
    lines.append(report["layers"]["legacy_safety"].get("summary", ""))
    lines.append(f"     {report['layers']['lp_verification'].get('message', '')}")
    lines.append(f"     {report['layers']['honeypot_multi_amount'].get('message', '')}")
    lines.append(f"     {report['layers']['deployer_history'].get('message', '')}")
    lines.append(f"     {report['layers']['holders_baseline'].get('message', '')}")
    verdict = "✅ PASSED" if report["passed"] else "❌ FAILED"
    lines.append(f"  {verdict} (score {report['score']}/100)")
    if report["blocking_reasons"]:
        lines.append(f"     🚫 Blocking: {', '.join(report['blocking_reasons'])}")
    return "\n".join(lines)


# ─── CONTINUOUS MONITORING DISPATCHER ──────────────────────

def run_continuous_safety_checks(token_address: str, baseline_liquidity_usd: float,
                                  prev_holder_snapshot: Optional[list] = None,
                                  do_mint_recheck: bool = False,
                                  rpc_url: str = DEFAULT_RPC_URL) -> dict:
    """Runs the per-cycle safety checks for an open position.
    Returns dict with `emergency_sell`, `warning`, individual layer results,
    and `new_holder_snapshot` for caller to persist.
    Caller decides how often to set do_mint_recheck=True (≥ every 5 min)."""
    out = {
        "emergency_sell": False,
        "emergency_reasons": [],
        "warnings": [],
        "liquidity": {},
        "holders": {},
        "mint": None,
        "new_holder_snapshot": prev_holder_snapshot or [],
    }

    liq = monitor_liquidity(token_address, baseline_liquidity_usd)
    out["liquidity"] = liq
    if liq.get("emergency_sell"):
        out["emergency_sell"] = True
        out["emergency_reasons"].append(liq["message"])
    elif liq.get("warning"):
        out["warnings"].append(liq["message"])

    holders = check_holder_changes(token_address, prev_holder_snapshot)
    out["holders"] = holders
    if holders.get("snapshot"):
        out["new_holder_snapshot"] = holders["snapshot"]
    if holders.get("warning"):
        out["warnings"].append(holders["message"])

    if do_mint_recheck:
        mint = recheck_mint_authority(token_address, rpc_url)
        out["mint"] = mint
        if mint.get("emergency_sell"):
            out["emergency_sell"] = True
            out["emergency_reasons"].append(mint["message"])

    return out


# ─── AUTO-SWEEP ──────────────────────────────────────────────

def check_and_sweep(wallet_pubkey: str, wallet_keypair, rpc_url: str) -> Optional[str]:
    """
    Check if balance exceeds threshold and sweep excess to cold wallet.
    Returns transaction signature if sweep happened, None otherwise.
    """
    if not SAFETY_CONFIG["auto_sweep_enabled"]:
        return None

    cold_wallet = SAFETY_CONFIG["cold_wallet_address"]
    if not cold_wallet:
        return None

    threshold = SAFETY_CONFIG["auto_sweep_threshold_sol"]

    try:
        # Get balance
        resp = requests.post(rpc_url, json={
            "jsonrpc": "2.0", "id": 1,
            "method": "getBalance",
            "params": [wallet_pubkey]
        }, timeout=10).json()

        balance_sol = resp.get("result", {}).get("value", 0) / 1_000_000_000

        if balance_sol <= threshold:
            return None

        sweep_amount = balance_sol - threshold
        # Keep 0.005 SOL for future operations
        sweep_amount = sweep_amount - 0.005
        if sweep_amount <= 0.001:
            return None

        print(f"\n  💸 AUTO-SWEEP: {sweep_amount:.4f} SOL → cold wallet")
        print(f"     Balance: {balance_sol:.4f} SOL | Threshold: {threshold} SOL")
        print(f"     Cold wallet: {cold_wallet[:12]}...")

        # Build and send transfer
        from solders.system_program import TransferParams, transfer
        from solders.transaction import Transaction
        from solders.pubkey import Pubkey
        from solders.hash import Hash

        bh_resp = requests.post(rpc_url, json={
            "jsonrpc": "2.0", "id": 1,
            "method": "getLatestBlockhash",
            "params": [{"commitment": "finalized"}]
        }, timeout=10).json()

        blockhash = Hash.from_string(bh_resp["result"]["value"]["blockhash"])

        ix = transfer(TransferParams(
            from_pubkey=wallet_keypair.pubkey(),
            to_pubkey=Pubkey.from_string(cold_wallet),
            lamports=int(sweep_amount * 1_000_000_000),
        ))

        tx = Transaction.new_signed_with_payer(
            [ix], wallet_keypair.pubkey(), [wallet_keypair], blockhash
        )

        import base64
        tx_b64 = base64.b64encode(bytes(tx)).decode('utf-8')

        send_resp = requests.post(rpc_url, json={
            "jsonrpc": "2.0", "id": 1,
            "method": "sendTransaction",
            "params": [tx_b64, {"encoding": "base64", "skipPreflight": True}]
        }, timeout=30).json()

        if "result" in send_resp:
            sig = send_resp["result"]
            print(f"     ✅ Swept {sweep_amount:.4f} SOL | TX: {sig}")
            return sig
        else:
            print(f"     ❌ Sweep failed: {send_resp.get('error', 'unknown')}")
            return None

    except ImportError:
        print(f"     ⚠️  solders not installed, cannot sweep")
        return None
    except Exception as e:
        print(f"     ❌ Sweep error: {str(e)[:80]}")
        return None


# ─── CLI TEST ────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python safety_module.py <TOKEN_ADDRESS>")
        print("Example: python safety_module.py 7EV9ShfBB5NGtGcFVBQPVUVVtW3D6Emr3dPmh8rBpump")
        sys.exit(1)

    token = sys.argv[1]
    use_full = "--full" in sys.argv

    print(f"\n🛡️ Safety check for: {token}\n")

    if use_full:
        report = run_full_safety_check(token)
        print(summarize_full_report(report))
        print()
        if report["passed"]:
            print("  ✅ Token PASSED full safety checks — OK to trade")
        else:
            print(f"  ❌ Token FAILED — blocking: {report['blocking_reasons']}")
    else:
        report = run_safety_check(token)
        print(report.summary())
        print()
        if report.passed:
            print("  ✅ Token PASSED safety checks — OK to trade")
        else:
            print("  ❌ Token FAILED safety checks — DO NOT BUY")

    print()
