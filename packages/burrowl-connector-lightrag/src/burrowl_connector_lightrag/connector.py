"""LightRAG 连接器（R12 D-22、T-P4.1）：图模式——导入 burrowl 整理好的图，不让 LightRAG 自己抽取实体。

为什么"按需重建"而不是逐条更新（T-P4.0 实测 lightrag-hku 1.5.7）：
  自定义图导入（insert_custom_kg）不写文档状态，adelete_by_doc_id 删不掉它导入的内容；
  LightRAG 又按名字合并实体（多条记录提到同一个概念时只有一个实体），无法按来源删除。
  所以：
  - upsert / delete 只更新本连接器的"暂存表"（每个发布条目一行），不碰 LightRAG；
  - 发布执行完后 finalize()：与上次构建比较——
      只有新增、且新增的实体名都没出现过 → 追加到当前构建；
      有修改、删除或实体名重叠 → 在新目录里整体重建，成功后切换"当前构建"，再删除旧目录；
    重建期间旧构建照常可查，重建失败旧构建不受影响；
  - 嵌入按"文本指纹 → 向量"缓存在 embed-cache.sqlite：内容没变的块和实体不再重算，重建主要是文件读写。
查询：aquery_data(only_need_context=True)，并由本连接器给出关键词，因此不调用 LLM；
命中的 chunk 带我们写入的 file_path（= local_key），据此回到本地固定版本核验。

目录（options.path 下）：
  embed-cache.sqlite            嵌入缓存（所有集合共用；换嵌入模型时按模型指纹隔离）
  <集合>/items.sqlite           暂存表与构建状态
  <集合>/builds/<构建 ID>/      LightRAG 工作目录
"""
import asyncio
import hashlib
import json
import re
import shutil
import sqlite3
import struct
import threading
import uuid
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

from burrowl.connectors.spi import (
    Capabilities,
    Collection,
    ConnectorError,
    ExternalItem,
    ExternalRef,
    Hit,
    PublishItem,
)

