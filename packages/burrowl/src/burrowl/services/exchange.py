"""交换包导出（R12 D-21、T-P3.2、§4.12；格式规范见 docs/reference/exchange-format.md）。

    <数据目录>/exports/<名称>/
      manifest.json      {format, generated_at, data_dir_id, items[{local_key, path, record_id, revision, sha256}], files{path: sha256}}
      vault/README.md    格式说明（本目录由 burrowl 生成，只读）
      vault/concepts/<概念名>.md、vault/records/<对象短名>/<handle>.md、vault/documents/<对象短名>/<handle>.md
      graph/nodes.jsonl、graph/edges.jsonl

交换包是派生数据，可以删除重建；不是真源（D-01）。导出前按敏感级别过滤（D-25，默认 confidential 及以上不导出），
依据链上有受限来源的记录也不导出。增量：按 manifest 比较，内容没变的文件不改写，消失的文件删除。
"""
import json
import re
from pathlib import Path
from typing import Any

from ..core.atomic import write_json_atomic
from ..core.clock import utc_now
from ..core.errors import BurrowlError
from ..core.hashing import canonical_hash, sha256_bytes
from .indexing import IndexService
from .memory.claims import BLOCKING, ClaimGraph
from .memory.context import Storage
from .render import SKIP_KINDS, Page

FORMAT = "burrowl-exchange/1"
EXPORTS_DIR = "exports"
SENSITIVITY_RANK = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
README = """# burrowl 交换包

本目录由 burrowl 生成（格式 {format}），**只读**：在这里的修改不会回到 burrowl，下次导出会被覆盖。

- `concepts/`：概念页（概念库中的 concept / entity 页面）。
- `records/<对象>/<handle>.md`：记忆记录。YAML 头的 `local_key`（记录 ID@修订）是回到 burrowl 固定版本的钥匙，
  外部系统必须原样保存。
- `documents/<对象>/<handle>.md`：文稿；技术内容用 `![[records/…#块]]` 嵌入，不复制正文。
- 链接 `[[路径|显示文字]]` 指向本库内的文件；目标没有导出时只保留文字。
- `<!-- burrowl:generated:start -->` 与 `<!-- burrowl:generated:end -->` 之间是自动生成的反链、关联与结论状态。
- 复核状态与成熟度只出现在 YAML 头和自动生成区块中，它们是导出时的状态，不是记录内容。
- `../graph/nodes.jsonl`、`../graph/edges.jsonl`：同一批内容的知识图（节点与边，边带类别与确定度）。
"""


def _sensitivity(owner, record: dict) -> str:
    return max(owner.sensitivity, record["sensitivity"], key=lambda v: SENSITIVITY_RANK.get(v, 3))


def collect(storage: Storage, *, owner_ids: list[str] | None = None, owner_types: list[str] | None = None,
            exclude_sensitivity: list[str] | None = None) -> list[dict]:
    """可以导出的记录（当前修订，已按敏感级别和依据链过滤）。文稿章节、复核等记录不单独成页，但仍进入图文件。"""
    excluded = set(exclude_sensitivity if exclude_sensitivity is not None else ["confidential"]) | {"restricted"}
    index = storage.owners.scan()
    graph = ClaimGraph(storage, index)
    out = []
    for owner in index.readable():
        if not owner.persisted or (owner_ids and owner.owner_id not in owner_ids) \
                or (owner_types and owner.owner_type not in owner_types):
            continue
        for record in storage.store.read_snapshot(owner).records.values():
            if _sensitivity(owner, record) in excluded:
                continue
            if any(e["code"] in BLOCKING for e in graph.access_errors(record["record_id"])):
                continue
            out.append(record)
    return out


