"""R12 T-P2.1–T-P2.3：契约增量不改旧指纹；handle 生成、唯一与不可变；正文链接解析、固定版本与重新固定。"""
import pytest

from burrowl.core.errors import BurrowlError
from burrowl.core.records import content_hash, record_hash
from burrowl.services.memory.claims import claim_hash
from burrowl.services.memory.context import Storage
from burrowl.services.memory.service import MemoryService
from burrowl.services.review import claim_lookup_factory
from tests.service.helpers import goal_draft, head_of, narrative_draft, register_source, request

OWNER = "RES-FLOATING-POINT-SUMMATION"


def test_existing_records_keep_their_hashes(sample_workspace):
    """D-27：契约只增不改——浮点样例全部记录的内容指纹、记录指纹与结论指纹保持不变。"""
    storage = Storage.open(sample_workspace)
    owner = storage.owners.scan().get(OWNER)
    records = storage.store.read_snapshot(owner).records.values()
    assert records
    for record in records:
        assert content_hash(record) == record["content_hash"]
        assert record_hash(record) == record["record_hash"]
        assert "links" not in record and "handle" not in record
        for claim in record["payload"].get("claims", []):
            assert claim_hash(claim) == claim_hash(dict(claim))


def _memory(root, concept_owner=None):
    storage = Storage.open(root)
    return storage, MemoryService(storage, claim_lookup_factory=claim_lookup_factory(storage),
                                  concept_owner=lambda: concept_owner)


def _commit(memory, owner_id, *operations):
    return memory.commit(request(owner_id, head_of(memory, owner_id), *operations))


def _new_owner(storage, slug="links-lab", title="链接实验"):
    return storage.owners.create("research", slug, title).owner_id


def test_handle_generated_unique_and_immutable(tmp_path):
    storage, memory = _memory(tmp_path)
    owner = _new_owner(storage)
    a = _commit(memory, owner, {"op": "put_record", "client_key": "a", "draft": goal_draft(owner)})
    b = _commit(memory, owner, {"op": "put_record", "client_key": "b", "draft": goal_draft(owner, "第二个目标")})
    first, second = a["record_results"][0], b["record_results"][0]
    assert first["handle"] == "研究目标" and second["handle"] == "研究目标-2"
    record = memory.snapshot(memory.owners().get(owner)).records[first["record_id"]]
    changed = dict(goal_draft(owner, "改写的目标"), handle="别的名字")
    with pytest.raises(BurrowlError) as caught:
        _commit(memory, owner, {"op": "put_record", "record_id": record["record_id"], "expected_revision": 1,
                                "draft": changed})
    assert caught.value.code == "INVALID_TRANSITION"
    previous = {"target_kind": "record", "target_id": record["record_id"], "revision": 1,
                "sha256": record["record_hash"], "locator": "record", "relation": "references"}
    revised = goal_draft(owner, "改写的目标")
    revised["payload"] |= {"previous_goal_ref": previous, "change_impact": "范围缩小"}
    kept = _commit(memory, owner, {"op": "put_record", "record_id": record["record_id"], "expected_revision": 1,
                                   "draft": revised})
    assert kept["record_results"][0]["handle"] == "研究目标"
    with pytest.raises(BurrowlError) as caught:
        _commit(memory, owner, {"op": "put_record", "client_key": "c",
                                "draft": dict(goal_draft(owner), handle="研究目标-2")})
    assert "已被使用" in caught.value.message


def _reference_handles(operations, records):
    """修复前的 O(B·N) 写法（每条新记录重新规范化全部已占用 handle），作为行为对照。"""
    from burrowl.core import links
    revising = {rid for _op, rid, _d, old in operations if old}
    taken = {}
    for rid, record in records.items():
        if rid not in revising or record.get("handle"):
            taken.setdefault(links.normalize(record.get("handle") or links.make_handle(record["title"])), rid)
    for _op, rid, draft, old in operations:
        if old and old.get("handle"):
            draft.setdefault("handle", old["handle"])
        elif "handle" not in draft:
            draft["handle"] = links.unique_handle(links.make_handle(draft.get("title", "")),
                                                  {k for k, v in taken.items() if v != rid})
        taken[links.normalize(draft["handle"])] = rid
    return [draft["handle"] for _op, _rid, draft, _old in operations]


