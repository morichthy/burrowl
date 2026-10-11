"""文本嵌入。fastembed 离线加载本地模型（新数据目录默认 jina-v2-base-zh；已有数据目录可能仍用 MiniLM）。

两种切分方式（R17，见 docs/design/R17_LATE_CHUNKING.md）：
- windows：每段单独编码，段长按模型真实上限切窗（R12 T-P0.1）；
- late（后切分）：整条文本连同前缀一次编码（不超过 late_context_tokens），取逐 token 输出，
  再按块/段落区间分别求平均。每个向量只覆盖一段（不超过 span_tokens），但编码时看过全文上下文。
  只适用于平均池化的模型；按段落边界分批时，每批都重复带上前缀。

指纹（fingerprint）由模型文件清单、库版本与切分规则决定：换模型或换规则后，
新旧向量按指纹隔离，不会混用；需要重建向量投影。
"""
import bisect
import hashlib
import json
import os
import re
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
    # 后切分是可选能力：带 late=True 属性的嵌入器另需提供
    #   segments(text) -> [(起, 止)] 与 late_embed(prefix, text, spans) -> 每段一个向量
    def embed_passages(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...
    def count_tokens(self, text: str) -> int: ...


_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n")


def segments(tokenizer: Any, text: str, span_tokens: int) -> list[tuple[int, int]]:
    """把正文切成不超过 span_tokens 的连续区间 [(起, 止)]，覆盖全文：
    相邻段落（空行分隔）合并到不超过上限；单个段落超长时按 token 数切开。区间首尾相接，空白归入前一段。"""
    if not text.strip():
        return []
    offsets = tokenizer.encode(text, add_special_tokens=False).offsets
    starts = [o[0] for o in offsets]
    bounds = [0, *(m.end() for m in _PARAGRAPH_BREAK.finditer(text)), len(text)]
    paragraphs = [(a, b) for a, b in zip(bounds, bounds[1:], strict=False) if b > a]

    def count(a: int, b: int) -> int:
        return bisect.bisect_left(starts, b) - bisect.bisect_left(starts, a)

    cuts: list[int] = []
    used = None  # 当前段已用的 token 数；None 表示还没有段
    for a, b in paragraphs:
        size = count(a, b)
        if used is not None and used + size <= span_tokens:
            used += size
            continue
        if size <= span_tokens:
            cuts.append(a)
            used = size
            continue
        first, last = bisect.bisect_left(starts, a), bisect.bisect_left(starts, b)  # 超长段落按 token 数切
        cuts.extend([a, *(starts[i] for i in range(first + span_tokens, last, span_tokens))])
        used = count(cuts[-1], b)
    cuts = sorted(set(cuts) | {0})
    return [(x, y) for x, y in zip(cuts, [*cuts[1:], len(text)], strict=True) if text[x:y].strip()]


def late_vectors(tokenizer: Any, run: Any, head: str, text: str, spans: list[tuple[int, int]],
                 context_tokens: int) -> list[list[float]]:
    """后切分的核心：[首特殊符号] + 前缀 + 正文 token + [尾特殊符号] 一次编码，逐段取平均并归一化。

    run(ids) 返回该序列每个位置的输出向量（形状 [长度, 维度]）。前缀加正文超过 context_tokens 时，
    按区间顺序分批（每批不超过上限、都带前缀），区间不跨批。前缀最多占上限的 1/PREFIX_SHARE。"""
    import numpy as np  # noqa: PLC0415 - 随 onnxruntime 安装

    wrapped = tokenizer.encode("", add_special_tokens=True).ids
    front, back = wrapped[:1], wrapped[1:]
    head_ids = tokenizer.encode(head, add_special_tokens=False).ids[:context_tokens // PREFIX_SHARE] if head else []
    encoded = tokenizer.encode(text, add_special_tokens=False)
    ids, starts = encoded.ids, [o[0] for o in encoded.offsets]
    ranges = [(bisect.bisect_left(starts, a), bisect.bisect_left(starts, b)) for a, b in spans]
    budget = max(1, context_tokens - len(front) - len(back) - len(head_ids))
    batches: list[list[int]] = []
    for index, (_a, b) in enumerate(ranges):
        if batches and b - ranges[batches[-1][0]][0] <= budget:
            batches[-1].append(index)
        else:
            batches.append([index])
    vectors: list[list[float]] = [[] for _ in spans]
    for batch in batches:
        lo = ranges[batch[0]][0]
        hi = min(ranges[batch[-1]][1], lo + budget)  # 单段超过预算（配置保证不会）时截到预算内
        # 编码窗口在预算内向两侧扩展，把区间外的前后文也送进模型（后切分的意义就在这些上下文）
        spare = budget - (hi - lo)
        w0 = max(0, lo - spare // 2)
        w1 = min(len(ids), hi + spare - (lo - w0))
        w0 = max(0, min(w0, w1 - budget))
        hidden = np.asarray(run(front + head_ids + ids[w0:w1] + back), dtype=np.float64)
        base = len(front) + len(head_ids) - w0
        for index in batch:
            a, b = max(ranges[index][0], lo), min(ranges[index][1], hi)
            rows = hidden[base + a:base + b] if b > a else hidden  # 没有 token 的区间（纯空白）用整批平均
            vector = rows.mean(axis=0)
            vectors[index] = (vector / (np.linalg.norm(vector) or 1.0)).tolist()
    return vectors


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
                 query_prefix: str | None = None, passage_prefix: str | None = None, chunking: str = "windows",
                 late_context_tokens: int = 2048, span_tokens: int = 512):
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
        _register_custom(TextEmbedding, model_name, dim)
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
        # 后切分：模型须是平均池化（逐 token 输出的平均就是它的原生向量），且位置上限足够长
        from fastembed.text.pooled_embedding import PooledEmbedding  # noqa: PLC0415
        positions = _max_positions(directory)
        supported = isinstance(self.model.model, PooledEmbedding) and (positions or 0) >= 1024
        self.late = supported and chunking in {"auto", "late"}
        self.late_context_tokens = min(late_context_tokens, positions or late_context_tokens)
        self.span_tokens = min(span_tokens, self.late_context_tokens // 2)
        rule = ({"rule": "late-v1", "context": self.late_context_tokens, "span": self.span_tokens} if self.late
                else {"rule": "token-v2", "max_tokens": self.max_tokens, "prefix_share": PREFIX_SHARE})
        # 窗口参数写进指纹：上限或切窗规则变化后，旧向量自动作废并重建
        self.fingerprint = "fe-" + hashlib.sha256(json.dumps(
            {"model": model_name, "files": files, "fastembed": fastembed.__version__,
             "windows": rule,
             "prefixes": [self.query_prefix, self.passage_prefix]}
        ).encode()).hexdigest()[:16]

    def windows(self, prefix: str, text: str) -> list[Window]:
        # 文档指令前缀与上下文信封一起计入前缀预算，保证"指令 + 信封 + 正文"不超过模型上限
        return token_windows(self.tokenizer, self.max_tokens, self.passage_prefix + prefix, text)

    def segments(self, text: str) -> list[tuple[int, int]]:
        return segments(self.tokenizer, text, self.span_tokens)

    def late_embed(self, prefix: str, text: str, spans: list[tuple[int, int]]) -> list[list[float]]:
        """后切分：复用 fastembed 已加载的推理会话取逐 token 输出（last_hidden_state），不另占一份模型内存。"""
        import numpy as np  # noqa: PLC0415
        session = self.model.model.model
        names = {i.name for i in session.get_inputs()}

        def run(ids: list[int]):
            feed = {"input_ids": np.asarray([ids], dtype=np.int64)}
            if "attention_mask" in names:
                feed["attention_mask"] = np.ones((1, len(ids)), dtype=np.int64)
            if "token_type_ids" in names:
                feed["token_type_ids"] = np.zeros((1, len(ids)), dtype=np.int64)
            return session.run(None, feed)[0][0]
        return late_vectors(self.tokenizer, run, self.passage_prefix + prefix, text, spans, self.late_context_tokens)

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


def _max_positions(directory: Path) -> int | None:
    """模型的位置编码上限（config.json 的 max_position_embeddings）。分词器配置里的上限可能更小（jina 写的是 512）。"""
    try:
        value = json.loads((directory / "config.json").read_text(encoding="utf-8")).get("max_position_embeddings")
        return int(value) if value else None
    except (OSError, ValueError, TypeError):
        return None


def _register_custom(text_embedding: Any, model_name: str, dim: int) -> None:
    """fastembed 未内置的已登记模型（model_store 中带 custom 的条目）：按其池化方式注册一次，之后与内置模型同样加载。"""
    from . import model_store  # noqa: PLC0415 - 只在需要时读取模型清单

    spec = next((m for m in model_store.EMBEDDING_MODELS.values() if m["model"] == model_name and "custom" in m), None)
    if spec is None or any(m["model"] == model_name for m in text_embedding.list_supported_models()):
        return
    from fastembed.common.model_description import ModelSource, PoolingType  # noqa: PLC0415
    text_embedding.add_custom_model(model=model_name, pooling=PoolingType[spec["custom"]["pooling"]],
                                    normalization=True, sources=ModelSource(hf=spec["repo"]), dim=dim,
                                    model_file=spec["custom"]["model_file"])


def _file_digest(path: Path) -> str:
    with path.open("rb") as stream:  # 模型文件可达数 GB，流式计算
        return hashlib.file_digest(stream, "sha256").hexdigest()


def query_truncated(embedder: Embedder, text: str) -> bool:
    """查询是否超过模型上限（超出部分不参与语义匹配）。检索结果据此报告 query_truncated 缺口。"""
    return embedder.count_tokens(text) > embedder.max_tokens
