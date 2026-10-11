"""检索评估（R12 T-P0.2，评估方案见 docs/design/R12_KNOWLEDGE_GRAPH_RETRIEVAL.md §7）。

在同一批题目上运行 IndexService.search_plan，输出逐题命中与汇总指标，供各阶段（B-1、B0、B1…）对比。
只读记忆记录；会在数据目录（或 --scratch 时的临时副本）中补齐派生索引。

    uv run python tools/eval_retrieval.py --data-dir <目录> --set <题目.jsonl> [--corpus fulltext|discovery]
        [--channels identity,lexical,dense] [--label B0] [--scratch] [--fake-vectors] [--legacy-windows]
        [--ablate envelope,record_text] [--model <见 burrowl models status>] [--max-tokens N]
        [--chunking auto|late|windows] [--late-context N] [--span-tokens N] [--graph-route] [--out <目录>]

题目文件每行一题：{id, question, variants[], type: fact|multihop|conditional|negative,
expected_owners[], expected_records[], must_not[], notes, checked_by_user}

指标（按题型分别给出，并给出总体）：
- Recall@k（记录级 / 对象级）：前 k 个命中中包含的期望项比例，$R@k=|E\\cap T_k|/|E|$，E 为期望集合，T_k 为前 k 个命中；
- MRR@10：第一个期望记录名次 r 的倒数 1/r（前 10 内没有则为 0）；
- 条件错误率：must_not 中的记录出现在前 10 的题目比例；
- 延迟 p50 / p95（毫秒，单次 search_plan 调用，含查询编码）；
- 上下文成本：前 10 个记录命中的文本 token 数之和（有真实模型时按其分词器，否则按字符）。
negative 题没有期望记录，只统计条件错误率，不计入 Recall 与 MRR。
"""
import argparse
import hashlib
import json
import shutil
import statistics
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from burrowl.app import App
from burrowl.index import projections, query_plan

TOP = 10


def load_set(path: Path) -> list[dict]:
    items = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for item in items:
        missing = {"id", "question", "type"} - set(item)
        if missing or item["type"] not in {"fact", "multihop", "conditional", "negative"}:
            raise SystemExit(f"题目 {item.get('id')} 格式不对：缺 {sorted(missing)} 或题型无效")
    return items


def legacy_windows(embedder) -> None:
    """B-1 基线专用：恢复修复前的固定 360/300 窗口（模型仍在 128 token 处截断），只用于对照，不进产品代码。"""
    from burrowl.index.embed import Window  # noqa: PLC0415

    def windows(title: str, text: str) -> list[Window]:
        tok = embedder.tokenizer
        prefix = tok.decode(tok.encode(title, add_special_tokens=False).ids[:40])
        offsets = tok.encode(text, add_special_tokens=False).offsets if text else []
        out = []
        for start_token in range(0, len(offsets), 300):
            window = offsets[start_token:start_token + 360]
            if window:
                out.append(Window(window[0][0], window[-1][1], prefix + "\n" + text[window[0][0]:window[-1][1]]))
        return out or [Window(0, 0, prefix)]

    embedder.windows = windows
    embedder.fingerprint = embedder.fingerprint + "-legacy360"


def ablate(names: list[str]) -> None:
    """消融实验：关掉某项 P1 改动，单独度量它的作用（只改本进程内的投影函数，不进产品代码）。
    envelope    — 向量前缀退回为记录标题（B1 之前的做法）；
    record_text — 技术单元记录级条目退回为只有检索说明（T-P1.4 之前）。"""
    from burrowl.core.hashing import canonical_hash  # noqa: PLC0415
    from burrowl.core.technical import description_text  # noqa: PLC0415
    from burrowl.services import indexing  # noqa: PLC0415

    original = indexing.BUILDERS["fulltext"]
    discovery = indexing.BUILDERS["discovery"]

    def resign(entry: dict, version: str) -> dict:
        entry = {k: v for k, v in entry.items() if k != "signature"}
        entry["signature"] = canonical_hash({"version": version, "entry": entry})
        return entry

    def patched(builder, version):
        def build(owner, records, badges, hidden):
            by_id = {r["record_id"]: r for r in records}
            out = []
            for entry in builder(owner, records, badges, hidden):
                if "envelope" in names:
                    entry = dict(entry, embed_prefix=entry["title"])
                record = by_id.get(entry["record_id"])
                if "record_text" in names and builder is original and record and record["kind"] == "detail" \
                        and entry["block_id"] is None and entry["kind"] == "detail":
                    extra = projections._payload_text({k: v for k, v in record["payload"].items() if k != "blocks"})
                    entry = dict(entry, text="\n".join(t for t in (description_text(record), extra,
                                                                    " ".join(record.get("keywords", []))) if t))
                out.append(resign(entry, version))
            return out
        return build

    indexing.BUILDERS["fulltext"] = patched(original, projections.FULLTEXT_VERSION)
    indexing.BUILDERS["discovery"] = patched(discovery, projections.DISCOVERY_VERSION)