def _handle_batch(existing, new):
    """existing 条已有记录（含无 handle 的旧记录）+ new 条无 handle 的新记录，标题大量重复。"""
    records = {f"R-{i}": {"title": f"实验 {i % 50}", **({"handle": f"已有-{i}"} if i % 3 else {})}
               for i in range(existing)}
    titles = ["同名记录", "ＦＵＬＬ　Width", "实验 7", "x" * 80]
    operations = [({}, f"N-{i}", {"title": titles[i % len(titles)] if i % 2 else f"新记录 {i}"}, None)
                  for i in range(new)]
    return records, operations


def test_handle_assignment_is_linear_and_unchanged(monkeypatch):
    """批量导入：handle 分配的规范化次数随记录数线性增长（评估项目 5183 条导入曾超过 4 小时），结果与旧写法一致。"""
    import copy

    from burrowl.core import links
    from burrowl.storage.memory_store import Snapshot

    records, operations = _handle_batch(300, 400)
    expected = _reference_handles(copy.deepcopy(operations), records)
    MemoryService._assign_handles(operations, Snapshot(None, None, records))
    assert [draft["handle"] for _op, _rid, draft, _old in operations] == expected
    assert len({links.normalize(h) for h in expected}) == len(expected)

    calls = 0
    original = links.normalize

    def counting(name):
        nonlocal calls
        calls += 1
        return original(name)

    monkeypatch.setattr(links, "normalize", counting)
    records, operations = _handle_batch(5000, 5000)
    MemoryService._assign_handles(operations, Snapshot(None, None, records))
    # 每条已有记录 1 次、每条新记录约 3 次（base、候选、写入 taken）；旧写法约 2.5×10^7 次
    assert calls < 5 * (5000 + 5000)


def _setup_links(tmp_path, concept_owner=None):
    storage, memory = _memory(tmp_path, concept_owner)
    owner = _new_owner(storage)
    evidence = register_source(storage, "SRC-LINK-1", "data/link.txt", "1e16 + 1 = 1e16\n")
    return storage, memory, owner, evidence


def test_links_resolve_in_batch_and_pin_versions(tmp_path):
    storage, memory, owner, evidence = _setup_links(tmp_path)
    first = narrative_draft(owner, evidence, "CLM-LINK-补偿抵消失效")
    first["title"] = "第三轮实验"
    second = narrative_draft(owner, evidence, "CLM-LINK-2", "见 [[!补偿抵消失效]]")
    second["title"] = "复核经过"
    second["body_markdown"] = "沿用 [[@第三轮实验|那次实验]] 的输入，涉及 [[补偿求和]]。`[[@不是链接]]`"
    result = _commit(memory, owner, {"op": "put_record", "client_key": "a", "draft": first},
                     {"op": "put_record", "client_key": "b", "draft": second})
    a, b = result["record_results"]
    assert b["unresolved_concepts"] == ["补偿求和"]   # 概念库未配置：不阻塞，报告出来
    record = memory.snapshot(memory.owners().get(owner)).records[b["record_id"]]
    by_text = {x["text"]: x for x in record["links"]}
    assert set(by_text) == {"@第三轮实验", "补偿求和", "!补偿抵消失效"}
    assert by_text["@第三轮实验"]["ref"]["target_id"] == a["record_id"]
    assert by_text["!补偿抵消失效"]["ref"]["target_kind"] == "claim"
    assert by_text["补偿求和"] == {"text": "补偿求和", "ref": None, "status": "unresolved_concept"}
    assert record["content_hash"] == content_hash(record)

    # 目标出了新修订：未改动的链接沿用旧版本；repin 才固定到当前版本
    _commit(memory, owner, {"op": "put_record", "record_id": a["record_id"], "expected_revision": 1,
                            "draft": dict(first, body_markdown="第三轮实验正文（修订）")})
    revised = dict(second, body_markdown=second["body_markdown"] + "\n补充一句。")
    _commit(memory, owner, {"op": "put_record", "record_id": b["record_id"], "expected_revision": 1, "draft": revised})
    record = memory.snapshot(memory.owners().get(owner)).records[b["record_id"]]
    assert {x["text"]: x for x in record["links"]}["@第三轮实验"]["ref"]["revision"] == 1
    _commit(memory, owner, {"op": "put_record", "record_id": b["record_id"], "expected_revision": 2, "repin": True,
                            "draft": dict(revised, body_markdown=revised["body_markdown"] + "再补一句。")})
    record = memory.snapshot(memory.owners().get(owner)).records[b["record_id"]]
    assert {x["text"]: x for x in record["links"]}["@第三轮实验"]["ref"]["revision"] == 2


