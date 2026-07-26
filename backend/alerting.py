"""
Alerting module.

The real integrations are stubbed. Both functions log what they *would* send
and return True. Wire real webhook URLs / HubSpot tokens where noted.
"""
from __future__ import annotations

import os
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)


def send_to_slack(payload: Dict[str, Any]) -> bool:
    """
    STUB. Replace with a real Slack Incoming Webhook POST when you're ready:

        webhook_url = os.environ["SLACK_WEBHOOK_URL"]
        requests.post(webhook_url, json={"text": ...}, timeout=5)

    Payload format is up to you; here we log a nicely-formatted message.
    """
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    text = (
        f":rotating_light: *High-intent visit* - {payload.get('company')} "
        f"(score {payload.get('intent_score')}, owner {payload.get('crm_owner')})\n"
        f"> {payload.get('summary')}"
    )
    if webhook_url:
        # NOTE: uncomment when you're actually wiring this up.
        # requests.post(webhook_url, json={"text": text}, timeout=5)
        logger.info("[slack] would POST to %s: %s", webhook_url, text)
    else:
        logger.info("[slack:stub] %s", text)
    return True


def update_hubspot_property(payload: Dict[str, Any]) -> bool:
    """
    STUB. Replace with a real HubSpot API call to update a company property,
    e.g. bumping `dark_funnel_intent_score` on the matched company record.

        headers = {"Authorization": f"Bearer {os.environ['HUBSPOT_API_KEY']}"}
        requests.patch(
            f"https://api.hubapi.com/crm/v3/objects/companies/{hubspot_id}",
            headers=headers,
            json={"properties": {"dark_funnel_intent_score": score}},
            timeout=5,
        )
    """
    api_key = os.environ.get("HUBSPOT_API_KEY", "").strip()
    if api_key:
        logger.info(
            "[hubspot] would PATCH company '%s' -> intent_score=%s",
            payload.get("company"),
            payload.get("intent_score"),
        )
    else:
        logger.info(
            "[hubspot:stub] company=%s score=%s owner=%s",
            payload.get("company"),
            payload.get("intent_score"),
            payload.get("crm_owner"),
        )
    return True
