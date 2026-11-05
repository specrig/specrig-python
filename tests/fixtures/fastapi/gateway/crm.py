import httpx

CUSTOMERS = "/customers"


class Crm:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def customer(self, id: int) -> httpx.Response:
        return await self._client.get(f"{CUSTOMERS}/{id}")

    async def note(self, id: int, text: str) -> httpx.Response:
        return await self._send("POST", f"/customers/{id}/notes", text)

    async def _send(self, method: str, path: str, body: str) -> httpx.Response:
        return await self._client.request(method, path, json=body)
