from fastapi import FastAPI

from .routes import orders

app = FastAPI()
app.include_router(orders.router, prefix="/api")


async def health() -> dict:
    return {}


app.add_api_route("/health", health)


@app.get("/files/{path:path}")
async def file(path: str) -> dict:
    return {"path": path}
