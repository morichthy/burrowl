"""R13 归属判定：为一项新任务给出候选对象和证据（只读），由 AI 判断沿用、新建或问用户。

- 召回与筛选包与阅读会话第一轮是同一个实现（ReadingService.preview → screen；R13 D-03）：
  三路召回、按对象汇总、按配置重排窗口。不建立阅读会话，不读完整材料。
- 召回之外只补两类证据（R13 D-04）：候选的当前目标与最新检查点（只读这两条指针记录）；
  paths 给出时的来源重叠（读图投影的边）。程序不给建议动作。
- 不建对象、不写记录。归属在第一次写入时才确认（R13 D-05），见 RULES 第 2 节。
"""
import os
import time
from pathlib import Path

from ..core.errors import BurrowlError
from .indexing import IndexService
from .memory.context import Storage
from .reading.service import ReadingService

EXCERPT = 200


def route(storage: Storage, index: IndexService, reading: ReadingService, *, task: str,
          variants: list[dict] | None = None, protected_terms: list[str] | None = None,
          paths: list[str] | None = None, owner_hint: str | None = None, limit: int = 5) -> dict:
    started = time.perf_counter()
    if not isinstance(task, str) or not task.strip():
        raise BurrowlError("INVALID_ARGUMENT", "task 需要一两句话写清本次任务的目标和交付物")
    if not 1 <= limit <= 10:
        raise BurrowlError("INVALID_ARGUMENT", "limit 范围 1..10", {"limit": limit})
    registry = storage.owners.scan()
    concept_owner = index.concept_owner()
    # Run 不是工作目标，证据归到父对象；概念库不承接任务成果。
    parent_of = {o.owner_id: o.native.get("owner_id") for o in registry.readable("run")}
    routable = {o.owner_id: o for o in registry.readable() if o.owner_type != "run" and o.owner_id != concept_owner}

    def target(owner_id: str | None) -> str | None:
        owner_id = parent_of.get(owner_id, owner_id) if owner_id else None
        return owner_id if owner_id in routable else None

    evidence: dict[str, list[dict]] = {}

    def add(owner_id: str | None, signal: str, detail: str) -> None:
        if owner_id is not None:
            evidence.setdefault(owner_id, []).append({"signal": signal, "detail": detail})

    hint = None
    if owner_hint:
        hint = target(registry.get(owner_hint).require_readable().owner_id)
        if hint is None:
            raise BurrowlError("INVALID_ARGUMENT", "指定的对象不能承接工作（Run 没有父对象，或是概念库）",
                               {"owner_id": owner_hint})
        add(hint, "user_specified", "用户指定")

    screened = reading.preview(task, variants=variants, protected_terms=protected_terms)
    gaps = list(screened["gaps"])
    packets: dict[str, dict] = {}
    for owner_id in screened["owners"]:
        mapped = target(owner_id)
        if mapped and mapped not in packets:  # Run 的命中并入父对象时保留父对象自己的筛选包（若有）
            packets[mapped] = screened["packets"][owner_id]
    ranked = list(packets)

    if paths:
        exact, nearby, path_gaps = _match_paths(storage, paths)
        gaps += path_gaps
        _ready, graph_gaps = index.ready_owners("graph", "edges")
        gaps += [g | {"needed_for": "source_overlap"} for g in graph_gaps]
        for source_ids, signal, prefix in ((exact, "source_overlap", "引用了 "),
                                           (nearby, "directory_affinity", "引用了同目录的 ")):
            cited: dict[str, set[str]] = {}
            for edge in index.db.graph_edges(sorted(source_ids)):
                owner_id = target(edge["declared_owner"]) if edge["dst"] in source_ids else None
                if owner_id:
                    cited.setdefault(owner_id, set()).add(edge["dst"])
            for owner_id, srcs in sorted(cited.items()):
                add(owner_id, signal, prefix + "、".join(sorted(srcs)))

    # 用户指定与有路径证据的对象即使不在召回前 limit 名也补入；只为这些候选读指针记录（R13 D-07 成本有界）。
    chosen = list(dict.fromkeys(([hint] if hint else []) + ranked[:limit]
                                + [o for o in evidence if o != hint and o not in ranked[:limit]]))
    candidates = [_candidate(storage, routable[o], packets.get(o), evidence.get(o, [])) for o in chosen]
    return {"task": task, "candidates": candidates, "gaps": gaps, "diagnostics": screened["diagnostics"],
            "cost": {"elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                     "recall_ms": screened["cost"]["wall_ms"],
                     "pointer_reads": sum(1 for o in chosen if routable[o].persisted)}}


def _candidate(storage: Storage, owner, packet: dict | None, evidence: list[dict]) -> dict:
    snapshot = storage.store.read_head(owner, ("goal", "checkpoint")) if owner.persisted else None
    goal = _visible(snapshot.pointer("goal")) if snapshot else None
    checkpoint = _visible(snapshot.pointer("checkpoint")) if snapshot else None
    return {
        "owner": {k: v for k, v in owner.summary().items() if k in {"owner_id", "owner_type", "title", "native_path"}},
        "packet": {"score": packet["score"], "channels": packet["channels"], "windows": packet["windows"]}
        if packet else None,
        "goal": {"record_id": goal["record_id"], "objective": goal["payload"].get("objective", "")[:EXCERPT]}
        if goal else None,
        "checkpoint": {"record_id": checkpoint["record_id"], "title": checkpoint["title"],
                       "next_step": checkpoint["payload"].get("next_step", "")[:EXCERPT]} if checkpoint else None,
        "evidence": evidence,
    }


def _visible(record: dict | None) -> dict | None:
    return record if record and record["sensitivity"] != "restricted" else None


def _match_paths(storage: Storage, paths: list[str]) -> tuple[set[str], set[str], list[dict]]:
    """paths → (完全对应的来源 ID, 同目录的来源 ID, 缺口)。SRC- ID 直接采用；路径按登记位置比较（Windows 不分大小写）。"""
    entries = storage.sources.describe()
    located = [(e["source_id"], Path(e["location"])) for e in entries
               if e.get("enabled", True) and not str(e["location"]).startswith("<")]
    known_ids = {e["source_id"] for e in entries}
    exact, nearby, gaps = set(), set(), []

    def norm(path: Path) -> str:
        return os.path.normcase(os.path.normpath(str(path)))

    for raw in paths:
        if raw.startswith("SRC-"):
            if raw in known_ids:
                exact.add(raw)
            else:
                gaps.append({"channel": "paths", "reason": "unknown_source", "path": raw})
            continue
        path = Path(raw)
        if not path.is_absolute():
            gaps.append({"channel": "paths", "reason": "not_absolute", "path": raw})
            continue
        key = norm(path)
        hits = {sid for sid, loc in located if norm(loc) == key}
        if hits:
            exact |= hits
            continue
        folder = key if path.is_dir() else norm(path.parent)
        near = {sid for sid, loc in located if norm(loc.parent) == folder or norm(loc).startswith(folder + os.sep)}
        nearby |= near
        if not near:
            gaps.append({"channel": "paths", "reason": "unregistered", "path": raw})
    return exact, nearby - exact, gaps
