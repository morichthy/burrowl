"""提交时的引用缺口提示（R21 D-20）：只检查结构，不判断科学支持或逐句归因。

没有外部依据的自足记录不强迫凑引用。提示不写入记录、不改变指纹，也不阻断保存；
代码中的链接、概念链接和图片标记不能充当文字出处。
"""
from ...core.links import parse

KINDS = {"detail", "narrative", "experience", "overview", "document_section"}


def _has_basis(value: object) -> bool:
    """识别固定引用和同批 client_key 引用；不把缺口描述当作已有依据。"""
    if isinstance(value, dict):
        if "target_kind" in value and "target_id" in value or "client_key" in value:
            return True
        return any(_has_basis(v) for k, v in value.items() if k != "missing_refs")
    return isinstance(value, list) and any(_has_basis(v) for v in value)


def citation_warnings(operations: list[dict]) -> list[dict]:
    """有结构化依据却无可读出处时，按正文或 L1 块返回可直接修订的路径。

L1 的共同原文出处可以在一个块开头说明，再由 requires_block_ids 带给依赖块；
仅认领 origin/locators 不等于给读者提供了可点击的出处。
"""
    warnings = []
    for i, operation in enumerate(operations):
        draft = operation.get("draft") or {}
        if not isinstance(draft, dict):
            continue  # 输入错误交给正式契约校验，提醒不能把它改成内部错误。
        if draft.get("kind") not in KINDS or not _has_basis({"sources": draft.get("sources"),
                                                            "payload": draft.get("payload")}):
            continue
        payload = draft.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        base = f"/operations/{i}/draft"
        blocks = payload.get("blocks") or []
        if not isinstance(blocks, list) or any(not isinstance(b, dict) for b in blocks):
            continue
        if draft["kind"] == "detail":
            if any(not isinstance(b.get("block_id"), str) or
                   not isinstance(b.get("requires_block_ids", []), list) or
                   any(not isinstance(k, str) for k in b.get("requires_block_ids", [])) for b in blocks):
                continue
            cited = {b["block_id"] for b in blocks if _cited(b.get("markdown", ""))}
            # 依赖链可传递共同出处；迭代至稳定，无递归深度限制。
            while True:
                more = {b["block_id"] for b in blocks if set(b.get("requires_block_ids", [])) & cited}
                if more <= cited:
                    break
                cited |= more
            fields = [(f"{base}/payload/blocks/{j}/markdown", b.get("markdown", ""))
                      for j, b in enumerate(blocks) if b["block_id"] not in cited]
        elif draft["kind"] == "document_section":
            fields = [(f"{base}/payload/blocks/{j}/markdown", b.get("markdown", ""))
                      for j, b in enumerate(blocks) if b.get("type") == "prose"]
        else:
            fields = [(f"{base}/body_markdown", draft.get("body_markdown", ""))]
        for path, text in fields:
            if isinstance(text, str) and text.strip() and not _cited(text):
                warnings.append({"code": "INLINE_CITATION_MISSING", "path": path,
                                 "message": "已有固定依据，但这段正文没有可读的记录、块或结论引用；"
                                            "sources、origin 和展开字段不能代替就近出处。此提示不判断逐句支持关系",
                                 "action": "在事实、公式、数字或判断旁补 [[@短名#块|出处]]，并核对 sources / "
                                           "evidence_refs；原件先登记 L0。共同出处可在块开头引用并声明块依赖；"
                                           "没有依据的推断应标明缺口，不编造引用"})
    return warnings


def _cited(text: str) -> bool:
    """复用正文解析器：忽略代码，仅记录/块/结论链接算作可读出处。"""
    return isinstance(text, str) and any(link.kind in {"record", "block", "claim"} for link in parse(text))
