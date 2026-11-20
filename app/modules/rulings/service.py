from datetime import datetime, timezone

from app.errors import ReviewError
from app.modules.retest.arbitrate import cell_key


def basis(snapshot, wafer, x, y, row_id):
    """校验采信依据：芯粒处于同次冲突，且结论指向该次冲突的原始行。"""
    decisions = snapshot["decisions"]
    if wafer not in decisions:
        raise ReviewError("WAFER_NOT_FOUND", "此批次无对应晶圆", 404)
    cell = decisions[wafer].get(cell_key(x, y))
    if cell is None:
        raise ReviewError("CELL_NOT_FOUND", "此位置不是有效芯粒", 404)
    if cell["result"] != "CONFLICT":
        raise ReviewError("NOT_IN_CONFLICT", "此芯粒不在冲突中，无需采信", 409)
    if row_id not in cell["evidence"]:
        raise ReviewError("RULING_WITHOUT_BASIS", "采信必须指向同次冲突的原始行，争议保持", 422)
    row = next((item for item in snapshot["rows"] if item["id"] == row_id), None)
    measurement = row.get("measurement") if row else None
    if measurement is None or measurement["attempt"] != cell["attempt"]:
        raise ReviewError("RULING_WITHOUT_BASIS", "采信必须指向同次冲突的原始行，争议保持", 422)
    return measurement


def publish(store, run_id, request):
    _, snapshot, _ = store.get(run_id)
    prior = store.find_ruling(run_id, request.wafer, request.x, request.y)
    if prior is not None:
        if prior["row_id"] == request.row_id:
            return {"ruling": prior, "reused": True}
        raise ReviewError("RULING_EXISTS", "此芯粒已有采信结论，结论不可改判", 409)
    measurement = basis(snapshot, request.wafer, request.x, request.y, request.row_id)
    ruling = {"wafer": request.wafer, "x": request.x, "y": request.y, "row_id": request.row_id,
              "attempt": measurement["attempt"], "result": measurement["result"],
              "bin": measurement["bin"], "reviewer": request.reviewer, "note": request.note,
              "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    recorded, created = store.put_ruling(run_id, ruling)
    if not created:
        if recorded["row_id"] == ruling["row_id"]:
            return {"ruling": recorded, "reused": True}
        raise ReviewError("RULING_EXISTS", "此芯粒已有采信结论，结论不可改判", 409)
    return {"ruling": recorded, "reused": False}
