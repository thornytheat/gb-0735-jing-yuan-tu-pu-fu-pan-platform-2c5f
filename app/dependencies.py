from typing import Annotated

from fastapi import Depends, Request

from app.errors import ReviewError
from app.modules.intake.catalog import restore_package
from app.modules.rulings.effective import apply_rulings


def run_service(request: Request):
    return request.app.state.runs


def run_archive(run_id: str, request: Request):
    return request.app.state.runs.store.get(run_id)


def review_archive(run_id: str, request: Request):
    """归档 + 采信结论叠加后的有效视图，供复判各页读取。"""
    store = request.app.state.runs.store
    manifest, snapshot, pinned = store.get(run_id)
    rulings = store.rulings(run_id)
    if rulings:
        if "decisions" not in snapshot:
            raise ReviewError("SNAPSHOT_DAMAGED", "归档摘要不符", 409)
        snapshot = apply_rulings(snapshot, rulings, restore_package(pinned).spec)
    return manifest, snapshot, pinned, rulings


Service = Annotated[object, Depends(run_service)]
Archive = Annotated[tuple, Depends(run_archive)]
Review = Annotated[tuple, Depends(review_archive)]


def for_wafer(mapping, wafer):
    if wafer not in mapping:
        raise ReviewError("WAFER_NOT_FOUND", "此批次无对应晶圆", 404)
    return mapping[wafer]