GENERATED = re.compile(r"\n?<!-- burrowl:generated:start -->.*?<!-- burrowl:generated:end -->\n?", re.S)
_SAFE = re.compile(r"[^0-9A-Za-z._-]+")
CHUNK_CHARS = 2400          # 单块上限（字符）；按 "## " 标题切，过长再按段落切
SCHEMA = """
CREATE TABLE IF NOT EXISTS items (local_key TEXT PRIMARY KEY, title TEXT NOT NULL, markdown TEXT NOT NULL,
    graph TEXT, content_hash TEXT NOT NULL, ref_uri TEXT, fixed_ref TEXT);
CREATE TABLE IF NOT EXISTS build (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def file_token(local_key: str) -> str:
    """写进 chunk.file_path 的标记。LightRAG 会规范化 file_path（只保留文件名部分），local_key 里的 ":" "/" 不能原样保留，
    所以写一个由 local_key 派生、不含分隔符的标记，查询时再映射回来。"""
    return "lk-" + hashlib.sha256(local_key.encode("utf-8")).hexdigest()[:24]


def _run(coro):
    """在独立线程的新事件循环里执行（调用方可能已在事件循环中，如工作台的异步接口）。"""
    box: dict[str, Any] = {}

    def target():
        try:
            box["value"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001 - 原样转交给调用线程
            box["error"] = exc
    thread = threading.Thread(target=target)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


def split_chunks(markdown: str) -> list[str]:
    """按二级标题切块；单块过长再按空行切。去掉自动生成区块（反链等对外部检索是噪声）。"""
    text = GENERATED.sub("\n", markdown).strip()
    sections = re.split(r"\n(?=## )", text)
    chunks: list[str] = []
    for section in sections:
        while len(section) > CHUNK_CHARS:
            cut = section.rfind("\n\n", 0, CHUNK_CHARS)
            cut = cut if cut > CHUNK_CHARS // 3 else CHUNK_CHARS
            chunks.append(section[:cut].strip())
            section = section[cut:]
        if section.strip():
            chunks.append(section.strip())
    return chunks or [text or "(空)"]


class EmbeddingCache:
    """文本指纹 → 向量（float32）。按模型指纹隔离：换模型后旧缓存不会被误用。"""

    def __init__(self, path: Path, model_fp: str):
        self.path, self.model_fp = path, model_fp
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db:
            db.execute("CREATE TABLE IF NOT EXISTS vectors (model_fp TEXT, digest TEXT, vector BLOB, "
                       "PRIMARY KEY(model_fp, digest))")
            db.commit()

    def lookup(self, texts: list[str]) -> dict[str, list[float]]:
        digests = {hashlib.sha256(t.encode("utf-8")).hexdigest(): t for t in texts}
        found = {}
        with closing(sqlite3.connect(self.path)) as db:
            for digest in digests:
                row = db.execute("SELECT vector FROM vectors WHERE model_fp=? AND digest=?",
                                 (self.model_fp, digest)).fetchone()
                if row:
                    found[digests[digest]] = list(struct.unpack(f"{len(row[0]) // 4}f", row[0]))
        return found

    def store(self, pairs: dict[str, list[float]]) -> None:
        with closing(sqlite3.connect(self.path)) as db:
            db.executemany("INSERT OR REPLACE INTO vectors VALUES(?,?,?)",
                           [(self.model_fp, hashlib.sha256(t.encode("utf-8")).hexdigest(),
                             struct.pack(f"{len(v)}f", *v)) for t, v in pairs.items()])
            db.commit()


class CodepointTokenizer:
    """离线分词：每个字符一个 token。LightRAG 默认用 tiktoken，首次使用要联网下载编码文件，离线环境会失败。
    只用于 LightRAG 内部的长度预算：中文接近真实 token 数，英文会高估（预算因此偏保守）。无状态，可共享。"""

    def encode(self, content: str) -> list[int]:
        return [ord(ch) for ch in content]

    def decode(self, tokens: list[int]) -> str:
        return "".join(chr(t) for t in tokens)

    def __deepcopy__(self, memo):
        return self


def default_rag_factory(working_dir: str, embed: Callable, dim: int):
    """真实的 LightRAG 实例。LLM 函数在图模式下不应被调用；被调用就是配置或版本问题，直接报错。"""
    try:
        import numpy as np  # noqa: PLC0415 - 可选依赖（burrowl-connector-lightrag[runtime]）
        from lightrag import LightRAG  # noqa: PLC0415
        from lightrag.utils import EmbeddingFunc, Tokenizer  # noqa: PLC0415
    except ImportError as exc:
        raise ConnectorError("未安装 LightRAG（burrowl-connector-lightrag[runtime]，lightrag-hku==1.5.7）") from exc

    async def no_llm(prompt, system_prompt=None, history_messages=None, **kwargs):
        raise ConnectorError("LightRAG 图模式不调用 LLM；出现这个错误说明查询没有带关键词或导入时发生了实体合并")

    async def embedding(texts):
        return np.array(await embed(list(texts)), dtype=np.float32)

    return LightRAG(working_dir=working_dir, llm_model_func=no_llm,
                    tokenizer=Tokenizer("codepoints", CodepointTokenizer()),
                    embedding_func=EmbeddingFunc(embedding_dim=dim, max_token_size=8192, func=embedding))


class LightRagConnector:
    type = "lightrag"

    def __init__(self, connector_id: str, *, base_url: str | None = None, api_key: str | None = None,
                 tenant_id: str | None = None, options: dict | None = None,
                 embedder=None, rag_factory: Callable | None = None):
        self.id = connector_id
        options = options or {}
        if not options.get("path"):
            raise ConnectorError("lightrag 需要 options.path（工作目录）")
        self.root = Path(str(options["path"])).resolve()
        self.top_k = int(options.get("top_k", 20))
        self._embedder = embedder
        self._rag_factory = rag_factory or default_rag_factory
        self._lock = threading.Lock()

    # ---------- 基础 ----------
    def embedder(self):
        if self._embedder is None:
            from burrowl.index import model_store  # noqa: PLC0415 - 只在真正需要嵌入时加载模型
            from burrowl.index.embed import FastEmbedEmbedder  # noqa: PLC0415
            spec = model_store.EMBEDDING_MODELS["minilm"]
            self._embedder = FastEmbedEmbedder(model_store.default_models_dir() / spec["dir"], spec["model"],
                                               spec["dim"])
        return self._embedder

    def _dir(self, collection: Collection) -> Path:
        target = (self.root / collection.id).resolve()
        if not target.is_relative_to(self.root):
            raise ConnectorError("路径越出 LightRAG 工作目录")
        return target

    def _db(self, collection: Collection) -> sqlite3.Connection:
        path = self._dir(collection) / "items.sqlite"
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path)
        db.executescript(SCHEMA)
        return db

    def _meta(self, db: sqlite3.Connection, key: str, default: Any = None) -> Any:
        row = db.execute("SELECT value FROM build WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def _set_meta(self, db: sqlite3.Connection, **values: Any) -> None:
        db.executemany("INSERT OR REPLACE INTO build VALUES(?,?)",
                       [(k, json.dumps(v, ensure_ascii=False)) for k, v in values.items()])

    def capabilities(self) -> Capabilities:
        return Capabilities(async_ingest=True, metadata_max_keys=50, graph_import=True)

    def health(self) -> dict[str, Any]:
        try:
            import lightrag  # noqa: PLC0415, F401 - 只检查是否已安装
            installed = True
        except ImportError:
            installed = False
        return {"ok": installed or self._rag_factory is not default_rag_factory, "server_version": "lightrag-1.5.7",
                "compat": "ok" if installed else "missing", "path": str(self.root),
                "mode": "graph (custom KG, rebuild on change)"}

    def ensure_collection(self, name: str) -> Collection:
        safe = _SAFE.sub("_", name)[:80] or "default"
        (self.root / safe).mkdir(parents=True, exist_ok=True)
        return Collection(safe, name)

    # ---------- 暂存（不碰 LightRAG）----------
    def upsert(self, collection: Collection, item: PublishItem, existing: ExternalRef | None) -> ExternalRef:
        with closing(self._db(collection)) as db:
            db.execute("INSERT OR REPLACE INTO items VALUES(?,?,?,?,?,?,?)",
                       (item.local_key, item.title, item.markdown, json.dumps(item.graph or {}, ensure_ascii=False),
                        item.content_hash, item.ref_uri, json.dumps(item.fixed_ref, ensure_ascii=False)))
            db.commit()
        return ExternalRef(item.local_key, "processing", "等待本次发布结束后构建")

    def delete(self, collection: Collection, ref: ExternalRef) -> None:
        with closing(self._db(collection)) as db:
            db.execute("DELETE FROM items WHERE local_key=?", (ref.external_id,))
            db.commit()

    def status(self, refs: list[ExternalRef]) -> list[ExternalRef]:
        out = []
        for ref in refs:
            found = None
            for path in self.root.glob("*/items.sqlite"):
                with closing(sqlite3.connect(path)) as db:
                    db.executescript(SCHEMA)
                    row = db.execute("SELECT content_hash FROM items WHERE local_key=?", (ref.external_id,)).fetchone()
                    built = self._meta(db, "built_items", {})
                if row:
                    found = "completed" if built.get(ref.external_id) == row[0] else "processing"
                    break
            out.append(ExternalRef(ref.external_id, found or "missing"))
        return out

    def list_items(self, collection: Collection) -> list[ExternalItem]:
        with closing(self._db(collection)) as db:
            return [ExternalItem(k, k, h, t) for k, h, t in db.execute("SELECT local_key, content_hash, title FROM items")]

    def read(self, ref: ExternalRef) -> dict[str, Any]:
        for path in self.root.glob("*/items.sqlite"):
            with closing(sqlite3.connect(path)) as db:
                db.executescript(SCHEMA)
                row = db.execute("SELECT title, markdown FROM items WHERE local_key=?", (ref.external_id,)).fetchone()
            if row:
                return {"title": row[0], "content": row[1], "metadata": {"local_key": ref.external_id}}
        raise ConnectorError("not found", status=404)

    # ---------- 构建 ----------
    def _embed(self, cache: EmbeddingCache):
        async def embed(texts: list[str]) -> list[list[float]]:
            known = cache.lookup(texts)
            missing = [t for t in dict.fromkeys(texts) if t not in known]
            if missing:
                fresh = dict(zip(missing, self.embedder().embed_passages(missing), strict=True))
                cache.store(fresh)
                known |= fresh
            self.embed_stats["cached"] += len(texts) - len(missing)
            self.embed_stats["computed"] += len(missing)
            return [known[t] for t in texts]
        return embed

    @staticmethod
    def custom_kg(rows: list[tuple]) -> dict[str, list[dict]]:
        """暂存条目 → LightRAG 的 custom_kg。chunk 的 file_path 写 local_key 的标记（file_token），查询命中据此回到本地。
        同名实体只保留一个（来源取第一个声明它的条目），避免 LightRAG 合并描述时调用 LLM。"""
        chunks, entities, relationships = [], {}, {}
        for local_key, title, markdown, graph_json in rows:
            parts = split_chunks(markdown)
            for index, part in enumerate(parts):
                chunks.append({"content": f"{title}\n{part}", "source_id": f"{local_key}#{index}",
                               "file_path": file_token(local_key), "chunk_order_index": index})
            graph = json.loads(graph_json or "{}")
            names = {e["id"]: e["name"] for e in graph.get("entities", [])}
            for entity in graph.get("entities", []):
                entities.setdefault(entity["name"], {"entity_name": entity["name"], "entity_type": entity["type"],
                                                     "description": entity["description"],
                                                     "source_id": f"{local_key}#0"})
            for rel in graph.get("relationships", []):
                src, dst = names.get(rel["src"]), names.get(rel["dst"])
                if not src or not dst or src == dst:
                    continue
                certainty = f"，确定度 {rel['certainty']}" if rel.get("certainty") else ""
                relationships.setdefault((src, dst, rel["type"]), {
                    "src_id": src, "tgt_id": dst, "keywords": rel["type"],
                    "description": f"{rel['type']}（{rel['category']}{certainty}）", "weight": 1.0,
                    "source_id": f"{local_key}#0"})
        return {"chunks": chunks, "entities": list(entities.values()), "relationships": list(relationships.values())}

    def finalize(self, collection: Collection) -> dict[str, Any]:
        """把暂存表同步进 LightRAG：没变化跳过；只有新增且实体名都是新的就追加；否则整体重建后切换。"""
        with self._lock, closing(self._db(collection)) as db:
            rows = db.execute("SELECT local_key, title, markdown, graph, content_hash FROM items ORDER BY local_key").fetchall()
            current = {r[0]: r[4] for r in rows}
            built = self._meta(db, "built_items", {})
            build_id = self._meta(db, "current_build")
            if build_id and current == built:
                return {"ok": True, "action": "unchanged", "build": build_id, "items": len(rows)}
            added = {k for k in current if k not in built}
            only_additions = build_id is not None and all(built.get(k) == current.get(k) for k in built)
            fresh_rows = [r[:4] for r in rows if r[0] in added]
            known_names = set(self._meta(db, "entity_names", []))
            kg_new = self.custom_kg(fresh_rows)
            new_names = {e["entity_name"] for e in kg_new["entities"]}
            self.embed_stats = {"cached": 0, "computed": 0}
            embedder = self.embedder()
            cache = EmbeddingCache(self.root / "embed-cache.sqlite", getattr(embedder, "fingerprint", "unknown"))
            dim = getattr(embedder, "dim", 384)
            if only_additions and not (new_names & known_names):
                target = self._dir(collection) / "builds" / build_id
                _run(self._insert(target, kg_new, cache, dim))
                action, names = "appended", known_names | new_names
            else:
                kg = self.custom_kg([r[:4] for r in rows])
                build_id = uuid.uuid4().hex[:12]
                target = self._dir(collection) / "builds" / build_id
                try:
                    _run(self._insert(target, kg, cache, dim))
                except Exception:
                    shutil.rmtree(target, ignore_errors=True)   # 重建失败：旧构建不受影响
                    raise
                action, names = "rebuilt", {e["entity_name"] for e in kg["entities"]}
            self._set_meta(db, current_build=build_id, built_items=current, entity_names=sorted(names))
            db.commit()
        for old in (self._dir(collection) / "builds").iterdir():
            if old.is_dir() and old.name != build_id:
                shutil.rmtree(old, ignore_errors=True)          # 切换之后再删旧构建
        return {"ok": True, "action": action, "build": build_id, "items": len(rows), "added": len(added),
                "embeddings": dict(self.embed_stats)}

    async def _insert(self, target: Path, kg: dict, cache: EmbeddingCache, dim: int) -> None:
        target.mkdir(parents=True, exist_ok=True)
        rag = self._rag_factory(str(target), self._embed(cache), dim)
        await rag.initialize_storages()
        try:
            if kg["chunks"] or kg["entities"]:
                await rag.ainsert_custom_kg(kg)
        finally:
            await rag.finalize_storages()

    # ---------- 检索 ----------
    def search(self, collections: list[Collection], query: str, limit: int) -> list[Hit]:
        hits: list[Hit] = []
        for collection in collections:
            with closing(self._db(collection)) as db:
                build_id = self._meta(db, "current_build")
                titles = dict(db.execute("SELECT local_key, title FROM items"))
            by_token = {file_token(k): k for k in titles}
            if not build_id:
                continue
            target = self._dir(collection) / "builds" / build_id
            self.embed_stats = {"cached": 0, "computed": 0}
            embedder = self.embedder()
            cache = EmbeddingCache(self.root / "embed-cache.sqlite", getattr(embedder, "fingerprint", "unknown"))
            data = _run(self._query(target, query, cache, getattr(embedder, "dim", 384), limit))
            seen: set[str] = set()
            for rank, chunk in enumerate(data.get("chunks", [])):
                local_key = by_token.get(Path(chunk.get("file_path") or "").name, "")
                if local_key in seen or local_key not in titles:
                    continue
                seen.add(local_key)
                hits.append(Hit(external_id=local_key, title=titles[local_key], content=chunk.get("content", "")[:300],
                                score=1.0 / (1 + rank), local_key=local_key))
        return sorted(hits, key=lambda h: -h.score)[:limit]

    async def _query(self, target: Path, query: str, cache: EmbeddingCache, dim: int, limit: int) -> dict:
        try:
            from lightrag import QueryParam  # noqa: PLC0415 - 可选依赖
        except ImportError:
            QueryParam = None  # noqa: N806 - 假实现（测试）不需要真实的 QueryParam
        rag = self._rag_factory(str(target), self._embed(cache), dim)
        await rag.initialize_storages()
        try:
            terms = [t for t in re.split(r"[\s，。、；：？！,.;:?!]+", query) if t] or [query]
            options = {"mode": "mix", "only_need_context": True, "hl_keywords": [query], "ll_keywords": terms,
                       "top_k": self.top_k, "chunk_top_k": max(limit, 10)}
            param = QueryParam(**options) if QueryParam else options
            result = await rag.aquery_data(query, param)
            return result.get("data", {}) if isinstance(result, dict) else {}
        finally:
            await rag.finalize_storages()
