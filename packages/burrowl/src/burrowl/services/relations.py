"""材料关系图（工作台"材料关系"页）：对象、记录、来源三类节点，以及作者保存的类型化边。

边来自：记录归属（belongs_to）、记录的固定引用（关系名即作者所写）、Run 归属（produces）、
关联记录（accepted 为实线；candidate 标记为候选）。图只是导航视图，不改变任何事实记录。
视图布局、分组名、桥接问题等是本机偏好（.burrowl/workbench/），不是业务数据。
"""
from collections import deque
from typing import Any

from ..core.atomic import exclusive_lock, read_json, write_json_atomic
from ..core.clock import utc_now
from ..core.errors import BurrowlError
from .indexing import IndexService
from .memory import associations
from .memory.claims import iter_refs
from .memory.context import Storage

VIEW_FILE = "workbench/view.json"
NOTES_FILE = "workbench/notes.json"
NOTE_KINDS = {"cluster-name", "bridge-question", "relation-idea"}


def build_graph(storage: Storage) -> dict:
    index = storage.owners.scan()
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    for owner in index.readable():
        nodes[owner.owner_id] = {"id": owner.owner_id, "type": "owner", "kind": owner.owner_type, "title": owner.title,
                                 "level": None, "owner_id": owner.owner_id, "fingerprint": owner.fingerprint}
        if owner.owner_type == "run" and owner.native.get("owner_id"):
            edges.append({"source": owner.native["owner_id"], "target": owner.owner_id, "type": "produces",
                          "origin": "run.json"})
        if not owner.persisted:
            continue
        for record in storage.store.read_snapshot(owner).records.values():
            if record["sensitivity"] == "restricted" or record["kind"] in {"review", "policy", "feedback"}:
                continue
            rid = record["record_id"]
            if record["kind"] == "association":
                payload = record["payload"]
                if payload["status"] in {"accepted", "candidate"}:
                    edges.append({"source": payload["from"]["target_id"], "target": payload["to"]["target_id"],
                                  "type": payload["relation"], "origin": rid,
                                  "candidate": payload["status"] == "candidate"})
                continue
            nodes[rid] = {"id": rid, "type": "record", "kind": record["kind"], "title": record["title"],
                          "level": record["level"], "owner_id": owner.owner_id, "fingerprint": record["record_hash"],
                          "keywords": record["keywords"], "claims": len(record["payload"].get("claims", []))}
            edges.append({"source": rid, "target": owner.owner_id, "type": "belongs_to", "origin": "memory"})
            for ref in iter_refs({"sources": record["sources"], "payload": record["payload"]}):
                if ref["target_kind"] == "file" and ref["target_id"] not in nodes:
                    nodes[ref["target_id"]] = {"id": ref["target_id"], "type": "source", "kind": "file",
                                               "title": ref["target_id"], "level": "L0", "owner_id": None,
                                               "fingerprint": ref.get("sha256")}
                if ref["target_id"] != rid:
                    edges.append({"source": rid, "target": ref["target_id"], "type": ref["relation"],
                                  "origin": rid})
    known = set(nodes)
    edges = [e for e in edges if e["source"] in known and e["target"] in known]
    unique = {(e["source"], e["target"], e["type"]): e for e in edges}
    return {"nodes": list(nodes.values()), "edges": list(unique.values()),
            "note": "边是作者保存的导航关系；可达不代表科学支持"}


def subgraph(graph: dict, *, center: str | None = None, hops: int = 2, kinds: list[str] | None = None,
             levels: list[str] | None = None, excluded: list[str] | None = None, query: str = "",
             limit: int = 400) -> dict:
    """按中心节点 + 跳数、类型/层级、关键词筛选子图；排除的节点不能成为通往其他节点的桥。"""
    blocked = set(excluded or [])
    nodes = {n["id"]: n for n in graph["nodes"] if n["id"] not in blocked
             and (kinds is None or n["kind"] in kinds or n["type"] == "owner")
             and (levels is None or n["level"] in levels or n["type"] != "record")}
    edges = [e for e in graph["edges"] if e["source"] in nodes and e["target"] in nodes]
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


def _owner_of(storage: Storage, record_id: str) -> str | None:
    for owner in storage.owners.scan().readable():
        if owner.persisted and record_id in storage.store.read_snapshot(owner).records:
            return owner.owner_id
    return None


class WorkbenchPrefs:
    """本机视图偏好与随手笔记（分组名、桥接问题等）。"""

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

    def notes(self) -> list[dict]:
        return self._read(NOTES_FILE, [])

    def add_note(self, kind: str, title: str, text: str, refs: list[str]) -> dict:
        if kind not in NOTE_KINDS or not title.strip():
            raise BurrowlError("INVALID_ARGUMENT", "笔记类型无效或缺标题", {"kinds": sorted(NOTE_KINDS)})
        notes = self.notes()
        item = {"id": f"WN-{len(notes) + 1}", "kind": kind, "title": title, "text": text, "refs": refs,
                "status": "open", "created_at": utc_now()}
        self._write(NOTES_FILE, [*notes, item])
        return item

    def resolve_note(self, note_id: str, status: str) -> dict:
        if status not in {"open", "done", "dismissed"}:
            raise BurrowlError("INVALID_ARGUMENT", "status 只能是 open / done / dismissed")
        notes = self.notes()
        for note in notes:
            if note["id"] == note_id:
                note["status"] = status
                self._write(NOTES_FILE, notes)
                return note
        raise BurrowlError("NOT_FOUND", "笔记不存在", {"id": note_id})
