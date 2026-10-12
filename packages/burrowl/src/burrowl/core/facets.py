"""可选的知识分面（knowledge_facets）领域校验。保存分类不等于复核。"""
import math

from .clock import TIME_RULE, is_utc, parse_utc
from .hashing import is_sha256

Errors = list[tuple[str, str]]


def validate_knowledge_facets(record: dict) -> Errors:
    facets = record["payload"].get("knowledge_facets")
    if facets is None:
        return []
    errors: Errors = []

    def add(path: str, message: str) -> None:
        errors.append(("/knowledge_facets" + path, message))

    def terms(values: list[str], path: str) -> None:
        if any(not v.strip() or v != v.strip() for v in values):
            add(path, "标签不能为空，首尾不能有空白")
        if len(set(values)) != len(values):
            add(path, "标签不能重复")

    def applicability(value: dict, path: str) -> None:
        terms(value["conditions"], path + "/conditions")
        terms(value["exclusions"], path + "/exclusions")
        if set(value["conditions"]) & set(value["exclusions"]):
            add(path, "同一条件不能既适用又排除")
        ends = []
        for name in ("valid_from", "valid_until"):
            raw = value[name]
            if raw is not None and not is_utc(raw):
                add(path + "/" + name, f"适用时间：{TIME_RULE}；收到 {raw!r}")
                raw = None
            ends.append(parse_utc(raw) if raw else None)
        if all(ends) and ends[0] >= ends[1]:
            add(path, "valid_from 必须早于 valid_until")

    def fixed(value: object, path: str) -> None:
        # 分类本身是证据断言，其引用必须已固定 SHA-256。
        if isinstance(value, dict):
            if "target_kind" in value and not is_sha256(value.get("sha256")):
                add(path + "/sha256", "依据引用必须写固定的 SHA-256")
            for key, child in value.items():
                fixed(child, f"{path}/{key}")
        elif isinstance(value, list):
            for position, child in enumerate(value):
                fixed(child, f"{path}/{position}")

    terms(facets["roles"], "/roles")
    if facets["roles"] and not facets["classification_basis"]:
        add("/classification_basis", "写了 roles 就要在 classification_basis 给出固定依据")
    if ("principle" in facets["roles"]) != (facets["principle_kind"] is not None):
        add("/principle_kind", "roles 含 principle 时必须同时写 principle_kind，反之亦然")
    applicability(facets["applicability"], "/applicability")
    for position, attempt in enumerate(facets["attempts"]):
        terms(attempt["conditions"], f"/attempts/{position}/conditions")
    for position, assessment in enumerate(facets["confidence"]):
        path = f"/confidence/{position}"
        if not assessment["assessed_by"].strip() or any(not assessment["method"][k].strip() for k in ("name", "version")):
            add(path, "置信度需要写评估者（assessed_by）和方法名称与版本")
        if assessment["level"] != "unknown" and not assessment["basis"]:
            add(path + "/basis", "给出置信等级时需要固定的评估依据（basis）")
        probability = assessment["calibrated_probability"]
        if probability is not None:
            if not math.isfinite(probability) or not 0 <= probability <= 1:
                add(path + "/calibrated_probability", "calibrated_probability 必须在 0 到 1 之间")
            if assessment["calibration_ref"] is None:
                add(path + "/calibration_ref", "给出数值概率时需要固定的校准依据（calibration_ref）")
        applicability(assessment["applicability"], path + "/applicability")
    fixed(facets, "")
    return errors
