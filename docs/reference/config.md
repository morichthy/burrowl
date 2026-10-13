# 配置参考（自动生成，勿手改）

配置文件：`<数据目录>/.burrowl/config.json`。通过工作台设置页、MCP `config_update` 或 `burrowl call config.update` 修改（JSON Merge Patch + expected_revision）。密钥只存引用。

## 默认值

```json
{
  "schema_version": 1,
  "collaboration": {
    "subagents": "auto",
    "subagent_requirements": "优先使用低成本、较低能力的可用模型，采用低推理深度；须能完成材料检索、完整阅读和结构化笔记。"
  },
  "query": {
    "result_limit": 20,
    "review_filter": "all",
    "budget": {
      "wall_ms": 300000,
      "read_bytes": 16777216,
      "output_chars": 80000,
      "candidates": 100,
      "graph_nodes": 50,
      "graph_edges": 100,
      "graph_hops": 2,
      "model_tokens": 512,
      "model_calls": 1,
      "model_input_tokens": 512,
      "model_output_tokens": 0,
      "rerank_items": 0
    }
  },
  "reading": {
    "strategy": "standard",
    "review_filter": "all",
    "result_limit": 10,
    "association": {
      "enabled": true,
      "max_rounds": 3,
      "expand_owners": 5,
      "min_score": 0.2,
      "concept_max_degree": 20,
      "target_nodes": 3,
      "target_pages": 2
    },
    "context": {
      "max_owners": 10,
      "note_max_tokens": 6000
    },
    "screening": {
      "regular_windows": 3,
      "max_windows": 4,
      "batch_owners": 10
    },
    "reranking": {
      "mode": "auto",
      "candidate_limit": 30,
      "window_tokens": 512
    },
    "budget": {
      "wall_ms": 300000,
      "read_bytes": 16777216,
      "output_chars": 80000,
      "candidates": 1000,
      "graph_nodes": 50,
      "graph_edges": 1000,
      "graph_hops": 2,
      "model_tokens": 65536,
      "model_calls": 20,
      "model_input_tokens": 65536,
      "model_output_tokens": 0,
      "rerank_items": 100
    }
  },
  "index": {
    "path": ".burrowl/index/index.sqlite3",
    "embedding": {
      "provider": "fastembed",
      "model": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
      "path": null,
      "dim": 384,
      "max_tokens": null,
      "query_prefix": null,
      "passage_prefix": null,
      "chunking": "auto",
      "late_context_tokens": 2048,
      "span_tokens": 512
    },
    "vector": {
      "kind": "sqlite-vec",
      "url": null,
      "secret_ref": null
    },
    "reranker": {
      "path": null
    },
    "ocr_enabled": true,
    "graph_route": {
      "enabled": false,
      "hops": 1,
      "seeds": 20,
      "fanout": 20,
      "weight": 0.5,
      "category_weights": {
        "structure": 0.3,
        "evidence": 1.0,
        "reference": 0.6,
        "navigation_supported": 0.7,
        "navigation_plausible": 0.4,
        "navigation_speculative": 0.2,
        "mention": 0.5,
        "claim_part": 0.6
      },
      "algorithm": "hops"
    },
    "hub_route": {
      "connectors": [],
      "weight": 0.5,
      "timeout_s": 10.0,
      "limit": 20
    }
  },
  "server": {
    "host": "127.0.0.1",
    "port": 0
  },
  "mcp": {
    "toolsets": [
      "memory",
      "review",
      "query",
      "reading",
      "provenance",
      "relations",
      "maintenance",
      "monitor",
      "exchange",
      "runs",
      "sources",
      "index",
      "hub",
      "wiki",
      "config",
      "system"
    ],
    "error_envelope": false,
    "mode": "auto"
  },
  "connectors": [],
  "graph": {
    "concept_owner_id": null
  },
  "diagnostics": {
    "request_log": "off"
  }
}
```

## JSON Schema

