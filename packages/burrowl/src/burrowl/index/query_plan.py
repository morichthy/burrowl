"""查询计划：调用者（AI）给出的少量等义改写 + 术语词库扩展，冻结为若干检索路线。

这里不运行任何翻译模型。词库只提供导航词（同义与单跳关联），不是证据也不扩大范围。
型号、数值等"保护项"必须原样出现在每个改写里——程序只能检查字面保留，不能证明译文正确。
每轮计划连同词库指纹一起冻结，续页时不因词库被修改而悄悄改变含义。
"""
import re
from collections import Counter
from copy import deepcopy
from typing import Any

from ..core.errors import BurrowlError
from ..core.hashing import canonical_hash
from .tokenize import tokens

MAX_VARIANTS = 3


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BurrowlError("INVALID_ARGUMENT", message)


def _texts(value: Any, name: str, limit: int = 64) -> list[str]:
    _require(isinstance(value, list) and len(value) <= limit, f"{name} 必须是不超过 {limit} 项的文本数组")
    _require(all(isinstance(v, str) and v.strip() and len(v) <= 1000 for v in value), f"{name} 含无效文本")
    return list(dict.fromkeys(value))


def _contains(text: str, term: str) -> bool:
    """ASCII 边界：X200 不匹配 X2000；中文按字面。"""
    return re.search(r"(?<![A-Za-z0-9_])" + re.escape(term) + r"(?![A-Za-z0-9_])", text, re.I) is not None


def _preserves(text: str, term: str) -> bool:
    return re.search(r"(?<![A-Za-z0-9_])" + re.escape(term) + r"(?![A-Za-z0-9_])", text) is not None


def numeric_tokens(text: str) -> list[str]:
    return re.findall(r"(?<![A-Za-z0-9])(?:[A-Za-z]+[-_]?)?\d+(?:[._-]\d+)*(?:[A-Za-z]+)?", text)


def _language(text: str) -> str:
    return "zh" if re.search("[一-鿿]", text) else "en" if re.search("[A-Za-z]", text) else "other"


def validate_terms(value: Any) -> list[dict]:
    """校验术语词库（.burrowl/query-terms.json）。"""
    _require(isinstance(value, dict) and value.get("schema_version") == 1 and isinstance(value.get("entries"), list)
             and len(value["entries"]) <= 2000, "术语词库格式或版本无效")
    ids = set()
    for item in value["entries"]:
        _require(isinstance(item, dict) and set(item) == {"id", "domain", "zh", "en", "aliases", "related",
                                                          "sources", "status"}, "术语条目字段不完整或有未知字段")
        _require(item["id"] not in ids, "术语 ID 重复")
        ids.add(item["id"])
        _require(item["status"] in {"active", "disabled"}, "术语状态无效")
        for key in ("zh", "en", "aliases", "related", "sources"):
            _texts(item[key], key)
        _require(bool(item["zh"] or item["en"] or item["aliases"]), "术语缺少名称")
        _require(item["status"] != "active" or bool(item["sources"]), "启用的术语必须注明出处")
    return value["entries"]


