"""嵌入模型本机实测（R12 T-P1.1）：吞吐、峰值内存、全量重建时间。

    uv run python tools/bench_embed.py --model minilm|qwen3-0.6b-q [--data-dir <目录>] [--max-tokens N]

在数据目录的临时副本上（默认浮点样例）用指定模型重建 fulltext/discovery 投影，报告：
- 加载耗时（秒）；
- 吞吐：编码的 token 数 / 编码耗时（token/s，只计 embed_passages）；
- 峰值内存：进程峰值工作集（MB，Windows 用 GetProcessMemoryInfo，其他系统用 ru_maxrss）；
- 全量重建耗时（秒，index.reconcile(force=True) 整体）与窗口数。
只读原数据目录；结果是本机、本次的测量，不代表其他机器。
"""
import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

from burrowl.app import App
from burrowl.core.config import IndexConfig
from burrowl.index import model_store
from burrowl.index.embed import FastEmbedEmbedder
from burrowl.services.indexing import IndexService
from burrowl.services.memory.context import Storage


def peak_memory_mb() -> float:
    if sys.platform == "win32":
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if not kernel32.K32GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return float("nan")
        return counters.PeakWorkingSetSize / 2**20
    import resource  # noqa: PLC0415
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", required=True, choices=sorted(model_store.EMBEDDING_MODELS))
    parser.add_argument("--data-dir", type=Path, default=Path("tests/fixtures/workspace"))
    parser.add_argument("--max-tokens", type=int, default=None, help="调小模型上限（例如 2048）以限制内存")
    args = parser.parse_args(argv)

    spec = model_store.EMBEDDING_MODELS[args.model]
    directory = model_store.default_models_dir() / spec["dir"]
    started = time.perf_counter()
    embedder = FastEmbedEmbedder(directory, spec["model"], spec["dim"], max_tokens=args.max_tokens,
                                 query_prefix=spec["query_prefix"])
    load_seconds = time.perf_counter() - started

    encoded = {"tokens": 0, "seconds": 0.0, "texts": 0}
    original = embedder.embed_passages

    def timed(texts: list[str]) -> list[list[float]]:
        t0 = time.perf_counter()
        vectors = original(texts)
        encoded["seconds"] += time.perf_counter() - t0
        encoded["tokens"] += sum(embedder.count_tokens(t) for t in texts)
        encoded["texts"] += len(texts)
        return vectors

    embedder.embed_passages = timed
    with tempfile.TemporaryDirectory(prefix="burrowl-bench-") as tmp:
        data = Path(tmp) / "data"
        shutil.copytree(args.data_dir, data, ignore=shutil.ignore_patterns("index.sqlite*", "eval"))
        App.open(data, fake_vectors=True)  # 只为初始化数据目录结构
        index = IndexService.open(Storage.open(data), IndexConfig(), embedder=embedder)
        t0 = time.perf_counter()
        index.reconcile(force=True)
        rebuild_seconds = time.perf_counter() - t0
    report = {"model": args.model, "fastembed_model": spec["model"], "max_tokens": embedder.max_tokens,
              "data_dir": str(args.data_dir), "load_seconds": round(load_seconds, 1),
              "rebuild_seconds": round(rebuild_seconds, 1), "windows": encoded["texts"],
              "tokens": encoded["tokens"],
              "tokens_per_second": round(encoded["tokens"] / encoded["seconds"]) if encoded["seconds"] else None,
              "peak_memory_mb": round(peak_memory_mb())}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
