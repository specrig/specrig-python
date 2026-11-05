from fastapi import APIRouter

router = APIRouter(prefix="/orders")


@router.get("/{order_id}")
async def order(order_id: int) -> dict:
    return {"id": order_id}


@router.api_route("/{order_id}/notes", methods=["POST", "PUT"])
async def note(order_id: int) -> dict:
    return {"id": order_id}
