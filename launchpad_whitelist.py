#!/usr/bin/env python3
"""
🚀 LAUNCHPAD WHITELIST
=======================
Known-safer launchpad deployer addresses. Tokens minted by these deployers
get a small score boost during safety scoring, on the theory that an
established launchpad has at least *some* abuse controls vs. a random EOA
spinning up a new mint.

This is a soft signal — being launchpad-deployed does NOT bypass any other
safety check (honeypot, LP burn, holder concentration, serial-rug history
still apply). It only nudges borderline tokens up.

## Adding entries

Drop entries into `WHITELISTED_LAUNCHPADS` as:
    "<base58 address>": "<human label>"

Verify each address from at least two independent sources before adding
(launchpad docs, on-chain explorer, or the platform's own announcements).
Wrong entries here will *upgrade* unsafe tokens — worse than missing entries.
"""

# Known launchpad / platform deployer addresses on Solana.
# TODO: verify and expand. Each entry should be cross-checked on Solscan
# or a similar explorer before being trusted in production scoring.
WHITELISTED_LAUNCHPADS: dict[str, str] = {
    # Pump.fun — primary mint authority used for tokens launched on pump.fun.
    # User-provided; verify on https://solscan.io/account/<address> before relying.
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P": "Pump.fun",

    # TODO: add Moonshot deployer address(es) once verified
    # TODO: add LetsBonk / Bonkfun deployer if/when launched
    # TODO: add Raydium LaunchLab deployer once verified
}


def is_whitelisted_launchpad(deployer_address: str) -> bool:
    """Return True if the given deployer is in the launchpad whitelist.

    Comparison is CASE-SENSITIVE — Solana addresses are base58.
    Returns False for empty input or unknown deployers.
    """
    if not deployer_address:
        return False
    return deployer_address in WHITELISTED_LAUNCHPADS


def launchpad_label(deployer_address: str) -> str:
    """Return the human label for a whitelisted deployer, or empty string."""
    if not deployer_address:
        return ""
    return WHITELISTED_LAUNCHPADS.get(deployer_address, "")
