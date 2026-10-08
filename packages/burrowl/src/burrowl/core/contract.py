"""记录契约说明：把 JSON Schema 整理成 AI 易读的字段表，并附经校验的示例草案。

供 memory.contract 使用。字段表只展开到 AI 写草案需要的深度；完整约束仍以 memory.schema.json 为准。
"""
import json
from functools import cache
from importlib.resources import files
from typing import Any

from .errors import BurrowlError
from .records import LEVELS
from .schema import current_versions, defs

# 由专用操作生成、不应由 AI 直接 put_record 的类型及其入口
INTERNAL_KINDS = {"review": "review.submit / review.batch", "policy": "memory.commit 的 set_policy 操作",
                  "consolidation": "memory.consolidate_apply", "feedback": "memory.feedback",
                  "representation": "（保留类型，当前检索不使用）"}
# 系统自动维护、草案里不要填写的字段
SYSTEM_FIELDS = {"record_id", "created_at", "created_by", "revision", "previous_revision", "updated_at",
                 "updated_by", "change_reason", "record_hash", "content_hash", "level", "schema_version"}
COMMON_NOTES = {
    "owner_id": "必须与提交请求的 owner_id 相同（一次提交只写一个对象）",
    "title": "能单独看懂的标题",
    "keywords": "检索关键词；技术名词同时给中文和英文，型号和数值原样保留",
    "body_markdown": "正文（Markdown，可用 $...$ 公式）。L1 detail、document、document_section 必须为空字符串",
    "record_reason": "为什么保存这一条",
    "sensitivity": "public / internal（默认）/ confidential / restricted；restricted 不进索引、不发布、不被引用",
    "sources": "本条依据的固定引用列表（见 Ref）；正文里的编号或 ID 要与这里对应",
    "provenance_gap": "依据缺口的说明；没有缺口填 null",
    "discovery": "可省略（取对象策略）。workspace_summary=全库检索可见；owner_only=只在指定该对象时可见",
    "payload": "各类型自己的结构化字段，见 payload_fields",
}
REF_NOTE = ("Ref = {target_kind: record|file|owner|claim, target_id, revision, sha256, locator, relation}。"
            "record：revision 与 sha256 取被引记录的 revision 与 record_hash（memory.inspect 可见）；"
            "file：revision=null，sha256 取 sources.list 中登记的指纹；owner（如 Run）：sha256 为业务卡指纹。"
            "relation 表示用途：supports/input/contradicts 是证据；references/contains/derived_from 等是展开或组织；"
            "相似、同类问题等导航关系用 association 记录，不是证据。")


def _describe(schema: dict, all_defs: dict, depth: int) -> Any:
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        if name in {"Ref", "SourceRef"}:
            return "Ref"
        return _describe(all_defs[name], all_defs, depth) if depth > 0 else name
    if "enum" in schema:
        return " | ".join(map(str, schema["enum"]))
    if "const" in schema:
        return f"固定为 {json.dumps(schema['const'], ensure_ascii=False)}"
    if "oneOf" in schema or "anyOf" in schema:
        options = [_describe(s, all_defs, depth) for s in schema.get("oneOf", schema.get("anyOf", []))]
        return " 或 ".join(o if isinstance(o, str) else json.dumps(o, ensure_ascii=False) for o in options)
    kind = schema.get("type")
    if kind == "array":
        item = _describe(schema.get("items", {}), all_defs, depth)
        least = schema.get("minItems", 0)
        return {"列表，元素": item, **({"至少": least} if least else {})}
    if kind == "object" and "properties" in schema:
        required = set(schema.get("required", []))
        return {(k if k in required else f"{k}（可选）"): _describe(v, all_defs, depth - 1)
                for k, v in schema["properties"].items()}
    if isinstance(kind, list):
        return " 或 ".join(kind)
    text = kind or "任意"
    return f"{text}（非空）" if schema.get("minLength") else text


@cache
def _example(kind: str) -> dict | None:
    path = files("burrowl.core.schema") / "examples" / f"{kind}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def contract(kind: str) -> dict[str, Any]:
    versions = current_versions()
    if kind not in versions:
        raise BurrowlError("INVALID_ARGUMENT", "未知的记录类型", {"kind": kind, "kinds": sorted(versions)})
    all_defs = defs()
    camel = "".join(p.title() for p in kind.split("_"))
    draft = all_defs[f"{camel}DraftV{versions[kind]}"]
    payload = draft["properties"]["payload"]
    required = set(draft.get("required", []))
    fields = {name: {"required": name in required, "note": COMMON_NOTES.get(name, "")}
              for name in draft["properties"] if name not in SYSTEM_FIELDS}
    example = _example(kind)
    return {"kind": kind, "level": LEVELS.get(kind), "schema_version": versions[kind],
            "write_with": INTERNAL_KINDS.get(kind, "memory.commit：{op:'put_record', client_key, draft}"),
            "guidance": example["note"] if example else "",
            "draft_fields": fields, "payload_fields": _describe(payload, all_defs, depth=3),
            "ref": REF_NOTE, "example_operation": example["operation"] if example else None,
            "see_also": "docs/RECORDING.md（逐层写作要求）"}


def kinds() -> list[dict[str, Any]]:
    versions = current_versions()
    return [{"kind": k, "level": LEVELS.get(k), "schema_version": v, "written_by": INTERNAL_KINDS.get(k, "put_record"),
             "summary": (_example(k) or {}).get("note", "")} for k, v in sorted(versions.items())]
