
from __future__ import annotations

import json
from dataclasses import dataclass

from shared.billing import CostSession
from shared.kb import load_article
from shared.llm import ChatMessage, LlmClient
from shared.tools import format_thread, get_order, get_ticket

from question1.agent import parse_resolution


@dataclass
class TicketResolution:
    action: str
    kb_article: str


class SupportPipeline:
    """Four-stage support pipeline with lightweight local processing."""

    def __init__(self, session: CostSession | None = None):
        self.session = session if session is not None else CostSession()
        self.llm = LlmClient(self.session)

    def handle_ticket(self, ticket_id: str) -> TicketResolution:
        ticket = get_ticket(ticket_id)
        order = get_order(ticket["order_id"])

        thread = format_thread(ticket)
        snapshot = json.dumps(order, separators=(",", ":"))

        # Stage 1: Triage
        triage = self._triage(thread, order)

        # Stage 2: Retrieve
        article_id, article_text = self._retrieve(order)

        # Stage 3: Compose
        draft = self._compose(
            ticket_id, thread, snapshot, triage, article_id, article_text
        )

        # Stage 4: Audit
        return self._audit(draft, article_id)

    def _triage(self, thread: str, order: dict) -> str:
        facts = {
            "customer_message": thread,
            "product_type": order.get("product_type"),
            "customized": order.get("customized", False),
            "delivered_days_ago": order.get("delivered_days_ago"),
            "opened": order.get("opened", False),
            "license_revealed": order.get("license_revealed", False),
            "damage_reported": order.get("damage_reported", False),
            "subscription_age_days": order.get("subscription_age_days"),
        }
        return json.dumps(facts, separators=(",", ":"))

    def _retrieve(self, order: dict) -> tuple[str, str]:
        product_type = order.get("product_type")
        delivered_days = int(order.get("delivered_days_ago", 99))

        if product_type == "subscription":
            article_id = "subscription-cooling-off"

        elif order.get("license_revealed"):
            article_id = "digital-license-no-refund"

        elif order.get("customized") and order.get("damage_reported"):
            article_id = "custom-items-no-replace"

        elif (
            order.get("damage_reported")
            and not order.get("customized")
            and delivered_days <= 7
        ):
            article_id = "damage-replacement-7-days"

        elif (
            product_type == "physical"
            and not order.get("opened")
            and delivered_days <= 30
        ):
            article_id = "returns-within-30-days"

        else:
            article_id = "returns-within-30-days"

        article_text = load_article(article_id)
        return article_id, article_text

    def _compose(
        self,
        ticket_id: str,
        thread: str,
        snapshot: str,
        triage: str,
        article_id: str,
        article_text: str,
    ) -> str:
        messages = [
            ChatMessage(
                "system",
                "You are ShopAssist. Determine the correct support action "
                "from the order facts and selected policy. "
                "Return JSON with action, kb_article, and customer_reply.",
            ),
            ChatMessage(
                "user",
                f"TICKET: {ticket_id}\n"
                f"THREAD: {thread}\n"
                f"TRIAGE: {triage}\n"
                f"SELECTED_ARTICLE_ID: {article_id}\n"
                f"POLICY: {article_text}\n"
                f"ORDER_SNAPSHOT: {snapshot}\n"
                "END_SNAPSHOT",
            ),
        ]
        return self.llm.chat("compose", messages)

    def _audit(self, draft: str, article_id: str) -> TicketResolution:
        parsed = parse_resolution(draft)
        allowed_actions = {
            "REFUND",
            "REPLACE",
            "SEND_ARTICLE",
            "ESCALATE_TIER2",
        }
        if parsed.action not in allowed_actions:
            raise ValueError(f"Invalid support action: {parsed.action}")
        if parsed.kb_article != article_id:
            raise ValueError(
                f"Article mismatch: expected {article_id}, "
                f"received {parsed.kb_article}"
            )
        return TicketResolution(
            action=parsed.action,
            kb_article=parsed.kb_article,
        )
