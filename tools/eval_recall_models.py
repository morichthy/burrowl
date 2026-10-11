"""嵌入模型召回对比：在外部固定题集上，用 burrowl 的切窗、分词与融合规则比较不同嵌入模型（只测召回，不调用生成式 AI）。

    uv run python tools/eval_recall_models.py --dataset <题集目录> --model minilm [--model jina-v2-base-zh ...]
        [--arm windows] [--arm late] [--arm whole] [--max-tokens N] [--late-context N] [--span-tokens N]
        [--split holdout] [--limit-queries N] [--cache-dir <目录>] [--out <结果.json>]

切分方式（--arm，可重复；与 --model 两两组合；评测方法见 docs/design/R17_LATE_CHUNKING.md §5）：
    windows  各段单独编码，按模型上限切窗（0.6 之前的方式；任何模型都可用）
    late     后切分：整篇（不超过 --late-context）一次编码，按段落区间（不超过 --span-tokens）分别池化
    whole    整篇一个向量：一次编码后对全部正文池化（超过 --late-context 的部分截掉，结果中报告截断篇数）
late / whole 只适用于平均池化、位置上限 ≥ 1024 的模型（如 jina-v2-base-zh），其他模型会被跳过并说明。

题集目录含 dataset/corpus.jsonl（{id, text, scope?}）与 dataset/queries.jsonl
（{query_id, query, relevant{文档ID: 等级}, forbidden_ids[], category, split}），即
A_work_auto_flow_evaluation/datasets/aligned-inputs-v3/* 的格式。

与产品一致的部分：切窗用 Embedder.windows（按模型真实上限，前缀占 1/4 以内，重叠约 1/6）；关键词用
burrowl 的分词与 FTS5 bm25；融合用 RRF（k=60，两路等权）；文档得分取其最好的窗口。
与产品不同的部分：语料直接当作一条条文档（没有记录层级、发现投影、对象汇总与重排），前缀用文档的
标题（SciFact 第一行）或 scope 代替上下文信封。所以结果只用于模型之间的相对比较，不是产品检索质量。

指标（按文档计）：Recall@k = 前 k 名中相关文档数 / 相关文档总数（等级 > 0）；
MRR@10 = 第一个相关文档名次的倒数；nDCG@10 按等级 $\\mathrm{DCG}=\\sum_i (2^{g_i}-1)/\\log_2(i+1)$ 归一；
误用率 = 前 10 名出现 forbidden_ids 的题目比例。没有相关文档的题（no_answer）只计误用率。
向量缓存按（题集指纹, 模型指纹）保存在 --cache-dir，换参数或模型自动重建。
"""
import argparse
import hashlib
import json
import math
import random
import sqlite3
import statistics
import sys
import time
from pathlib import Path

import numpy as np

from burrowl.index import model_store
from burrowl.index.embed import FastEmbedEmbedder
from burrowl.index.fusion import RRF_K
from burrowl.index.tokenize import fts_document, fts_query

sys.path.insert(0, str(Path(__file__).parent))
from bench_embed import peak_memory_mb  # noqa: E402

TOP = 100


