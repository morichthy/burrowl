# 交换格式 burrowl-exchange/2

burrowl 把记忆导出为一个**交换包**：Markdown 库 + 图 JSONL + manifest。外部工具（Obsidian、LightRAG、qmd、WeKnora 的文件夹导入……）读取交换包，各自建索引。交换包是**派生数据**：可以删除重建，不是真源（R12 D-01、D-21）。

- 生成：`exchange.export(name, owner_ids?, owner_types?, exclude_sensitivity?)`，写到数据目录的 `exports/<name>/`。
- Markdown 库连接器（mdvault）发布时使用同一个渲染器和同一套页面格式（路径相对于连接器的集合目录）。
- 实现：`services/render.py`（页面）、`services/exchange.py`（导出）、`index/graph.py`（节点与边）。

## 目录

```text
exports/<名称>/
  manifest.json
  vault/
    README.md                         格式说明；"本目录由 burrowl 生成，只读"
    concepts/<概念名>.md               概念库中 page_type 为 concept / entity 的页面
    records/<对象短名>/<handle>.md     记忆记录
    documents/<对象短名>/<handle>.md   文稿（技术内容用嵌入，不复制正文）
  graph/
    nodes.jsonl
    edges.jsonl
```

- **对象短名**：业务卡的 `short_name`，没有时用 `slug`，再没有用 owner_id。
- **handle**：记录的 `handle`；旧记录没有时按当前标题生成。同一对象内同名时追加 `-2`、`-3`。
- 文件名去掉 `\ / : * ? " < > | # ^ [ ]` 等字符，最长 80 个字符，保留中文。

## manifest.json

| 字段 | 含义 |
|---|---|
| `format` | 固定为 `burrowl-exchange/2`（/2：边类别改为 mention / claim_part，去掉 `derived`，R23） |
| `generated_at` | 最后一次有变化的导出时间（UTC） |
| `data_dir_id` | 数据目录的稳定标识（路径哈希的前 16 位），用来区分不同来源的包 |
| `filter` | 本次导出的范围：owner_ids、owner_types、exclude_sensitivity |
| `items[]` | `{local_key, path, record_id, revision, sha256}`：每个页面对应的记录固定版本。`sha256` 是记录的 record_hash |
| `files` | `{相对路径: 文件 sha256}`，用于增量比较 |
| `counts` | records / nodes / edges 数量 |

## 页面格式

```markdown
---
burrowl_id: "MEM-…"              # 内部身份
local_key: "MEM-…@3"             # 记录 ID@修订；外部系统必须原样保存，用于回到固定版本核对
handle: "第三轮实验"
owner: "RES-…"
kind: "detail"
level: "L1"
revision: 3
sha256: "…"                      # 记录的 record_hash
aliases: []
domains: []
tags: [...]
claims: [{"id": "CLM-…", "review": "accepted", "maturity": "replicated"}]   # 导出时的状态
---
# 标题
## kahan
……块正文（块内原有标题降一级）……
## 结论
- 结论陈述（适用范围：…） ^c-1a2b3c4d
<!-- burrowl:generated:start -->
## 关联与引用（自动生成）
**被引用**：[[records/…|标题]]、…
- 同一问题（推测）：[[records/…|标题]]
- 结论 CLM-…：复核 accepted，成熟度 replicated
<!-- burrowl:generated:end -->
```

- YAML 头的值用 JSON 写（JSON 是 YAML 1.2 的子集）。
- **复核状态、成熟度和统计只出现在 YAML 头和自动生成区块中**，是导出时的状态，不是记录内容（D-18）。
- **链接**：正文、块、结论陈述里的 `[[…]]` 改写为 `[[<库内路径>|显示文字]]`（路径不带 `.md`）。块链接为 `[[records/…#<block_id>]]`，结论链接为 `[[records/…#^c-<8 位>]]`。目标没有导出（范围外、敏感级别过滤、概念未建页）时只保留显示文字。
- **L1 块**：每块以 `## <block_id>` 输出，块内原有标题降一级，使标题嵌入能取到完整的块。
- **结论锚点**：`^c-` + 结论 ID 的 SHA-256 前 8 位（Obsidian 的块 ID 只能用 ASCII）。
- **文稿页**：`## 章节标题`，衔接文字原样（链接改写），技术块写成 `![[records/<对象>/<handle>#<block_id>]]`，不复制正文。
- **自动生成区块**：从图投影算出，外部工具不要编辑这一段；下次导出会整体替换。

## 图文件

每行一个 JSON 对象。

`nodes.jsonl`：

| 字段 | 含义 |
|---|---|
| `id` | 节点身份：对象 ID、记录 ID、`记录ID#块`、结论 ID、来源 SRC-… |
| `type` | owner / record / block / claim / concept / source |
| `title`、`owner`、`level`、`kind`、`domains` | 展示与筛选用 |
| `review`、`maturity` | 结论节点的复核徽标与有效成熟度 |
| `local_key`、`path` | 有页面的节点：固定版本键与库内文件路径 |

`edges.jsonl`：

| 字段 | 含义 |
|---|---|
| `id` | 边身份（由声明者、来源字段、两端、类型计算） |
| `src`、`dst`、`type` | 两端与关系名 |
| `category` | structure / evidence / reference / navigation / mention / claim_part（结论要素边的 `type` 是角色名或 condition） |
| `certainty` | 关联边的确定度：speculative / plausible / supported |
| `state` | active；关联边为 candidate / accepted；所属结论失效时结论要素边为 invalid |
| `declared_by` | 声明这条边的记录或结论 |

范围：属于导出记录的节点，加上被导出边引用的对象与来源；两端都在范围内的边全部导出。

## 过滤与增量

- 默认不导出 `confidential`，`restricted` 永远不导出（D-25）；依据链上有受限或不可用来源的记录也不导出。
- 文稿章节、复核、策略、反馈、关联、表示、收口记录不单独成页（关联画成边，复核状态进 YAML 头），但属于范围内的记录仍进入图文件。
- 增量：内容没变的文件不改写；上次导出有、这次没有的文件删除；manifest 只在有变化时更新。

## 兼容规则

- `format` 的主版本变化（`/2`）表示不兼容的改动：目录结构、`local_key` 语义、链接写法。
- 同一主版本内只增加字段，不删除、不改含义；读取方应忽略不认识的字段。
- 外部系统回流的实体与关系只能作为候选（`relations.candidates`，origin 为 `tool:<连接器 id>`），从不直接写成记录。
- 在交换包里编辑的内容不会回到 burrowl；要修改知识，修改记录后重新导出。
