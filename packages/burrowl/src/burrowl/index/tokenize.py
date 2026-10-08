"""关键词切分：中文双字滑窗，英文保留完整标识符并拆蛇形/驼峰。

不是语义分词模型。双字让"温漂"这类两字词可命中，长句也有部分重叠；
完整标识符可精确查函数名。索引与查询必须使用同一版本（TOKENIZER_VERSION）。
"""
import re

TOKENIZER_VERSION = "bigram-cjk-v1"
_TERMS = re.compile(r"[㐀-鿿]+|[A-Za-z0-9_]+")
_CJK = re.compile(r"[㐀-鿿]+")
_PARTS = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|[0-9]+")


def tokens(text: str) -> list[str]:
    out: list[str] = []
    for term in _TERMS.findall(text or ""):
        if _CJK.fullmatch(term):
            out.extend([term] if len(term) == 1 else [term[i:i + 2] for i in range(len(term) - 1)])
        else:
            out.append(term.casefold())
            out.extend(part.casefold() for part in _PARTS.findall(term))
    return out


def fts_document(text: str) -> str:
    return " ".join(tokens(text))


def fts_query(text: str, *, max_terms: int = 64) -> str | None:
    """"任一词命中"的 FTS5 表达式；每个词加引号，避免被当作运算符。"""
    unique = list(dict.fromkeys(tokens(text)))[:max_terms]
    if not unique:
        return None
    return " OR ".join('"' + term.replace('"', '""') + '"' for term in unique)