def test_unresolved_record_link_fails_with_candidates(tmp_path):
    storage, memory, owner, evidence = _setup_links(tmp_path)
    first = narrative_draft(owner, evidence, "CLM-LINK-A")
    first["title"] = "第三轮实验"
    _commit(memory, owner, {"op": "put_record", "client_key": "a", "draft": first})
    draft = narrative_draft(owner, evidence, "CLM-LINK-B")
    draft["body_markdown"] = "见 [[@第三轮试验]] 与 [[@第三轮实验#kahan]]"
    with pytest.raises(BurrowlError) as caught:
        memory.commit(request(owner, head_of(memory, owner), {"op": "put_record", "client_key": "b", "draft": draft},
                              dry_run=True))
    assert caught.value.code == "UNRESOLVED_LINK"
    problems = {e["link"]: e for e in caught.value.errors}
    assert "第三轮实验" in problems["[[@第三轮试验]]"]["candidates"]
    assert "块" in problems["[[@第三轮实验#kahan]]"]["message"]


def test_concept_links_resolve_through_library_and_merge(tmp_path):
    storage = Storage.open(tmp_path)
    library = storage.owners.create("knowledge", "concepts", "概念库").owner_id
    _, memory, owner, evidence = _setup_links(tmp_path, concept_owner=library)

    def concept(title, aliases=(), **extra):
        return {"owner_id": library, "kind": "wiki_page", "title": title, "body_markdown": f"{title}的定义。",
                "record_reason": "建立概念", "sensitivity": "internal", "sources": [evidence],
                "payload": {"page_type": "concept", "aliases": list(aliases), "source_refs": [evidence], **extra}}

    made = _commit(memory, library, {"op": "put_record", "client_key": "k", "draft": concept("补偿求和", ["Kahan 求和"])},
                   {"op": "put_record", "client_key": "c", "draft": concept("补偿算法")})
    kahan, other = (r["record_id"] for r in made["record_results"])
    draft = narrative_draft(owner, evidence, "CLM-LINK-C")
    draft["body_markdown"] = "[[kahan　求和]] 与 [[补偿算法]] 与 [[未建概念]]"
    result = _commit(memory, owner, {"op": "put_record", "client_key": "n", "draft": draft})
    record = memory.snapshot(memory.owners().get(owner)).records[result["record_results"][0]["record_id"]]
    by_text = {x["text"]: x for x in record["links"]}
    assert by_text["kahan　求和"]["ref"]["target_id"] == kahan          # 别名、全角空格、大小写都能匹配
    assert by_text["kahan　求和"]["ref"]["relation"] == "mentions"
    assert result["record_results"][0]["unresolved_concepts"] == ["未建概念"]

    # 合并：被并页 status=merged，新链接转向保留页
    snapshot = memory.snapshot(memory.owners().get(library))
    target = snapshot.records[kahan]
    merged_into = {"target_kind": "record", "target_id": kahan, "revision": target["revision"],
                   "sha256": target["record_hash"], "locator": "record", "relation": "references"}
    _commit(memory, library, {"op": "put_record", "record_id": other, "expected_revision": 1,
                              "draft": concept("补偿算法", status="merged", merged_into=merged_into)})
    again = narrative_draft(owner, evidence, "CLM-LINK-D")
    again["body_markdown"] = "[[补偿算法]]"
    result = _commit(memory, owner, {"op": "put_record", "client_key": "m", "draft": again})
    record = memory.snapshot(memory.owners().get(owner)).records[result["record_results"][0]["record_id"]]
    assert record["links"][0]["ref"]["target_id"] == kahan


