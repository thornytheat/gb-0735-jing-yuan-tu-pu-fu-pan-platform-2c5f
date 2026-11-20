from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from app.modules.rulings.service import publish

RULING = {"wafer": "W-07", "x": 3, "y": 3, "row_id": "probe-b.csv:3", "reviewer": "张工", "note": "夜班复核"}


def adopt(client, run_base, **overrides):
    return client.post(run_base + "/rulings", json={**RULING, **overrides})


def test_ruling_adopts_original_row_and_recalculates_shared_evidence(client, run_base):
    before = client.get(run_base + "/yield").json()
    assert before["summary"]["conflict"] == 2 and before["wafers"][0]["status"] == "MATCH"

    reply = adopt(client, run_base)
    assert reply.status_code == 201, reply.text
    assert reply.json()["reused"] is False
    ruling = reply.json()["ruling"]
    assert {key: ruling[key] for key in ("row_id", "attempt", "result", "bin", "reviewer", "note")} == {
        "row_id": "probe-b.csv:3", "attempt": 2, "result": "FAIL", "bin": 7,
        "reviewer": "张工", "note": "夜班复核"}

    # 结论可追溯到原始行，测次链保留冲突历史
    cell = client.get(run_base + "/mask/W-07/cell", params={"x": 3, "y": 3}).json()
    assert (cell["decision"]["result"], cell["decision"]["bin"]) == ("FAIL", 7)
    assert cell["decision"]["ruling"]["row_id"] == "probe-b.csv:3"
    assert [step["result"] for step in cell["decision"]["history"]] == ["PASS", "CONFLICT"]
    conflicting = [row for row in cell["evidence"] if row["id"] in cell["decision"]["evidence"]]
    assert {row["code"] for row in conflicting} == {"SAME_ATTEMPT_CONFLICT"}
    assert {row["bucket"] for row in conflicting} == {"isolated"}

    # 冲突清单、缺陷簇、批次良率与交接差额按同一份证据重算
    conflicts = client.get(run_base + "/retest/W-07", params={"result": "CONFLICT"}).json()["cells"]
    assert [(cell["x"], cell["y"]) for cell in conflicts] == [(4, 4)]
    members = {point for group in client.get(run_base + "/clusters/W-07").json()
               for point in map(tuple, group["points"])}
    assert (3, 3) in members and len(members) == 24
    audit = client.get(run_base + "/yield").json()
    wafer = audit["wafers"][0]
    assert {key: wafer[key] for key in ("passed", "failed", "conflict")} == {"passed": 275, "failed": 24, "conflict": 1}
    assert wafer["delta"] == {"valid": 0, "passed": 0, "failed": 1, "pending": 0, "conflict": -1}
    assert wafer["status"] == "DIFFERENCE"
    assert audit["summary"]["conflict"] == 1 and audit["summary"]["failed"] == 34

    # 原行不被采信抹掉：隔离账与原因码分布不变
    assert audit["rows"]["isolated"] == before["rows"]["isolated"] == 18
    assert audit["reasons"] == before["reasons"]
    assert audit["reasons"]["SAME_ATTEMPT_CONFLICT"] == 6
    assert client.get(run_base + "/rulings").json()["rulings"] == [ruling]


def test_ruling_without_basis_keeps_conflict(client, run_base):
    for row_id in ["probe-a.csv:1", "probe-b.csv:5", "probe-a.csv:9999"]:
        reply = adopt(client, run_base, x=4, y=4, row_id=row_id)
        assert (reply.status_code, reply.json()["code"]) == (422, "RULING_WITHOUT_BASIS"), row_id
    conflicts = client.get(run_base + "/retest/W-07", params={"result": "CONFLICT"}).json()["cells"]
    assert {(cell["x"], cell["y"]) for cell in conflicts} == {(3, 3), (4, 4)}
    assert client.get(run_base + "/rulings").json()["rulings"] == []
    assert client.get(run_base + "/yield").json()["summary"]["conflict"] == 2


