"""Domain matching between resolved visits and target accounts."""
from __future__ import annotations

from typing import Optional
from difflib import SequenceMatcher

from sqlalchemy.orm import Session

from models import TargetAccount


def _normalise_domain(domain: Optional[str]) -> str:
    if not domain:
        return ""
    d = domain.strip().lower()
    # strip protocol
    if "://" in d:
        d = d.split("://", 1)[1]
    # strip path
    d = d.split("/", 1)[0]
    # strip common subdomains
    for prefix in ("www.", "m.", "app.", "shop."):
        if d.startswith(prefix):
            d = d[len(prefix):]
    return d


def match_domain(db: Session, resolved_domain: Optional[str]) -> Optional[TargetAccount]:
    """Return the matching TargetAccount for a resolved visitor domain, or None."""
    norm = _normalise_domain(resolved_domain)
    if not norm:
        return None

    # 1. Exact match
    hit = db.query(TargetAccount).filter(TargetAccount.domain == norm).first()
    if hit:
        return hit

    # 2. Endswith match (visitor sub-brand or subdomain of a target)
    all_accounts = db.query(TargetAccount).all()
    for acc in all_accounts:
        if norm.endswith("." + acc.domain) or acc.domain.endswith("." + norm):
            return acc

    # 3. Fuzzy match, root domain (before TLD) similarity >= 0.9
    norm_root = norm.split(".")[0]
    best = None
    best_ratio = 0.0
    for acc in all_accounts:
        acc_root = acc.domain.split(".")[0]
        ratio = SequenceMatcher(None, norm_root, acc_root).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best = acc
    if best is not None and best_ratio >= 0.9:
        return best

    return None
