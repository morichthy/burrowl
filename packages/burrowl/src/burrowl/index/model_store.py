"""本机模型目录：安装、校验与状态。所有数据目录共享一份模型。

    <models>/multilingual-minilm/      默认嵌入模型（fastembed 的 ONNX 量化版，约 220 MB，128 token）
    <models>/qwen3-embedding-0.6b-q/   可选长上下文嵌入模型（int8，约 1.1 GB，32k token；R12 T-P1.2）
    <models>/cross-encoder/            重排模型（含 model-manifest.json，逐文件 sha256 校验）

模型目录：环境变量 BURROWL_MODELS_DIR → %LOCALAPPDATA%/burrowl/models（其他系统 ~/.local/share/burrowl/models）。
"""
import hashlib
import json
import os
import shutil
from pathlib import Path

from ..core.errors import BurrowlError

EMBEDDING_DIR = "multilingual-minilm"
RERANKER_DIR = "cross-encoder"
EMBEDDING_REPO = "Qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q"
EMBEDDING_FILES = ["model_optimized.onnx", "tokenizer.json", "config.json", "special_tokens_map.json",
                   "tokenizer_config.json"]
# 可安装的嵌入模型。model 是 fastembed 的模型名（配置 index.embedding.model 填它），
# query_prefix 是该模型要求的查询指令（fastembed 不会自动加；填进 index.embedding.query_prefix）。
EMBEDDING_MODELS: dict[str, dict] = {
    "minilm": {"dir": EMBEDDING_DIR, "repo": EMBEDDING_REPO, "files": EMBEDDING_FILES, "dim": 384,
               "model": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", "query_prefix": None,
               "size": "约 220 MB"},
    "qwen3-0.6b-q": {"dir": "qwen3-embedding-0.6b-q", "repo": "Qdrant/Qwen3-Embedding-0.6B-onnx",
                     "files": ["onnx/model_quantized.onnx", "tokenizer.json", "config.json", "tokenizer_config.json",
                               "special_tokens_map.json", "vocab.json", "merges.txt"],
                     "dim": 1024, "model": "Qwen/Qwen3-Embedding-0.6B-Q",
                     "query_prefix": "Instruct: Given a question, retrieve research records that answer it\nQuery:",
                     "size": "约 1.1 GB"},
}
RERANKER_REPO = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"


def default_models_dir() -> Path:
    if value := os.environ.get("BURROWL_MODELS_DIR"):
        return Path(value)
    if home := os.environ.get("BURROWL_HOME"):
        # 安装包内：BURROWL_HOME 是版本目录，或经 current 联接到达；resolve() 穿过联接，取 <根>/models
        return Path(home).resolve().parent.parent / "models"
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / "burrowl" / "models" if base else Path.home() / ".local" / "share" / "burrowl" / "models"


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def embedding_dir_for(models: Path, model_name: str) -> Path | None:
    """按 fastembed 模型名找到本机模型目录（配置没写 path 时使用）；不是已登记的模型返回 None。"""
    spec = next((m for m in EMBEDDING_MODELS.values() if m["model"] == model_name), None)
    return models / spec["dir"] if spec else None


def _installed(directory: Path, files: list[str]) -> bool:
    return all((directory / f).is_file() for f in files)


def status(models: Path) -> dict:
    embedding = models / EMBEDDING_DIR
    reranker = models / RERANKER_DIR
    manifest = reranker / "model-manifest.json"
    return {"models_dir": str(models),
            "embedding": {"path": str(embedding),
                          "installed": all((embedding / f).is_file() for f in ("model_optimized.onnx", "tokenizer.json"))},
            "embedding_models": {key: {"path": str(models / spec["dir"]), "model": spec["model"], "dim": spec["dim"],
                                       "installed": _installed(models / spec["dir"], spec["files"])}
                                 for key, spec in EMBEDDING_MODELS.items()},
            "reranker": {"path": str(reranker), "installed": manifest.is_file()}}


def install_embedding(models: Path, key: str = "minilm") -> dict:
    """从 Hugging Face 下载嵌入模型（需要联网；之后完全离线使用）。key 见 EMBEDDING_MODELS。"""
    if key not in EMBEDDING_MODELS:
        raise BurrowlError("INVALID_ARGUMENT", "未知的嵌入模型", {"model": key, "known": sorted(EMBEDDING_MODELS)})
    try:
        from huggingface_hub import hf_hub_download  # noqa: PLC0415 - 可选依赖（burrowl[models]）
    except ImportError as exc:
        raise BurrowlError("CAPABILITY_UNAVAILABLE", "未安装 burrowl[models]，无法下载模型") from exc
    spec = EMBEDDING_MODELS[key]
    target = models / spec["dir"]
    target.mkdir(parents=True, exist_ok=True)
    for name in spec["files"]:
        hf_hub_download(spec["repo"], name, local_dir=target)
    return status(models)["embedding_models"][key]


def install_reranker(models: Path, source: Path | None = None) -> dict:
    """安装重排模型：从本机已有目录复制（含 model-manifest.json），或从 Hugging Face 下载并生成清单。"""
    target = models / RERANKER_DIR
    target.mkdir(parents=True, exist_ok=True)
    if source is not None:
        manifest_path = next((p for p in (source / "model-manifest.json", source.parent / "model-manifest.json")
                              if p.is_file()), None)
        if manifest_path is None:
            raise BurrowlError("INVALID_ARGUMENT", "来源目录缺少 model-manifest.json", {"path": str(source)})
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for name, digest in manifest["files"].items():
            if _sha256(source / name) != digest:
                raise BurrowlError("INTEGRITY_ERROR", f"来源模型文件指纹不匹配：{name}")
            shutil.copy2(source / name, target / name)
        (target / "model-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                                    encoding="utf-8")
        return status(models)["reranker"]
    try:
        from huggingface_hub import hf_hub_download  # noqa: PLC0415 - 可选依赖（burrowl[models]）
    except ImportError as exc:
        raise BurrowlError("CAPABILITY_UNAVAILABLE", "未安装 burrowl[models]，无法下载模型") from exc
    onnx = "model_quint8_avx2.onnx"
    names = {onnx: f"onnx/{onnx}", "tokenizer.json": "tokenizer.json", "config.json": "config.json",
             "tokenizer_config.json": "tokenizer_config.json", "special_tokens_map.json": "special_tokens_map.json"}
    files = {}
    for name, remote in names.items():
        downloaded = Path(hf_hub_download(RERANKER_REPO, remote, local_dir=target / ".download"))
        shutil.copy2(downloaded, target / name)
        files[name] = _sha256(target / name)
    shutil.rmtree(target / ".download", ignore_errors=True)
    manifest = {"schema_version": 1, "model": RERANKER_REPO, "onnx_file": onnx, "max_length": 512, "files": files}
    (target / "model-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return status(models)["reranker"]