def ranked_ids(items: list[dict], field: str) -> list[str]:
    return list(dict.fromkeys(item[field] for item in items))


def recall(expected: list[str], ranked: list[str], k: int) -> float | None:
    return len(set(expected) & set(ranked[:k])) / len(expected) if expected else None


def mrr(expected: list[str], ranked: list[str]) -> float | None:
    if not expected:
        return None
    return next((1 / rank for rank, rid in enumerate(ranked[:TOP], start=1) if rid in expected), 0.0)


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))] if ordered else 0.0


def summarize(rows: list[dict]) -> dict:
    def mean(name: str) -> float | None:
        values = [r[name] for r in rows if r[name] is not None]
        return round(statistics.fmean(values), 3) if values else None
    return {"n": len(rows), "recall@5_record": mean("recall@5_record"), "recall@10_record": mean("recall@10_record"),
            "recall@5_owner": mean("recall@5_owner"), "recall@10_owner": mean("recall@10_owner"),
            "mrr@10": mean("mrr@10"),
            "must_not_rate": round(sum(r["must_not_hit"] for r in rows) / len(rows), 3) if rows else None,
            "latency_p50_ms": round(percentile([r["latency_ms"] for r in rows], 0.5), 1),
            "latency_p95_ms": round(percentile([r["latency_ms"] for r in rows], 0.95), 1),
            "context_tokens_mean": round(statistics.fmean(r["context_tokens"] for r in rows), 1) if rows else None}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--set", required=True, type=Path, dest="question_set")
    parser.add_argument("--corpus", default="fulltext", choices=["fulltext", "discovery"])
    parser.add_argument("--channels", default="identity,lexical,dense")
    parser.add_argument("--label", default="run")
    parser.add_argument("--scratch", action="store_true", help="复制数据目录到临时目录再评估（样例数据必须用）")
    parser.add_argument("--fake-vectors", action="store_true", help="用确定性假向量（只验证流程，不代表效果）")
    parser.add_argument("--legacy-windows", action="store_true", help="B-1：修复截断之前的分窗方式")
    parser.add_argument("--model", help="换用本机已安装的嵌入模型（见 burrowl models install），不改数据目录配置")
    parser.add_argument("--max-tokens", type=int, help="与 --model 一起用：调小模型上限")
    parser.add_argument("--chunking", default="auto", choices=["auto", "late", "windows"],
                        help="与 --model 一起用：向量切分方式（R17；late 只对平均池化的长上下文模型生效）")
    parser.add_argument("--late-context", type=int, default=2048, help="与 --model 一起用：后切分的上下文长度")
    parser.add_argument("--span-tokens", type=int, default=512, help="与 --model 一起用：后切分每个向量的最大长度")
    parser.add_argument("--graph-route", action="store_true", help="打开图路线（R12 T-P2.9，默认关闭）")
    parser.add_argument("--ablate", default="", help="关掉的 P1 改动，逗号分隔：envelope,record_text")
    parser.add_argument("--out", type=Path, help="结果目录；默认 <数据目录>/eval/runs，--scratch 时为 build/eval/runs")
    args = parser.parse_args(argv)

    questions = load_set(args.question_set)
    ablations = [a for a in args.ablate.split(",") if a]
    if set(ablations) - {"envelope", "record_text"}:
        raise SystemExit(f"未知的消融项：{ablations}")
    if ablations:
        ablate(ablations)
    channels = tuple(c.strip() for c in args.channels.split(",") if c.strip())
    data_dir = args.data_dir.resolve()
    out_dir = args.out or (Path("build/eval/runs") if args.scratch else data_dir / "eval" / "runs")
    with tempfile.TemporaryDirectory(prefix="burrowl-eval-") as tmp:
        if args.scratch:
            shutil.copytree(data_dir, Path(tmp) / "data", ignore=shutil.ignore_patterns("index.sqlite*", "eval"))
            data_dir = Path(tmp) / "data"
        embedder = None
        if args.model:
            from burrowl.index import model_store  # noqa: PLC0415
            from burrowl.index.embed import FastEmbedEmbedder  # noqa: PLC0415
            spec = model_store.EMBEDDING_MODELS[args.model]
            embedder = FastEmbedEmbedder(model_store.default_models_dir() / spec["dir"], spec["model"], spec["dim"],
                                         max_tokens=args.max_tokens, query_prefix=spec["query_prefix"],
                                         chunking=args.chunking, late_context_tokens=args.late_context,
                                         span_tokens=args.span_tokens)
        app = App.open(data_dir, fake_vectors=args.fake_vectors, embedder=embedder)
        embedder = app.index.db.embedder
        if args.legacy_windows:
            if embedder is None or not hasattr(embedder, "tokenizer"):
                raise SystemExit("--legacy-windows 需要真实嵌入模型")
            legacy_windows(embedder)
        started = time.perf_counter()
        app.index.reconcile(force=True)
        route = app.config.current().index.graph_route
        if args.graph_route:
            route = route.model_copy(update={"enabled": True})
        build_seconds = time.perf_counter() - started
        count = getattr(embedder, "count_tokens", None) or len

        rows = []
        for item in questions:
            variants = [{"language": "zh", "question": v} for v in item.get("variants", [])][:2]
            plan = query_plan.build(item["question"], variants=variants)
            hits, timings = {}, {}
            for group in ("record", "owner"):
                t0 = time.perf_counter()
                hits[group] = app.index.search_plan(plan, corpus=args.corpus, group=group, channels=channels,
                                                    dense="dense" in channels, graph_route=route)
                timings[group] = (time.perf_counter() - t0) * 1000
            latency = timings["record"]  # 延迟只计记录级那一次调用
            records = ranked_ids(hits["record"]["items"], "record_id")
            owners = ranked_ids(hits["owner"]["items"], "owner_id")
            expected = item.get("expected_records", [])
            expected_owners = item.get("expected_owners", [])
            top_items = hits["record"]["items"][:TOP]
            rows.append({
                "id": item["id"], "type": item["type"],
                "recall@5_record": recall(expected, records, 5), "recall@10_record": recall(expected, records, 10),
                "recall@5_owner": recall(expected_owners, owners, 5),
                "recall@10_owner": recall(expected_owners, owners, 10),
                "mrr@10": mrr(expected, records),
                "must_not_hit": bool(set(item.get("must_not", [])) & set(records[:TOP])),
                "latency_ms": latency,
                "context_tokens": sum(count(f"{h['title']}\n{h['text']}") for h in top_items),
                "top": [{"record_id": h["record_id"], "block_id": h.get("block_id"), "title": h["title"],
                         "score": round(h["score"], 5), "routes": [s["route"] for s in h["query_sources"]]}
                        for h in top_items],
                "gaps": hits["record"]["gaps"]})

    by_type = {t: summarize([r for r in rows if r["type"] == t]) for t in sorted({r["type"] for r in rows})}
    report = {
        "label": args.label, "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "question_set": {"path": str(args.question_set), "sha256":
                         hashlib.sha256(args.question_set.read_bytes()).hexdigest()[:16], "count": len(questions)},
        "corpus": args.corpus, "channels": list(channels), "ablations": ablations,
        "legacy_windows": args.legacy_windows, "graph_route": args.graph_route,
        "model_fp": getattr(embedder, "fingerprint", None), "max_tokens": getattr(embedder, "max_tokens", None),
        "config_fp": hashlib.sha256(app.config.current().model_dump_json().encode()).hexdigest()[:16],
        "projection_versions": {"fulltext": projections.FULLTEXT_VERSION, "discovery": projections.DISCOVERY_VERSION},
        "index_build_seconds": round(build_seconds, 2),
        "overall": summarize(rows), "by_type": by_type, "questions": rows}
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = out_dir / f"{stamp}-{args.label}.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    columns = ["n", "recall@5_record", "recall@10_record", "recall@10_owner", "mrr@10", "must_not_rate",
               "latency_p50_ms", "latency_p95_ms", "context_tokens_mean"]
    print(f"## {args.label}（{args.corpus}，{'+'.join(channels)}，模型 {report['model_fp']}）\n")
    print("| 题型 | " + " | ".join(columns) + " |")
    print("|---" * (len(columns) + 1) + "|")
    for name, summary in [("全部", report["overall"]), *by_type.items()]:
        print(f"| {name} | " + " | ".join("—" if summary[c] is None else str(summary[c]) for c in columns) + " |")
    print(f"\n结果：{target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
