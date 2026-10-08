"""显式字段条件核对（重排特征）：只在已固定回读的正文里找"字段：值"句或表格行。

不推断自然语言条件；复杂范围、条件句、缺单位一律 unknown。结果只影响排序，不代表工程适用性已复核。
"""
import math
import operator as ops
import re
from copy import deepcopy

from ..core.errors import BurrowlError

_OPS = {"eq": ops.eq, "ne": ops.ne, "lt": ops.lt, "le": ops.le, "gt": ops.gt, "ge": ops.ge}
_UNITS = {"°C": ("temperature", 1, 273.15), "℃": ("temperature", 1, 273.15), "K": ("temperature", 1, 0),
          "Pa": ("pressure", 1, 0), "kPa": ("pressure", 1000, 0), "MPa": ("pressure", 1_000_000, 0)}
_NUMBER = re.compile(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([^\s\d]+)?")
_HEDGES = re.compile(r"(?:或者|以及|可能|至少|至多|仅当|如果|或|且)")


def _line(value: object, bound: int) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= bound and all(ord(c) >= 32 for c in value)


def validate_conditions(raw: list | None) -> list[dict]:
    """最多 16 条显式条件：{id, kind: literal|numeric, field, expected, operator, unit?}。"""
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > 16:
        raise BurrowlError("INVALID_ARGUMENT", "条件最多 16 条")
    result, ids = [], set()
    for item in raw:
        if not isinstance(item, dict) or set(item) - {"id", "kind", "field", "expected", "operator", "unit"}:
            raise BurrowlError("INVALID_ARGUMENT", "条件字段无效")
        out = deepcopy(item)
        if not _line(out.get("id"), 80) or not _line(out.get("field"), 80) or out["id"] in ids:
            raise BurrowlError("INVALID_ARGUMENT", "条件 id/field 必须是单行短文本且 id 不重复")
        ids.add(out["id"])
        out["id"], out["field"] = out["id"].strip(), out["field"].strip()
        if out.get("kind") not in ("literal", "numeric") or out.get("operator") not in _OPS:
            raise BurrowlError("INVALID_ARGUMENT", "不支持的条件类型或运算符")
        if out["kind"] == "literal":
            if out["operator"] not in ("eq", "ne") or not _line(out.get("expected"), 160) or "unit" in out:
                raise BurrowlError("INVALID_ARGUMENT", "文本条件只能用 eq/ne，且不带单位")
            out["expected"] = out["expected"].strip()
        elif type(out.get("expected")) not in (int, float) or not math.isfinite(out["expected"]):
            raise BurrowlError("INVALID_ARGUMENT", "数值条件的期望值必须是有限数")
        if "unit" in out and (not _line(out["unit"], 24) or any(c.isspace() for c in out["unit"])):
            raise BurrowlError("INVALID_ARGUMENT", "单位必须是短的无空白文本")
        result.append(out)
    return result


def _judge(condition: dict, value: str) -> bool | None:
    expected, op = condition["expected"], condition["operator"]
    if condition["kind"] == "literal":
        negated = re.match(r"^(?:不是|不等于|非|not\s+)(.+)$", value, re.IGNORECASE)
        observed = negated[1].strip() if negated else value
        if observed.endswith(".") or not re.fullmatch(r"[\w./+\-]+", observed) or _HEDGES.search(observed):
            return None
        if negated:
            return (op == "ne") if observed == expected else None
        return (observed == expected) if op == "eq" else (observed != expected)
    match = _NUMBER.fullmatch(value)
    if not match:
        return None
    observed, unit, target = float(match[1]), match[2] or "", condition.get("unit", "")
    if unit != target:
        source, goal = _UNITS.get(unit), _UNITS.get(target)
        if source is None or goal is None or source[0] != goal[0]:
            return None
        observed, expected = observed * source[1] + source[2], expected * goal[1] + goal[2]
    if math.isclose(observed, expected, rel_tol=1e-12, abs_tol=1e-12):
        observed = expected
    return _OPS[op](observed, expected)


def evaluate(conditions: list[dict], evidence_parts: list[dict]) -> list[dict]:
    """evidence_parts: [{text, ref, role}]。返回每条条件的 satisfied / conflict / unknown 及原文依据。"""
    out = []
    for condition in validate_conditions(conditions):
        field = re.escape(condition["field"])
        line = re.compile(r"^\s*(?:[-*]\s+)?" + field + r"\s*[:：=]\s*(.+?)\s*$")
        table = re.compile(r"^\s*\|\s*" + field + r"\s*\|\s*([^|]+)\s*\|\s*$")
        evidence, judgments = [], []
        for part in evidence_parts:
            if not isinstance(part, dict) or not isinstance(part.get("text"), str) or not part.get("ref"):
                continue
            for sentence in re.split(r"[\n\r；;。]", part["text"]):  # 不跨句绑定相邻字段的值
                match = line.fullmatch(sentence) or table.fullmatch(sentence)
                if match:
                    judgment = _judge(condition, match[1].strip())
                    judgments.append(judgment)
                    evidence.append({"ref": deepcopy(part["ref"]), "quote": sentence.strip(),
                                     "status": "unknown" if judgment is None else
                                     "satisfied" if judgment else "conflict"})
        known = {j for j in judgments if j is not None}
        ambiguous = len(known) > 1
        status = "unknown" if not known or ambiguous or None in judgments else ("satisfied" if True in known else "conflict")
        out.append({"id": condition["id"], "status": status, "evidence": evidence,
                    "reason": "ambiguous" if ambiguous else "explicit_field" if status != "unknown" else "insufficient_evidence"})
    return out
