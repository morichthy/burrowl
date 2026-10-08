"""工作区配置契约（`.burrowl/config.json`）。MCP 工具与工作台设置页读写的是同一个模型。

所有字段都有默认值：文件缺失时使用默认配置；保存时写完整快照。
配置只是后续请求的默认值：不改变已存在阅读会话的额度，也不授予任何来源权限。
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

LEVEL_NAMES = ("L0", "L1", "L2", "L3", "L4")
ReviewFilter = Literal["all", "reviewed_only", "exclude_unsafe", "unreviewed_only"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Budget(_Model):
    """累计硬上限。wall_ms 计活动执行时间（不含用户阅读停留）；0 表示禁止该类操作。"""

    wall_ms: int = Field(300_000, ge=1, le=600_000)
    read_bytes: int = Field(16 * 1024 * 1024, ge=0, le=32 * 1024 * 1024)
    output_chars: int = Field(80_000, ge=0, le=100_000)
    candidates: int = Field(100, ge=0, le=2000)
    graph_nodes: int = Field(50, ge=0, le=1000)
    graph_edges: int = Field(100, ge=0, le=2000)
    graph_hops: int = Field(2, ge=0, le=6)
    model_tokens: int = Field(512, ge=0, le=200_000)
    model_calls: int = Field(1, ge=0, le=20)
    model_input_tokens: int = Field(512, ge=0, le=150_000)
    model_output_tokens: int = Field(0, ge=0, le=50_000)
    rerank_items: int = Field(0, ge=0, le=200)




def reading_budget() -> Budget:
    return Budget(candidates=1000, model_calls=20, model_tokens=65536, model_input_tokens=65536, rerank_items=100)


class Collaboration(_Model):
    subagents: Literal["auto", "off"] = "auto"
    subagent_requirements: str = Field(
        "优先使用低成本、较低能力的可用模型，采用低推理深度；须能完成材料检索、完整阅读和结构化笔记。",
        min_length=1, max_length=2000)


class QueryDefaults(_Model):
    result_limit: int = Field(20, ge=1, le=100)
    review_filter: ReviewFilter = "all"
    budget: Budget = Field(default_factory=Budget)


class Association(_Model):
    enabled: bool = True
    max_rounds: int = Field(3, ge=1, le=20)


class ReadingContext(_Model):
    max_owners: int = Field(10, ge=1, le=100)
    note_max_tokens: int = Field(6000, ge=512, le=50_000)


class Screening(_Model):
    regular_windows: int = Field(3, ge=1, le=4)
    max_windows: int = Field(4, ge=1, le=4)
    batch_owners: int = Field(10, ge=1, le=100)

    @model_validator(mode="after")
    def _windows(self):
        if self.regular_windows > self.max_windows:
            raise ValueError("每个 Owner 的常规窗口数不能大于窗口上限")
        return self


class Reranking(_Model):
    mode: Literal["off", "auto", "required"] = "auto"
    candidate_limit: int = Field(30, ge=1, le=100)
    window_tokens: int = Field(512, ge=64, le=512)


class ReadingDefaults(_Model):
    strategy: Literal["standard", "associative", "quick"] = "standard"
    review_filter: ReviewFilter = "all"
    result_limit: int = Field(10, ge=1, le=100)
    association: Association = Field(default_factory=Association)
    context: ReadingContext = Field(default_factory=ReadingContext)
    screening: Screening = Field(default_factory=Screening)
    reranking: Reranking = Field(default_factory=Reranking)
    budget: Budget = Field(default_factory=reading_budget)

    @model_validator(mode="after")
    def _rerank_window(self):
        if self.reranking.mode != "off" and self.reranking.candidate_limit < self.result_limit:
            raise ValueError("重排候选窗不能小于阅读交付数量")
        return self


class Embedding(_Model):
    provider: Literal["fastembed", "none"] = "fastembed"
    model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    path: str | None = Field(None, description="本地模型目录；相对路径相对数据目录。为空时使用安装包自带模型")
    dim: int = Field(384, ge=1, le=4096)
    max_tokens: int | None = Field(None, ge=16, le=131072,
                                   description="模型输入上限（token）；为空时读取模型自带值。只能调小，不能超过模型真实上限")
    query_prefix: str | None = Field(None, max_length=500,
                                     description="查询指令前缀（如 Qwen3 的 Instruct…Query:）；fastembed 不会自动添加")
    passage_prefix: str | None = Field(None, max_length=500, description="文档指令前缀；多数模型不需要")


class VectorBackend(_Model):
    kind: Literal["sqlite-vec", "qdrant-server", "none"] = "sqlite-vec"
    url: str | None = Field(None, description="qdrant-server 的地址，如 http://127.0.0.1:6333")
    secret_ref: str | None = None


class RerankerModel(_Model):
    path: str | None = Field(None, description="ONNX Cross-encoder 模型目录；为空时使用安装包自带模型")


class CategoryWeights(_Model):
    """图路线中各类边的权重（R12 §4.4）。关联边按确定度取三档之一。"""
    structure: float = Field(0.3, ge=0, le=1)
    evidence: float = Field(1.0, ge=0, le=1)
    reference: float = Field(0.6, ge=0, le=1)
    navigation_supported: float = Field(0.7, ge=0, le=1)
    navigation_plausible: float = Field(0.4, ge=0, le=1)
    navigation_speculative: float = Field(0.2, ge=0, le=1)
    concept: float = Field(0.5, ge=0, le=1)
    claim_relation: float = Field(0.6, ge=0, le=1, description="再乘结论的成熟度系数")


class GraphRoute(_Model):
    """图路线（R12 §4.11）：从关键词与向量融合后的前几名出发沿图扩展，作为 RRF 中的一路。只是导航。"""
    enabled: bool = Field(False, description="默认关闭；评估显示收益后再由用户决定是否打开（D-24）")
    hops: int = Field(1, ge=1, le=2)
    seeds: int = Field(20, ge=1, le=100)
    fanout: int = Field(20, ge=1, le=200, description="每个节点最多扩展的边数")
    weight: float = Field(0.5, ge=0, le=2, description="在 RRF 融合中的路线权重")
    category_weights: CategoryWeights = Field(default_factory=CategoryWeights)
    algorithm: Literal["hops"] = "hops"


class HubRoute(_Model):
    """中枢路线（R12 §4.11、D-23）：外部检索作为 RRF 中的一路。命中必须对上本地记录、回到固定版本核验。"""
    connectors: list[str] = Field(default_factory=list, description="参与的连接器 id；为空表示关闭")
    weight: float = Field(0.5, ge=0, le=2)
    timeout_s: float = Field(10.0, gt=0, le=120, description="单次外部检索的超时；超时按不可用处理并报告缺口")
    limit: int = Field(20, ge=1, le=100)


class IndexConfig(_Model):
    path: str = Field(".burrowl/index/index.sqlite3", description="检索索引文件；可放到其他磁盘，删除后可重建")
    embedding: Embedding = Field(default_factory=Embedding)
    vector: VectorBackend = Field(default_factory=VectorBackend)
    reranker: RerankerModel = Field(default_factory=RerankerModel)
    ocr_enabled: bool = True
    graph_route: GraphRoute = Field(default_factory=GraphRoute)
    hub_route: HubRoute = Field(default_factory=HubRoute)


class ServerConfig(_Model):
    host: Literal["127.0.0.1"] = "127.0.0.1"
    port: int = Field(0, ge=0, le=65535, description="0 表示随机端口")


DEFAULT_TOOLSETS = ["memory", "review", "query", "reading", "provenance", "relations", "maintenance", "monitor", "exchange",
                    "runs", "sources", "index", "hub", "wiki", "config", "system"]


class McpConfig(_Model):
    toolsets: list[str] = Field(default_factory=lambda: list(DEFAULT_TOOLSETS))
    mode: Literal["auto", "manual"] = Field(
        "auto", description="auto：接入 burrowl 的对话默认按 burrowl-work / burrowl-read / burrowl-consolidate 工作，成果存入本数据目录；"
                            "manual：只在用户明确要求时使用。启动参数 --mode 优先；修改后需重启 MCP 服务")


class PublishPolicy(_Model):
    """哪些知识发布到外部知识中枢。默认只发布已复核的 L4/L3 概览经验与成稿文档。"""

    levels: list[Literal["L0", "L1", "L2", "L3", "L4"]] = Field(default_factory=lambda: ["L4", "L3"])
    include_documents: bool = True
    review_filter: ReviewFilter = "reviewed_only"
    owner_types: list[str] | None = None
    owner_ids: list[str] | None = None
    exclude_sensitivity: list[Literal["internal", "confidential", "restricted"]] = Field(
        default_factory=lambda: ["confidential", "restricted"])
    on_retract: Literal["delete", "mark"] = "delete"


class WikiSettings(_Model):
    mode: Literal["off", "auto", "authored", "both"] = "both"
    synthesis_model_id: str | None = Field(None, description="WeKnora 自动 Wiki 使用的模型 ID")


class ConnectorConfig(_Model):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,31}$")
    type: str = Field(pattern=r"^[a-z][a-z0-9-]{0,31}$", description="连接器类型，如 weknora、mdvault")
    enabled: bool = True
    base_url: str | None = None
    tenant_id: str | None = None
    secret_ref: str | None = Field(None, description="keyring:burrowl/<id> 或 env:VAR；配置文件里从不存明文")
    collection: str | None = Field(None, description="目标知识库（WeKnora 为知识库 ID 或名称）")
    options: dict[str, str | int | bool | None] = Field(default_factory=dict)
    publish: PublishPolicy = Field(default_factory=PublishPolicy)
    wiki: WikiSettings = Field(default_factory=WikiSettings)


class GraphSettings(_Model):
    concept_owner_id: str | None = Field(
        None, description="概念库对象（owner_type=knowledge）的 owner_id；为空时 [[概念]] 链接都记为未解析，不阻塞提交")


class BurrowlConfig(_Model):
    schema_version: Literal[1] = 1
    collaboration: Collaboration = Field(default_factory=Collaboration)
    query: QueryDefaults = Field(default_factory=QueryDefaults)
    reading: ReadingDefaults = Field(default_factory=ReadingDefaults)
    index: IndexConfig = Field(default_factory=IndexConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    mcp: McpConfig = Field(default_factory=McpConfig)
    connectors: list[ConnectorConfig] = Field(default_factory=list)
    graph: GraphSettings = Field(default_factory=GraphSettings)

    @model_validator(mode="after")
    def _unique_connectors(self):
        ids = [c.id for c in self.connectors]
        if len(ids) != len(set(ids)):
            raise ValueError("连接器 id 不能重复")
        return self

    def connector(self, connector_id: str) -> ConnectorConfig:
        for item in self.connectors:
            if item.id == connector_id:
                return item
        raise KeyError(connector_id)
