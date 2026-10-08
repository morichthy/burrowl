"""时间线与原始材料视图（样例数据）。"""
from burrowl.services.memory import history, raw_materials
from burrowl.services.memory.context import Storage

OWNER = "RES-FLOATING-POINT-SUMMATION"


def test_timeline_includes_revisions_and_runs(sample_workspace):
    storage = Storage.open(sample_workspace)
    page = history.timeline(storage, OWNER, limit=500)
    assert page["total"] > 30 and page["missing"] == []
    kinds = {row["kind"] for row in page["items"]}
    assert {"run", "detail", "overview"} <= kinds
    assert any(not row["current"] for row in page["items"])  # 旧修订也在时间线中
    first = next(row for row in page["items"] if row["kind"] == "overview")
    assert history.record_payload(storage, OWNER, first["record_id"], first["revision"])["kind"] == "overview"


def test_raw_material_listing_and_read(sample_workspace):
    storage = Storage.open(sample_workspace)
    listing = raw_materials.listing(storage, OWNER)
    assert listing["total"] > 10 and not listing["content_loaded"]
    registered = next(i for i in listing["items"] if i["status"] == "registered" and i["path"].endswith(".json"))
    content = raw_materials.read(storage, OWNER, registered["material_id"], max_chars=500)
    assert content["media_type"] == "text/plain" and content["verified_sha256"] == registered["sha256"]
