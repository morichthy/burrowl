"""结构化参数的形状（R20 D-03、D-09）：AI 直接编写的对象/列表参数在这里声明字段。

一份 JSON Schema 两种用途：
- 发布：内联后作为该参数的 JSON Schema 交给 MCP/HTTP，AI 填参数时就能看到字段、必填与枚举；
- 校验：registry.call 用 core.jsonschema 按同一份 schema 校验，错误一次列全、中文、带完整路径（D-05）。

记录相关的形状（Operation、Attribution、Ref、AssociationPayload）直接取自记录契约 memory.schema.json，
不另写一套；记录草案本身随 kind 变化，只发布为"对象 + 说明"，字段由 memory_contract(kind) 查询。
"""
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Annotated, Any

from pydantic import Field, WithJsonSchema

from ...core.schema import defs as record_defs


@dataclass(frozen=True)
class Shape:
    """附在参数注解上的校验形状。schema 可引用 defs 中的定义（$ref: #/$defs/X）。"""

    schema: dict
    defs: dict = field(default_factory=dict)


DRAFT_NOTE = ("记录草案（对象）。至少写 kind、title、body_markdown、payload、sources、record_reason；"
              "owner_id、sensitivity、discovery 可省略（由服务补上）；修订已有记录时写 change_reason。"
              "payload 的结构随 kind 不同，写之前用 memory_contract(kind) 查字段和示例")
DRAFT_STUB = {"type": "object", "description": DRAFT_NOTE}
REF_NOTE = ("固定引用 {target_kind, target_id, revision, sha256, locator, relation}，不能只写 ID。"
            "记录：revision 与 sha256 取 memory_inspect 的 revision 与 record_hash，或提交回执的 fixed_ref；"
            "文件：revision 写 null，sha256 取 sources_list 的指纹")


