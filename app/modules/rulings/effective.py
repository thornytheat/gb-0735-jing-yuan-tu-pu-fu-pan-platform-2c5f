from app.modules.clusters.components import components
from app.modules.retest.arbitrate import cell_key
from app.modules.yield_audit.compare import compare


def apply_rulings(snapshot, rulings, spec):
    """把采信结论叠加到归档裁决上，按同一份证据重算缺陷簇与勾稽。

    归档快照本身不被修改：冲突测次链、隔离账与原始行保持原样，
    只有被采信芯粒的最终状态改由结论给出。
    """
    if not rulings:
        return snapshot
    decisions = {wafer: dict(cells) for wafer, cells in snapshot["decisions"].items()}
    touched = set()
    for ruling in rulings:
        wafer = ruling["wafer"]
        key = cell_key(ruling["x"], ruling["y"])
        cell = dict(decisions[wafer][key])
        cell["result"] = ruling["result"]
        cell["bin"] = ruling["bin"]
        cell["attempt"] = ruling["attempt"]
        cell["ruling"] = {"row_id": ruling["row_id"], "reviewer": ruling["reviewer"],
                          "note": ruling["note"], "created_at": ruling["created_at"],
                          "overruled": "CONFLICT"}
        decisions[wafer][key] = cell
        touched.add(wafer)
    clusters = dict(snapshot["clusters"])
    for wafer in touched:
        clusters[wafer] = components(decisions[wafer])
    audit = compare(spec, snapshot["masks"], decisions, snapshot["rows"], snapshot["files"])
    return {**snapshot, "decisions": decisions, "clusters": clusters, "audit": audit}
