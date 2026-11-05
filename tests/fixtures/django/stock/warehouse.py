import requests
from django.conf import settings


class Warehouse:
    def __init__(self) -> None:
        self._session = requests.Session()
        self._url = settings.WAREHOUSE_URL

    def reserve(self, sku: str) -> None:
        self._session.post(f"{self._url}/reservations/{sku}", timeout=5).raise_for_status()