def test_l1_claim_rules(sample_workspace):
    """D-07：L1 可写 fact/calculation 结论，block_id 必须是本单元的块。"""
    storage, memory = _memory(sample_workspace)
    owner = memory.owners().get(OWNER)
    record = memory.snapshot(owner).records["MEM-c52df8fb-ca9e-53a7-a49b-342592884a50"]
    from burrowl.services.memory.rules import draft_from  # noqa: PLC0415
    draft = draft_from(record)
    ref = draft["sources"][0]
    claim = {"claim_id": "CLM-FP-ULP", "statement": "1e16 + 1 仍返回 1e16", "kind": "fact",
             "scope": "binary64", "evidence_refs": [dict(ref, relation="supports")], "block_id": "trace"}
    draft["payload"]["claims"] = [claim]
    ok = memory.commit(request(OWNER, head_of(memory, OWNER), {"op": "put_record", "record_id": record["record_id"],
                                                               "expected_revision": record["revision"], "draft": draft},
                               dry_run=True))
    assert ok["valid"]
    draft["payload"]["claims"] = [dict(claim, kind="inference", block_id="nope")]
    with pytest.raises(BurrowlError) as caught:
        memory.commit(request(OWNER, head_of(memory, OWNER), {"op": "put_record", "record_id": record["record_id"],
                                                              "expected_revision": record["revision"], "draft": draft},
                              dry_run=True))
    # R20 D-05：路径从提交参数的根算起，指向要改的草案字段
    paths = {e["path"] for e in caught.value.errors}
    assert {"/operations/0/draft/payload/claims/0/kind", "/operations/0/draft/payload/claims/0/block_id"} <= paths


def test_concept_library_names_are_unique_and_feed_query_terms(tmp_path):
    """T-P2.4：有效概念的名称与别名在概念库内唯一；概念名进入查询术语扩展。"""
    from burrowl.services.memory import concepts  # noqa: PLC0415

    storage = Storage.open(tmp_path)
    library = storage.owners.create("knowledge", "concepts", "概念库", {"short_name": "概念"}).owner_id
    _, memory, _owner, evidence = _setup_links(tmp_path, concept_owner=library)

    def concept(title, aliases=()):
        return {"owner_id": library, "kind": "wiki_page", "title": title, "body_markdown": "定义。",
                "record_reason": "建立概念", "sensitivity": "internal", "sources": [evidence],
                "payload": {"page_type": "concept", "aliases": list(aliases), "source_refs": [evidence]}}

    _commit(memory, library, {"op": "put_record", "client_key": "a", "draft": concept("收敛（数值）", ["数值收敛"])})
    with pytest.raises(BurrowlError) as caught:
        _commit(memory, library, {"op": "put_record", "client_key": "b", "draft": concept("数值收敛")})
    assert caught.value.details["conflicts"][0]["title"] == "收敛（数值）"
    _commit(memory, library, {"op": "put_record", "client_key": "c", "draft": concept("收敛（优化）")})

    records = list(memory.snapshot(memory.owners().get(library)).records.values())
    entries, digest = concepts.term_entries(records, "数值收敛 的判据")
    assert [e["zh"] for e in entries] == [["收敛（数值）"]] and digest
    assert concepts.term_entries(records, "无关的问题") == ([], None)