def _inline(node: Any, all_defs: dict, stubs: dict[str, dict]) -> Any:
    """展开本地 $ref（记录契约没有递归引用），stubs 中的定义换成说明性的占位（不展开）。"""
    if isinstance(node, list):
        return [_inline(v, all_defs, stubs) for v in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        name = node["$ref"].removeprefix("#/$defs/")
        target = deepcopy(stubs[name]) if name in stubs else _inline(all_defs[name], all_defs, stubs)
        return {**target, **{k: v for k, v in node.items() if k != "$ref"}}
    return {k: _inline(v, all_defs, stubs) for k, v in node.items() if k != "$defs"}


def shaped(py_type: Any, schema: dict, description: str, *, defs: dict | None = None, nullable: bool = False,
           examples: list | None = None) -> Any:
    """参数注解：pydantic 只校验外层类型（对象/列表），字段由 Shape 校验；发布的 JSON Schema 是内联后的 schema。"""
    all_defs = RECORD_DEFS if defs is None else defs
    published = _inline(schema, all_defs, {"RequestDraft": DRAFT_STUB})
    published = {**published, "description": description}
    if examples:
        published["examples"] = examples
    if nullable:
        published = {"anyOf": [published, {"type": "null"}], "description": description}
    return Annotated[py_type, Field(description=description), WithJsonSchema(published), Shape(schema, all_defs)]


def _record(name: str) -> dict:
    return {"$ref": f"#/$defs/{name}"}


def _obj(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


TEXT = {"type": "string", "minLength": 1}
TEXTS = {"type": "array", "items": TEXT}

# ---------- 记录契约中的形状 ----------
RECORD_DEFS = record_defs()
OPERATIONS = {"type": "array", "minItems": 1, "items": _record("Operation")}
ATTRIBUTION = _record("Attribution")
REF_LIST = {"type": "array", "items": _record("Ref")}


def association_payload() -> dict:
    """关联 payload：完整字段取自契约；快速添加只要 from、to、explanation（其余由服务补齐，见 associations.quick_payload）。"""
    payload = deepcopy(RECORD_DEFS["AssociationPayload"])
    payload["required"] = ["from", "to", "explanation"]
    return payload


ASSOCIATION_PAYLOAD = association_payload()

# ---------- 阅读与检索 ----------
VARIANTS = {"type": "array", "maxItems": 2, "items": _obj({
    "id": {**TEXT, "description": "可选的改写编号"},
    "language": {"enum": ["zh", "en", "other"]},
    "question": {**TEXT, "description": "等义改写的问句；型号、数值必须原样保留"},
    "lexical_terms": {**TEXTS, "description": "可选：词法检索用的关键词，不写按问句切词"},
}, ["language", "question"])}

FIELD_CONDITIONS = {"type": "array", "maxItems": 16, "items": _obj({
    "id": TEXT,
    "kind": {"enum": ["literal", "numeric"]},
    "field": {**TEXT, "description": "原文“字段：值”中的字段名"},
    "operator": {"enum": ["eq", "ne", "lt", "le", "gt", "ge"], "description": "literal 只能用 eq/ne"},
    "expected": {"type": ["string", "number"], "description": "literal 写文本，numeric 写数值"},
    "unit": {**TEXT, "description": "仅 numeric 可写，如 °C、kPa"},
}, ["id", "kind", "field", "operator", "expected"])}

JUDGMENTS = {"type": "array", "minItems": 1, "items": _obj({
    "owner_id": TEXT,
    "verdict": {"enum": ["relevant", "uncertain", "irrelevant"]},
    "reason": {**TEXT, "description": "依据窗口里的什么内容做出判断"},
    "digest": {**TEXT, "description": "原样抄筛选包的 digest"},
    "accepted_windows": {**TEXTS, "description": "仅 quick 策略：可作为笔记依据的窗口 entry_id"},
}, ["owner_id", "verdict", "reason", "digest"])}

EXPAND_FROM = {"type": "array", "maxItems": 3, "items": _obj({
    "ref": {**TEXT, "description": "本笔记 sources 中的一项"},
    "why": {**TEXT, "description": "为什么值得顺着关系去追"},
}, ["ref", "why"])}

# ---------- 维护、工作台笔记、复核 ----------
DECISIONS = {"type": "array", "minItems": 1, "items": _obj({
    "target_id": {**TEXT, "description": "计划条目的目标 ID"},
    "action": {"enum": ["retain", "revise", "resynthesize", "retract", "defer"]},
    "reason": TEXT,
    "draft": {"$ref": "#/$defs/RequestDraft", "description": "revise / resynthesize 时必填：完整的新草案"},
}, ["target_id", "action", "reason"])}

STEPS = {"type": "array", "minItems": 1, "maxItems": 10, "items": {"oneOf": [
    _obj({"op": {"enum": ["memory_commit", "memory.commit"]}, "owner_id": TEXT,
          "operations": OPERATIONS,
          "expected_head": {"type": ["string", "null"], "description": "该对象当前 HEAD；新对象为 null"}},
         ["op", "owner_id", "operations", "expected_head"]),
    _obj({"op": {"enum": ["relations_decide", "relations.decide"]}, "owner_id": TEXT,
          "payload": ASSOCIATION_PAYLOAD, "reason": TEXT},
         ["op", "owner_id", "payload"]),
]}}

REVIEW_ITEMS = {"type": "array", "minItems": 1, "items": _obj({
    "target_claim_id": TEXT,
    "state": {"enum": ["accepted", "disputed", "retracted", "superseded", "not-reviewed"]},
    "reason": TEXT,
    "scope": {"type": ["string", "null"]},
    "evidence_refs": REF_LIST,
    "replacement_claim_id": {"type": ["string", "null"]},
    "maturity": {"enum": ["conjecture", "preliminary", "replicated", "established", None]},
    "evidence_basis": {"enum": ["engineering_case", "proof", "authoritative_source", "replication", None]},
    "requested_maturity": {"enum": ["established", None]},
}, ["target_claim_id", "state", "reason"])}

# ---------- Run 与来源 ----------
RUN_FIELDS = {"type": "object", "additionalProperties": False, "properties": {
    "title": TEXT, "question": {"type": "string"},
    "conclusion": {"type": "string", "description": "只写执行摘要（成功或失败、产物、异常）"},
    "limitations": {"type": "array", "items": {"type": "string"}},
    "parameters": {"type": "object", "description": "本次执行的参数（键值）"},
    "status": {"enum": ["planned", "running", "succeeded", "failed", "abandoned"]},
    "keywords": {"type": "array", "items": {"type": "string"}},
    "parent_run_ids": {"type": "array", "items": TEXT},
}}

SOURCE_ITEMS = {"type": "array", "minItems": 1, "items": _obj({
    "source_id": {**TEXT, "description": "SRC- 开头的来源 ID"},
    "path": {**TEXT, "description": "工作目录等外部位置用绝对路径；数据目录内可用相对路径"},
    "title": {"type": "string"},
    "sensitivity": {"enum": ["public", "internal", "confidential", "restricted"]},
}, ["source_id", "path"])}


def shape_of(annotation: Any) -> Shape | None:
    """取出注解上的 Shape（没有则 None）。"""
    return next((m for m in getattr(annotation, "__metadata__", ()) if isinstance(m, Shape)), None)
