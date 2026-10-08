"""溯源与影响（只读视图，不改任何复核状态）：

    impact             某些内容变化后，哪些下游需要重新审阅（导航边只提示更新，supports/input 才算正式依赖）
    question_validity  已解决的问题，其答案依据现在是否仍成立（保留历史"已解决"状态，只报告需复核）
    lineage            沿依据追到实际可访问的原件身份（同一原件只算一次；来源数≠独立的科学支持数）
"""
from collections import defaultdict, deque

from ...core.errors import BurrowlError
from ...core.hashing import canonical_hash
from .claims import DEPENDENT, ClaimGraph, issue, iter_refs
from .context import Storage


def _readable(graph: ClaimGraph, target: str, cache: dict[str, bool]) -> bool:
    """影响路径里的身份也受可见性约束：撤权的记录连 ID 都不通过路径泄露。"""
    if target not in cache:
        cache[target] = not any(e["code"] in {"ACCESS_DENIED", "UNSAFE_PATH"} for e in graph.access_errors(target))
    return cache[target]


def impact(storage: Storage, changed_ids: list[str]) -> dict:
    if not changed_ids or any(not isinstance(t, str) or not t for t in changed_ids):
        raise BurrowlError("INVALID_ARGUMENT", "changed_ids 必须是非空的规范身份列表")
    graph = ClaimGraph(storage)
    visible: dict[str, bool] = {}
    if any(not _readable(graph, t, visible) for t in changed_ids):
        raise BurrowlError("ACCESS_DENIED", "所选变化来源不在当前可读范围")
    reverse: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for rid, record in graph.records.items():
        if not _readable(graph, rid, visible):
            continue
        payload = {k: v for k, v in record["payload"].items() if k != "claims"}
        for ref in iter_refs({"sources": record["sources"], "payload": payload}):
            reverse[ref["target_id"]].append((rid, ref["relation"]))
        for claim in record["payload"].get("claims", []):
            for ref in claim["evidence_refs"]:
                reverse[ref["target_id"]].append((claim["claim_id"], ref["relation"]))
            reverse[rid].append((claim["claim_id"], "input"))       # 记录内容变了，内嵌结论的复核绑定失效
            reverse[claim["claim_id"]].append((rid, "references"))  # 结论复核变化只影响容器的展示
    queue = deque((t, True, [t]) for t in changed_ids)
    visited, results = set(), {}
    while queue:
        target, formal, path = queue.popleft()
        if (target, formal) in visited:
            continue
        visited.add((target, formal))
        for dependent, relation in sorted(reverse[target]):
            if dependent in path or not _readable(graph, dependent, visible):
                continue
            propagated = formal and relation in DEPENDENT
            result = results.setdefault(dependent, {"canonical_id": dependent, "needs_review": True,
                                                    "formal_dependency": False, "paths": []})
            result["formal_dependency"] |= propagated
            if (next_path := path + [dependent]) not in result["paths"]:
                result["paths"].append(next_path)
            queue.append((dependent, propagated, next_path))
    return {"changed_ids": changed_ids, "affected": [results[k] for k in sorted(results)],
            "note": "needs_review 只是审阅候选；formal_dependency=true 表示经由 supports/input 传递", "writes": 0}


def question_validity(storage: Storage, question_id: str, *, scope: str | None = None) -> dict:
    graph = ClaimGraph(storage)
    record = graph.records.get(question_id)
    if not record or record["kind"] != "question":
        raise BurrowlError("NOT_FOUND", "问题记录不存在", {"record_id": question_id})
    if not _readable(graph, question_id, {}):
        raise BurrowlError("ACCESS_DENIED", "所选问题不在当前可读范围")
    errors = []
    status = record["payload"]["status"]
    if status == "resolved":
        for ref in record["payload"]["resolution_refs"]:
            try:
                answer = graph._reference(ref)
                if answer and answer in graph.claims:
                    errors.extend(dict(f, path=[*f["path"], question_id]) for f in graph.analyze(answer, scope))
            except BurrowlError as exc:
                errors.append(issue(exc.code, ref["target_id"], exc.message, [ref["target_id"], question_id]))
    return {"record_id": question_id, "revision": record["revision"], "status": status,
            "needs_review": status == "resolved" and bool(errors), "errors": errors, "writes": 0}


def lineage(storage: Storage, target_ids: list[str]) -> dict:
    graph = ClaimGraph(storage)
    queue = deque(({"target_id": t}, [t]) for t in target_ids)
    seen, sources, missing = set(), {}, []
    while queue:
        pinned, path = queue.popleft()
        tid = pinned["target_id"]
        key = (tid, pinned.get("revision"), pinned.get("sha256"))
        if key in seen:
            continue
        seen.add(key)
        if failures := graph.access_errors(tid, pinned.get("revision")):
            missing.extend({"target_id": tid, "code": e["code"], "path": path} for e in failures)
            continue
        refs: list[dict] = []
        if tid in graph.claims:
            claim, record = graph.claims[tid]
            refs = [*claim["evidence_refs"], *record["sources"]]
        elif tid in graph.records:
            record = graph._revision(tid, pinned.get("revision"))
            refs = list(record["sources"])
            if record["kind"] == "source":
                refs.append(record["payload"]["source_ref"])
            elif record["kind"] in {"narrative", "experience", "overview"}:
                refs += [r for c in record["payload"]["claims"] for r in c["evidence_refs"]]
        elif not graph.index.find(tid):
            missing.append({"target_id": tid, "code": "UNRESOLVED_REFERENCE", "path": path})
        for ref in refs:
            if ref["target_kind"] == "file":
                try:
                    source = storage.sources.resolve(ref["target_id"])
                except BurrowlError as exc:
                    missing.append({"target_id": ref["target_id"], "code": exc.code, "path": path})
                    continue
                identity = canonical_hash({"path": str(source.path), "sha256": ref["sha256"]})
                item = sources.setdefault(identity, {"source_identity": identity, "source_ids": [],
                                                     "sha256": ref["sha256"], "paths": []})
                if ref["target_id"] not in item["source_ids"]:
                    item["source_ids"].append(ref["target_id"])
                item["paths"].append([*path, ref["target_id"]])
            elif ref["target_kind"] in {"record", "claim"}:
                queue.append((ref, [*path, ref["target_id"]]))
    return {"source_count": len(sources), "sources": [sources[k] for k in sorted(sources)], "missing": missing,
            "meaning": "可访问的不同原件身份数；不推断独立性，也不等于科学支持数"}
