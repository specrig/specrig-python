import httpx
from sqlalchemy import update

from .db import Order, Session
from .settings import settings


def settle_payments() -> int:
    with httpx.Client(base_url=settings.billing_url, timeout=settings.billing_timeout) as billing:
        paid = billing.get("/invoices", params={"status": "paid"})
        paid.raise_for_status()
    orders = [invoice["order"] for invoice in paid.json()]
    with Session.begin() as session:
        session.execute(update(Order).where(Order.id.in_(orders)).values(paid=True))
    return len(orders)
