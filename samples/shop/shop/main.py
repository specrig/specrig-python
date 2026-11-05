from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import OperationalError

from .billing import Billing
from .db import Order, Session
from .settings import settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.billing = Billing(settings.billing_url, settings.billing_timeout)
    yield
    await app.state.billing.close()


app = FastAPI(lifespan=lifespan)


def problem(status: int, type: str, title: str) -> JSONResponse:
    return JSONResponse({"type": type, "title": title}, status, media_type="application/problem+json")


@app.exception_handler(OperationalError)
async def database_down(request: Request, error: OperationalError) -> JSONResponse:
    return problem(503, "database_unavailable", "The shop cannot reach its orders; come back later")


@app.exception_handler(httpx.TransportError)
async def billing_down(request: Request, error: httpx.TransportError) -> JSONResponse:
    return problem(503, "billing_unavailable", "The shop cannot reach billing; come back later")


class Placing(BaseModel):
    id: int
    amount: int = Field(gt=0)


@app.get("/api/orders/{order_id}")
async def open_order(order_id: int, request: Request) -> JSONResponse:
    with Session() as session:
        order = session.get(Order, order_id)
    if order is None:
        return problem(404, "order_not_found", f"The shop has no order {order_id}")
    if order.paid:
        return JSONResponse({"id": order.id, "toPay": 0})
    invoice = await request.app.state.billing.invoice(order.id)
    return JSONResponse({"id": order.id, "toPay": 0 if invoice is None else invoice["amount"]})


@app.post("/api/orders", status_code=201)
async def place_order(placing: Placing, request: Request, background: BackgroundTasks) -> dict:
    with Session.begin() as session:
        session.add(Order(id=placing.id, amount=placing.amount, paid=False))
    background.add_task(request.app.state.billing.ask_to_invoice, placing.id, placing.amount)
    return {"id": placing.id}