def test_ruling_rejects_calm_cell_unknown_wafer_and_out_of_mask(client, run_base):
    calm = adopt(client, run_base, x=2, y=3, row_id="probe-a.csv:546")
    assert (calm.status_code, calm.json()["code"]) == (409, "NOT_IN_CONFLICT")
    missing = adopt(client, run_base, wafer="W-99")
    assert (missing.status_code, missing.json()["code"]) == (404, "WAFER_NOT_FOUND")
    outside = adopt(client, run_base, x=12, y=12)
    assert (outside.status_code, outside.json()["code"]) == (404, "CELL_NOT_FOUND")
    assert client.post("/api/runs/unknown/rulings", json=RULING).json()["code"] == "RUN_NOT_FOUND"


def test_same_batch_retry_adds_no_new_ruling_and_overrule_is_rejected(client, run_base):
    first = adopt(client, run_base)
    assert first.status_code == 201 and first.json()["reused"] is False
    retry = adopt(client, run_base, note="重试不改结论")
    assert retry.status_code == 200 and retry.json()["reused"] is True
    assert retry.json()["ruling"] == first.json()["ruling"]
    overrule = adopt(client, run_base, row_id="probe-a.csv:544")
    assert (overrule.status_code, overrule.json()["code"]) == (409, "RULING_EXISTS")
    rulings = client.get(run_base + "/rulings").json()["rulings"]
    assert rulings == [first.json()["ruling"]]


def test_parallel_publish_commits_one_ruling(client, run_base):
    store = client.app.state.runs.store
    run_id = run_base.rsplit("/", 1)[1]
    requests = [SimpleNamespace(wafer="W-07", x=3, y=3, reviewer="张工", note="", row_id=row_id)
                for row_id in ("probe-b.csv:3", "probe-a.csv:544") * 2]

    def attempt(request):
        try:
            return publish(store, run_id, request)
        except Exception as error:  # noqa: BLE001 - 并发下只允许结论冲突这一种失败
            return error

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(attempt, requests))
    published = [result for result in results if not isinstance(result, Exception)]
    rejected = [result for result in results if isinstance(result, Exception)]
    assert len(published) == 2 and sum(not result["reused"] for result in published) == 1
    assert len(rejected) == 2 and {result.code for result in rejected} == {"RULING_EXISTS"}
    assert len(store.rulings(run_id)) == 1


def test_failed_publish_restores_previous_batch_results(client, run_base):
    store = client.app.state.runs.store
    before = client.get(run_base + "/yield").json()
    with store.connect() as connection:
        connection.execute("CREATE TRIGGER fail_ruling AFTER INSERT ON wafer_rulings "
                           "BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
    failed = adopt(client, run_base)
    assert (failed.status_code, failed.json()["code"]) == (503, "STORAGE_ERROR")
    assert client.get(run_base + "/rulings").json()["rulings"] == []
    assert client.get(run_base + "/yield").json() == before
    with store.connect() as connection:
        connection.execute("DROP TRIGGER fail_ruling")
    assert adopt(client, run_base).status_code == 201
    assert client.get(run_base + "/yield").json()["summary"]["conflict"] == 1


def test_archive_rows_and_export_stay_verifiable_after_ruling(client, run_base):
    manifest = client.get(run_base).json()
    adopt(client, run_base)
    bundle = client.get(run_base + "/export").json()
    assert bundle["manifest"]["result_sha256"] == manifest["result_sha256"]
    assert bundle["snapshot"]["decisions"]["W-07"]["3,3"]["result"] == "CONFLICT"
    assert [row["row_id"] for row in bundle["rulings"]] == ["probe-b.csv:3"]
    assert client.post(run_base + "/rerun").json()["verified"] is True
    again = client.get(run_base + "/export")
    assert "rulings" in again.json()


def test_pass_adoption_recalculates_yield_without_touching_clusters(client, run_base):
    reply = adopt(client, run_base, row_id="probe-a.csv:544")
    assert reply.status_code == 201 and reply.json()["ruling"]["result"] == "PASS"
    audit = client.get(run_base + "/yield").json()
    wafer = audit["wafers"][0]
    assert {key: wafer[key] for key in ("passed", "failed", "conflict")} == {"passed": 276, "failed": 23, "conflict": 1}
    assert wafer["delta"]["passed"] == 1 and wafer["delta"]["conflict"] == -1
    members = {point for group in client.get(run_base + "/clusters/W-07").json()
               for point in map(tuple, group["points"])}
    assert (3, 3) not in members and len(members) == 23