def build(question: str, *, keywords: list[str] | None = None, variants: list[dict] | None = None,
          protected_terms: list[str] | None = None, domains: list[str] | None = None,
          corpus_language: str = "unknown", terms: list[dict] | None = None, terms_sha256: str | None = None) -> dict:
    """生成冻结的查询计划。variants: [{id, language(zh|en|other), question, lexical_terms}]，最多 2 条。"""
    _require(isinstance(question, str) and question.strip() and len(question) <= 12000, "问题不能为空且不超过 12000 字")
    numeric = numeric_tokens(question)
    protected = list(dict.fromkeys(_texts(protected_terms or [], "protected_terms") + numeric))
    _require(all(_preserves(question, t) for t in protected), "保护项必须来自原问题")
    _require(corpus_language in {"zh", "en", "mixed", "unknown"}, "语料语言无效")
    domains = _texts(domains or [], "domains", 20)
    plan_variants = [{"id": "original", "language": _language(question), "question": question,
                      "lexical_terms": list(keywords or []) or tokens(question)[:32]}]
    for item in variants or []:
        _require(isinstance(item, dict) and set(item) <= {"id", "language", "question", "lexical_terms"},
                 "查询改写字段无效")
        _require(item.get("language") in {"zh", "en", "other"} and isinstance(item.get("question"), str)
                 and item["question"].strip(), "查询改写缺少语言或语句")
        terms_list = _texts(item.get("lexical_terms") or tokens(item["question"])[:32], "lexical_terms", 32)
        _require(all(_preserves(item["question"], t) for t in protected), "查询改写丢失了保护项（型号/数值），请修正")
        _require(set(numeric_tokens(item["question"])) == set(numeric), "查询改写增加或改变了数值/型号，请修正")
        same = next((v for v in plan_variants if " ".join(v["question"].casefold().split())
                     == " ".join(item["question"].casefold().split())), None)
        if same:
            same["lexical_terms"] = list(dict.fromkeys(same["lexical_terms"] + terms_list))[:32]
            continue
        variant_id = item.get("id") or f"v{len(plan_variants)}"
        _require(variant_id not in {v["id"] for v in plan_variants}, "查询改写 ID 重复")
        plan_variants.append({"id": variant_id, "language": item["language"], "question": item["question"],
                              "lexical_terms": terms_list})
    _require(len(plan_variants) <= MAX_VARIANTS, f"查询变体总数（含原问题）最多 {MAX_VARIANTS} 条")
    seed = "\n".join(v["question"] + "\n" + "\n".join(v["lexical_terms"]) for v in plan_variants)
    matched, related = [], []
    for entry in terms or []:
        family = entry["zh"] + entry["en"] + entry["aliases"]
        if entry["status"] != "active" or entry["domain"] not in ["general", *domains] \
                or not any(_contains(seed, t) for t in family):
            continue
        _require(len(matched) < 16, "匹配的术语超过 16 条，请缩小领域或问题")
        matched.append(entry["id"])
        related.extend(entry["related"])
        for variant in plan_variants:
            variant["lexical_terms"] = list(dict.fromkeys(
                variant["lexical_terms"] + entry.get(variant["language"], family) + entry["aliases"]))
    for variant in plan_variants:
        variant["lexical_terms"] = list(dict.fromkeys(protected + variant["lexical_terms"]))
        _require(len(variant["lexical_terms"]) <= 64, "扩展后词项超过 64 个，请缩小问题")
    related = list(dict.fromkeys(related))
    _require(len(related) <= 32, "关联词超过 32 个，请缩小问题")
    plan = {"variants": plan_variants, "protected_terms": protected, "corpus_language": corpus_language,
            "domains": domains, "matched_terms": matched, "related_terms": related,
            "terms_sha256": terms_sha256, "translation_check": "literal_protection_only"}
    plan["plan_id"] = "QP-" + canonical_hash(plan)[:16]
    return plan


def routes(plan: dict) -> list[dict]:
    """展开为检索路线：身份 1 条；每个变体各一条关键词与向量路线；关联词低权重关键词路线。

    同一语言的多个变体平分权重，避免同义改写重复投票。
    """
    first = plan["variants"][0]
    result = [{"id": "original:identity", "variant_id": "original", "kind": "identity", "channel": "identity",
               "text": first["question"], "weight": 1.0}]
    counts = Counter(v["language"] for v in plan["variants"])
    for variant in plan["variants"]:
        weight = 1.0 / counts[variant["language"]]
        result.append({"id": variant["id"] + ":lexical", "variant_id": variant["id"], "kind": "equivalent",
                       "channel": "lexical", "text": " ".join(variant["lexical_terms"]), "weight": weight})
        result.append({"id": variant["id"] + ":dense", "variant_id": variant["id"], "kind": "equivalent",
                       "channel": "dense", "text": variant["question"], "weight": weight})
    if plan["related_terms"]:
        result.append({"id": "related:lexical", "variant_id": "related", "kind": "related", "channel": "lexical",
                       "text": " ".join(plan["protected_terms"] + plan["related_terms"]), "weight": 0.25})
    return deepcopy(result)
