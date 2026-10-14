"""记录契约说明：把 JSON Schema 整理成 AI 易读的字段表，并附经校验的示例草案。

供 memory.contract 使用。字段表只展开到 AI 写草案需要的深度；完整约束仍以 memory.schema.json 为准。
R20 D-02：每个草案字段标为 required / optional / conditional 三类之一（服务端字段另列，不要写），
由 schema 的 required 加下面的默认值表生成，一致性由 tests/service/test_contract_fill.py 锁住。
"""
import json
from functools import cache
from importlib.resources import files
from typing import Any

from . import predicates
from .clock import TIME_RULE, is_time_key
from .errors import BurrowlError
from .records import LEVELS, SERVER_FIELDS
from .schema import current_versions, defs

# 由专用操作生成、不应由 AI 直接 put_record 的类型及其入口
INTERNAL_KINDS = {"review": "review_submit / review_batch", "policy": "memory_commit 的 set_policy 操作",
                  "consolidation": "memory_consolidate_apply", "feedback": "memory_feedback",
                  "representation": "（保留类型，当前检索不使用）"}
# 服务端维护、草案里不要写的字段（SERVER_FIELDS 写了会被拒绝；level / schema_version 由类型决定）
SYSTEM_FIELDS = SERVER_FIELDS | {"level", "schema_version"}
# 省略时服务端补的值：schema 里必填、但 AI 可以不写（service.prepare、records.normalize_draft 负责补）
DEFAULTED = {
    "owner_id": "取提交的 owner_id",
    "discovery": "按记录类型取默认（知识记录全库可见，工作状态只在本对象可见）；修订时沿用原值",
    "sensitivity": "新记录为 internal；修订时沿用原值",
    "keywords": "[]",
    "provenance_gap": "null",
    "handle": "按标题生成",
}
# 视情况必须写的字段：说明什么时候要写
CONDITIONAL = {
    "change_reason": "修订已有记录时写为什么改（重新激活、状态转换时必填，否则取 record_reason）；新建时不写",
}
COMMON_NOTES = {
    "owner_id": "可以省略；写了就必须与提交请求的 owner_id 相同（一次提交只写一个对象）",
    "title": "能单独看懂的标题",
    "keywords": "检索关键词；技术名词同时给中文和英文，型号和数值原样保留",
    "body_markdown": "正文（Markdown，可用 $...$ 公式）。L1 detail、document、document_section 必须为空字符串。"
                     "可写链接：[[概念]]、[[@记录handle]]、[[@对象短名/handle]]、[[@handle#块]]、[[!结论短名]]，"
                     "提交时由服务解析并固定版本（见 docs/GLOSSARY 的“链接”）",
    "handle": "可选。记录在本对象内的可读短名，供 [[@handle]] 链接使用；不填时按标题自动生成，创建后不可修改。"
              "不能含空格和 / # | [ ] @ !",
    "record_reason": "为什么保存这一条",
    "sensitivity": "public / internal / confidential / restricted；restricted 不进索引、不发布、不被引用",
    "sources": "本条依据的固定引用列表（见 Ref）；正文就近用 [[@短名#块|出处]] 对应这些依据，"
               "不能只填列表。正文不要写来源 ID 或哈希；原件引用登记它的 L0 短名",
    "provenance_gap": "依据缺口的说明；没有缺口不写（即 null）",
    "discovery": ("一般不写：知识记录（L1–L4、关联、概念页）默认 workspace_summary（全库检索可见），目标、检查点、"
                  "路线、问题等工作状态默认 owner_only（只在指定该对象时可见）；对象策略显式设置时以对象为准；"
                  "修订时不写则沿用原值。只有需要保密时才写 owner_only"),
    "payload": "各类型自己的结构化字段，见 payload_fields",
    "change_reason": "为什么改（修订说明）",
}
PAYLOAD_DEFAULTED = {"missing_refs"}  # records.normalize_draft 补 []
TIME_FORMAT = "带时区的时间（如 2026-10-13T16:00:00+08:00 或 2026-10-13T08:00:00Z，保存时换算为 UTC）"
PAYLOAD_LEGEND = ("payload_fields 中标“（可选）”的可以不写，其余都必须写（列表可为空列表，可为 null 的写 null）；"
                  "名称以 _at 结尾的字段与 valid_from / valid_until 都是时间：" + TIME_RULE + "，不能只写日期或不带时区")
WRITE_WITH = ("memory_commit 的 operations 中：新建 {op:'put_record', client_key, draft}；"
              "修订 {op:'put_record', record_id, expected_revision（当前修订号，memory_inspect 可见）, draft（完整的新内容）}。"
              "client_key 与 record_id 只能二选一；同批新记录之间用 {client_key: 'x'} 引用")
