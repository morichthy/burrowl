"""记忆记录的身份、指纹与校验（每类记录只接受现行版本）。

层级：source=L0，detail=L1，narrative=L2，experience=L3，overview=L4；
document / document_section 及工作流类记录 level=null。层级不是可信等级。
校验是纯函数：需要读取引用时由服务层传入 resolver，本模块不访问文件。
"""
import copy
import re
from collections.abc import Callable
from typing import Any

from .clock import is_utc
from .errors import BurrowlError, schema_error
from .facets import validate_knowledge_facets
from .hashing import canonical_hash, is_sha256
from .jsonschema import pointer, validate
from .schema import current_versions, defs
from .technical import validate_composition, validate_unit

LEVELS: dict[str, str | None] = {
    "source": "L0", "detail": "L1", "narrative": "L2", "experience": "L3", "overview": "L4",
    "document": None, "document_section": None, "question": None, "goal": None, "route": None,
    "checkpoint": None, "association": None, "representation": None, "policy": None,
    "consolidation": None, "feedback": None, "review": None, "wiki_page": None,
}
CONTENT_KINDS = ("source", "detail", "narrative", "experience", "overview", "document", "document_section")
# 参与 content_hash 的字段；审计字段（时间、作者、修订号）不在内。
# links（R12 D-09）是服务端解析正文链接的结果；没有链接时省略这个键，已有记录的指纹因此不变（D-27）。
# handle（D-28）不参与内容指纹（只进 record_hash），所以给旧记录补上 handle 不会让复核失效。
CONTENT_FIELDS = ("owner_id", "kind", "level", "title", "keywords", "body_markdown",
                  "payload", "sources", "provenance_gap", "discovery", "sensitivity", "links")
OPTIONAL_CONTENT_FIELDS = {"links"}
SERVER_FIELDS = {"record_id", "revision", "previous_revision", "record_hash", "content_hash",
                 "created_at", "created_by", "updated_at", "updated_by", "links"}
# 内容展开边：作者显式保存的固定引用，必须指向对应角色的记录。
LINK_ROLES = {"process_refs": "narrative", "technical_refs": "detail", "experience_refs": "experience"}
_RECORD_ID = re.compile(r"MEM-[A-Za-z0-9][A-Za-z0-9-]*")
_WIKI_SLUG = re.compile(r"[a-z][a-z0-9-]*/[a-z0-9][a-z0-9-]*")
HANDLE_MAX = 64
HANDLE_FORBIDDEN = set("/#|[]@!")  # 这些字符在链接写法里有语法含义（§4.3）
# 结论 relation 的谓词：开放词表 + "x-" 前缀的自定义谓词（R12 §4.5）
PREDICATES = {"improves", "degrades", "causes", "prevents", "fails_on", "applies_to", "requires", "equivalent_to",
              "contradicts", "part_of"}
L1_CLAIM_KINDS = {"fact", "calculation"}  # D-07：L1 只写事实与计算类结论
_ORIGIN = re.compile(r"human|ai|unknown|tool:[A-Za-z0-9][A-Za-z0-9._-]*")

Resolver = Callable[[dict], Any]
SourceReader = Callable[[dict], str]


def content_hash(record: dict) -> str:
    return canonical_hash({key: record[key] for key in CONTENT_FIELDS
                           if key not in OPTIONAL_CONTENT_FIELDS or record.get(key)})


REF_SHAPE = "{target_kind: record|file|owner|claim, target_id, revision, sha256, locator, relation}"


def require_ref(value: Any, field: str) -> dict:
    """入口参数里的固定引用必须是完整的引用对象；只写一个 ID 字符串时给出参数错误，而不是在深处报内部错误。"""
    if not isinstance(value, dict) or value.get("target_kind") not in {"record", "file", "owner", "claim"}             or not isinstance(value.get("target_id"), str) or not value["target_id"]:
        raise BurrowlError("INVALID_ARGUMENT", f"{field} 必须是固定引用对象，不能只写 ID",
                           {"field": field, "expected": REF_SHAPE,
                            "hint": "记录引用取 memory.inspect 中的 revision 与 record_hash；检索命中可直接用其 ref"})
    return value


def handle_errors(handle: object) -> str | None:
    """handle 的书写规则（同一对象内唯一由服务层检查）；合规返回 None。"""
    if not isinstance(handle, str) or not handle.strip() or handle != handle.strip():
        return "Handle must be a non-empty string without leading or trailing spaces"
    if len(handle) > HANDLE_MAX:
        return f"Handle must be at most {HANDLE_MAX} characters"
    if HANDLE_FORBIDDEN & set(handle) or any(ch.isspace() for ch in handle):
        return "Handle cannot contain spaces or any of / # | [ ] @ !"
    return None


