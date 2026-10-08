"""导出（Markdown / JSON / HTML）与检索使用反馈。导出不产生第二份规范记录，也不复制原件。"""
import html
import json
import uuid

from ...core.errors import BurrowlError
from ...core.hashing import canonical_hash
from ...core.technical import render_full
from . import raw_materials
from .context import RefResolver
from .service import MemoryService

FORMATS = {"markdown": "md", "json": "json", "html": "html"}


def export(memory: MemoryService, owner_id: str, *, record_ids: list[str] | None = None,
           exclude_ids: list[str] | None = None, fmt: str = "markdown", include_raw_materials: bool = False) -> dict:
    if fmt not in FORMATS:
        raise BurrowlError("INVALID_ARGUMENT", "导出格式只能是 markdown / json / html")
    view = memory.inspect(owner_id)
    selected = set(record_ids or view["records"]) - set(exclude_ids or [])
    if unknown := selected - view["records"].keys():
        raise BurrowlError("NOT_FOUND", "导出选择含不可读或不存在的记录", {"record_ids": sorted(unknown)[:20]})
    raw = raw_materials.listing(memory.storage, owner_id) if include_raw_materials else None
    # 原件清单已单独列出时，不再把 source 登记记录当正文重复导出
    records = [r for rid, r in view["records"].items() if rid in selected and not (raw and r["kind"] == "source")]
    manifest = {"owner_id": owner_id, "head": view["head"], "exclude_ids": exclude_ids or [],
                "records": [{k: r[k] for k in ("record_id", "revision", "content_hash")} for r in records]}
    if raw is not None:
        manifest["raw_materials"] = raw["items"]
    if fmt == "json":
        content = json.dumps({"manifest": manifest, "records": records}, ensure_ascii=False, indent=2)
    else:
        parts = [f"# 记忆导出：{owner_id}"]
        if raw is not None:
            parts.append("## L0 原始材料清单（只含登记信息，不复制原件）\n\n" +
                         "\n".join(f"- {i['path']} · {i['status']} · {i['sha256'] or '未固定'}" for i in raw["items"]))
        for record in records:
            body = render_full(record) if record["kind"] == "detail" else record["body_markdown"]
            parts.append(f"## {record['title']}\n\n{record['record_id']} · r{record['revision']} · {record['kind']}"
                         f"\n\n{body}{_boundaries(record)}{_sources(record)}")
        content = "\n\n".join(parts)
        if fmt == "html":  # 不渲染原始 HTML：导出内容即使含脚本标记也只是惰性文本
            content = ('<!doctype html><html lang="zh"><meta charset="utf-8"><title>记忆导出</title>'
                       f"<body><pre>{html.escape(content)}</pre></body></html>")
    return {"filename": f"memory-export.{FORMATS[fmt]}", "content": content, "manifest": manifest, "writes": 0}


def _boundaries(record: dict) -> str:
    """经验的适用边界是正文之外最容易被误用的部分，导出时单独列出。"""
    if record["kind"] != "experience":
        return ""
    payload, rows = record["payload"], []
    for key, label in (("applicable", "适用"), ("prohibited", "不适用"), ("failure_modes", "失败模式"),
                       ("retry_conditions", "重试条件")):
        if payload.get(key):
            rows.append(f"**{label}**：" + "；".join(payload[key]))
    return "\n\n" + "\n\n".join(rows) if rows else ""


def _sources(record: dict) -> str:
    if not record["sources"]:
        return ""
    lines = [f"- [{i}] {r['target_kind']}:{r['target_id']}" + (f" r{r['revision']}" if r.get("revision") else "")
             + (f" · {r['sha256'][:12]}" if r.get("sha256") else "") + f" · {r['relation']}"
             + (f" · {r['locator']}" if r.get("locator") else "") for i, r in enumerate(record["sources"], 1)]
    return "\n\n**固定来源**\n\n" + "\n".join(lines)


def feedback(memory: MemoryService, owner_id: str, *, query_id: str, target: dict, label: str, note: str = "",
             adopted_in: dict | None = None, actor: dict, request_id: str | None = None) -> dict:
    """记录"检索结果是否有用/实际被采用"。采用次数不是科学复核。

    幂等：同一 request_id 的重试返回原回执（指纹绑定反馈内容，不受 HEAD 变化影响）。
    """
    if label == "outcome":
        if not adopted_in:
            raise BurrowlError("INVALID_ARGUMENT", "实际应用结果需引用后续 Run 或记录")
        if adopted_in.get("target_kind") not in {"owner", "record"} or (
                adopted_in["target_kind"] == "owner" and not str(adopted_in.get("target_id", "")).startswith("RUN-")):
            raise BurrowlError("INVALID_ARGUMENT", "adopted_in 必须指向实际应用它的 Run 或记录")
    resolver = RefResolver(memory.storage, memory.owners())
    resolved = resolver(target)
    if adopted_in:
        resolver(adopted_in)
    owner = memory.owners().get(owner_id)
    draft = {"owner_id": owner_id, "kind": "feedback", "title": "检索使用反馈", "body_markdown": "", "keywords": [],
             "sources": [target, *([adopted_in] if adopted_in else [])], "provenance_gap": None,
             "record_reason": note or "记录检索使用结果", "discovery": "owner_only",
             "sensitivity": resolved.get("sensitivity", "internal") if isinstance(resolved, dict) else "internal",
             "payload": {"query_id": query_id, "target": target, "label": label, "note": note, "adopted_in": adopted_in}}
    head = memory.snapshot(owner).commit_id if owner.persisted else None
    digest = canonical_hash({"feedback": [owner_id, query_id, target, label, note, adopted_in]})
    return memory.commit({"schema_version": 3, "request_id": request_id or str(uuid.uuid4()), "actor": actor,
                          "owner_id": owner_id, "expected_head": head,
                          "operations": [{"op": "put_record", "client_key": "feedback", "draft": draft}]},
                         hash_override=digest)
