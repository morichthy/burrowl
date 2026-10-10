"""图投影（R12 D-10、§4.4、§4.10）：从一个对象的当前记录确定性地生成节点和边（纯函数，不读写文件）。

AI 只写记录，程序把记录"翻译"成点和线；要改图只能改记录，图投影删掉后可以重新算出来。

通用抽边规则（新增记录类型无需改这里）：
  ① 记录归属（belongs_to）、L1 的块与块依赖、记录包含的结论；
  ② sources 与 payload 中任意位置的固定引用（type = 引用里写的关系名）；
  ③ 服务端解析的 links（概念 → mentions，记录/块/结论 → references）；
  ④ 结论的依据（evidence_refs）与 relation（主体、对象，以及派生的快捷边）；
  ⑤ association 记录的两个端点（rejected / withdrawn 不进图）；
  ⑥ 复核中的 superseded（替代结论 → 被替代结论）。
category 由程序按 type 查表得出，AI 从不填写。
"""
from typing import Any

from ..core.hashing import canonical_hash

GRAPH_VERSION = "graph-v2"  # v2：Run 节点由引用它的对象投影（R14 D-11）
SKIP_KINDS = {"review", "policy", "feedback", "association", "representation"}  # 不成为节点（association 画成边）
CONCEPT_PAGE_TYPES = {"concept", "entity"}
CATEGORY = {
    **dict.fromkeys(("belongs_to", "contains", "part_of", "depends_on", "produces", "member_of", "precedes"),
                    "structure"),
    **dict.fromkeys(("supports", "input", "contradicts", "supersedes"), "evidence"),
    **dict.fromkeys(("references", "background", "derived_from", "prerequisite", "refines", "used_in"), "reference"),
    **dict.fromkeys(("same_problem", "same_failure_mode", "analogous_to", "related_to", "similar_structure",
                     "same_entity"), "navigation"),
    **dict.fromkeys(("mentions", "subject_of", "object_of"), "concept"),
}
CATEGORIES = ("structure", "evidence", "reference", "navigation", "concept", "claim_relation")
UNSAFE = {"disputed", "retracted", "superseded", "stale"}


def category(edge_type: str, *, derived: bool = False, field: str = "") -> str:
    if derived:
        return "claim_relation"
    if field == "association":
        return "navigation"  # 关联只做导航，即使关系名是 supports 也不是证据
    return CATEGORY.get(edge_type, "reference")


def target_node(ref: dict) -> str:
    """固定引用 → 节点身份：块引用指向 "MEM-…#块"，其余用 target_id。"""
    locator = str(ref.get("locator") or "")
    if ref.get("target_kind") == "record" and locator.startswith("block:"):
        return f"{ref['target_id']}#{locator[6:]}"
    return ref["target_id"]


def _refs(value: Any, skip: frozenset = frozenset({"missing_refs"})):
    if isinstance(value, dict):
        if "target_kind" in value and "target_id" in value:
            yield value
            return
        for key, item in value.items():
            if key not in skip:
                yield from _refs(item, skip)
    elif isinstance(value, list):
        for item in value:
            yield from _refs(item, skip)


def _is_concept(record: dict) -> bool:
    return record["kind"] == "wiki_page" and record["payload"].get("page_type") in CONCEPT_PAGE_TYPES


