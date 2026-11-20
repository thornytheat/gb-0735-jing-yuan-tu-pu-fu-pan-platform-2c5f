from fastapi import APIRouter

from app.dependencies import Review
from app.modules.mask.view import grid, inspect

router = APIRouter(prefix="/api/runs/{run_id}/mask")


@router.get("/{wafer}")
def mask(archive: Review, wafer: str):
    return grid(archive[1], wafer)


@router.get("/{wafer}/cell")
def cell(archive: Review, wafer: str, x: int, y: int):
    return inspect(archive[1], wafer, x, y)
