"""
IP -> Company resolution.

Pluggable resolver:
  - If IPINFO_TOKEN env var is set, calls ipinfo.io free tier.
  - Otherwise, uses a deterministic mock resolver that maps fake IP ranges to
    the seeded target accounts, so the whole app works with zero API keys.

Every resolver returns:
    {
      "company":       str | None,
      "domain":        str | None,
      "industry":      str | None,
      "traffic_type":  "business" | "residential" | "hosting",
    }
"""
from __future__ import annotations

import os
import hashlib
import logging
from typing import Optional, Dict
from functools import lru_cache

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Deterministic mock mapping: /24 subnets -> target accounts.
# Ranges 10.x.x.x, 172.16-31.x.x, 192.168.x.x are RFC1918 (residential-like).
# 52.x.x.x, 34.x.x.x, 3.x.x.x are AWS/GCP (hosting).
# 74.x.x.x, 208.x.x.x are business-like leased ranges (Comcast Business etc.)
# ---------------------------------------------------------------------------

MOCK_COMPANY_SUBNETS: Dict[str, Dict[str, str]] = {
    # subnet_prefix (first three octets) -> resolved data
    "74.101.10":  {"company": "Acme Corp",           "domain": "acme.com",             "industry": "Manufacturing",       "traffic_type": "business"},
    "74.101.11":  {"company": "Globex Corp",         "domain": "globex.com",           "industry": "Conglomerate",        "traffic_type": "business"},
    "74.101.12":  {"company": "Initech",             "domain": "initech.com",          "industry": "Software",            "traffic_type": "business"},
    "74.101.13":  {"company": "Umbrella Corp",       "domain": "umbrellacorp.com",     "industry": "Biotech",             "traffic_type": "business"},
    "74.101.14":  {"company": "Wayne Enterprises",   "domain": "wayneenterprises.com", "industry": "Aerospace",           "traffic_type": "business"},
    "74.101.15":  {"company": "Stark Industries",    "domain": "starkindustries.com",  "industry": "Defense",             "traffic_type": "business"},
    "208.55.20":  {"company": "Cyberdyne Systems",   "domain": "cyberdyne.com",        "industry": "Robotics",            "traffic_type": "business"},
    "208.55.21":  {"company": "Wonka Industries",    "domain": "wonka.com",            "industry": "Consumer Goods",      "traffic_type": "business"},
    "208.55.22":  {"company": "Massive Dynamic",     "domain": "massivedynamic.com",   "industry": "R&D",                 "traffic_type": "business"},
    "208.55.23":  {"company": "Tyrell Corp",         "domain": "tyrell.com",           "industry": "Bio-Engineering",     "traffic_type": "business"},
    "208.55.24":  {"company": "Aperture Science",    "domain": "aperturescience.com",  "industry": "Research",            "traffic_type": "business"},
    "208.55.25":  {"company": "Pied Piper",          "domain": "piedpiper.com",        "industry": "Software",            "traffic_type": "business"},
    "208.55.26":  {"company": "Hooli",               "domain": "hooli.com",            "industry": "Internet",            "traffic_type": "business"},
    "208.55.27":  {"company": "Vandelay Industries", "domain": "vandelay.com",         "industry": "Import/Export",       "traffic_type": "business"},
    "208.55.28":  {"company": "Dunder Mifflin",      "domain": "dundermifflin.com",    "industry": "Paper",               "traffic_type": "business"},

    # A couple of hosting/CDN ranges (should not match target accounts)
    "52.200.10":  {"company": "Amazon AWS",          "domain": "amazonaws.com",        "industry": "Cloud",               "traffic_type": "hosting"},
    "34.120.5":   {"company": "Google Cloud",        "domain": "googleusercontent.com","industry": "Cloud",               "traffic_type": "hosting"},
}


def _mock_lookup(ip: str) -> dict:
    parts = ip.split(".")
    if len(parts) != 4:
        return {"company": None, "domain": None, "industry": None, "traffic_type": "residential"}
    prefix = ".".join(parts[:3])

    if prefix in MOCK_COMPANY_SUBNETS:
        return dict(MOCK_COMPANY_SUBNETS[prefix])

    # RFC1918 or 24./76./98. treated as residential (Comcast/Verizon consumer)
    first = int(parts[0]) if parts[0].isdigit() else 0
    if first in (10, 172, 192, 24, 76, 98):
        return {"company": None, "domain": None, "industry": None, "traffic_type": "residential"}

    # Everything else: unknown business
    # Use a deterministic hash so the same IP always resolves the same way.
    h = hashlib.md5(ip.encode()).hexdigest()
    return {
        "company": f"Unknown-{h[:6]}",
        "domain": f"unknown-{h[:6]}.example",
        "industry": "Unknown",
        "traffic_type": "business",
    }


def _ipinfo_lookup(ip: str, token: str) -> Optional[dict]:
    try:
        r = requests.get(f"https://ipinfo.io/{ip}", params={"token": token}, timeout=3)
        if r.status_code != 200:
            return None
        data = r.json()
        company = (data.get("company") or {}).get("name") or data.get("org")
        domain = (data.get("company") or {}).get("domain")
        industry = (data.get("company") or {}).get("type")
        # ipinfo's "type" for company is one of: business, education, government, isp, hosting
        traffic_type = "business"
        if data.get("hosting") or (industry and "hosting" in str(industry).lower()):
            traffic_type = "hosting"
        elif industry and industry.lower() in ("isp",):
            traffic_type = "residential"
        return {
            "company": company,
            "domain": domain,
            "industry": industry,
            "traffic_type": traffic_type,
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("ipinfo lookup failed for %s: %s", ip, e)
        return None


@lru_cache(maxsize=2048)
def resolve_ip(ip: str) -> dict:
    """Resolve an IP to company info. Cached in-process."""
    token = os.environ.get("IPINFO_TOKEN", "").strip()
    if token:
        result = _ipinfo_lookup(ip, token)
        if result and result.get("company"):
            return result
        # Fall through to mock if ipinfo has no data
    return _mock_lookup(ip)