def graph(owner, records: list[dict], badges: dict[str, str], hidden: set[str],
          maturity: dict[str, str | None] | None = None,
          run_titles: dict[str, str] | None = None) -> tuple[list[dict], list[dict]]:
    """owner：projections.OwnerInfo；badges / maturity：结论 ID → 复核徽标 / 有效成熟度（服务层给出）。
    run_titles：本对象记录引用的 Run → 标题。Run 不再作为对象投影（R14 D-11），Run 节点由引用它的对象各投影一行，
    与被引用的来源节点同理。"""
    maturity = maturity or {}
    visible = [r for r in records if r["record_id"] not in hidden and r["sensitivity"] != "restricted"]
    nodes: dict[str, dict] = {}
    edges: dict[str, dict] = {}

    def node(node_id: str, node_type: str, title: str, record: dict | None = None, **extra: Any) -> None:
        extra.setdefault("ref", None)
        nodes.setdefault(node_id, {
            "node_id": node_id, "node_type": node_type, "owner_id": owner.owner_id,
            "record_id": record["record_id"] if record else None, "title": title,
            "level": record.get("level") if record else None, "kind": record["kind"] if record else node_type,
            "domains": list((record or {}).get("payload", {}).get("domains", [])) if record else [],
            "sensitivity": record["sensitivity"] if record else owner.sensitivity,
            "discovery": record["discovery"] if record else "workspace_summary",
            "review": None, "maturity": None, **extra})

    def edge(src: str, dst: str, edge_type: str, record: dict, field: str, *, certainty: str | None = None,
             state: str = "active", origin: str | None = None, derived: bool = False,
             declared_by: str | None = None) -> None:
        if src == dst:
            return
        declared = declared_by or record["record_id"]
        key = canonical_hash({"by": declared, "field": field, "src": src, "dst": dst, "type": edge_type})[:32]
        edges.setdefault(key, {"edge_id": key, "src": src, "dst": dst,
                               "category": category(edge_type, derived=derived, field=field), "type": edge_type,
                               "certainty": certainty, "state": state, "origin": origin, "declared_by": declared,
                               "declared_owner": owner.owner_id, "declared_rev": record.get("revision"),
                               "field": field, "derived": derived})

    node(owner.owner_id, "owner", owner.title, ref={"target_kind": "owner", "target_id": owner.owner_id,
                                                    "revision": None, "sha256": owner.fingerprint, "locator": "",
                                                    "relation": "references"})
    for record in visible:
        rid, payload = record["record_id"], record["payload"]
        if record["kind"] in SKIP_KINDS:
            continue
        record_ref = {"target_kind": "record", "target_id": rid, "revision": record["revision"],
                      "sha256": record["record_hash"], "locator": "record", "relation": "references"}
        node(rid, "concept" if _is_concept(record) else "record", record["title"], record, ref=record_ref)
        edge(rid, owner.owner_id, "belongs_to", record, "owner_id")
        if record["kind"] == "detail":                                      # ① 块与块依赖
            for block in payload.get("blocks", []):
                block_node = f"{rid}#{block['block_id']}"
                node(block_node, "block", f"{record['title']} › {block['block_id']}", record, block_id=block["block_id"],
                     ref=record_ref | {"locator": f"block:{block['block_id']}"})
                edge(rid, block_node, "contains", record, "blocks")
                for required in block.get("requires_block_ids", []):
                    edge(block_node, f"{rid}#{required}", "depends_on", record, "blocks.requires_block_ids")
        for ref in record["sources"]:                                       # ② 依据与引用
            edge(rid, target_node(ref), ref["relation"], record, "sources")
        skip = frozenset({"missing_refs", "claims"})
        for key, value in payload.items():
            if key in skip:
                continue
            for ref in _refs(value):
                edge(rid, target_node(ref), ref["relation"], record, f"payload.{key}")
        links = {x["text"]: x for x in record.get("links", [])}
        for link in links.values():                                          # ③ 正文链接
            if link["ref"] is not None:
                edge(rid, target_node(link["ref"]), link["ref"]["relation"], record, "links")
        for claim in payload.get("claims", []) or []:                       # ④ 结论
            cid = claim["claim_id"]
            badge = badges.get(cid, "not-reviewed")
            node(cid, "claim", claim["statement"], record, review=badge, maturity=maturity.get(cid),
                 ref={"target_kind": "claim", "target_id": cid, "revision": None, "sha256": canonical_hash(claim),
                      "locator": "statement", "relation": "references"})
            edge(rid, cid, "contains", record, "claims")
            if claim.get("block_id"):
                edge(cid, f"{rid}#{claim['block_id']}", "part_of", record, "claims.block_id")
            for ref in claim["evidence_refs"]:
                edge(cid, target_node(ref), ref["relation"], record, "claims.evidence_refs", declared_by=cid)
            relation = claim.get("relation")
            if relation:
                ends = {}
                for side, edge_type in (("subject", "subject_of"), ("object", "object_of")):
                    text = relation[side].strip()
                    text = text[2:-2].split("|", 1)[0].strip() if text.startswith("[[") and text.endswith("]]") else text
                    resolved = links.get(text)
                    if resolved and resolved["ref"] is not None:
                        ends[side] = target_node(resolved["ref"])
                        edge(cid, ends[side], edge_type, record, f"claims.relation.{side}", declared_by=cid)
                if len(ends) == 2:  # 快捷边：主体 →（谓词）→ 对象，状态继承结论的复核（D-11）
                    edge(ends["subject"], ends["object"], relation["predicate"], record, "claims.relation",
                         derived=True, declared_by=cid, state="invalid" if badge in UNSAFE else badge)
    for record in visible:
        payload = record["payload"]
        if record["kind"] == "association" and payload["status"] not in {"rejected", "withdrawn"}:  # ⑤ 关联
            edge(target_node(payload["from"]), target_node(payload["to"]), payload["relation"], record,
                 "association", certainty=payload.get("certainty") or "plausible", state=payload["status"],
                 origin=payload.get("origin") or "unknown")
        if record["kind"] == "review" and payload["state"] == "superseded" and payload.get("replacement_claim_id"):
            edge(payload["replacement_claim_id"], payload["target_claim_id"], "supersedes", record,  # ⑥ 替代
                 "review.replacement_claim_id")
    file_refs = {ref["target_id"]: ref for record in visible for ref in _refs([record["sources"], record["payload"]])
                 if ref.get("target_kind") == "file"}
    run_refs = {ref["target_id"]: ref for record in visible for ref in _refs([record["sources"], record["payload"]])
                if ref.get("target_kind") == "owner" and ref["target_id"] in (run_titles or {})}
    for ref_target in {e["dst"] for e in edges.values()} - nodes.keys():   # 被引用的已登记来源
        if ref_target.startswith("SRC-"):
            ref = file_refs.get(ref_target)
            node(ref_target, "source", ref_target,
                 ref=dict(ref, locator="", relation="references") if ref else None)
        elif ref_target in run_refs:                                    # 被引用的 Run（R14 D-11）
            node(ref_target, "owner", run_titles[ref_target], kind="run",
                 ref=dict(run_refs[ref_target], locator="", relation="references"))
    return list(nodes.values()), list(edges.values())


def signature(nodes: list[dict], edges: list[dict]) -> str:
    return canonical_hash({"version": GRAPH_VERSION, "nodes": sorted(canonical_hash(n) for n in nodes),
                           "edges": sorted(e["edge_id"] + canonical_hash(e) for e in edges)})
