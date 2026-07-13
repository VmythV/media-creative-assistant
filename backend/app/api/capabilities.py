from fastapi import APIRouter, Request

router = APIRouter(tags=["capabilities"])


@router.get("/capabilities")
def get_capabilities(request: Request) -> dict:
    return request.app.state.capabilities
