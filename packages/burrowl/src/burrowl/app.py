"""组合根：按数据目录与配置装配所有服务。接口层（HTTP / MCP / CLI）只通过 App 访问服务。

模型目录查找顺序：配置中的路径 → 本机模型目录（BURROWL_MODELS_DIR 或 %LOCALAPPDATA%/burrowl/models，见 index/model_store）
→ 不可用（向量/重排如实报告降级）。用 `burrowl models install` 安装。
"""
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .core.config import BurrowlConfig
from .index.embed import Embedder
from .index.model_store import EMBEDDING_DIR, RERANKER_DIR, default_models_dir, embedding_dir_for
from .services.config import ConfigService
from .services.hub import HubService
from .services.indexing import IndexService, build_embedder
from .services.maintenance import MaintenanceService
from .services.memory.context import Storage
from .services.memory.service import MemoryService
from .services.monitor import Monitor
from .services.query.service import QueryService
from .services.reading.service import ReadingService
from .services.relations import WorkbenchPrefs
from .services.review import ReviewService, claim_lookup_factory
from .services.runs import RunService
from .storage.config_store import ConfigStore
from .storage.layout import Workspace


@dataclass
class App:
    root: Path
    storage: Storage
    config: ConfigService
    index: IndexService
    memory: MemoryService
    review: ReviewService
    query: QueryService
    reading: ReadingService
    runs: RunService
    maintenance: MaintenanceService
    monitor: Monitor
    prefs: WorkbenchPrefs
    hub: HubService
    lock: threading.RLock = field(default_factory=threading.RLock)
    # 本进程的接入方式，由启动参数给出、不写入数据目录：
    # work_dir 是用户/agent 放原始数据与代码的工作目录（登记其中的文件时自动建工作根），mcp_mode 覆盖配置中的运行模式。
    work_dir: Path | None = None
    mcp_mode: str | None = None
    skills_dir: Path | None = None   # 工作流说明（Skill 与公共规则）所在目录，供 system.guide 读取

    @classmethod
    def open(cls, data_dir: str | Path, *, create: bool = False, embedder: Embedder | None = None,
             fake_vectors: bool = False) -> "App":
        workspace = Workspace.open(data_dir, create=create)
        storage = Storage.open(workspace.root)
        config_service = ConfigService(ConfigStore(workspace))
        cfg: BurrowlConfig = config_service.current()
        shared = default_models_dir()
        if embedder is None and not fake_vectors:
            embedder = build_embedder(cfg.index, workspace.root,
                                      default_model_dir=(embedding_dir_for(shared, cfg.index.embedding.model)
                                                         or shared / EMBEDDING_DIR) if shared else None)
        index = IndexService.open(storage, cfg.index, embedder=embedder, fake_vectors=fake_vectors,
                                  concept_owner=lambda: config_service.current().graph.concept_owner_id)
        memory = MemoryService(storage, index=index, claim_lookup_factory=claim_lookup_factory(storage),
                               concept_owner=lambda: config_service.current().graph.concept_owner_id)
        review = ReviewService(storage, memory)
        hub = HubService(storage, config_service.current,
                         graph_source=lambda: (index.ensure_graph(), index.db.graph_nodes(),
                                               index.db.graph_all_edges())[1:])
        reranker = Path(cfg.index.reranker.path) if cfg.index.reranker.path else (shared / RERANKER_DIR if shared else None)
        if reranker is not None and not reranker.is_absolute():
            reranker = workspace.root / reranker
        return cls(root=workspace.root, storage=storage, config=config_service, index=index, memory=memory,
                   review=review, query=QueryService(storage, index, config_service.current, hub=hub),
                   reading=ReadingService(storage, index, config_service.current,
                                          reranker_dir=reranker if reranker and reranker.is_dir() else None),
                   runs=RunService(storage), maintenance=MaintenanceService(memory, review),
                   monitor=Monitor(storage), prefs=WorkbenchPrefs(storage),
                   hub=hub)

    def capabilities(self) -> dict:
        """当前可用能力（供工作台与 agent 了解降级情况）。"""
        cfg = self.config.current()
        return {"data_dir": str(self.root), "work_dir": str(self.work_dir) if self.work_dir else None,
                "mcp_mode": self.mcp_mode or cfg.mcp.mode,
                "vector": {"backend": cfg.index.vector.kind, "embedder": getattr(self.index.db.embedder, "fingerprint", None),
                           "ready": self.index.db.embedder is not None},
                "reranker": {"ready": self.reading.reranker_dir is not None, "mode": cfg.reading.reranking.mode},
                "connectors": [{"id": c.id, "type": c.type, "enabled": c.enabled} for c in cfg.connectors]}