REF_NOTE = ("Ref = {target_kind: record|file|owner|claim, target_id, revision, sha256, locator, relation, note（可选）}。"
            "record：revision 与 sha256 取被引记录的 revision 与 record_hash（memory_inspect 可见）；"
            "file：revision=null，sha256 取 sources_list 中登记的指纹；owner（如 Run）：sha256 为业务卡指纹。"
            "relation 按判断问题选，按写在哪里只用这几个："
            "结论与 L2 阶段的 evidence_refs → supports（能证明）/ contradicts（是反例）/ input（作为输入的数据）；"
            "sources 与其他固定引用 → 以上三个或 references（只是参考）。"
            "\"值得放在一起看\"用 relations_decide 写关联（related_to / same_problem / same_failure_mode / analogous_to），"
            "不是证据；\"A 会影响 B\"写成结论。其他关系名仍可读取，但提交时会返回 warning。"
            "note 写这份材料的哪部分、怎样支持或反驳：contradicts（反例）必须写，否则返回 warning；supports 建议写。")


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
        return {(k if k in required else f"{k}（可选）"): _time(k, _describe(v, all_defs, depth - 1))
                for k, v in schema["properties"].items()}
    if isinstance(kind, list):
        return " 或 ".join(kind)
    text = kind or "任意"
    return f"{text}（非空）" if schema.get("minLength") else text


def _time(name: str, text: Any) -> Any:
    """时间字段的写法（records.normalize_draft 换算、validate 检查）在说明里直接写出来，不让 AI 只看到 string。"""
    if is_time_key(name) and isinstance(text, str):
        return text.replace("string（非空）", TIME_FORMAT).replace("string", TIME_FORMAT)
    return text


@cache
def _example(kind: str) -> dict | None:
    path = files("burrowl.core.schema") / "examples" / f"{kind}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _draft_schema(kind: str) -> dict:
    versions = current_versions()
    if kind not in versions:
        raise BurrowlError("INVALID_ARGUMENT", "未知的记录类型", {"kind": kind, "kinds": sorted(versions)})
    camel = "".join(p.title() for p in kind.split("_"))
    return defs()[f"{camel}DraftV{versions[kind]}"]


def fill(name: str, required: set[str]) -> str:
    """一个草案顶层字段对 AI 而言是否必须写：required / optional / conditional。"""
    if name in CONDITIONAL:
        return "conditional"
    return "required" if name in required and name not in DEFAULTED else "optional"


def draft_fields(kind: str) -> dict[str, dict]:
    draft = _draft_schema(kind)
    required = set(draft.get("required", []))
    fields = {}
    for name in draft["properties"]:
        if name in SYSTEM_FIELDS:
            continue
        entry = {"fill": fill(name, required), "note": CONDITIONAL.get(name) or COMMON_NOTES.get(name, "")}
        if name in DEFAULTED:
            entry["default"] = DEFAULTED[name]
        fields[name] = entry
    return fields


def field_notes(kind: str) -> dict[str, str]:
    """报错时给缺失字段附的说明；未知类型返回空表。"""
    try:
        return {name: entry["note"] for name, entry in draft_fields(kind).items() if entry["note"]}
    except BurrowlError:
        return {}


def _payload_fields(draft: dict, all_defs: dict) -> Any:
    described = _describe(draft["properties"]["payload"], all_defs, depth=3)
    if isinstance(described, dict):
        described = {(f"{k}（可选）" if k in PAYLOAD_DEFAULTED else k): v for k, v in described.items()}
    return described


def _has_claims(draft: dict, all_defs: dict) -> bool:
    payload = draft["properties"]["payload"]
    if "$ref" in payload:
        payload = all_defs[payload["$ref"].rsplit("/", 1)[-1]]
    return "claims" in (payload.get("properties") or {})


def contract(kind: str) -> dict[str, Any]:
    versions = current_versions()
    draft = _draft_schema(kind)
    all_defs = defs()
    example = _example(kind)
    return {"kind": kind, "level": LEVELS.get(kind), "schema_version": versions[kind],
            "write_with": INTERNAL_KINDS.get(kind, WRITE_WITH),
            "guidance": example["note"] if example else "",
            "citation_guidance": "写完事实、公式、数字或判断就就近引用，再核对 sources / evidence_refs。"
                                 "共同原文出处可在 L1 块开头引用 L0，依赖块用 requires_block_ids 带出处。"
                                 "L2–L4 链接具体 L1 块或结论；只有概念链接、展开字段或来源列表不算就近引用。"
                                 "导入 L1 默认逐字保留原文与原语言；排版修复以外的总结、改写和矛盾分析放 L2–L4，"
                                 "标原文主张或 AI 归纳；L2 只写真实过程。缺口如实说明，不凑引用。"
                                 "INLINE_CITATION_MISSING 只提示结构性漏引，没有 warning 也须逐段自查。",
            "draft_fields": draft_fields(kind), "do_not_write": sorted(SYSTEM_FIELDS & set(draft["properties"])),
            "payload_legend": PAYLOAD_LEGEND,
            "payload_fields": _payload_fields(draft, all_defs),
            "ref": REF_NOTE, **({"claim_relation": predicates.guide()} if _has_claims(draft, all_defs) else {}),
            "example_operation": example["operation"] if example else None,
            "see_also": "skills/RECORDING.md（逐层写作要求）"}


def example_operation(kind: str) -> dict:
    """契约示例中的提交操作（经 tests/service/test_draft_examples.py 校验）。"""
    example = _example(kind)
    if example is None:
        raise BurrowlError("NOT_FOUND", "该类型没有示例草案", {"kind": kind})
    return json.loads(json.dumps(example["operation"]))


def kinds() -> list[dict[str, Any]]:
    versions = current_versions()
    return [{"kind": k, "level": LEVELS.get(k), "schema_version": v, "written_by": INTERNAL_KINDS.get(k, "put_record"),
             "summary": (_example(k) or {}).get("note", "")} for k, v in sorted(versions.items())]
