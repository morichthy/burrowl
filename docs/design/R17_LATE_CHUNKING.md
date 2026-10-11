# R17 后切分与嵌入模型选择

状态：P1 已实现（0.6.0）；补充评测待用户进行（§5、§7）。
相关：[R12 知识图谱与检索升级](R12_KNOWLEDGE_GRAPH_RETRIEVAL.md)（截断修复、上下文信封、Qwen3 实测）、[DEVELOPMENT_HISTORY](../DEVELOPMENT_HISTORY.md#r17-后切分与嵌入模型选择2026-10-11)。

## 1. 问题

- 默认嵌入模型 MiniLM 一次只能读 128 token。实测（测试数据目录）每个向量窗口的正文中位数是 94 token，一条 5618 token 的 L1 记录被切成约 70 个窗口。
- 上下文信封最多只能占窗口的 1/4，也就是 32 token；而它要放的内容中位数是 77 token，所以检索问题和定义块基本都被裁掉了。
- 长记录按"最好的那个窗口"计分，窗口越多，命中的机会越多。
- 512 token 的模型（bge-zh）仍不能保证大块完整。而把窗口直接调到 2048，又会把记录级条目里的多个块压进同一个向量，造成过度压缩（§2）。

## 2. 讨论经过与取舍

| 方案 | 结论 |
|---|---|
| 换窗口更大的模型，各段仍单独编码（windows 512） | 块大多在 512 以内，但超过 512 的大块仍会按固定长度切开，切开后每段丢失上下文 |
| 整条记录一个向量（whole，长窗口） | 不会切错，但一个向量要代表多个意思，具体问题的相似度会被冲淡；不同记录的长向量也会越来越像 |
| 把上限调到 2048 | 依据只有一个 1704 token 的块，太单薄；而且真正受影响的是记录级条目（中位约 1500 token），等于把多个块合进一个向量 |
| **后切分（late）** | **选用**。整条记录一次编码，每个 token 都看过全文；再按块或段落分别求平均，每个向量只覆盖一段 |

用户提到一篇文章的观点："块切错导致的检索损失，比直接用长窗口更大。"这类比较通常针对按固定长度切块。burrowl 的 L1 块是作者写好的自然边界，真正会切错的只有两处：超过上限的大块，和记录级条目的拼接文本。后切分同时回应了两方面的担心：不丢上下文，也不过度压缩。Jina 的后切分论文观察到，长窗口模型在长文本上能提升检索，但在短文本上仍然表现更好。2025 年的对比研究则发现，后切分的效果随模型和数据集差异很大。所以本设计保留三种方式（§5），由用户在自己的数据上比较。

## 3. 决定

- **D-01 切分方式**：配置 `index.embedding.chunking` 取 `auto | late | windows`，默认 auto。模型是平均池化、且位置上限不低于 1024 时使用 late，否则使用 windows（0.6 之前的方式）。
- **D-02 两个长度分开**：
  - `late_context_tokens`（默认 2048）只决定每段编码时能看到多少上下文，不决定向量粒度。2048 是在 CPU 上的折中：注意力随长度平方增长，测试数据中记录级条目的中位数约 1500 token，大多能一次编码完。超过时按段落边界分批编码，每批都带前缀。
  - `span_tokens`（默认 512）决定一个向量最多覆盖多长。块或段落超过时，先按空行切开，再按 token 数切开。要求不超过 `late_context_tokens` 的一半。
  - 这两个默认值都是工程上的折中，没有实验依据，留给 §5 的评测确定。
- **D-03 L1 的分组**：整条 L1 记录只编码一次。块级条目的向量取自它在这次编码中的区间；记录级条目的向量只取块以外的部分（检索说明、其余字段、关键词）。没有块外文字时，记录向量取各块向量的平均。关键词检索仍然索引全部正文。
- **D-04 其他条目**：L2–L4 记录、发现投影、结论、来源分段，各自编码一次，再按段落区间分别求平均。
- **D-05 默认模型**：新建的数据目录在配置里写明 `jinaai/jina-embeddings-v2-base-zh`（768 维）。代码里的配置默认值仍是 MiniLM：`burrowl init` 不写配置文件，没有配置文件的旧数据目录升级后不会被悄悄换模型。已有数据目录用 `config.update` 切换（§6）。
- **D-06 选择 jina 的理由**：
  1. 它是中英双语模型，适合中英混用的科研记录；
  2. 平均池化，位置上限 8192，满足后切分的前提；后切分方法本身也是 Jina 提出的；
  3. 体量适中：onnx 文件约 640 MB，1.6 亿参数；
  4. 合成中文题集上表现最好（§7）。

  其他候选的问题：
  - bge-small-zh / bge-base-zh：C-MTEB 检索得分分别为 61.77 / 69.49，但只有 512 位置上限、使用 CLS 池化，不能做后切分；英文科研（SciFact）上比 MiniLM 还差。
  - Qwen3-Embedding-0.6B：取最后一个 token 作为向量，不是平均池化；R12 实测在 CPU 上太重。
  - EmbeddingGemma：需要同意 Gemma 许可条款。
  - bge-m3：约 2.2 GB，体量接近 Qwen3。
- **D-07 推理会话复用**：后切分复用 fastembed 已经加载的推理会话，读取逐 token 输出 `last_hidden_state`，不额外加载一份模型。查询向量仍由 fastembed 计算。实测整段编码时，后切分得到的向量与模型原生向量的余弦相似度为 0.99999994。
- **D-08 指纹**：切分规则（late-v1、context、span）写进嵌入指纹，切换方式或参数后向量自动重建。windows 方式的指纹与 0.5 完全相同，旧索引不需要重建。

## 4. 实现

| 位置 | 内容 |
|---|---|
| `core/config.py` | `Embedding.chunking / late_context_tokens / span_tokens` 与长度校验 |
| `index/embed.py` | `segments()` 段落切分；`late_vectors()` 一次编码逐段取平均，编码窗口在预算内向两侧扩展、带上前后文；`FastEmbedEmbedder.late / segments / late_embed`；`_max_positions()` 读取模型位置上限；`_register_custom()` 注册 fastembed 未内置的模型 |
| `index/late.py` | `late_windows()`：L1 记录与块的分组（D-03），只输出变化了的条目 |
| `index/db.py` | `sync_vectors` 在 `embedder.late` 为真时走后切分 |
| `index/model_store.py` | 登记 jina-v2-base-zh、bge-small-zh、bge-base-zh；`DEFAULT_EMBEDDING`、`new_data_dir_embedding()` |
| `app.py` | 新建数据目录时写入默认嵌入配置（D-05） |
| `packaging/` | 离线包打包 jina、MiniLM 与重排模型；安装时逐个补齐共享目录里缺少的模型 |
| `tools/eval_recall_models.py` | 外部题集上的模型 × 切分方式对比（§5） |
| `tools/eval_retrieval.py` | 产品检索流程评测，新增 `--chunking / --late-context / --span-tokens` |

## 5. 评测方法（供后续比较）

目标：在固定题集上比较"嵌入模型 × 切分方式 × 参数"，只测召回，不调用生成式 AI。

**对比的方式（arm）**

| arm | 做法 | 回答的问题 |
|---|---|---|
| windows | 各段单独编码，按模型上限切窗 | 0.6 之前的方式；任何模型都能测 |
| whole | 整篇一次编码，所有正文求平均得一个向量（超过上下文的部分截掉并报告篇数） | "直接用长窗口"的主张 |
| late | 整篇一次编码，按段落区间分别求平均 | 本设计 |

**两层评测**

1. **外部固定题集**（`tools/eval_recall_models.py`）：语料按文档计，复用产品的切窗、分词、融合规则（RRF k=60，向量和关键词两路等权），文档得分取最好的一段。
   ```
   uv run python tools/eval_recall_models.py --dataset <题集目录> \
       --model jina-v2-base-zh --model minilm --arm windows --arm late --arm whole \
       [--late-context 2048] [--span-tokens 512] [--split holdout] --out build/recall-eval/<名称>.json
   ```
   题集采用 `A_work_auto_flow_evaluation/datasets/aligned-inputs-v3/*` 的格式：`dataset/corpus.jsonl` 与 `queries.jsonl`。向量按"题集 × 模型指纹 × arm"缓存在 `build/recall-cache/`，同一组合重跑时不再编码。
2. **产品检索流程**（`tools/eval_retrieval.py`）：在数据目录副本上用真实的投影、信封、对象汇总和发现投影检索，按题型给出召回率、MRR 和条件错误率。
   ```
   uv run python tools/eval_retrieval.py --data-dir <数据目录> --set <题目.jsonl> --scratch \
       --model jina-v2-base-zh --chunking late --label <名称> [--channels dense]
   ```

**每次比较固定**：题集与分割、指标定义、融合方式；只改变模型、arm 和参数。报告时分开给出只看向量通道和三路融合的结果，比率要给出分母（题数），并附上编码耗时、查询 p50/p95 和峰值内存。

**建议的实验组**
1. jina：windows / whole / late 三组，在 SciFact（英文科研摘要）和中文长记录题集上各跑一遍；
2. late 参数：`span_tokens` 取 128 / 256 / 512，`late_context_tokens` 取 1024 / 2048 / 4096；
3. 记录级向量的组成：只用检索说明（现状），或检索说明加各块平均（需要改 `index/late.py`）；
4. 量化版 jina（int8，161 MB）与 fp32 版的效果和速度对比。

**题集的局限**
- 合成中文题集每篇都很短（不超过 138 字），只能测模型质量，测不出切分的差别。
- SciFact 是英文摘要，多数在 512 token 以内。
- 浮点样例只有 13 题、一个对象，而且 R12 在这套题上调过参数。
- 要判断切分方式，需要以中文长 L1 记录为主的真实数据题集。

## 6. 切换方法

1. 安装模型：`burrowl models install --embedding-model jina-v2-base-zh`。0.6 的离线完整包已经自带，安装时会复制到共享模型目录。
2. 修改配置（数据目录级，需要管理员权限）：
   ```
   burrowl call config.update --json "{\"patch\": {\"index\": {\"embedding\": {\"model\": \"jinaai/jina-embeddings-v2-base-zh\", \"dim\": 768, \"query_prefix\": null}}}, \"expected_revision\": \"<config.get 返回的 revision>\"}"
   ```
   想保留多个模型的索引来回比较时，可以同时改 `index.path`，例如 `.burrowl/index/index-jina.sqlite3`，这样各模型的索引分别保存在不同文件里。
3. 重启工作台和 MCP，然后运行 `index.reconcile`。指纹变了，向量会自动重建。
4. 换回原来的模型：把 model、dim（以及 path）改回去，再执行 reconcile。

## 7. 进度与实测记录

| 日期 | 项 | 结果 |
|---|---|---|
| 2026-10-11 | 合成中文题集（holdout 540 题，1200 篇，按文档计） | 向量通道 Recall@10 / MRR@10：MiniLM 0.70 / 0.47；bge-small-zh 0.98 / 0.62；bge-base-zh 1.00 / 0.69；jina 1.00 / **0.75**。中英混合类 Recall@10：MiniLM 0.40，其余均为 1.00。jina 三种 arm 结果几乎相同（文档都很短） |
| 2026-10-11 | SciFact（英文科研摘要，300 题，5183 篇） | 向量通道 Recall@10：MiniLM 0.71、bge-small-zh 0.30、bge-base-zh 0.58；关键词通道 0.79。**jina 未测完**：fp32 版在 2 线程下编码约每秒 450 token，估计要 2 小时，用户决定最后再做 |
| 2026-10-11 | 浮点样例（产品流程，13 题） | 融合后 R@10 / MRR：MiniLM 0.944 / 0.885；jina windows 0.903 / 0.787；jina late 0.903 / 0.715。只看向量通道：MiniLM 0.903 / 0.799；jina windows 0.931 / 0.734；jina late **0.972** / 0.631。late 召回最高但名次靠后，multihop 类下降最多。题量小、只有一个对象、R12 曾在这套题上调参，仅作方向参考 |
| 2026-10-11 | 真实模型测试 | 后切分整段编码与原生向量一致；同一段文字放在不同上下文中，向量不同（确实用到了上下文）。上线前发现并修复：编码窗口原本只覆盖区间本身，区间外的上下文被丢掉 |

**待定**
- jina 在 SciFact 上的结果，以及三种 arm 在英文科研摘要上的对比。
- 后切分在浮点样例上 MRR 下降的原因。可能和记录级向量只取检索说明有关（§5 第 3 组），也可能只是 13 题的噪声。
- 两个长度默认值是否合适（§5 第 2 组）。
- 是否改用量化版以减少编码时间。
