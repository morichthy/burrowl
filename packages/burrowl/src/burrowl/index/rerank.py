"""离线 ONNX Cross-encoder 重排（可选组件，需要 burrowl[models]）。

只从本地模型目录加载，不下载、不执行模型仓库代码；加载前逐个文件核对清单 SHA-256。
分数是相关性 logit，不是校准过的"正确概率"，也不是复核状态。
模型目录需包含 model-manifest.json（或位于其父目录），格式：
    {"model": "...", "onnx_file": "x.onnx", "max_length": 512, "files": {"文件名": "sha256", ...}}
"""
import hashlib
import json
import math
import threading
from pathlib import Path
from typing import Protocol

from ..core.errors import BurrowlError

_CACHE: dict[tuple, "OnnxCrossEncoder"] = {}
_LOCK = threading.Lock()


class Reranker(Protocol):
    identity: dict
    max_length: int
    batch_size: int

    def count_tokens(self, question: str, text: str) -> int: ...
    def score_pairs(self, pairs: list[tuple[str, str]]) -> list[float]: ...


class OnnxCrossEncoder:
    def __init__(self, model_dir: Path, manifest: dict, digest: str):
        try:
            import onnxruntime as ort  # noqa: PLC0415 - 可选依赖
            from tokenizers import Tokenizer  # noqa: PLC0415
        except ImportError as exc:
            raise BurrowlError("CAPABILITY_UNAVAILABLE", "未安装 onnxruntime/tokenizers（burrowl[models]）") from exc
        self.max_length = manifest.get("max_length", 512)
        self.batch_size = 16
        self.identity = {"provider": "onnx-cross-encoder", "model": manifest["model"],
                         "revision": manifest.get("revision", ""), "manifest_sha256": digest,
                         "max_length": self.max_length}
        self._tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self._tokenizer.no_truncation()  # 统一关闭截断/补齐，避免低估 token 数
        self._tokenizer.no_padding()
        self._pad = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))["pad_token_id"]
        self._session = ort.InferenceSession(str(model_dir / manifest["onnx_file"]), providers=["CPUExecutionProvider"])
        self._inputs = {i.name for i in self._session.get_inputs()}
        if not {"input_ids", "attention_mask"} <= self._inputs or self._inputs - {"input_ids", "attention_mask",
                                                                                   "token_type_ids"}:
            raise BurrowlError("CAPABILITY_UNAVAILABLE", "不支持的重排模型输入格式")

    def count_tokens(self, question: str, text: str) -> int:
        return len(self._tokenizer.encode(question, text).ids)

    def score_pairs(self, pairs: list[tuple[str, str]]) -> list[float]:
        import numpy as np  # noqa: PLC0415 - 随 onnxruntime 安装
        encodings = [self._tokenizer.encode(q, t) for q, t in pairs]
        if any(len(e.ids) > self.max_length for e in encodings):
            raise BurrowlError("INVALID_ARGUMENT", "重排输入超过模型长度上限；未截断也未推理")
        scores: list[float] = []
        for offset in range(0, len(encodings), self.batch_size):
            batch = encodings[offset:offset + self.batch_size]
            width = max(len(e.ids) for e in batch)
            arrays = {"input_ids": [e.ids + [self._pad] * (width - len(e.ids)) for e in batch],
                      "attention_mask": [e.attention_mask + [0] * (width - len(e.ids)) for e in batch],
                      "token_type_ids": [e.type_ids + [0] * (width - len(e.ids)) for e in batch]}
            logits = np.asarray(self._session.run(None, {n: np.asarray(arrays[n], dtype=np.int64)
                                                         for n in self._inputs})[0]).reshape(-1)
            if logits.size != len(batch) or not all(math.isfinite(float(x)) for x in logits):
                raise BurrowlError("CAPABILITY_UNAVAILABLE", "重排模型输出无效")
            scores.extend(float(x) for x in logits)
        return scores


def load(model_dir: str | Path) -> OnnxCrossEncoder:
    directory = Path(model_dir).resolve()
    manifest_path = next((p for p in (directory / "model-manifest.json", directory.parent / "model-manifest.json")
                          if p.is_file()), None)
    if manifest_path is None:
        raise BurrowlError("CAPABILITY_UNAVAILABLE", "重排模型缺少 model-manifest.json", {"path": str(directory)})
    raw = manifest_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    manifest = json.loads(raw)
    files = manifest.get("files", {})
    if not {"tokenizer.json", "config.json", manifest.get("onnx_file")} <= files.keys():
        raise BurrowlError("CAPABILITY_UNAVAILABLE", "重排模型清单缺必要文件")
    paths = {name: directory / name for name in files}
    if any(not p.resolve().is_relative_to(directory) for p in paths.values()):
        raise BurrowlError("UNSAFE_PATH", "重排模型清单含越界路径")
    states = tuple((name, p.stat().st_size, p.stat().st_mtime_ns) for name, p in sorted(paths.items()))
    key = (str(directory), digest, states)
    with _LOCK:
        if key not in _CACHE:
            for name, path in paths.items():
                with path.open("rb") as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != files[name]:
                        raise BurrowlError("CAPABILITY_UNAVAILABLE", f"重排模型文件指纹不匹配：{name}")
            _CACHE.clear()  # 只保留最近一个模型，不在进程内累积大模型
            _CACHE[key] = OnnxCrossEncoder(directory, manifest, digest)
        return _CACHE[key]
