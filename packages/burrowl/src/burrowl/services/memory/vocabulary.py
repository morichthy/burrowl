"""两层关系词表（R19 D-09）：AI 面对的推荐关系名按"写在哪里"缩小；超出推荐范围不拒绝，只返回 warning。
反例引用（contradicts）缺少 note 同样只返回 warning（R23 D-13）。

内部词表（全部关系名）仍由契约校验，旧数据照常读取；图投影按 index/graph.py 的统一边标准把关系名映射到类别。
AI 真正需要判断的只是类别边界：依据、矛盾、作为输入、只是参考、值得放在一起看。
"""
from typing import Any

EVIDENCE = ("supports", "contradicts", "input")                  # 结论与 L2 阶段的依据字段
REFERENCE = ("supports", "contradicts", "input", "references")   # 记录 sources 与其他固定引用
ASSOCIATION = ("related_to", "same_problem", "same_failure_mode", "analogous_to")
# 不检查的记录类型：文稿的章节与收录关系、复核、策略等由程序或专门操作写入
SKIP_KINDS = {"document", "document_section", "review", "policy", "consolidation", "feedback", "representation"}
GUIDE = "按判断问题选关系（RULES 第 6 节）：能证明 → supports；是反例 → contradicts；作为输入的数据 → input；只是参考 → references"


def relation_warnings(operations: list[dict]) -> list[dict]:
    """遍历本批草案中的固定引用与关联关系名，列出超出推荐范围的项（不拒绝，D-27）。"""
    warnings: list[dict] = []
    for index, operation in enumerate(operations):
        draft = operation.get("draft") or {}
        if draft.get("kind") in SKIP_KINDS:
            continue
        base = f"/operations/{index}/draft"
        for i, ref in enumerate(draft.get("sources") or []):
            _check(warnings, f"{base}/sources/{i}", ref, REFERENCE)
        payload = draft.get("payload") or {}
        if draft.get("kind") == "association":
            relation = payload.get("relation")
            if relation is not None and relation not in ASSOCIATION:
                warnings.append(_warning(f"{base}/payload/relation", relation, ASSOCIATION,
                                         "关联只表达导航关系；说不清时用 related_to。可以验证的判断写成结论"))
            payload = {k: v for k, v in payload.items() if k not in {"from", "to", "relation"}}
        _walk(warnings, f"{base}/payload", payload, evidence=False)
    return warnings


def _walk(warnings: list[dict], path: str, value: Any, *, evidence: bool) -> None:
    if isinstance(value, dict):
        if "target_kind" in value and "target_id" in value:
            _check(warnings, path, value, EVIDENCE if evidence else REFERENCE)
            return
        for key, item in value.items():
            if key != "missing_refs":
                _walk(warnings, f"{path}/{key}", item, evidence=evidence or key == "evidence_refs")
    elif isinstance(value, list):
        for i, item in enumerate(value):
            _walk(warnings, f"{path}/{i}", item, evidence=evidence)


def _check(warnings: list[dict], path: str, ref: Any, allowed: tuple[str, ...]) -> None:
    relation = ref.get("relation") if isinstance(ref, dict) else None
    if relation is not None and relation not in allowed:
        warnings.append(_warning(path, relation, allowed, GUIDE))
    if relation == "contradicts" and not (ref.get("note") or "").strip():  # R23 D-13：反例要说明反驳了什么
        warnings.append({"code": "EVIDENCE_NOTE_MISSING", "path": path, "relation": relation,
                         "message": "反例引用没有 note：写明这份材料的哪部分、怎样反驳这条判断，点开依据边时会显示",
                         "action": "在该引用中补 note 后修订记录"})


def _warning(path: str, relation: str, allowed: tuple[str, ...], message: str) -> dict:
    return {"code": "RELATION_NOT_RECOMMENDED", "path": path, "relation": relation, "recommended": list(allowed),
            "message": f"关系名 {relation} 不在此处的推荐范围（{'、'.join(allowed)}）。{message}；确有理由保留时，"
                       "在 record_reason 或 change_reason 中写明",
            "action": "改用推荐的关系名修订该记录，或在 record_reason / change_reason 写明保留理由"}
