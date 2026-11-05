from django.db import OperationalError
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET

from .models import Reminder


def problem(status: int, type: str, title: str) -> JsonResponse:
    return JsonResponse({"type": type, "title": title}, status=status, content_type="application/problem+json")


@require_GET
def last_reminder(request: HttpRequest, order: int) -> JsonResponse:
    try:
        last = Reminder.objects.filter(order=order).order_by("-sent_on", "-id").first()
    except OperationalError:
        return problem(503, "database_unavailable", "The reminders cannot reach their database; come back later")
    if last is None:
        return problem(404, "reminder_not_found", f"Order {order} was never reminded of")
    return JsonResponse({"order": last.order, "amount": last.amount, "sentOn": last.sent_on.isoformat()})
