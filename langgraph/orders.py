"""A stand-in for the system that actually knows what was ordered.

In a real deployment this module is a thin client over Shopify, NetSuite,
Cin7, or whatever holds purchase orders — the agent does not own this data and
must not become a second copy of it. It is a dict here so the runbook runs with
no external service.

The only thing worth copying from this file is the shape of the boundary: the
agent asks "what did we order on PO-1042?" and gets facts back. It never asks
the model, and it never caches the answer in e2a.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

POStatus = Literal["open", "confirmed", "escalated", "cancelled"]


@dataclass
class PurchaseOrder:
    """One open commitment with one supplier.

    `quantity`, `unit_price_usd` and `need_by` are what the agent measures the
    supplier's reply against. Everything the agent decides is a comparison
    against these three numbers, done in Python — never by the model.
    """

    po_number: str
    supplier_email: str
    supplier_name: str
    sku: str
    description: str
    quantity: int
    unit_price_usd: float
    need_by: date
    status: POStatus = "open"
    # Free-text notes a human left. Shown to the model as context, never as
    # instructions — see the fencing in chase.py.
    notes: str = ""


# Seed data. Synthetic addresses only: `.example` and `example.com` are
# reserved and cannot receive mail, so a misconfigured run cannot email a real
# supplier.
_ORDERS: dict[str, PurchaseOrder] = {
    po.po_number: po
    for po in [
        PurchaseOrder(
            po_number="PO-1042",
            supplier_email="sales@northwind-textiles.example",
            supplier_name="Northwind Textiles",
            sku="TEE-ORG-BLK-M",
            description="Organic cotton tee, black, size M",
            quantity=500,
            unit_price_usd=6.40,
            need_by=date(2026, 9, 12),
            notes="Holiday drop. Marketing has committed to a 20 Sep launch.",
        ),
        PurchaseOrder(
            po_number="PO-1043",
            supplier_email="orders@harbor-packaging.example",
            supplier_name="Harbor Packaging",
            sku="BOX-MAIL-M",
            description="Recycled mailer box, medium",
            quantity=2000,
            unit_price_usd=0.31,
            need_by=date(2026, 8, 29),
        ),
    ]
}


def get(po_number: str) -> PurchaseOrder | None:
    return _ORDERS.get(po_number)


def open_orders() -> list[PurchaseOrder]:
    return [po for po in _ORDERS.values() if po.status == "open"]


def set_status(po_number: str, status: POStatus) -> None:
    """Write the outcome back to the system of record.

    A no-op against a dict here. In a real integration this is the call that
    must be idempotent — the webhook that triggers it is delivered at least
    once.
    """
    if po_number in _ORDERS:
        _ORDERS[po_number].status = status


# ── The join ────────────────────────────────────────────────────────────────
# The PO number IS the conversation id. Every message about PO-1042 — the
# opening ask, each chase, every supplier reply — carries
# `conversation_id="po-PO-1042"`, assigned by this app on the first send.
#
# This is why there is no mapping table anywhere in this runbook. An inbound
# email arrives, and `po_number_for(email.conversation_id)` is the whole
# lookup. Let e2a assign the conversation id instead and you would need to
# store the one it picked, which is a row that can drift from the mailbox.

CONVERSATION_PREFIX = "po-"


def conversation_id_for(po_number: str) -> str:
    return f"{CONVERSATION_PREFIX}{po_number}"


def po_number_for(conversation_id: str) -> str | None:
    """Reverse the join. Returns None for a conversation this app did not open
    — an unsolicited email to the agent's address, most often."""
    if not conversation_id.startswith(CONVERSATION_PREFIX):
        return None
    return conversation_id[len(CONVERSATION_PREFIX) :]
