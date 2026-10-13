"""材料关系图（工作台"材料关系"页）：读取索引中的图投影（R12 T-P2.8，抽边规则见 index/graph.py）。

节点：对象、记录、L1 块、结论、概念、来源；边按 category 分为 structure / evidence / reference /
navigation / mention / claim_part（R23 D-11），关联边带 certainty，候选关联标记为 candidate。
图只是导航视图，不改变任何事实记录；投影不新鲜时先补齐（不调用嵌入模型）。
视图布局、分组名、桥接问题等是本机偏好（.burrowl/workbench/），不是业务数据。
"""
from collections import deque
from typing import Any

from ..core.atomic import exclusive_lock, read_json, write_json_atomic
from ..core.errors import BurrowlError
from .indexing import IndexService
from .memory import associations
from .memory.context import Storage

VIEW_FILE = "workbench/view.json"


def build_graph(storage: Storage, index: IndexService) -> dict:
    index.ensure_graph()
    nodes = {}
    for n in index.db.graph_nodes():
        nodes[n["node_id"]] = {
            "id": n["node_id"], "type": n["node_type"], "kind": n["kind"], "title": n["title"], "level": n["level"],
            "owner_id": None if n["node_type"] == "source" else n["owner_id"],
            "record_id": n["record_id"], "block_id": n["block_id"], "domains": n["domains"],
            "review": n["review"], "maturity": n["maturity"], "ref": n["ref"]}
    # claim 节点的声明者是 CLM ID；反查它所在的记录，供 UI 在 claim 被隐藏时仍可
    # 用现有 memory.inspect 读取完整声明上下文。这里只输出已进入可读图投影的记录。
    record_nodes = {n["id"]: n for n in nodes.values() if n["type"] in {"record", "concept"}}

    def declared_record(edge: dict) -> dict | None:
        declarer = nodes.get(edge["declared_by"])
        record_id = (declarer.get("record_id") or declarer["id"]) if declarer else None
        record = record_nodes.get(record_id) if record_id else None
        if not record:
            return None
        ref = record.get("ref") or {}
        return {"owner_id": record.get("owner_id"), "record_id": record_id,
                "revision": ref.get("revision")}

    edges = [{"id": e["edge_id"], "source": e["src"], "target": e["dst"], "type": e["type"],
              "category": e["category"], "certainty": e["certainty"], "state": e["state"],
              # 保留旧字段供现有调用方使用；它表达声明者记录，不是 association 的 origin。
              "origin": e["declared_by"], "declared_by": e["declared_by"],
              "declared_owner": e["declared_owner"], "declared_rev": e["declared_rev"],
              "field": e["field"], "association_origin": e["origin"],
              "declared_record": declared_record(e),
              "candidate": e["state"] == "candidate"}
             for e in index.db.graph_all_edges() if e["src"] in nodes and e["dst"] in nodes]
    return {"nodes": list(nodes.values()), "edges": edges,
            "note": "边由记录投影得出：作者保存的引用与关联、结论的参与者与条件；可达不代表科学支持"}


def subgraph(graph: dict, *, center: str | None = None, hops: int = 2, kinds: list[str] | None = None,
             levels: list[str] | None = None, excluded: list[str] | None = None, query: str = "",
             limit: int = 400, categories: list[str] | None = None, domains: list[str] | None = None,
             excluded_domains: list[str] | None = None, node_types: list[str] | None = None) -> dict:
    """按中心节点 + 跳数、类型/层级、关键词、边类别、领域筛选子图；排除的节点不能成为通往其他节点的桥。

    domains / excluded_domains 只作用于带领域的节点（概念页）：没有领域的节点不受影响（D-12：全库一个概念库，
    按领域筛选或屏蔽）。node_types 为 None 时显示全部节点类型，空列表则不显示节点。"""
    blocked = set(excluded or [])
    wanted, masked = set(domains or []), set(excluded_domains or [])

    def domain_ok(n: dict) -> bool:
        values = set(n.get("domains") or [])
        return not values or ((not wanted or values & wanted) and not (masked and values <= masked))

    nodes = {n["id"]: n for n in graph["nodes"] if n["id"] not in blocked
             and (kinds is None or n["kind"] in kinds or n["type"] in {"owner", "block", "claim", "concept", "source"})
             and (levels is None or n["level"] in levels or n["type"] not in {"record", "block"})
             and (node_types is None or n["type"] in node_types) and domain_ok(n)}
    edges = [e for e in graph["edges"] if e["source"] in nodes and e["target"] in nodes
             and (categories is None or e.get("category") in categories)]
    if query:
        needle = query.casefold()
        hits = {i for i, n in nodes.items() if needle in (n["title"] or "").casefold()
                or any(needle in k.casefold() for k in n.get("keywords", []))}
        center_ids = hits
    else:
        center_ids = {center} if center in nodes else set()
    if center_ids:
        adjacency: dict[str, set[str]] = {}
        for e in edges:
            adjacency.setdefault(e["source"], set()).add(e["target"])
            adjacency.setdefault(e["target"], set()).add(e["source"])
        keep, queue = set(center_ids), deque((c, 0) for c in center_ids)
        while queue:
            node, depth = queue.popleft()
            if depth >= hops:
                continue
            for neighbor in adjacency.get(node, ()):
                if neighbor not in keep:
                    keep.add(neighbor)
                    queue.append((neighbor, depth + 1))
        nodes = {i: n for i, n in nodes.items() if i in keep}
        edges = [e for e in edges if e["source"] in nodes and e["target"] in nodes]
    truncated = len(nodes) > limit
    if truncated:
        ordered = sorted(nodes, key=lambda i: (i not in center_ids, nodes[i]["type"] != "owner", i))[:limit]
        nodes = {i: nodes[i] for i in ordered}
        edges = [e for e in edges if e["source"] in nodes and e["target"] in nodes]
    return {"nodes": list(nodes.values()), "edges": edges, "truncated": truncated, "centers": sorted(center_ids)}


