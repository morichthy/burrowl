"""关系导航：沿作者保存的固定引用与已采纳的关联记录，有界地深化（图遍历）。

关系来自三处：记录的 sources / payload 引用（关系名即作者所写）、文稿编排（contains）、
技术块前置（depends_on），以及 status=accepted 的关联记录（association）。
同研究、同关键词或时间相邻都不会自动成为关系；导航可达不等于科学支持。
"""
from collections import deque
from collections.abc import Iterator

from ...core.errors import BurrowlError
from ..memory.reader import Reader, fixed_ref

REF_FIELDS = ("evidence_refs", "result_refs", "conflict_refs", "claim_refs", "process_refs", "technical_refs",
              "experience_refs", "run_refs", "question_refs", "basis_refs")


def forward_edges(reader: Reader, ref: dict) -> Iterator[tuple[dict, str, str]]:
    """一个节点的出边：(目标引用, 关系, 来源字段)。"""
    if ref["target_kind"] != "record":
        return
    record = reader.record(ref)
    payload = record["payload"]
    if record["kind"] == "document":
        for ref_ in payload["section_refs"]:
            yield ref_, "contains", "section_refs"
    elif record["kind"] == "document_section":
        for block in payload["blocks"]:
            if block["type"] == "unit":
                for block_id in block.get("block_ids") or [None]:
                    target = dict(block["ref"])
                    if block_id:
                        target["locator"] = "block:" + block_id
                    yield target, "contains", "blocks"
    elif record["kind"] == "association":
        return  # 关联记录的端点由 adopted_edges 统一处理（只看已采纳的）
    for source in record["sources"]:
        yield source, source["relation"], "sources"
    for name in REF_FIELDS:
        for item in payload.get(name, []):
            if isinstance(item, dict) and "target_kind" in item:
                yield item, item["relation"], name
    locator = ref.get("locator") or ""
    if record["kind"] == "detail" and locator.startswith("block:"):
        blocks = {b["block_id"]: b for b in payload["blocks"]}
        for required in blocks.get(locator[6:], {}).get("requires_block_ids", []):
            yield fixed_ref(record) | {"locator": "block:" + required}, "depends_on", "requires_block_ids"


def adopted_edges(reader: Reader, record_ids: set[str]) -> Iterator[tuple[str, dict, str, dict]]:
    """已采纳的关联记录：(本端 record_id, 另一端引用, 关系, 关联记录引用)。"""
    for owner in reader.index.readable():
        if not owner.persisted:
            continue
        for record in reader.state(owner.owner_id).records.values():
            if record["kind"] != "association" or record["payload"]["status"] != "accepted" \
                    or record["sensitivity"] == "restricted":
                continue
            payload = record["payload"]
            for near, far in ((payload["from"], payload["to"]), (payload["to"], payload["from"])):
                if near["target_id"] in record_ids:
                    yield near["target_id"], far, payload["relation"], fixed_ref(record)


def adopted_neighbors(reader: Reader, record_ids: list[str]) -> list[dict]:
    seen, out = set(), []
    for _near, far, _relation, _assoc in adopted_edges(reader, set(record_ids)):
        if far["target_id"] not in seen and far["target_id"] not in record_ids:
            seen.add(far["target_id"])
            out.append(far)
    return out


def deepen(reader: Reader, ledger, seeds: list[dict], *, hops: int = 2, relations: list[str] | None = None,
           include_associations: bool = True, allowed_owner_ids: set[str] | None = None) -> dict:
    """从种子出发做有界广度优先遍历。节点/边/跳数都计入预算；环与菱形只计一次。"""
    wanted = set(relations) if relations else None
    hops = min(hops, ledger.remaining("graph_hops") or 0) if hops else 0
    nodes: dict[tuple, dict] = {}
    edges: list[dict] = []
    gaps: list[dict] = []
    queue = deque((seed, 0) for seed in seeds)

    def key(ref: dict) -> tuple:
        return ref["target_kind"], ref["target_id"], ref.get("revision"), ref.get("locator") or ""

    for seed in seeds:
        nodes[key(seed)] = {"ref": seed, "depth": 0}
    while queue:
        ref, depth = queue.popleft()
        if depth >= hops:
            continue
        ledger.checkpoint()
        try:
            outgoing = list(forward_edges(reader, ref))
            if include_associations and ref["target_kind"] == "record":
                outgoing += [(far, relation, "association:" + assoc["target_id"])
                             for _near, far, relation, assoc in adopted_edges(reader, {ref["target_id"]})]
        except BurrowlError as exc:
            if exc.code == "BUDGET_EXCEEDED":
                raise
            gaps.append({"target_id": ref["target_id"], "code": exc.code})
            continue
        for target, relation, field in outgoing:
            if wanted is not None and relation not in wanted:
                continue
            if target["target_kind"] == "record" and allowed_owner_ids is not None:
                try:
                    if reader.locate(target["target_id"]) not in allowed_owner_ids:
                        continue
                except BurrowlError:
                    gaps.append({"target_id": target["target_id"], "code": "NOT_FOUND"})
                    continue
            ledger.charge("graph_edges", 1)
            edges.append({"from": ref, "to": target, "relation": relation, "field": field})
            if key(target) not in nodes:
                ledger.charge("graph_nodes", 1)
                nodes[key(target)] = {"ref": target, "depth": depth + 1}
                queue.append((target, depth + 1))
    for node in nodes.values():  # 节点当前是否可读：失败只标注，不删除路径
        try:
            if node["ref"]["target_kind"] == "record":
                record = reader.record(node["ref"])
                node.update(title=record["title"], kind=record["kind"], level=record["level"],
                            owner_id=record["owner_id"])
            else:
                node.update(title=node["ref"]["target_id"], kind=node["ref"]["target_kind"])
        except BurrowlError as exc:
            node["issue"] = exc.code
    return {"nodes": list(nodes.values()), "edges": edges, "gaps": gaps,
            "note": "导航可达只说明存在作者保存的关系，不代表科学支持"}
