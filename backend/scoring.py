"""
Intent scoring engine.

Score is 0-100. Formula:

    score = clamp(
        page_weight
      + duration_bonus
      + repeat_bonus
      + tier_bonus,
        0, 100
    )

Weights are exposed as module constants so you can tune them yourself.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

# ---------------------------------------------------------------------------
# TUNEABLE WEIGHTS - edit these to change how "intent" is scored.
# ---------------------------------------------------------------------------

# Base score by page keyword. First matching keyword wins.
# High-intent pages (pricing, demo requests) score much higher than blog posts.
PAGE_WEIGHTS: list[tuple[str, int]] = [
    ("/pricing",       55),
    ("/demo",          65),
    ("/contact-sales", 60),
    ("/enterprise",    50),
    ("/integrations",  45),
    ("/case-studies",  35),
    ("/docs",          25),
    ("/product",       30),
    ("/features",      30),
    ("/blog",          10),
    ("/",              15),   # generic landing
]
DEFAULT_PAGE_WEIGHT = 10

# Session duration: +1 point per 15 seconds, capped.
DURATION_POINTS_PER_15S = 1
DURATION_MAX_POINTS = 20

# A repeat visit within N hours by the same company adds bonus points.
REPEAT_WINDOW_HOURS = 48
REPEAT_BONUS = 15

# Target-account tier bonus (A tier gets a lift, C tier a small penalty)
TIER_BONUS = {"A": 10, "B": 5, "C": 0}

# Cap all scores between these two values.
SCORE_MIN = 0
SCORE_MAX = 100

# ---------------------------------------------------------------------------


def score_page(page: str) -> int:
    """Return the base page-type score."""
    if not page:
        return DEFAULT_PAGE_WEIGHT
    p = page.lower()
    for keyword, weight in PAGE_WEIGHTS:
        if keyword in p:
            return weight
    return DEFAULT_PAGE_WEIGHT


def score_duration(duration_sec: int) -> int:
    if duration_sec <= 0:
        return 0
    pts = (duration_sec // 15) * DURATION_POINTS_PER_15S
    return min(pts, DURATION_MAX_POINTS)


def score_repeat(now: datetime, prior_timestamps: Iterable[datetime]) -> int:
    """+REPEAT_BONUS if the same company visited within REPEAT_WINDOW_HOURS."""
    cutoff = now - timedelta(hours=REPEAT_WINDOW_HOURS)
    for ts in prior_timestamps:
        # Compare in a tz-safe way.
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts >= cutoff and ts < now:
            return REPEAT_BONUS
    return 0


def score_tier(tier: Optional[str]) -> int:
    if not tier:
        return 0
    return TIER_BONUS.get(tier.upper(), 0)


def compute_intent_score(
    page: str,
    duration_sec: int,
    timestamp: datetime,
    prior_timestamps: Iterable[datetime],
    tier: Optional[str] = None,
) -> int:
    """Return the final 0-100 intent score."""
    total = (
        score_page(page)
        + score_duration(duration_sec)
        + score_repeat(timestamp, prior_timestamps)
        + score_tier(tier)
    )
    return max(SCORE_MIN, min(SCORE_MAX, int(total)))