def similar(storage: Storage, index: IndexService, seeds: list[str], *, method: str = "keyword",
            limit: int = 30) -> dict:
    """关联候选：关键词重合（确定性），或语义近邻（用检索向量，需模型）。都只是待判断的线索。"""
    if method == "keyword":
        return associations.propose(storage, seeds, limit=limit)
    if method != "semantic":
        raise BurrowlError("INVALID_ARGUMENT", "method 只能是 keyword 或 semantic")
    if index.db.embedder is None:
        raise BurrowlError("CAPABILITY_UNAVAILABLE", "语义近邻需要本地嵌入模型")
    entries = {e["record_id"]: e for e in index.db.entries([f"fulltext:{o}:{s}" for s in seeds
                                                             for o in [_owner_of(storage, s)] if o])}
    candidates = []
    for seed, entry in entries.items():
        result = index.search(f"{entry['title']}\n{entry['text'][:400]}", channels=("dense",), limit=limit + 1)
        for item in result["items"]:
            if item["record_id"] != seed and item["kind"] != "claim":
                candidates.append({"from_id": seed, "to_id": item["record_id"], "title": item["title"],
                                   "score": round(item["channel_scores"].get("dense") or 0, 4), "method": "semantic"})
    candidates.sort(key=lambda c: -c["score"])
    return {"candidates": candidates[:limit], "missing": sorted(set(seeds) - set(entries))}


def candidates(storage: Storage, index: IndexService, *, record_id: str | None = None, owner_id: str | None = None,
               kinds: list[str] | None = None, limit: int = 50) -> dict:
    """保存后自动算出的候选（R12 T-P2.10）：提到概念却没链接、向量近邻、外部工具回流。
    已经判断过的端点对（任何状态的关联）不再出现；候选只是线索，判断后用 relations.decide 或改写正文链接。"""
    judged = {frozenset((a["from"]["target_id"], a["to"]["target_id"]))
              for a in associations.list_associations(storage)}
    rows = [r for r in index.db.candidates(record_ids=[record_id] if record_id else None, owner_id=owner_id)
            if frozenset((r["src"], r["dst"])) not in judged and (kinds is None or r["kind"] in kinds)]
    titles = {n["node_id"]: n["title"] for n in index.db.graph_nodes(sorted({x for r in rows for x in (r["src"], r["dst"])}))}
    items = [{**r, "src_title": titles.get(r["src"]), "dst_title": titles.get(r["dst"])} for r in rows[:limit]]
    return {"candidates": items, "total": len(rows),
            "note": "concept_mention：正文提到了概念却没写 [[链接]]；similar：向量近邻；tool:*：外部工具回流。都需要判断"}


def _owner_of(storage: Storage, record_id: str) -> str | None:
    for owner in storage.owners.scan().readable():
        if owner.persisted and record_id in storage.store.read_snapshot(owner).records:
            return owner.owner_id
    return None


class WorkbenchPrefs:
    """关系图的本机视图偏好（布局、分组、中心、排除项）。工作台笔记见 services/notes.py（R15 D-01）。"""

    def __init__(self, storage: Storage):
        self.ws = storage.ws

    def _read(self, name: str, default: Any) -> Any:
        path = self.ws.state(name)
        return read_json(path) if path.exists() else default

    def _write(self, name: str, value: Any) -> None:
        path = self.ws.state(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_lock(path.with_suffix(".lock"), "workbench-prefs"):
            write_json_atomic(path, value, pretty=True)

    def view(self) -> dict:
        return self._read(VIEW_FILE, {})

    def save_view(self, view: dict) -> dict:
        allowed = {"name", "positions", "groups", "center", "excluded", "mode", "filters"}
        if not isinstance(view, dict) or set(view) - allowed:
            raise BurrowlError("INVALID_ARGUMENT", "视图字段无效", {"allowed": sorted(allowed)})
        self._write(VIEW_FILE, view)
        return view

