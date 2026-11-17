
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from shared.billing import CostSession
from shared.kb import load_article
from shared.llm import ChatMessage, LlmClient
from shared.tools import format_thread, get_order, get_ticket


@dataclass
class TicketResolution:
    action: str
    kb_article: str
    customer_reply: str
    raw_completion: str


def parse_resolution(text: str) -> TicketResolution:
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise ValueError(f"No JSON in model output:\n{text[:400]}")

    data = json.loads(m.group())

    return TicketResolution(
        action=str(data["action"]).upper(),
        kb_article=str(data["kb_article"]),
        customer_reply=str(data.get("customer_reply", "")),
        raw_completion=text,
    )


class SupportCopilot:
    """Cost-optimized ShopAssist Tier-1 copilot."""

    def __init__(self, session: CostSession | None = None):
        self.session = session if session is not None else CostSession()
        self.llm = LlmClient(self.session)

    def select_article(self, order: dict) -> str:
        ptype = order.get("product_type")

        if ptype == "subscription":
            return "subscription-cooling-off"

        if order.get("license_revealed"):
            return "digital-license-no-refund"

        if order.get("customized") and order.get("damage_reported"):
            return "custom-items-no-replace"

        if (
            order.get("damage_reported")
            and not order.get("customized")
            and int(order.get("delivered_days_ago", 99)) <= 7
        ):
            return "damage-replacement-7-days"

        if (
            ptype == "physical"
            and not order.get("opened")
            and int(order.get("delivered_days_ago", 99)) <= 30
        ):
            return "returns-within-30-days"

        return "returns-within-30-days"

    def build_messages(self, ticket_id: str) -> list[ChatMessage]:
        ticket = get_ticket(ticket_id)
        order = get_order(ticket["order_id"])

        thread = format_thread(ticket)
        snapshot = json.dumps(order, separators=(",", ":"))

        article_id = self.select_article(order)
        article_text = load_article(article_id)

        system = (
            "You are ShopAssist, a Tier-1 customer support copilot. "
            "Determine the correct support action from the order facts "
            "and selected policy. Return compact JSON with action, "
            "kb_article and customer_reply."
        )

        user = (
            f"TICKET: {ticket_id}\n"
            f"THREAD: {thread}\n"
            f"SELECTED_ARTICLE: {article_id}\n"
            f"POLICY: {article_text}\n"
            f"ORDER_SNAPSHOT: {snapshot}\nEND_SNAPSHOT"
        )

        return [
            ChatMessage("system", system),
            ChatMessage("user", user),
        ]

    def handle_ticket(self, ticket_id: str) -> TicketResolution:
        messages = self.build_messages(ticket_id)
        completion = self.llm.chat("handle_ticket", messages)
        return parse_resolution(completion)