def record_hash(record: dict) -> str:
    return canonical_hash({k: v for k, v in record.items() if k != "record_hash"})


def definition_name(kind: str, part: str = "Draft") -> str:
    return "".join(p.title() for p in kind.split("_")) + f"{part}V{current_versions()[kind]}"


def _reference_errors(record: dict, resolver: Resolver | None) -> list[dict]:
    """固定引用格式 + 可选解析；时间字段必须是 UTC。"""
    errors: list[dict] = []

    def check_ref(ref: dict, path: str) -> None:
        revision = ref.get("revision")
        if revision is not None and (type(revision) is not int or revision < 1):
            errors.append(schema_error(path + "/revision", "Fixed revisions must be positive integers"))
        if ref["target_kind"] == "record":
            if type(revision) is not int or revision < 1:
                errors.append(schema_error(path + "/revision", "Record references need a positive fixed revision"))
        elif not is_sha256(ref.get("sha256")):
            errors.append(schema_error(path + "/sha256", "Reference needs a SHA-256 fingerprint"))
        if ref.get("sha256") is not None and not is_sha256(ref["sha256"]):
            errors.append(schema_error(path + "/sha256", "Invalid SHA-256"))
        if resolver is None:
            return
        try:
            resolved = resolver(ref)
            if resolved is None or resolved is False:
                raise LookupError
        except (LookupError, ValueError):
            # 回调异常可能含原文或私有路径，只返回安全文本。
            errors.append(schema_error(path, "Reference target could not be resolved", "UNRESOLVED_REFERENCE"))
            return
        match = re.fullmatch(r"/payload/(process_refs|technical_refs|experience_refs)/\d+", path)
        if match:
            expected = LINK_ROLES[match[1]]
            if not isinstance(resolved, dict) or resolved.get("kind") != expected:
                errors.append(schema_error(path, "Content link resolves to the wrong content role"))
            elif str(ref.get("locator") or "").startswith("block:"):
                block_id = ref["locator"][6:]
                if expected != "detail" or not any(
                        b.get("block_id") == block_id for b in resolved.get("payload", {}).get("blocks", [])):
                    errors.append(schema_error(path + "/locator", "Content link names a missing technical block"))

    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            if "target_kind" in value:
                check_ref(value, path)
            for key, item in value.items():
                if key.endswith("_at") and item is not None and not is_utc(item):
                    errors.append(schema_error(pointer(path, key), "Expected RFC3339 UTC timestamp ending Z"))
                walk(item, pointer(path, key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, pointer(path, index))

    walk(record, "")
    return errors


def _source_excerpt_errors(record: dict, read_source: SourceReader | None) -> list[dict]:
    """逐字导出必须与原文选区完全一致（不规范化空白与换行）。

    lines:N-M 为 1 起的闭区间（保留行尾）；chars:N-M 为 0 起、半开的 Unicode 码点区间。
    """
    payload = record["payload"]
    if record["kind"] != "source" or payload["acquisition"] == "original_link":
        return []
    ref = payload["source_ref"]
    if not ref["locator"]:
        return [schema_error("/payload/source_ref/locator", "An excerpt requires a precise locator")]
    if payload["acquisition"] != "verbatim_export":
        return []
    match = re.fullmatch(r"(lines|chars):(\d+)-(\d+)", ref["locator"])
    if not match or read_source is None:
        return [schema_error("/payload/source_ref/locator",
                             "Verbatim export requires readable original text and lines:N-M or chars:N-M")]
    try:
        original = read_source(ref)
    except (ValueError, LookupError, OSError):
        return [schema_error("/payload/source_ref", "Original text could not be read for comparison")]
    if not isinstance(original, str):
        return [schema_error("/body_markdown", "Source is not text; use original_link")]
    unit, first, last = match[1], int(match[2]), int(match[3])
    values: list[str] | str = original.splitlines(keepends=True) if unit == "lines" else original
    start, stop = (first - 1, last) if unit == "lines" else (first, last)
    if start < 0 or stop <= start or stop > len(values):
        return [schema_error("/body_markdown", "Source locator is outside original text")]
    if "".join(values[start:stop]) != record["body_markdown"]:
        return [schema_error("/body_markdown", "Body is not identical to the selected original text")]
    return []


def _domain_errors(record: dict, *, allow_review: bool) -> list[dict]:
    kind, p = record["kind"], record["payload"]
    errors: list[dict] = []

    def add(path: str, message: str, code: str = "INVALID_SCHEMA") -> None:
        errors.append(schema_error(path, message, code))

    for name in LINK_ROLES:
        for position, ref in enumerate(p.get(name, [])):
            if ref["target_kind"] != "record" or type(ref.get("revision")) is not int or ref["revision"] < 1 \
                    or not is_sha256(ref.get("sha256")):
                add(f"/payload/{name}/{position}", "Content links require fixed record revision and SHA-256")
    if not record["sources"] and not record["provenance_gap"]:
        add("/provenance_gap", "Explain missing provenance when sources are empty")
    if kind == "review":
        if not allow_review:
            add("/kind", "Review records require the dedicated review service")
        if not p["target_claim_id"].startswith("CLM-"):
            add("/payload/target_claim_id", "Review target must be a claim identity")
        if not is_sha256(p["target_content_hash"]):
            add("/payload/target_content_hash", "Review must bind a SHA-256 content fingerprint")
        if p["state"] == "accepted" and (not p["scope"] or not p["evidence_refs"]):
            add("/payload/evidence_refs", "Accepted review requires scope and evidence")
        if p["state"] == "superseded" and (not p["replacement_claim_id"]
                                           or p["replacement_claim_id"] == p["target_claim_id"]):
            add("/payload/replacement_claim_id", "Superseded review needs a different replacement claim")
    if "handle" in record and (problem := handle_errors(record["handle"])):
        add("/handle", problem)
    if kind == "wiki_page":
        if "slug" in p and not _WIKI_SLUG.fullmatch(p["slug"]):  # slug 可省略（R12 §4.6），提供时才校验
            add("/payload/slug", "Wiki slug must be '<type>/<name>' in lowercase letters, digits and hyphens")
        if len(p.get("category_path", [])) > 3:
            add("/payload/category_path", "Category path has at most 3 levels")
        status = p.get("status", "active")
        if (status == "merged") != ("merged_into" in p):
            add("/payload/merged_into", "merged_into is required exactly when status is merged")
        if (status == "split") != ("split_into" in p):
            add("/payload/split_into", "split_into is required exactly when status is split")
        for index, ref in enumerate(p["source_refs"]):
            if not is_sha256(ref.get("sha256")) and not (ref["target_kind"] == "record" and type(ref.get("revision")) is int):
                add(f"/payload/source_refs/{index}", "Wiki sources must be fixed references")
    if kind == "association" and "origin" in p and not _ORIGIN.fullmatch(p["origin"]):
        add("/payload/origin", "Origin must be human, ai, unknown or tool:<connector id>")
    if kind == "review":
        maturity = p.get("maturity")
        if maturity == "established" and (p["state"] != "accepted" or not p["evidence_refs"]
                                          or "evidence_basis" not in p):
            add("/payload/maturity", "Established maturity requires accepted state, evidence_basis and evidence")
        if "evidence_basis" in p and maturity != "established":
            add("/payload/evidence_basis", "evidence_basis only applies to established maturity")
        if "requested_maturity" in p and (p["state"] not in {"not-reviewed", "accepted"} or maturity == "established"):
            add("/payload/requested_maturity", "A maturity request goes with not-reviewed or accepted, below established")
    if kind == "association" and p["relation"] == "analogous_to":
        if not p["shared_structure"]:
            add("/payload/shared_structure", "Analogy requires a shared problem structure")
        if not p["transfer_limits"]:
            add("/payload/transfer_limits", "Analogy requires non-transferable boundaries")
    if kind == "question" and p["status"] == "resolved" and not p["resolution_refs"]:
        add("/payload/resolution_refs", "Resolved question requires fixed answer references", "INVALID_TRANSITION")
    if kind in ("question", "route") and p["status"] == "superseded" and (
            not p["replacement_ref"] or p["replacement_ref"]["target_id"] == record.get("record_id")):
        add("/payload/replacement_ref", "Superseded item requires a different replacement", "INVALID_TRANSITION")
    if kind == "route" and p["status"] == "blocked" and not p["blocker"]:
        add("/payload/blocker", "Blocked route requires a blocker", "INVALID_TRANSITION")
    if kind == "consolidation":
        remaining = {canonical_hash(r) for r in p["remaining_refs"]}
        for index, decision in enumerate(p["decisions"]):
            if decision["action"] == "defer" and canonical_hash(decision["target"]) not in remaining:
                add(f"/payload/decisions/{index}", "Deferred target must remain in remaining_refs")
    claims = p.get("claims", [])
    block_ids = {b["block_id"] for b in p.get("blocks", [])} if kind == "detail" else set()
    for index, claim in enumerate(claims):
        if not claim["claim_id"].startswith("CLM-") or len(claim["claim_id"]) == 4:
            add(f"/payload/claims/{index}/claim_id", "Claim ID must use CLM- prefix")
        if kind == "detail" and claim["kind"] not in L1_CLAIM_KINDS:
            add(f"/payload/claims/{index}/kind", "L1 claims must be fact or calculation")
        if "block_id" in claim and (kind != "detail" or claim["block_id"] not in block_ids):
            add(f"/payload/claims/{index}/block_id", "block_id must name a block of this technical unit")
        predicate = claim.get("relation", {}).get("predicate")
        if predicate is not None and predicate not in PREDICATES and not re.fullmatch(r"x-[a-z0-9_-]+", predicate):
            add(f"/payload/claims/{index}/relation/predicate", "Unknown predicate; use the vocabulary or an x- prefix")
    if len({c["claim_id"] for c in claims}) != len(claims):
        add("/payload/claims", "Duplicate claim identity")
    if "revision" in record:
        revision = record["revision"]
        if revision < 1 or record.get("previous_revision") != (None if revision == 1 else revision - 1):
            add("/previous_revision", "Revision chain must increment by one")
    if "record_id" in record and not _RECORD_ID.fullmatch(record["record_id"]):
        add("/record_id", "Record ID must be a safe MEM- identity")
    return errors


def normalize_draft(draft: dict) -> dict:
    """补齐不涉及服务端身份的默认值；不伪造 ID、时间或指纹。"""
    record = copy.deepcopy(draft)
    kind = record.get("kind")
    if kind in LEVELS:
        record.setdefault("schema_version", current_versions()[kind])
        record.setdefault("level", LEVELS[kind])
    record.setdefault("keywords", [])
    record.setdefault("provenance_gap", None)
    if isinstance(record.get("keywords"), list) and all(isinstance(v, str) for v in record["keywords"]):
        record["keywords"] = list(dict.fromkeys(record["keywords"]))
    if isinstance(record.get("payload"), dict):
        record["payload"].setdefault("missing_refs", [])
    return record


def validate_record(draft: dict, *, resolver: Resolver | None = None, read_source: SourceReader | None = None,
                    allow_review: bool = False) -> tuple[dict, list[dict]]:
    """返回 (规范化后的记录, 错误列表)。错误为空才可保存。"""
    if not isinstance(draft, dict):
        return draft, [schema_error("", "Record must be an object")]
    record = normalize_draft(draft)
    kind = record.get("kind")
    if kind not in LEVELS:
        return record, [schema_error("/kind", "Unknown record kind")]
    if record["schema_version"] != current_versions()[kind]:
        return record, [schema_error("/schema_version", "Only the current record version is accepted")]
    all_defs = defs()
    errors = validate(record, all_defs[definition_name(kind)], all_defs)
    if errors:
        return record, errors
    errors.extend(_domain_errors(record, allow_review=allow_review))
    errors.extend(schema_error("/payload" + p, m) for p, m in validate_knowledge_facets(record))
    if kind == "detail":
        errors.extend(schema_error("/payload" + p, m) for p, m in validate_unit(record))
    if kind in ("document", "document_section"):
        errors.extend(schema_error("/payload" + p, m) for p, m in validate_composition(record, resolver))
    errors.extend(_reference_errors(record, resolver))
    errors.extend(_source_excerpt_errors(record, read_source))
    if "content_hash" in record and record["content_hash"] != content_hash(record):
        errors.append(schema_error("/content_hash", "Stored hash does not match canonical content"))
    if "record_hash" in record and record["record_hash"] != record_hash(record):
        errors.append(schema_error("/record_hash", "Stored hash does not match canonical content"))
    return record, errors


# 每种提交操作允许的记录类型（put_record 不限）。
OPERATION_KINDS = {"set_policy": "policy", "transition_question": "question",
                   "decide_association": "association", "save_checkpoint": "checkpoint"}


def validate_commit_request(request: dict, *, allow_review: bool = False) -> list[dict]:
    """提交请求的结构预检；引用解析与批次身份由服务层完成。"""
    all_defs = defs()
    errors = validate(request, all_defs["CommitRequest"], all_defs)
    if errors:
        return errors
    for index, operation in enumerate(request["operations"]):
        path = f"/operations/{index}"
        draft = operation["draft"]
        for key in sorted(SERVER_FIELDS & draft.keys()):
            errors.append(schema_error(f"{path}/draft/{key}", "Server-owned field cannot be supplied in a request"))
        expected = OPERATION_KINDS.get(operation["op"])
        if (draft.get("kind") == "review" and not allow_review) or (expected and draft.get("kind") != expected):
            errors.append(schema_error(path + "/draft/kind", "Operation does not permit this record kind"))
        creating, updating = "client_key" in operation, "record_id" in operation
        if creating == updating or (updating and operation.get("expected_revision", 0) < 1) \
                or (creating and "expected_revision" in operation):
            errors.append(schema_error(path, "Use client_key for creation or record_id and positive expected_revision for update"))
    return errors
