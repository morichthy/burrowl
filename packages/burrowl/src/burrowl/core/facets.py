"""可选的知识分面（knowledge_facets）领域校验。保存分类不等于复核。"""
import math

from .clock import is_utc, parse_utc
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
            add(path, "Facet labels must be nonblank and have no surrounding whitespace")
        if len(set(values)) != len(values):
            add(path, "Duplicate facet labels are not allowed")

    def applicability(value: dict, path: str) -> None:
        terms(value["conditions"], path + "/conditions")
        terms(value["exclusions"], path + "/exclusions")
        if set(value["conditions"]) & set(value["exclusions"]):
            add(path, "An applicability condition cannot also be excluded")
        ends = []
        for name in ("valid_from", "valid_until"):
            raw = value[name]
            if raw is not None and not is_utc(raw):
                add(path + "/" + name, "Applicability time must be RFC3339 UTC ending Z")
                raw = None
            ends.append(parse_utc(raw) if raw else None)
        if all(ends) and ends[0] >= ends[1]:
            add(path, "Applicability interval must have valid_from before valid_until")

    def fixed(value: object, path: str) -> None:
        # 分类本身是证据断言，其引用必须已固定 SHA-256。
        if isinstance(value, dict):
            if "target_kind" in value and not is_sha256(value.get("sha256")):
                add(path + "/sha256", "Facet evidence requires a fixed SHA-256 fingerprint")
            for key, child in value.items():
                fixed(child, f"{path}/{key}")
        elif isinstance(value, list):
            for position, child in enumerate(value):
                fixed(child, f"{path}/{position}")

    terms(facets["roles"], "/roles")
    if facets["roles"] and not facets["classification_basis"]:
        add("/classification_basis", "Authored knowledge roles require fixed classification evidence")
    if ("principle" in facets["roles"]) != (facets["principle_kind"] is not None):
        add("/principle_kind", "The principle role and its explicit subtype must be supplied together")
    applicability(facets["applicability"], "/applicability")
    for position, attempt in enumerate(facets["attempts"]):
        terms(attempt["conditions"], f"/attempts/{position}/conditions")
    for position, assessment in enumerate(facets["confidence"]):
        path = f"/confidence/{position}"
        if not assessment["assessed_by"].strip() or any(not assessment["method"][k].strip() for k in ("name", "version")):
            add(path, "Confidence requires a named assessor and method version")
        if assessment["level"] != "unknown" and not assessment["basis"]:
            add(path + "/basis", "A known confidence level requires fixed assessment evidence")
        probability = assessment["calibrated_probability"]
        if probability is not None:
            if not math.isfinite(probability) or not 0 <= probability <= 1:
                add(path + "/calibrated_probability", "Calibrated probability must be finite and between zero and one")
            if assessment["calibration_ref"] is None:
                add(path + "/calibration_ref", "A numeric probability requires fixed calibration evidence")
        applicability(assessment["applicability"], path + "/applicability")
    fixed(facets, "")
    return errors
