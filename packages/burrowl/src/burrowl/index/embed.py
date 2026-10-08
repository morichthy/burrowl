"""文本嵌入。默认 fastembed 加载本地多语言 MiniLM（384 维，离线，不联网下载）。

指纹（fingerprint）由模型文件清单、库版本与切窗规则决定：换模型或换规则后，
新旧向量按指纹隔离，不会混用；需要重建向量投影。
"""
import hashlib
import json
import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..core.errors import BurrowlError

# R12 T-P0.1（E6）：窗口大小必须按模型的真实输入上限计算。旧实现固定 360 token 窗口，
# 而 MiniLM 的分词器在 128 token 处静默截断，58%–71% 的正文从未进入向量。
PREFIX_SHARE = 4             # 前缀（标题或上下文信封）最多占窗口的 1/PREFIX_SHARE（R12 §4.11）
DEFAULT_MAX_TOKENS = 512     # 既读不到模型上限、配置也没给时的保守值
SPECIAL_TOKENS = 2           # 编码时自动加入的首尾特殊符号（CLS/SEP 或 <s></s>）
BATCH_TOKENS = 16384         # 每批编码的 token 总量上限：长窗口模型按批内最长序列补齐，注意力内存随长度平方增长


@dataclass(frozen=True)
class Window:
    start: int
    end: int
    text: str


