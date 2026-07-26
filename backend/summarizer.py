"""
AI session summarizer.

Calls Anthropic Claude via the emergentintegrations library. If no LLM key is
available OR the API call fails, we return a deterministic template-based
summary so the app never breaks.
"""
from __future__ import annotations

import os
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# Model configured to match the problem statement.
CLAUDE_MODEL = "claude-sonnet-4-6"
PROVIDER = "anthropic"


def _template_summary(company: str, pages: List[str], total_duration_sec: int, visits: int) -> str:
    """Deterministic fallback used when no LLM key is set or the API errors out."""
    unique_pages = list(dict.fromkeys(pages))  # preserve order, dedupe
    minutes = round(total_duration_sec / 60, 1)
    pages_str = ", ".join(unique_pages[:3]) if unique_pages else "the site"
    times = "once" if visits <= 1 else f"{visits} times"
    return (
        f"{company}'s team spent about {minutes} min on {pages_str} "
        f"and visited {times} in the last 48h."
    )


async def summarize_session(
    company: str,
    pages: List[str],
    total_duration_sec: int,
    visits: int,
    industry: Optional[str] = None,
) -> str:
    """Return a one-sentence human-readable summary of the session."""
    api_key = os.environ.get("EMERGENT_LLM_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return _template_summary(company, pages, total_duration_sec, visits)

    try:
        # Import lazily so app still starts if lib isn't installed.
        from emergentintegrations.llm.chat import LlmChat, UserMessage

        chat = LlmChat(
            api_key=api_key,
            session_id=f"summary-{company}",
            system_message=(
                "You are a B2B sales-intel assistant. Write ONE concise "
                "sentence (max 25 words) summarising an anonymous website "
                "visitor session for a sales rep. No preamble."
            ),
        ).with_model(PROVIDER, CLAUDE_MODEL)

        prompt = (
            f"Company: {company}\n"
            f"Industry: {industry or 'unknown'}\n"
            f"Pages visited (in order): {pages}\n"
            f"Total time on site: {total_duration_sec} seconds\n"
            f"Sessions in last 48h: {visits}\n\n"
            "Write ONE sentence summarising the intent."
        )
        response = await chat.send_message(UserMessage(text=prompt))
        # emergentintegrations returns a string from send_message.
        text = (response or "").strip().split("\n")[0]
        return text or _template_summary(company, pages, total_duration_sec, visits)
    except Exception as e:  # noqa: BLE001
        logger.warning("Claude summary failed, using template. err=%s", e)
        return _template_summary(company, pages, total_duration_sec, visits)