def load(dataset: Path, split: str | None, limit: int | None) -> tuple[list[dict], list[dict], str]:
    corpus_path, query_path = dataset / "dataset" / "corpus.jsonl", dataset / "dataset" / "queries.jsonl"
    digest = hashlib.sha256(corpus_path.read_bytes()).hexdigest()[:16]
    corpus = [json.loads(line) for line in corpus_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    queries = [json.loads(line) for line in query_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if split:
        queries = [q for q in queries if q.get("split") == split]
    if limit and len(queries) > limit:
        queries = sorted(random.Random(0).sample(queries, limit), key=lambda q: q["query_id"])  # 固定种子抽样
    return corpus, queries, digest


def split_title(doc: dict) -> tuple[str, str]:
    """前缀：有标题行（SciFact）用标题，否则用 scope；正文为其余部分。"""
    text = doc["text"]
    if doc.get("scope") == "external" and "\n" in text:
        title, body = text.split("\n", 1)
        return title, body
    return doc.get("scope") or "", text


def build_vectors(embedder, corpus: list[dict], cache: Path, arm: str) -> tuple[np.ndarray, np.ndarray, dict]:
    if cache.is_file():
        data = np.load(cache)
        return data["vectors"], data["owners"], json.loads(str(data["stats"]))
    texts, owners, rows, truncated, tokens = [], [], [], 0, 0
    started = time.perf_counter()
    for index, doc in enumerate(corpus):
        prefix, body = split_title(doc)
        tokens += embedder.count_tokens(prefix + body)
        if arm == "windows":
            pieces = [w.text for w in embedder.windows(prefix, body)]
            texts += pieces
            owners += [index] * len(pieces)
            continue
        spans = embedder.segments(body) if arm == "late" else ([(0, len(body))] if body.strip() else [])
        if not spans:  # 没有正文只编码前缀（与产品一致）
            texts.append(prefix)
            owners.append(index)
            rows += embedder.embed_passages([prefix])
            continue
        if arm == "whole" and embedder.count_tokens(prefix + body) > embedder.late_context_tokens:
            truncated += 1
        texts += [body[a:b] for a, b in spans]
        owners += [index] * len(spans)
        rows += embedder.late_embed(prefix, body, spans)
    vectors = np.asarray(embedder.embed_passages(texts) if arm == "windows" else rows, dtype=np.float32)
    seconds = time.perf_counter() - started
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True) + 1e-12
    stats = {"arm": arm, "windows": len(texts), "tokens": tokens, "encode_seconds": round(seconds, 1),
             "tokens_per_second": round(tokens / seconds, 1) if seconds else None, "truncated_docs": truncated}
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez(cache, vectors=vectors, owners=np.asarray(owners, dtype=np.int32), stats=json.dumps(stats))
    return vectors, np.asarray(owners, dtype=np.int32), stats


def lexical_index(corpus: list[dict]) -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.execute("CREATE VIRTUAL TABLE docs USING fts5(text)")
    db.executemany("INSERT INTO docs(rowid, text) VALUES (?, ?)",
                   [(i + 1, fts_document(doc["text"])) for i, doc in enumerate(corpus)])
    return db


def lexical(db: sqlite3.Connection, query: str) -> list[int]:
    expression = fts_query(query)
    if not expression:
        return []
    rows = db.execute("SELECT rowid FROM docs WHERE docs MATCH ? ORDER BY bm25(docs) LIMIT ?", (expression, TOP))
    return [r[0] - 1 for r in rows]


def dense(vectors: np.ndarray, owners: np.ndarray, query_vector: np.ndarray, n_docs: int) -> list[int]:
    scores = vectors @ query_vector
    best = np.full(n_docs, -np.inf, dtype=np.float32)
    np.maximum.at(best, owners, scores)  # 文档得分 = 最好的窗口
    order = np.argsort(-best)[:TOP]
    return [int(i) for i in order if np.isfinite(best[i])]


def rrf(*rankings: list[int]) -> list[int]:
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, doc in enumerate(ranking, 1):
            scores[doc] = scores.get(doc, 0.0) + 1.0 / (RRF_K + rank)
    return sorted(scores, key=lambda d: -scores[d])[:TOP]


def metrics(ranking: list[str], query: dict) -> dict:
    relevant = {d: g for d, g in query.get("relevant", {}).items() if g > 0}
    forbidden = set(query.get("forbidden_ids") or [])
    out = {"forbidden@10": float(bool(forbidden & set(ranking[:10])))}
    if relevant:
        out["recall@5"] = len(relevant.keys() & set(ranking[:5])) / len(relevant)
        out["recall@10"] = len(relevant.keys() & set(ranking[:10])) / len(relevant)
        out["mrr@10"] = next((1 / r for r, d in enumerate(ranking[:10], 1) if d in relevant), 0.0)
        dcg = sum((2 ** relevant.get(d, 0) - 1) / math.log2(r + 1) for r, d in enumerate(ranking[:10], 1))
        ideal = sum((2 ** g - 1) / math.log2(r + 1) for r, g in enumerate(sorted(relevant.values(), reverse=True)[:10], 1))
        out["ndcg@10"] = dcg / ideal if ideal else 0.0
    return out


def summarize(rows: list[dict]) -> dict:
    keys = sorted({k for r in rows for k in r})
    return {k: {"mean": round(statistics.fmean(v), 4), "n": len(v)}
            for k in keys if (v := [r[k] for r in rows if k in r])}


def run(model: str, arm: str, options, dataset: Path, corpus, queries, digest) -> dict | None:
    spec = model_store.EMBEDDING_MODELS[model]
    started = time.perf_counter()
    embedder = FastEmbedEmbedder(model_store.default_models_dir() / spec["dir"], spec["model"], spec["dim"],
                                 max_tokens=options.max_tokens, query_prefix=spec["query_prefix"],
                                 chunking="windows" if arm == "windows" else "late",
                                 late_context_tokens=options.late_context, span_tokens=options.span_tokens)
    load_seconds = time.perf_counter() - started
    if arm != "windows" and not embedder.late:
        print(f"[{model}/{arm}] 跳过：该模型不是平均池化或位置上限不足 1024，不能后切分", flush=True)
        return None
    cache = options.cache_dir / f"{dataset.name}-{digest}-{embedder.fingerprint}-{arm}.npz"
    vectors, owners, stats = build_vectors(embedder, corpus, cache, arm)
    db = lexical_index(corpus)
    ids = [doc["id"] for doc in corpus]
    per_channel: dict[str, list[dict]] = {"dense": [], "lexical": [], "hybrid": []}
    by_category: dict[str, dict[str, list[dict]]] = {}
    latencies = []
    for query in queries:
        t0 = time.perf_counter()
        vector = np.asarray(embedder.embed_query(query["query"]), dtype=np.float32)
        vector /= np.linalg.norm(vector) + 1e-12
        d = dense(vectors, owners, vector, len(corpus))
        latencies.append((time.perf_counter() - t0) * 1000)
        lx = lexical(db, query["query"])
        for channel, ranking in (("dense", d), ("lexical", lx), ("hybrid", rrf(d, lx))):
            row = metrics([ids[i] for i in ranking], query)
            per_channel[channel].append(row)
            by_category.setdefault(query.get("category", "all"), {}).setdefault(channel, []).append(row)
    latencies.sort()
    return {"model": model, "arm": arm, "fastembed_model": spec["model"], "fingerprint": embedder.fingerprint,
            "max_tokens": embedder.max_tokens,
            "late": {"context": embedder.late_context_tokens, "span": embedder.span_tokens} if embedder.late else None,
            "dim": spec["dim"], "load_seconds": round(load_seconds, 1),
            "index": stats, "query_ms_p50": round(latencies[len(latencies) // 2], 1),
            "query_ms_p95": round(latencies[int(len(latencies) * 0.95)], 1), "peak_memory_mb": round(peak_memory_mb()),
            "queries": len(queries), "overall": {c: summarize(r) for c, r in per_channel.items()},
            "by_category": {cat: {c: summarize(r) for c, r in chans.items()} for cat, chans in sorted(by_category.items())}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--model", action="append", required=True, choices=sorted(model_store.EMBEDDING_MODELS))
    parser.add_argument("--arm", action="append", choices=["windows", "late", "whole"],
                        help="切分方式，可重复；默认只测 windows")
    parser.add_argument("--max-tokens", type=int, default=None, help="windows：调小模型上限（对所有 --model 生效）")
    parser.add_argument("--late-context", type=int, default=2048, help="late/whole：一次编码的上下文长度（token）")
    parser.add_argument("--span-tokens", type=int, default=512, help="late：每个向量覆盖的最大长度（token）")
    parser.add_argument("--split", default="holdout")
    parser.add_argument("--limit-queries", type=int, default=None)
    parser.add_argument("--cache-dir", type=Path, default=Path("build/recall-cache"))
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    corpus, queries, digest = load(args.dataset, args.split, args.limit_queries)
    results = []
    for model, arm in [(m, a) for m in args.model for a in args.arm or ["windows"]]:
        print(f"[{model}/{arm}] 文档 {len(corpus)}，题目 {len(queries)} …", flush=True)
        result = run(model, arm, args, args.dataset, corpus, queries, digest)
        if result is None:
            continue
        results.append(result)
        o = result["overall"]
        print(f"[{model}] 上限 {result['max_tokens']} token，窗口 {result['index']['windows']}，"
              f"编码 {result['index']['encode_seconds']} s；查询 p50 {result['query_ms_p50']} ms；"
              f"Recall@10 向量 {o['dense'].get('recall@10', {}).get('mean')} / 融合 {o['hybrid'].get('recall@10', {}).get('mean')}；"
              f"nDCG@10 融合 {o['hybrid'].get('ndcg@10', {}).get('mean')}", flush=True)
    report = {"dataset": str(args.dataset), "corpus_sha256_16": digest, "split": args.split, "results": results}
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