```json
{
  "$defs": {
    "Association": {
      "additionalProperties": false,
      "description": "联想 = 按对象的一跳图扩展（R19 D-06、D-11）。默认值是初值，等真实题集建成后再调。",
      "properties": {
        "enabled": {
          "default": true,
          "title": "Enabled",
          "type": "boolean"
        },
        "max_rounds": {
          "default": 3,
          "maximum": 20,
          "minimum": 1,
          "title": "Max Rounds",
          "type": "integer"
        },
        "expand_owners": {
          "default": 5,
          "description": "每轮最多新增的候选对象",
          "maximum": 20,
          "minimum": 1,
          "title": "Expand Owners",
          "type": "integer"
        },
        "min_score": {
          "default": 0.2,
          "description": "候选对象的最低分（图分数 × 焦点相关度之和）",
          "maximum": 10,
          "minimum": 0,
          "title": "Min Score",
          "type": "number"
        },
        "concept_max_degree": {
          "default": 20,
          "description": "度数不超过它的概念、来源才能中转（热门概念不中转）",
          "maximum": 1000,
          "minimum": 0,
          "title": "Concept Max Degree",
          "type": "integer"
        },
        "target_nodes": {
          "default": 3,
          "description": "每个候选对象的关系窗口与定点阅读节点上限",
          "maximum": 10,
          "minimum": 1,
          "title": "Target Nodes",
          "type": "integer"
        },
        "target_pages": {
          "default": 2,
          "description": "每个候选对象定点阅读的页数上限",
          "maximum": 10,
          "minimum": 1,
          "title": "Target Pages",
          "type": "integer"
        }
      },
      "title": "Association",
      "type": "object"
    },
    "Budget": {
      "additionalProperties": false,
      "description": "累计硬上限。wall_ms 计活动执行时间（不含用户阅读停留）；0 表示禁止该类操作。",
      "properties": {
        "wall_ms": {
          "default": 300000,
          "maximum": 600000,
          "minimum": 1,
          "title": "Wall Ms",
          "type": "integer"
        },
        "read_bytes": {
          "default": 16777216,
          "maximum": 33554432,
          "minimum": 0,
          "title": "Read Bytes",
          "type": "integer"
        },
        "output_chars": {
          "default": 80000,
          "maximum": 100000,
          "minimum": 0,
          "title": "Output Chars",
          "type": "integer"
        },
        "candidates": {
          "default": 100,
          "maximum": 2000,
          "minimum": 0,
          "title": "Candidates",
          "type": "integer"
        },
        "graph_nodes": {
          "default": 50,
          "maximum": 1000,
          "minimum": 0,
          "title": "Graph Nodes",
          "type": "integer"
        },
        "graph_edges": {
          "default": 100,
          "maximum": 2000,
          "minimum": 0,
          "title": "Graph Edges",
          "type": "integer"
        },
        "graph_hops": {
          "default": 2,
          "maximum": 6,
          "minimum": 0,
          "title": "Graph Hops",
          "type": "integer"
        },
        "model_tokens": {
          "default": 512,
          "maximum": 200000,
          "minimum": 0,
          "title": "Model Tokens",
          "type": "integer"
        },
        "model_calls": {
          "default": 1,
          "maximum": 20,
          "minimum": 0,
          "title": "Model Calls",
          "type": "integer"
        },
        "model_input_tokens": {
          "default": 512,
          "maximum": 150000,
          "minimum": 0,
          "title": "Model Input Tokens",
          "type": "integer"
        },
        "model_output_tokens": {
          "default": 0,
          "maximum": 50000,
          "minimum": 0,
          "title": "Model Output Tokens",
          "type": "integer"
        },
        "rerank_items": {
          "default": 0,
          "maximum": 200,
          "minimum": 0,
          "title": "Rerank Items",
          "type": "integer"
        }
      },
      "title": "Budget",
      "type": "object"
    },
    "CategoryWeights": {
      "additionalProperties": false,
      "description": "图路线中各类边的权重（R12 §4.4）。关联边按确定度取三档之一。",
      "properties": {
        "structure": {
          "default": 0.3,
          "maximum": 1,
          "minimum": 0,
          "title": "Structure",
          "type": "number"
        },
        "evidence": {
          "default": 1.0,
          "maximum": 1,
          "minimum": 0,
          "title": "Evidence",
          "type": "number"
        },
        "reference": {
          "default": 0.6,
          "maximum": 1,
          "minimum": 0,
          "title": "Reference",
          "type": "number"
        },
        "navigation_supported": {
          "default": 0.7,
          "maximum": 1,
          "minimum": 0,
          "title": "Navigation Supported",
          "type": "number"
        },
        "navigation_plausible": {
          "default": 0.4,
          "maximum": 1,
          "minimum": 0,
          "title": "Navigation Plausible",
          "type": "number"
        },
        "navigation_speculative": {
          "default": 0.2,
          "maximum": 1,
          "minimum": 0,
          "title": "Navigation Speculative",
          "type": "number"
        },
        "mention": {
          "default": 0.5,
          "maximum": 1,
          "minimum": 0,
          "title": "Mention",
          "type": "number"
        },
        "claim_part": {
          "default": 0.6,
          "description": "结论要素边（R23 D-11），再乘所属结论的成熟度系数",
          "maximum": 1,
          "minimum": 0,
          "title": "Claim Part",
          "type": "number"
        }
      },
      "title": "CategoryWeights",
      "type": "object"
    },
    "Collaboration": {
      "additionalProperties": false,
      "properties": {
        "subagents": {
          "default": "auto",
          "enum": [
            "auto",
            "off"
          ],
          "title": "Subagents",
          "type": "string"
        },
        "subagent_requirements": {
          "default": "优先使用低成本、较低能力的可用模型，采用低推理深度；须能完成材料检索、完整阅读和结构化笔记。",
          "maxLength": 2000,
          "minLength": 1,
          "title": "Subagent Requirements",
          "type": "string"
        }
      },
      "title": "Collaboration",
      "type": "object"
    },
    "ConnectorConfig": {
      "additionalProperties": false,
      "properties": {
        "id": {
          "pattern": "^[a-z][a-z0-9-]{0,31}$",
          "title": "Id",
          "type": "string"
        },
        "type": {
          "description": "连接器类型，如 weknora、mdvault",
          "pattern": "^[a-z][a-z0-9-]{0,31}$",
          "title": "Type",
          "type": "string"
        },
        "enabled": {
          "default": true,
          "title": "Enabled",
          "type": "boolean"
        },
        "base_url": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Base Url"
        },
        "tenant_id": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Tenant Id"
        },
        "secret_ref": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "keyring:burrowl/<id> 或 env:VAR；配置文件里从不存明文",
          "title": "Secret Ref"
        },
        "collection": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "目标知识库（WeKnora 为知识库 ID 或名称）",
          "title": "Collection"
        },
        "options": {
          "additionalProperties": {
            "anyOf": [
              {
                "type": "string"
              },
              {
                "type": "integer"
              },
              {
                "type": "boolean"
              },
              {
                "type": "null"
              }
            ]
          },
          "title": "Options",
          "type": "object"
        },
        "publish": {
          "$ref": "#/$defs/PublishPolicy"
        },
        "wiki": {
          "$ref": "#/$defs/WikiSettings"
        }
      },
      "required": [
        "id",
        "type"
      ],
      "title": "ConnectorConfig",
      "type": "object"
    },
    "Diagnostics": {
      "additionalProperties": false,
      "properties": {
        "request_log": {
          "default": "off",
          "description": "请求日志（.burrowl/logs/requests.jsonl）：off 不记；summary 记每次调用的工具名、耗时、结果与参数结构（不含内容）；full 另记参数原文与返回内容（截断长字段），用于排查 MCP 传参问题。启动参数 --request-log 或环境变量 BURROWL_REQUEST_LOG 优先；修改后立即生效",
          "enum": [
            "off",
            "summary",
            "full"
          ],
          "title": "Request Log",
          "type": "string"
        }
      },
      "title": "Diagnostics",
      "type": "object"
    },
    "Embedding": {
      "additionalProperties": false,
      "properties": {
        "provider": {
          "default": "fastembed",
          "enum": [
            "fastembed",
            "none"
          ],
          "title": "Provider",
          "type": "string"
        },
        "model": {
          "default": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
          "title": "Model",
          "type": "string"
        },
        "path": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "本地模型目录；相对路径相对数据目录。为空时使用安装包自带模型",
          "title": "Path"
        },
        "dim": {
          "default": 384,
          "maximum": 4096,
          "minimum": 1,
          "title": "Dim",
          "type": "integer"
        },
        "max_tokens": {
          "anyOf": [
            {
              "maximum": 131072,
              "minimum": 16,
              "type": "integer"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "模型输入上限（token）；为空时读取模型自带值。只能调小，不能超过模型真实上限",
          "title": "Max Tokens"
        },
        "query_prefix": {
          "anyOf": [
            {
              "maxLength": 500,
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "查询指令前缀（如 Qwen3 的 Instruct…Query:）；fastembed 不会自动添加",
          "title": "Query Prefix"
        },
        "passage_prefix": {
          "anyOf": [
            {
              "maxLength": 500,
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "文档指令前缀；多数模型不需要",
          "title": "Passage Prefix"
        },
        "chunking": {
          "default": "auto",
          "description": "向量切分方式。late=后切分：整条记录（不超过 late_context_tokens）一次编码，再按块/段落分别求平均，每段带着全文上下文；windows=各段单独编码（按模型上限切窗）。auto：模型是平均池化且位置上限≥1024 时用 late",
          "enum": [
            "auto",
            "late",
            "windows"
          ],
          "title": "Chunking",
          "type": "string"
        },
        "late_context_tokens": {
          "default": 2048,
          "description": "后切分一次编码的上下文长度（token）。只决定每段能看到多少上下文，不决定向量粒度；越长越慢、越占内存（注意力随长度平方增长）。超过时按段落边界分批编码",
          "maximum": 8192,
          "minimum": 256,
          "title": "Late Context Tokens",
          "type": "integer"
        },
        "span_tokens": {
          "default": 512,
          "description": "后切分每个向量覆盖的最大长度（token）：块或段落超过时按段落、再按 token 切开，避免一个向量混入多个意思",
          "maximum": 2048,
          "minimum": 64,
          "title": "Span Tokens",
          "type": "integer"
        }
      },
      "title": "Embedding",
      "type": "object"
    },
    "GraphRoute": {
      "additionalProperties": false,
      "description": "图路线（R12 §4.11）：从关键词与向量融合后的前几名出发沿图扩展，作为 RRF 中的一路。只是导航。",
      "properties": {
        "enabled": {
          "default": false,
          "description": "默认关闭；评估显示收益后再由用户决定是否打开（D-24）",
          "title": "Enabled",
          "type": "boolean"
        },
        "hops": {
          "default": 1,
          "maximum": 2,
          "minimum": 1,
          "title": "Hops",
          "type": "integer"
        },
        "seeds": {
          "default": 20,
          "maximum": 100,
          "minimum": 1,
          "title": "Seeds",
          "type": "integer"
        },
        "fanout": {
          "default": 20,
          "description": "每个节点最多扩展的边数",
          "maximum": 200,
          "minimum": 1,
          "title": "Fanout",
          "type": "integer"
        },
        "weight": {
          "default": 0.5,
          "description": "在 RRF 融合中的路线权重",
          "maximum": 2,
          "minimum": 0,
          "title": "Weight",
          "type": "number"
        },
        "category_weights": {
          "$ref": "#/$defs/CategoryWeights"
        },
        "algorithm": {
          "const": "hops",
          "default": "hops",
          "title": "Algorithm",
          "type": "string"
        }
      },
      "title": "GraphRoute",
      "type": "object"
    },
    "GraphSettings": {
      "additionalProperties": false,
      "properties": {
        "concept_owner_id": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "概念库对象（owner_type=knowledge）的 owner_id；为空时 [[概念]] 链接都记为未解析，不阻塞提交",
          "title": "Concept Owner Id"
        }
      },
      "title": "GraphSettings",
      "type": "object"
    },
    "HubRoute": {
      "additionalProperties": false,
      "description": "中枢路线（R12 §4.11、D-23）：外部检索作为 RRF 中的一路。命中必须对上本地记录、回到固定版本核验。",
      "properties": {
        "connectors": {
          "description": "参与的连接器 id；为空表示关闭",
          "items": {
            "type": "string"
          },
          "title": "Connectors",
          "type": "array"
        },
        "weight": {
          "default": 0.5,
          "maximum": 2,
          "minimum": 0,
          "title": "Weight",
          "type": "number"
        },
        "timeout_s": {
          "default": 10.0,
          "description": "单次外部检索的超时；超时按不可用处理并报告缺口",
          "exclusiveMinimum": 0,
          "maximum": 120,
          "title": "Timeout S",
          "type": "number"
        },
        "limit": {
          "default": 20,
          "maximum": 100,
          "minimum": 1,
          "title": "Limit",
          "type": "integer"
        }
      },
      "title": "HubRoute",
      "type": "object"
    },
    "IndexConfig": {
      "additionalProperties": false,
      "properties": {
        "path": {
          "default": ".burrowl/index/index.sqlite3",
          "description": "检索索引文件；可放到其他磁盘，删除后可重建",
          "title": "Path",
          "type": "string"
        },
        "embedding": {
          "$ref": "#/$defs/Embedding"
        },
        "vector": {
          "$ref": "#/$defs/VectorBackend"
        },
        "reranker": {
          "$ref": "#/$defs/RerankerModel"
        },
        "ocr_enabled": {
          "default": true,
          "title": "Ocr Enabled",
          "type": "boolean"
        },
        "graph_route": {
          "$ref": "#/$defs/GraphRoute"
        },
        "hub_route": {
          "$ref": "#/$defs/HubRoute"
        }
      },
      "title": "IndexConfig",
      "type": "object"
    },
    "McpConfig": {
      "additionalProperties": false,
      "properties": {
        "toolsets": {
          "items": {
            "type": "string"
          },
          "title": "Toolsets",
          "type": "array"
        },
        "error_envelope": {
          "default": false,
          "description": "MCP 错误兼容返回，默认关闭。开启后所有 MCP 调用返回 {ok,result/error} 普通结果包，业务失败仍明确为 ok=false，并携带有界错误摘要；用于会隐藏 isError 内容的客户端。不改变执行、错误日志或 HTTP/CLI；保存后下一次 MCP 调用生效，可随时关闭",
          "title": "Error Envelope",
          "type": "boolean"
        },
        "mode": {
          "default": "auto",
          "description": "auto：接入 burrowl 的对话默认按 burrowl-work / burrowl-read / burrowl-consolidate 工作，成果存入本数据目录；manual：只在用户明确要求时使用。启动参数 --mode 优先；修改后需重启 MCP 服务",
          "enum": [
            "auto",
            "manual"
          ],
          "title": "Mode",
          "type": "string"
        }
      },
      "title": "McpConfig",
      "type": "object"
    },
    "PublishPolicy": {
      "additionalProperties": false,
      "description": "哪些知识发布到外部知识中枢。默认只发布已复核的 L4/L3 概览经验与成稿文档。",
      "properties": {
        "levels": {
          "items": {
            "enum": [
              "L0",
              "L1",
              "L2",
              "L3",
              "L4"
            ],
            "type": "string"
          },
          "title": "Levels",
          "type": "array"
        },
        "include_documents": {
          "default": true,
          "title": "Include Documents",
          "type": "boolean"
        },
        "review_filter": {
          "default": "reviewed_only",
          "enum": [
            "all",
            "reviewed_only",
            "exclude_unsafe",
            "unreviewed_only"
          ],
          "title": "Review Filter",
          "type": "string"
        },
        "owner_types": {
          "anyOf": [
            {
              "items": {
                "type": "string"
              },
              "type": "array"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Owner Types"
        },
        "owner_ids": {
          "anyOf": [
            {
              "items": {
                "type": "string"
              },
              "type": "array"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Owner Ids"
        },
        "exclude_sensitivity": {
          "items": {
            "enum": [
              "internal",
              "confidential",
              "restricted"
            ],
            "type": "string"
          },
          "title": "Exclude Sensitivity",
          "type": "array"
        },
        "on_retract": {
          "default": "delete",
          "enum": [
            "delete",
            "mark"
          ],
          "title": "On Retract",
          "type": "string"
        }
      },
      "title": "PublishPolicy",
      "type": "object"
    },
    "QueryDefaults": {
      "additionalProperties": false,
      "properties": {
        "result_limit": {
          "default": 20,
          "maximum": 100,
          "minimum": 1,
          "title": "Result Limit",
          "type": "integer"
        },
        "review_filter": {
          "default": "all",
          "enum": [
            "all",
            "reviewed_only",
            "exclude_unsafe",
            "unreviewed_only"
          ],
          "title": "Review Filter",
          "type": "string"
        },
        "budget": {
          "$ref": "#/$defs/Budget"
        }
      },
      "title": "QueryDefaults",
      "type": "object"
    },
    "ReadingContext": {
      "additionalProperties": false,
      "properties": {
        "max_owners": {
          "default": 10,
          "maximum": 100,
          "minimum": 1,
          "title": "Max Owners",
          "type": "integer"
        },
        "note_max_tokens": {
          "default": 6000,
          "maximum": 50000,
          "minimum": 512,
          "title": "Note Max Tokens",
          "type": "integer"
        }
      },
      "title": "ReadingContext",
      "type": "object"
    },
    "ReadingDefaults": {
      "additionalProperties": false,
      "properties": {
        "strategy": {
          "default": "standard",
          "enum": [
            "standard",
            "associative",
            "quick"
          ],
          "title": "Strategy",
          "type": "string"
        },
        "review_filter": {
          "default": "all",
          "enum": [
            "all",
            "reviewed_only",
            "exclude_unsafe",
            "unreviewed_only"
          ],
          "title": "Review Filter",
          "type": "string"
        },
        "result_limit": {
          "default": 10,
          "maximum": 100,
          "minimum": 1,
          "title": "Result Limit",
          "type": "integer"
        },
        "association": {
          "$ref": "#/$defs/Association"
        },
        "context": {
          "$ref": "#/$defs/ReadingContext"
        },
        "screening": {
          "$ref": "#/$defs/Screening"
        },
        "reranking": {
          "$ref": "#/$defs/Reranking"
        },
        "budget": {
          "$ref": "#/$defs/Budget"
        }
      },
      "title": "ReadingDefaults",
      "type": "object"
    },
    "RerankerModel": {
      "additionalProperties": false,
      "properties": {
        "path": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "ONNX Cross-encoder 模型目录；为空时使用安装包自带模型",
          "title": "Path"
        }
      },
      "title": "RerankerModel",
      "type": "object"
    },
    "Reranking": {
      "additionalProperties": false,
      "properties": {
        "mode": {
          "default": "auto",
          "enum": [
            "off",
            "auto",
            "required"
          ],
          "title": "Mode",
          "type": "string"
        },
        "candidate_limit": {
          "default": 30,
          "maximum": 100,
          "minimum": 1,
          "title": "Candidate Limit",
          "type": "integer"
        },
        "window_tokens": {
          "default": 512,
          "maximum": 512,
          "minimum": 64,
          "title": "Window Tokens",
          "type": "integer"
        }
      },
      "title": "Reranking",
      "type": "object"
    },
    "Screening": {
      "additionalProperties": false,
      "properties": {
        "regular_windows": {
          "default": 3,
          "maximum": 4,
          "minimum": 1,
          "title": "Regular Windows",
          "type": "integer"
        },
        "max_windows": {
          "default": 4,
          "maximum": 4,
          "minimum": 1,
          "title": "Max Windows",
          "type": "integer"
        },
        "batch_owners": {
          "default": 10,
          "maximum": 100,
          "minimum": 1,
          "title": "Batch Owners",
          "type": "integer"
        }
      },
      "title": "Screening",
      "type": "object"
    },
    "ServerConfig": {
      "additionalProperties": false,
      "properties": {
        "host": {
          "const": "127.0.0.1",
          "default": "127.0.0.1",
          "title": "Host",
          "type": "string"
        },
        "port": {
          "default": 0,
          "description": "0 表示随机端口",
          "maximum": 65535,
          "minimum": 0,
          "title": "Port",
          "type": "integer"
        }
      },
      "title": "ServerConfig",
      "type": "object"
    },
    "VectorBackend": {
      "additionalProperties": false,
      "properties": {
        "kind": {
          "default": "sqlite-vec",
          "enum": [
            "sqlite-vec",
            "qdrant-server",
            "none"
          ],
          "title": "Kind",
          "type": "string"
        },
        "url": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "qdrant-server 的地址，如 http://127.0.0.1:6333",
          "title": "Url"
        },
        "secret_ref": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Secret Ref"
        }
      },
      "title": "VectorBackend",
      "type": "object"
    },
    "WikiSettings": {
      "additionalProperties": false,
      "properties": {
        "mode": {
          "default": "both",
          "enum": [
            "off",
            "auto",
            "authored",
            "both"
          ],
          "title": "Mode",
          "type": "string"
        },
        "synthesis_model_id": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "WeKnora 自动 Wiki 使用的模型 ID",
          "title": "Synthesis Model Id"
        }
      },
      "title": "WikiSettings",
      "type": "object"
    }
  },
  "additionalProperties": false,
  "properties": {
    "schema_version": {
      "const": 1,
      "default": 1,
      "title": "Schema Version",
      "type": "integer"
    },
    "collaboration": {
      "$ref": "#/$defs/Collaboration"
    },
    "query": {
      "$ref": "#/$defs/QueryDefaults"
    },
    "reading": {
      "$ref": "#/$defs/ReadingDefaults"
    },
    "index": {
      "$ref": "#/$defs/IndexConfig"
    },
    "server": {
      "$ref": "#/$defs/ServerConfig"
    },
    "mcp": {
      "$ref": "#/$defs/McpConfig"
    },
    "connectors": {
      "items": {
        "$ref": "#/$defs/ConnectorConfig"
      },
      "title": "Connectors",
      "type": "array"
    },
    "graph": {
      "$ref": "#/$defs/GraphSettings"
    },
    "diagnostics": {
      "$ref": "#/$defs/Diagnostics"
    }
  },
  "title": "BurrowlConfig",
  "type": "object"
}
```
