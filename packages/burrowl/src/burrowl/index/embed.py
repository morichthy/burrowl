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
from typing import Protocol

from ..core.errors import BurrowlError

WINDOW_TOKENS, WINDOW_STRIDE, TITLE_TOKENS = 360, 300, 40


@dataclass(frozen=True)
class Window:
    start: int
    end: int
    text: str


class Embedder(Protocol):
    fingerprint: str
    dim: int

    def windows(self, title: str, text: str) -> list[Window]: ...
    def embed_passages(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...
    def count_tokens(self, text: str) -> int: ...


def pack(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


class FakeEmbedder:
    """测试与无模型环境用：按字符二元组哈希出确定性向量，语义上只有"字面相近"。"""

    def __init__(self, dim: int = 64):
        self.dim = dim
        self.fingerprint = f"fake-bigram-{dim}"

    def _vector(self, text: str) -> list[float]:
        values = [0.0] * self.dim
        text = text.casefold()
        for i in range(max(1, len(text) - 1)):
            digest = hashlib.blake2b(text[i:i + 2].encode(), digest_size=4).digest()
            values[int.from_bytes(digest, "little") % self.dim] += 1.0
        norm = sum(v * v for v in values) ** 0.5 or 1.0
        return [v / norm for v in values]

    def windows(self, title: str, text: str) -> list[Window]:
        size, stride = 400, 300
        if not text:
            return [Window(0, 0, title)]
        return [Window(s, min(len(text), s + size), f"{title}\n{text[s:s + size]}")
                for s in range(0, max(1, len(text) - (size - stride)), stride)]

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    def count_tokens(self, text: str) -> int:
        return len(text)


class FastEmbedEmbedder:
    """本地 ONNX 嵌入。按模型真实 tokenizer 分窗，避免长中文片段被静默截断。"""

    def __init__(self, model_dir: str | Path, model_name: str, dim: int = 384):
        directory = Path(model_dir)
        if not (directory / "model_optimized.onnx").is_file() and not any(directory.glob("*.onnx")):
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
        files = sorted((p.name, hashlib.sha256(p.read_bytes()).hexdigest()) for p in directory.iterdir()
                       if p.is_file() and p.suffix in {".onnx", ".json"})
        self.fingerprint = "fe-" + hashlib.sha256(json.dumps(
            {"model": model_name, "files": files, "fastembed": fastembed.__version__,
             "windows": [WINDOW_TOKENS, WINDOW_STRIDE, TITLE_TOKENS]}).encode()).hexdigest()[:16]

    def windows(self, title: str, text: str) -> list[Window]:
        prefix = self.tokenizer.decode(self.tokenizer.encode(title, add_special_tokens=False).ids[:TITLE_TOKENS])
        offsets = self.tokenizer.encode(text, add_special_tokens=False).offsets if text else []
        result = []
        for start_token in range(0, len(offsets), WINDOW_STRIDE):
            window = offsets[start_token:start_token + WINDOW_TOKENS]
            if window:
                start, end = window[0][0], window[-1][1]
                result.append(Window(start, end, prefix + "\n" + text[start:end]))
        return result or [Window(0, 0, prefix)]

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        vectors = [v.tolist() for v in self.model.passage_embed(texts, batch_size=16)]
        if len(vectors) != len(texts) or any(len(v) != self.dim for v in vectors):
            raise BurrowlError("CAPABILITY_UNAVAILABLE", "编码器返回的数量或维度与配置不符")
        return vectors

    def embed_query(self, text: str) -> list[float]:
        if self.count_tokens(text) > 512:
            raise BurrowlError("INVALID_ARGUMENT", "语义查询超过 512 token；请缩短查询（不会静默截断）")
        return next(iter(self.model.query_embed(text))).tolist()

    def count_tokens(self, text: str) -> int:
        return len(self.tokenizer.encode(text, add_special_tokens=True).ids)