def graph_files(index: IndexService, records: list[dict], paths: dict[str, str]) -> tuple[list[dict], list[dict]]:
    """与导出范围一致的节点和边：节点属于导出的记录，或是被引用的对象与来源；边的两端都在范围内。"""
    exported = {r["record_id"] for r in records}
    nodes = []
    for node in index.db.graph_nodes():
        if node["record_id"] in exported or node["node_type"] in {"owner", "source"}:
            local_key = None
            if node["record_id"] in exported and node["node_type"] in {"record", "concept"}:
                record = next(r for r in records if r["record_id"] == node["record_id"])
                local_key = f"{record['record_id']}@{record['revision']}"
            nodes.append({"id": node["node_id"], "type": node["node_type"], "title": node["title"],
                          "owner": node["owner_id"], "level": node["level"], "kind": node["kind"],
                          "domains": node["domains"], "review": node["review"], "maturity": node["maturity"],
                          "local_key": local_key,
                          "path": paths[node["record_id"]] + ".md" if node["record_id"] in paths else None})
    ids = {n["id"] for n in nodes}
    edges = [{"id": e["edge_id"], "src": e["src"], "dst": e["dst"], "category": e["category"], "type": e["type"],
              "certainty": e["certainty"], "state": e["state"], "declared_by": e["declared_by"], "derived": e["derived"]}
             for e in index.db.graph_all_edges() if e["src"] in ids and e["dst"] in ids]
    used = {x for e in edges for x in (e["src"], e["dst"])}
    nodes = [n for n in nodes if n["type"] not in {"owner", "source"} or n["id"] in used]  # 只留被引用的对象与来源
    ids = {n["id"] for n in nodes}
    edges = [e for e in edges if e["src"] in ids and e["dst"] in ids]
    return sorted(nodes, key=lambda n: n["id"]), sorted(edges, key=lambda e: e["id"])


def export(storage: Storage, index: IndexService, name: str, *, owner_ids: list[str] | None = None,
           owner_types: list[str] | None = None, exclude_sensitivity: list[str] | None = None,
           concept_owner: str | None = None) -> dict[str, Any]:
    if not _NAME.fullmatch(name or ""):
        raise BurrowlError("INVALID_ARGUMENT", "导出名称只能用字母、数字、点、下划线和连字符（不超过 64 个字符）")
    index.ensure_graph()
    eligible = collect(storage, owner_ids=owner_ids, owner_types=owner_types, exclude_sensitivity=exclude_sensitivity)
    records = [r for r in eligible if r["kind"] not in SKIP_KINDS]       # 单独成页的记录
    page = Page.build(storage, records, concept_owner=concept_owner, edges=index.db.graph_all_edges())
    files: dict[str, bytes] = {"vault/README.md": README.format(format=FORMAT).encode("utf-8")}
    items = []
    for record in sorted(records, key=lambda r: page.paths[r["record_id"]]):
        path = f"vault/{page.paths[record['record_id']]}.md"
        files[path] = page.render(record).encode("utf-8")
        items.append({"local_key": f"{record['record_id']}@{record['revision']}", "path": path,
                      "record_id": record["record_id"], "revision": record["revision"],
                      "sha256": record["record_hash"]})
    nodes, edges = graph_files(index, eligible, page.paths)
    files["graph/nodes.jsonl"] = "".join(json.dumps(n, ensure_ascii=False) + "\n" for n in nodes).encode("utf-8")
    files["graph/edges.jsonl"] = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in edges).encode("utf-8")

    target = storage.ws.path(f"{EXPORTS_DIR}/{name}")
    manifest_path = target / "manifest.json"
    previous = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    old_files = previous.get("files", {})
    written, unchanged = [], []
    for relative, data in files.items():
        digest = sha256_bytes(data)
        path = target / relative
        if old_files.get(relative) == digest and path.is_file() and sha256_bytes(path.read_bytes()) == digest:
            unchanged.append(relative)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        written.append(relative)
    deleted = []
    for relative in sorted(set(old_files) - set(files)):
        path = target / relative
        if path.is_file() and path.resolve().is_relative_to(target.resolve()):
            path.unlink()
            deleted.append(relative)
    body = {"format": FORMAT, "data_dir_id": canonical_hash(str(storage.ws.root))[:16],
            "filter": {"owner_ids": owner_ids, "owner_types": owner_types,
                       "exclude_sensitivity": exclude_sensitivity if exclude_sensitivity is not None else ["confidential"]},
            "items": items, "files": {k: sha256_bytes(v) for k, v in sorted(files.items())},
            "counts": {"records": len(items), "nodes": len(nodes), "edges": len(edges)}}
    if written or deleted or {k: v for k, v in previous.items() if k != "generated_at"} != body:
        write_json_atomic(manifest_path, {**body, "generated_at": utc_now()}, pretty=True)
    return {"path": str(target), "format": FORMAT, "written": len(written), "unchanged": len(unchanged),
            "deleted": len(deleted), **body["counts"],
            "note": "交换包是派生数据：可以删除重建；在其中的修改不会回到 burrowl"}


def export_path(storage: Storage, name: str) -> Path:
    return storage.ws.path(f"{EXPORTS_DIR}/{name}")
