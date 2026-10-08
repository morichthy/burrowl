"""记录指纹与校验：对旧系统真实生成的样例逐条核对，保证算法逐字节一致。"""
from collections import Counter

import pytest

from burrowl.core.hashing import canonical_hash
from burrowl.core.records import (
    LEVELS,
    content_hash,
    normalize_draft,
    record_hash,
    validate_commit_request,
    validate_record,
)
from burrowl.core.schema import current_versions
from tests.conftest import load, sample_memory_home


def all_records():
    home = sample_memory_home()
    return [load(p) for p in sorted(home.glob("commits/*/records/*.json"))]


def test_every_stored_record_hash_matches_new_implementation():
    records = all_records()
    assert len(records) > 20
    for record in records:
        assert content_hash(record) == record["content_hash"], record["record_id"]
        assert record_hash(record) == record["record_hash"], record["record_id"]


def test_head_manifest_chain_matches_canonical_hash():
    home = sample_memory_home()
    head = load(home / "HEAD.json")
    manifest_hash, commit_id, seen = head["manifest_hash"], head["commit_id"], 0
    while commit_id:
        manifest = load(home / "commits" / commit_id / "manifest.json")
        assert canonical_hash(manifest) == manifest_hash
        manifest_hash, commit_id = manifest["parent_manifest_hash"], manifest["parent_commit_id"]
        seen += 1
    assert seen == head["generation"]


def test_current_records_validate_and_legacy_versions_are_rejected():
    home = sample_memory_home()
    head = load(home / "HEAD.json")
    manifest = load(home / "commits" / head["commit_id"] / "manifest.json")
    outcomes = Counter()
    for entry in manifest["record_heads"].values():
        record = load(home / entry["path"])
        _, errors = validate_record(record, allow_review=True)
        is_current = record["kind"] in LEVELS and record["schema_version"] == current_versions()[record["kind"]]
        if is_current:
            assert errors == [], (record["kind"], errors[:3])
            outcomes["current"] += 1
        else:
            assert errors, record["kind"]
            outcomes["legacy"] += 1
    assert outcomes["current"] >= 10


def test_draft_defaults_and_server_fields():
    draft = normalize_draft({"kind": "overview", "keywords": ["a", "a", "b"], "payload": {}})
    assert draft["schema_version"] == 4 and draft["level"] == "L4"
    assert draft["keywords"] == ["a", "b"] and draft["payload"]["missing_refs"] == []
    errors = validate_commit_request({
        "schema_version": 3, "request_id": "r1", "actor": {"id": "t", "kind": "ai"}, "owner_id": "RES-X",
        "expected_head": None,
        "operations": [{"op": "put_record", "client_key": "a", "draft": {"kind": "goal", "record_id": "MEM-1"}}]})
    assert any(e["path"] == "/operations/0/draft/record_id" for e in errors)


@pytest.mark.parametrize("kind", ["event", "map"])
def test_removed_kinds_are_unknown(kind):
    _, errors = validate_record({"kind": kind})
    assert errors[0]["path"] == "/kind"
