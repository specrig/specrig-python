import httpx


class Billing:
    def __init__(self, url: str, timeout: float) -> None:
        self._client = httpx.AsyncClient(base_url=url, timeout=timeout)

    async def invoice(self, order: int) -> dict | None:
        answer = await self._client.get(f"/invoices/{order}")
        if answer.status_code == 404:
            return None
        answer.raise_for_status()
        return answer.json()

    async def ask_to_invoice(self, order: int, amount: int) -> None:
        answer = await self._client.post("/invoices", json={"order": order, "amount": amount})
        answer.raise_for_status()

    async def close(self) -> None:
        await self._client.aclose()
