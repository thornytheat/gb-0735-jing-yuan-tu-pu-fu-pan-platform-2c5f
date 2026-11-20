from typing import Annotated

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field, StringConstraints

from app.dependencies import Service
from app.modules.intake.contract import TEXT
from app.modules.rulings.service import publish

router = APIRouter(prefix="/api/runs/{run_id}/rulings")

NAME = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30)]


class RulingRequest(BaseModel):
    wafer: TEXT
    x: int = Field(ge=-30, le=30)
    y: int = Field(ge=-30, le=30)
    row_id: str = Field(min_length=1, max_length=80)
    reviewer: NAME
    note: str = Field(default="", max_length=200)


@router.get("")
def rulings(run_id: str, service: Service):
    service.store.get(run_id)
    return {"rulings": service.store.rulings(run_id)}


@router.post("", status_code=201)
def publish_ruling(run_id: str, request: RulingRequest, service: Service, response: Response):
    result = publish(service.store, run_id, request)
    if result["reused"]:
        response.status_code = 200
    return result
