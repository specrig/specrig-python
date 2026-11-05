from datetime import date

from django.conf import settings
from django.core.cache import cache

from .billing import Billing
from .models import Reminder

DAY = 24 * 60 * 60


def send_reminders() -> int:
    billing = Billing()
    today = date.today()
    sent = 0
    for invoice in billing.unpaid():
        if date.fromisoformat(invoice["dueOn"]) >= today or cache.get(f"reminded:{invoice['order']}"):
            continue
        billing.remind(invoice["order"], invoice["amount"])
        Reminder.objects.create(order=invoice["order"], amount=invoice["amount"], sent_on=today)
        cache.set(f"reminded:{invoice['order']}", True, timeout=DAY)
        sent += 1
        if sent >= settings.REMINDERS_LIMIT:
            break
    return sent