def window_plan(max_tokens: int, prefix_tokens: int) -> tuple[int, int]:
    """给定模型上限与前缀实际 token 数，返回 (正文窗口, 步长)。步长约为窗口的 5/6，相邻窗口重叠约 1/6。"""
    size = max(8, max_tokens - SPECIAL_TOKENS - 1 - prefix_tokens)  # 1 = 前缀与正文之间的换行
    return size, max(1, size * 5 // 6)


class Embedder(Protocol):
    fingerprint: str
    dim: int
    max_tokens: int  # 模型一次能看到的 token 上限（含特殊符号）；超出部分会被模型截断

    def windows(self, prefix: str, text: str) -> list[Window]: ...
    def embed_passages(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...
    def count_tokens(self, text: str) -> int: ...


def pack(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


class FakeEmbedder:
    """测试与无模型环境用：按字符二元组哈希出确定性向量，语义上只有"字面相近"。"""

    def __init__(self, dim: int = 64):
        self.dim = dim
        self.max_tokens = 4096  # 假向量没有截断；按字符计数
        self.fingerprint = f"fake-bigram-{dim}"

    def _vector(self, text: str) -> list[float]:
        values = [0.0] * self.dim
        text = text.casefold()
        for i in range(max(1, len(text) - 1)):
            digest = hashlib.blake2b(text[i:i + 2].encode(), digest_size=4).digest()
            values[int.from_bytes(digest, "little") % self.dim] += 1.0
        norm = sum(v * v for v in values) ** 0.5 or 1.0
        return [v / norm for v in values]

    def windows(self, prefix: str, text: str) -> list[Window]:
        size, stride = 400, 300
        if not text:
            return [Window(0, 0, prefix)]
        return [Window(s, min(len(text), s + size), f"{prefix}\n{text[s:s + size]}")
                for s in range(0, max(1, len(text) - (size - stride)), stride)]

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    def count_tokens(self, text: str) -> int:
        return len(text)


def token_windows(tokenizer: Any, max_tokens: int, prefix: str, text: str) -> list[Window]:
    """按真实 tokenizer 切窗：每个窗口"前缀 + 换行 + 正文片段"编码后（含特殊符号）不超过 max_tokens，
    且全部正文都至少落在一个窗口里。前缀（标题或上下文信封）超过窗口的 1/4 时从尾部裁掉——
    信封按保留优先级排行，所以先丢定义块原文、再丢问题，标题链最后才被裁。
    tokenizer 需提供 encode(text, add_special_tokens=...) → ids/offsets 与 decode(ids)。
    """
    prefix_ids = tokenizer.encode(prefix, add_special_tokens=False).ids[:max_tokens // PREFIX_SHARE]
    prefix = tokenizer.decode(prefix_ids)
    size, stride = window_plan(max_tokens, len(tokenizer.encode(prefix, add_special_tokens=False).ids))
    offsets = tokenizer.encode(text, add_special_tokens=False).offsets if text else []
    result: list[Window] = []
    start_token = 0
    while start_token < len(offsets):
        end_token = min(len(offsets), start_token + size)
        # 子串重新编码时，切口处的子词可能拆成更多 token：超限就从尾部收缩，保证真的放得下
        while True:
            start, end = offsets[start_token][0], offsets[end_token - 1][1]
            candidate = prefix + "\n" + text[start:end]
            if end_token - start_token <= 1 or \
                    len(tokenizer.encode(candidate, add_special_tokens=True).ids) <= max_tokens:
                break
            end_token -= 1
        result.append(Window(start, end, candidate))
        if end_token >= len(offsets):
            break
        start_token = min(start_token + stride, end_token)  # 收缩后步长不能越过窗口末尾，否则会漏正文
    return result or [Window(0, 0, prefix)]


def _model_max_tokens(model: Any) -> int | None:
    """fastembed 把模型的训练上限写在内部 tokenizer 的截断设置里（MiniLM 为 128）。读不到返回 None。"""
    try:
        truncation = model.model.tokenizer.truncation
        return int(truncation["max_length"]) if truncation else None
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


class FastEmbedEmbedder:
    """本地 ONNX 嵌入。按模型真实输入上限分窗（R12 T-P0.1），超出的部分不会被静默丢弃。"""

    def __init__(self, model_dir: str | Path, model_name: str, dim: int = 384, max_tokens: int | None = None,
                 query_prefix: str | None = None, passage_prefix: str | None = None):
        directory = Path(model_dir)
        if not any(directory.rglob("*.onnx")):
            raise BurrowlError("CAPABILITY_UNAVAILABLE", "本地嵌入模型缺失；请先安装模型文件",
                           {"path": str(directory)})
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        try:
            import fastembed  # noqa: PLC0415
            from fastembed import TextEmbedding  # noqa: PLC0415 - 可选依赖
            from tokenizers import Tokenizer  # noqa: PLC0415
        except ImportError as exc:
            raise BurrowlError("CAPABILITY_UNAVAILABLE", "未安装 fastembed（burrowl[models]）") from exc
        self.tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
        self.tokenizer.no_truncation()
        self.model = TextEmbedding(model_name=model_name, specific_model_path=str(directory),
                                   local_files_only=True, threads=2, providers=["CPUExecutionProvider"])
        self.dim = dim
        # 模型要求的指令前缀（例如 Qwen3 的查询指令）；fastembed 不会自动加，空字符串表示不用
        self.query_prefix, self.passage_prefix = query_prefix or "", passage_prefix or ""
        # 优先用模型自带的上限；配置值只在读不到时使用，且不能超过模型上限（超过就会被截断）
        detected = _model_max_tokens(self.model)
        self.max_tokens = min(v for v in (detected, max_tokens) if v) if (detected or max_tokens) \
            else DEFAULT_MAX_TOKENS
        files = sorted((p.relative_to(directory).as_posix(), _file_digest(p)) for p in directory.rglob("*")
                       if p.is_file() and p.suffix in {".onnx", ".json"})
        # 窗口参数写进指纹：上限或切窗规则变化后，旧向量自动作废并重建
        self.fingerprint = "fe-" + hashlib.sha256(json.dumps(
            {"model": model_name, "files": files, "fastembed": fastembed.__version__,
             "windows": {"rule": "token-v2", "max_tokens": self.max_tokens, "prefix_share": PREFIX_SHARE},
             "prefixes": [self.query_prefix, self.passage_prefix]}
        ).encode()).hexdigest()[:16]

    def windows(self, prefix: str, text: str) -> list[Window]:
        # 文档指令前缀与上下文信封一起计入前缀预算，保证"指令 + 信封 + 正文"不超过模型上限
        return token_windows(self.tokenizer, self.max_tokens, self.passage_prefix + prefix, text)

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        """按长度排序后分批，每批 token 总量不超过 BATCH_TOKENS，避免长序列补齐导致内存暴涨。"""
        lengths = [self.count_tokens(t) for t in texts]
        order = sorted(range(len(texts)), key=lambda i: lengths[i])
        vectors: list[list[float] | None] = [None] * len(texts)
        batch: list[int] = []
        for index in [*order, None]:
            if batch and (index is None or (len(batch) + 1) * max(lengths[index], lengths[batch[-1]]) > BATCH_TOKENS):
                encoded = self.model.passage_embed([texts[i] for i in batch], batch_size=len(batch))
                for i, vector in zip(batch, encoded, strict=True):
                    vectors[i] = vector.tolist()
                batch = []
            if index is not None:
                batch.append(index)
        if any(v is None or len(v) != self.dim for v in vectors):
            raise BurrowlError("CAPABILITY_UNAVAILABLE", "编码器返回的数量或维度与配置不符")
        return vectors  # type: ignore[return-value]

    def embed_query(self, text: str) -> list[float]:
        """超过 max_tokens 的查询由模型截断；调用方用 query_truncated() 判断并在 gaps 中报告。"""
        return next(iter(self.model.query_embed(self.query_prefix + text))).tolist()

    def count_tokens(self, text: str) -> int:
        return len(self.tokenizer.encode(text, add_special_tokens=True).ids)


def _file_digest(path: Path) -> str:
    with path.open("rb") as stream:  # 模型文件可达数 GB，流式计算
        return hashlib.file_digest(stream, "sha256").hexdigest()


def query_truncated(embedder: Embedder, text: str) -> bool:
    """查询是否超过模型上限（超出部分不参与语义匹配）。检索结果据此报告 query_truncated 缺口。"""
    return embedder.count_tokens(text) > embedder.max_tokens
