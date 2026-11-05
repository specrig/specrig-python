import requests
from django.conf import settings


class Billing:
    def __init__(self) -> None:
        self._session = requests.Session()
        self._url = settings.BILLING_URL.rstrip("/")

    def unpaid(self) -> list[dict]:
        answer = self._session.get(f"{self._url}/invoices", params={"status": "unpaid"}, timeout=5)
        answer.raise_for_status()
        return answer.json()

    def remind(self, order: int, amount: int) -> None:
        answer = self._session.post(f"{self._url}/reminders", json={"order": order, "amount": amount}, timeout=5)
        answer.raise_for_status()
