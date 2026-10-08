# burrowl：AI 研发工作区

burrowl 帮助你和 AI 助手（Claude Code、Codex、Cursor、Gemini CLI 等支持 MCP 的 agent）一起做研发工作：

- 把过程和成果保存为**可追溯、带版本的记忆**；
- 支持检索与完整阅读；
- 用复核区分"已确认"和"未确认"的知识；
- 把挑选出的知识发布到 WeKnora 等中心知识库。

接入后，AI 助手通过 MCP（AI 客户端调用外部工具的标准协议）使用 burrowl，按三个工作流 Skill 工作，成果存进你指定的**数据目录**；原始数据和代码仍留在你的**工作目录**里，只被引用、不被复制。

## 能做什么

| 场景                     | 做法                                                                                        |
| ------------------------ | ------------------------------------------------------------------------------------------- |
| 让 AI 记住研究过程和结论 | AI 按 L0–L4 层级保存原始依据、技术细节、经过、可复用认识和概览，每次保存都是带版本的提交   |
| 找已有经验、读懂材料     | 阅读会话：AI 检索、筛选、逐个完整阅读、写带出处的研究笔记；你在工作台实时观看和干预         |
| 确认哪些结论可靠         | 工作台"知识复核"页逐条确认、质疑、撤回或替代；检索可以只看已确认的知识                      |
| 执行实验并留痕           | Run 记录脚本、输入版本、产物、日志和结论                                                    |
| 来源变了怎么办           | "维护与监测"页发现受影响的内容，逐项决定保留、修订或撤回                                    |
| 形成报告                 | 把技术单元和衔接文字编排成完整过程稿或精简报告                                              |
| 汇总到中心知识库         | 发布已复核的知识到 WeKnora 或 Markdown 库（Obsidian 可直接打开），也可在那里检索和生成 Wiki |
| 换电脑、换盘             | 一个备份包带走记忆和被引用的原件，恢复时自动重定向                                          |

## 先理解几个概念

完整定义见 [术语表](docs/GLOSSARY.md)。

```text
工作目录（你的项目：原始数据、代码）        数据目录（burrowl 的存储）
D:/proj/                                    D:/BurrowlData/main/
  data/实验.csv   ◄── 只登记路径和指纹 ──     .burrowl/sources.json   来源登记
  analysis.py                                research/<课题>/     记忆、文稿、工作清单
                                             runs/、.burrowl/…        Run 登记、阅读会话、配置
```

- **数据目录**：存放记忆、文稿、阅读会话、Run 登记和配置的文件夹，和程序分开，可以有多个。
- **工作目录**：你和 AI 放原始数据与代码的地方，通常就是 AI 助手打开的项目目录。原件留在原处，burrowl 只登记"路径 + 内容指纹"并按 SRC- 编号引用。登记过的工作目录叫**工作根**，整个目录搬家时只改一次位置。
- **运行模式**：`auto`——任何项目中的实质工作默认按工作流进行（全局启用）；`manual`——默认不启用，只在用 `burrowl enable` 启用的项目中或你明确要求时使用。详见下文。
- **Owner（归属对象）**：一项研究、一个项目、一个核心算法、一份数据、一个工具或一次 Run。记忆都挂在 Owner 下。
- **L0–L4**：记录的层级。L0 原始依据 → L1 技术细节 → L2 过程经过 → L3 可复用认识 → L4 概览。层级表示内容职责，不表示可信程度。
- **提交与固定引用**：每次保存都是不可变的提交，可以回看历史。引用固定到"记录 + 修订号 + 指纹"，原文变了能被发现。
- **复核**：结论（claim）可以被确认、质疑、撤回或替代。内容一改，原来的复核自动失效。
- **阅读会话**：AI 检索、筛选并完整阅读材料的过程，笔记带固定出处。
- **知识中枢**：外部知识库（如 WeKnora），默认只接收已复核的知识。本地记忆始终是唯一的权威数据。

三种状态不要混淆：**已记录**（保存成功）、**已执行**（有真实 Run）、**已复核**（有人在明确范围内确认过）。

## MCP 与 Skill 的关系

|            | MCP 服务（`burrowl mcp`）                                                          | Skill（工作流说明）                                                  |
| ---------- | ------------------------------------------------------------------------------------ | -------------------------------------------------------------------- |
| 是什么     | 后台程序，给 AI 提供可调用的**工具**：保存记忆、检索、阅读、登记原件、复核…… | Markdown**说明书**：遇到某类任务按什么步骤、什么标准用这些工具 |
| 是否必需   | **必需**。没有它 AI 什么都存不了、查不了                                       | 不必需。同样的内容也可由 MCP 工具`system_guide` 读取               |
| 适用客户端 | 所有支持 MCP 的 agent                                                                | 支持 SKILL.md 的客户端会自动按需加载，更稳定                         |

Skill 里反引号中的工具名（如 `memory_owners`）都由 burrowl MCP 服务提供，客户端里可能带 `burrowl` 前缀显示。

## 运行模式：全局启用，还是按项目启用

| 模式       | 效果                                                                                                                                                            | 适合                        |
| ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------- |
| `auto`   | 在**任何项目**中，实质工作（研究、分析、实验、资料整理）默认按 burrowl-work / burrowl-read / burrowl-consolidate 进行，成果存入数据目录。相当于全局 Skill | 把 burrowl 当作日常工作方式 |
| `manual` | 默认**不启用**；只在用 `burrowl enable` 启用过的项目中，或你在对话中明确要求时才使用                                                                    | 只有部分项目需要留痕        |

- 项目开关：在项目目录运行 `burrowl enable` 启用、`burrowl disable` 关闭。它会写 `.burrowl.json`，并在项目的 `AGENTS.md`（以及已存在的 `CLAUDE.md`、`GEMINI.md`）中维护一段说明。`disable` 在 auto 模式下也能单独关闭某个项目。
- 两种模式下，简单问答、读源码或配置都不会进入工作流。
- 生效原理：MCP 服务每次被客户端启动时，把"是否启用、数据目录、工作目录、怎么读取工作流说明"写进它给 AI 的服务说明。项目开关另外写进项目说明文件，覆盖不读 MCP 服务说明的客户端。
- 已知边界：AI 是否严格照做取决于客户端和模型，程序无法强制。

## 安装

安装分两步：先把程序装到本机，再用 `burrowl connect` 把它接入你使用的 AI 客户端。

### 让 AI 助手替你安装（推荐）

在任意 AI 助手（Claude Code、Codex、Cursor、Gemini CLI 等）中，把下面这段话发给它，尖括号换成你的实际内容：

> 请按 `<burrowl 仓库或解压目录>/README.md` 中"安装步骤（AI 助手照此执行）"一节为我安装 burrowl。
> 安装包：`<…/burrowl-0.1.0-win64.zip>`；数据目录：`<D:/BurrowlData/main>`；运行模式：`<auto 或 manual>`；接入的客户端：你自己（以及 `<其他客户端>`）。

AI 会执行下面的步骤，最后报告结果，并告诉你还需要做什么（通常只有"重启客户端或新开对话"）。

### 安装步骤（AI 助手照此执行）

执行规则：

- 每步执行后检查输出；失败就停下，把完整输出报告给用户，不要跳过或猜测修复。
- 不修改步骤中没有提到的文件，不设置任何密钥。
- 下面的命令在 PowerShell 中执行。

需要用户提供（缺哪项就问哪项）：

| 参数     | 含义                                                                 | 默认     |
| -------- | -------------------------------------------------------------------- | -------- |
| 安装来源 | 离线包`burrowl-<版本>-win64.zip`，或 burrowl 源码仓库              | —       |
| 数据目录 | burrowl 存放记忆的文件夹，不存在会新建。不要放在项目目录或安装目录里 | —       |
| 运行模式 | `auto`（全局启用）或 `manual`（按项目启用）                      | `auto` |
| 客户端   | 你自己是哪个客户端，以及用户还要接入哪些，见第 3 步的表              | 你自己   |
| 接入范围 | `user`（本机所有项目）或 `project`（只对一个项目）               | `user` |

**方式 A：离线安装包**

1. 解压到一个新的空目录，再运行安装脚本。脚本没有交互：把程序装到 `%LOCALAPPDATA%\burrowl\versions\<版本>`，并把 `burrowl` 加入用户 PATH。

   ```powershell
   $unpack = Join-Path $env:TEMP ("burrowl-setup-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
   Expand-Archive -LiteralPath "<安装包.zip>" -DestinationPath $unpack
   ```

   包内有一层 `burrowl-<版本>\` 目录，安装脚本在其中：

   ```powershell
   & (Get-ChildItem $unpack -Recurse -Filter setup.cmd | Select-Object -First 1).FullName
   ```

   成功标志：输出"当前版本：<版本>"，随后打印模型状态 JSON。当前终端的 PATH 还没刷新，本次安装的后续命令都用完整路径调用：

   ```powershell
   $burrowl = "$env:LOCALAPPDATA\burrowl\bin\burrowl.cmd"
   ```
2. 安装模型。只需一次，需要联网下载约 220 MB；安装包名带 `-models` 时已经包含，跳过这一步。

   ```powershell
   & $burrowl models install
   ```

   下载失败不影响使用，只是检索退化为只用关键词。告诉用户稍后重试即可。
3. 接入客户端。数据目录不存在会新建；重复运行是安全的；`-c` 可以写多次。

   ```powershell
   & $burrowl connect --data-dir "<数据目录>" --mode auto -c <客户端>
   ```

   | `-c` 取值        | MCP 配置写到（user 级 / project 级）                           | Skill 装到                                       |
   | ------------------ | -------------------------------------------------------------- | ------------------------------------------------ |
   | `claude-code`    | `claude mcp add-json --scope user` / `<项目>\.mcp.json`    | `~\.claude\skills` / `<项目>\.claude\skills` |
   | `codex`          | `~\.codex\config.toml` / `<项目>\.codex\config.toml`       | `~\.codex\skills` / `<项目>\.codex\skills`   |
   | `cursor`         | `~\.cursor\mcp.json` / `<项目>\.cursor\mcp.json`           | 不装（用`system_guide`）                       |
   | `vscode`         | `%APPDATA%\Code\User\mcp.json` / `<项目>\.vscode\mcp.json` | 不装（用`system_guide`）                       |
   | `gemini`         | `~\.gemini\settings.json` / `<项目>\.gemini\settings.json` | 不装（用`system_guide`）                       |
   | `claude-desktop` | `%APPDATA%\Claude\claude_desktop_config.json`                | 不装（用`system_guide`）                       |
   | `generic`        | 不写文件，打印通用配置（JSON 与 TOML），由你填进客户端         | 不装；需要时加`--skills-dir <目录>`            |


   - 已有配置中的其他服务原样保留，只替换名为 burrowl 的一项。
   - 只对一个项目接入时，加 `--scope project --project "<项目目录>"`。此时工作目录固定为该项目；user 级的工作目录取客户端启动 MCP 时的当前目录。
   - 客户端支持 SKILL.md 但不在表中时，用 `--skills-dir <它的技能目录>` 安装 Skill。
   - 运行模式为 manual 时，告诉用户：在需要的项目里运行 `burrowl enable <项目目录>`。
4. 验证：

   ```powershell
   & $burrowl doctor --data-dir "<数据目录>"
   ```

   - 输出 JSON 中 `data_dir` 是用户指定的数据目录。`vector.ready` 为 false 只表示模型没装好。
   - 第 3 步输出中列出的配置文件里有 `burrowl` 一项，`args` 含 `--data-dir` 和 `--mode`。
5. 向用户报告：安装版本、数据目录、运行模式、接入了哪些客户端（及写入的文件）、模型是否就绪。然后请用户**重启客户端或新开对话**，客户端提示时同意启用 burrowl。之后在客户端的 MCP 列表中应能看到 burrowl。

**方式 B：从源码仓库安装（开发者）**

需要 Python 3.12、[uv](https://docs.astral.sh/uv/) 和 Node.js。在仓库目录下执行：

```powershell
uv sync --all-packages --all-extras
```

```powershell
npm --prefix frontend ci; npm --prefix frontend run build
```

之后把方式 A 中的 `& $burrowl` 换成 `uv run burrowl`，从第 2 步继续，同样在仓库目录下执行。

- 这样登记的 MCP 用的是仓库虚拟环境里的 Python，所以仓库不能再移动。
- 生成离线安装包：`uv run python packaging/build.py`，加 `--with-models` 把模型一起打包，产物在 `dist/`。

### 通用 MCP 配置

不在上表中的客户端，照抄 `burrowl connect -c generic` 打印的配置即可，格式如下：

```json
{
  "mcpServers": {
    "burrowl": {
      "command": "C:\\Users\\<你>\\AppData\\Local\\burrowl\\current\\python\\python.exe",
      "args": ["-m", "burrowl.interfaces.cli", "mcp", "--data-dir", "D:\\BurrowlData\\main", "--mode", "auto"],
      "env": {"PYTHONUTF8": "1", "PYTHONNOUSERSITE": "1"}
    }
  }
}
```

- `command` 指向 `current` 联接，以后升级或回滚版本不用改配置。
- `--work-dir <目录>` 可选：固定工作目录；不写时用客户端启动服务时的当前目录。
- 客户端不支持 Skill 时不需要做别的：AI 会按 MCP 服务说明调用 `system_guide` 读取工作流说明。

## 接入后怎样使用

### 改数据目录、工作目录或运行模式

这三项都是客户端 MCP 配置里的启动参数，改完后**重启客户端或新开对话**生效。改法任选其一：

- 用新参数重新运行 `burrowl connect`，会覆盖原来的 burrowl 配置项；
- 直接编辑客户端配置中 burrowl 一项的 `--data-dir`、`--work-dir`、`--mode`。

另外两个开关：

- 按项目启用或关闭：在项目里运行 `burrowl enable` / `burrowl disable`，优先于运行模式。
- 工作台"设置 → MCP 运行模式"改的是数据目录的默认模式，只在启动参数没写 `--mode` 时起作用。

一个数据目录可以被多个项目、多个客户端共用，每个项目的工作目录会登记成不同的工作根。

### 在对话中工作

启用时（auto 模式，或项目已 `burrowl enable`），直接说你要做的事，AI 会自己选择工作流：

| 你说                                            | AI 做什么                                            |
| ----------------------------------------------- | ---------------------------------------------------- |
| "继续上次浮点求和的研究"                        | work：找到对象，按检查点和工作清单续接               |
| "把 data/ 下的实验数据导入，整理成可复用的记录" | work：登记原件（留在原处）、分批阅读、按 L0–L4 保存 |
| "我们以前关于 XX 有什么经验？"                  | read：检索、筛选、完整阅读，给出带出处的笔记         |
| "这一阶段收个尾" / "这个来源更新了"             | consolidate：列出变化、修订、复核、交接              |
| "这个函数是做什么的？"                          | 不进入工作流，直接回答                               |

- 有用的中间成果 AI 会主动保存；你说"先别记"就不记。
- AI 自查通过不算"已复核"。只有你明确说核对过、在什么范围内成立，AI 才会代为登记确认。

未启用时，只在你说"用 burrowl…""记到记忆里""查一下记忆"等时才使用。

### 启动工作台

```bash
burrowl serve
```

浏览器会打开工作台，它只监听本机。阅读会话的实时进度、等你处理的请求、知识复核都在这里。

以下操作只能由你在工作台完成，AI 调用不到：批准全文补偿、执行发布、停用来源、保存关系图视图、写入密钥。

| 页面       | 用途                                                                                                   |
| ---------- | ------------------------------------------------------------------------------------------------------ |
| 首页       | 当前阅读、等待你回复的请求、复核概况、运行环境                                                         |
| 阅读会话   | 看 AI 的检索、筛选判断、阅读进度和笔记；批准或拒绝全文补偿、回答问题、改判、补充联想方向、中止         |
| 知识复核   | 待复核队列，一键确认（先自动预检）、质疑、撤回、替代，支持批量；查看历史与失效原因                     |
| 检索       | 本地记忆或知识中枢；单选"全部 / 只看已复核 / 排除有问题的 / 只看未复核"；选中结果组包阅读              |
| 系统记忆   | 按对象浏览记录、文稿（全文、目录、依据变化影响）、原始材料、时间线；记录页可查看依据与影响             |
| 关系图     | 对象和记录之间的关系；过滤、以节点为中心展开、查看并判断关联候选、社区着色、保存视图、随手笔记         |
| 维护与监测 | 扫描来源变化、处置维护候选、执行语义维护计划；工作目录搬家后重定向；管理来源登记（停用、调整敏感级别） |
| 知识中枢   | 测试连接、预览发布、确认执行、查看解析状态、浏览 Wiki                                                  |
| 设置       | 索引与模型、查询与阅读默认值、筛选/重排/预算、MCP 运行模式与工具组、连接器与密钥                       |

### 导入已有资料

告诉 AI 资料在哪里、做什么用、允许读取的范围。AI 会：

1. 找已有对象或建立新对象（已有说明文档可直接"采用"为对象，原文不改）；
2. 登记原件：只记路径和指纹，原件留在原处；工作目录之外的整个资料文件夹会登记为工作根；
3. 分批阅读，写出概览、方法、经验等记录，并在工作清单中记下读到哪里；
4. 检索验证这些资料能被找到。

登记不等于已读，已读不等于已验证。原件登记后被改动，引用它的结论会显示"依据已变化"。

### 连接 WeKnora 或 Markdown 库

- **WeKnora**：用 Docker Desktop 部署，步骤见 [deploy/weknora/README.md](deploy/weknora/README.md)。
  1. 在 WeKnora 中创建 API Key；
  2. 运行 `burrowl secret set keyring:burrowl/weknora`，把它写入本机凭据管理器；
  3. 在设置页添加连接器（type=weknora）。
- **Markdown 库**：type 填 `mdvault`，`options.path` 填库目录。

发布流程是：测试连接 → 预览发布（默认只含已复核内容）→ 确认执行。

## 备份与迁移

```bash
burrowl backup D:/备份/research-2026-10.zip
```

- **包含什么**：全部记忆、来源登记、阅读会话和配置，**以及数据目录外被登记引用的原件**（工作目录里的数据、代码等），每个文件都附 sha256。检索索引可以重建，所以不包含。
- **只打包没变过的原件**：登记后被改过或已丢失的原件不打包，因为引用指向的是登记时的内容。它们会在输出的 `external_skipped` 中列出。
- **不打包未登记的文件**：工作目录里没被登记的文件（仓库其余部分、虚拟环境等）请用 git 等方式自行迁移。
- 只要记忆、不要原件时，加 `--no-external`。

在新电脑上，先按"安装"装好程序，再恢复：

```bash
burrowl restore D:/备份/research-2026-10.zip D:/BurrowlData/main --map ROOT-PROJ=E:/projects/proj --default
```

- 恢复前完整校验，只写入不存在或为空的目录，绝不覆盖已有数据。
- **原件的去向**按以下顺序决定：

  1. 有 `--map` 的，放到指定位置。键可以是工作根编号（`ROOT-…`，见 `burrowl verify-backup <包>` 输出的 `roots`），也可以是旧路径前缀（如 `--map D:/资料=E:/资料`）；
  2. 原位置还有同一份文件（同机恢复）的，不动；
  3. 其余放到与数据目录并列的 `<数据目录名>-work/` 下。
- 目标位置已有内容不同的文件时，整个恢复中止，什么都不写。
- 恢复后，来源登记自动改写到新位置，旧位置记在 `relocated_from`。所有固定引用都不需要改。
- 最后重建索引，并把项目重新接入：

  ```bash
  burrowl call index.reconcile --data-dir D:/BurrowlData/main
  ```

  ```bash
  burrowl connect --data-dir D:/BurrowlData/main -c <客户端>
  ```

**只是工作目录搬家**（数据目录不变）：把整个目录移到新位置后，在工作台"维护与监测 → 工作目录"填新位置，或让 AI 调用 `sources_relocate_root`。程序会逐个核对文件指纹，全部一致才改指向。

**检查数据目录是否完好**：

```bash
burrowl validate
```

它只读检查提交链、写锁和残留、来源文件是否变化或缺失、索引是否落后。

## 数据目录如何维护

| 位置                                                | 保存什么、由谁维护                                                                                   |
| --------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| `research/`、`projects/`                        | 研究和项目：业务卡、计划、工作清单、所属 Run                                                         |
| `core-algorithms/`                                | 公司核心算法卡，必须有公司文档依据                                                                   |
| `knowledge/`、`reports/`、`data/`、`tools/` | 知识、报告、数据卡、工具登记                                                                         |
| `runs/`                                           | 不属于任何对象的 Run（新 Run 优先放在所属对象下）                                                    |
| 对象内的`memory/`                                 | 规范记忆（提交、HEAD、回执），**只通过工具修改**                                               |
| `.burrowl/`                                       | 程序状态：配置、来源登记与工作根、检索索引（可重建）、阅读会话、维护计划、监测、中枢映射、工作台偏好 |

- 不要手工修改 `memory/` 和 `.burrowl/` 中的文件。
- 原件保持只读；内容更新时用新的来源 ID 登记。
- 模型放在 `%LOCALAPPDATA%\burrowl\models`，所有数据目录共用；可用环境变量 `BURROWL_MODELS_DIR` 改位置。

## 命令速查

| 命令                                                                                                      | 作用                                                |
| --------------------------------------------------------------------------------------------------------- | --------------------------------------------------- |
| `burrowl connect -d <数据目录> -c <客户端> [--mode auto 或 manual] [--scope project -p <项目>] [--dev]` | 一步接入：数据目录 + 客户端 MCP 配置 + Skill        |
| `burrowl enable [<项目>]` / `burrowl disable [<项目>]`                                                | 在项目中启用 / 关闭 burrowl 工作流                  |
| `burrowl init <数据目录>`                                                                               | 新建数据目录并设为默认                              |
| `burrowl serve`                                                                                         | 启动工作台                                          |
| `burrowl doctor` / `burrowl validate`                                                                 | 检查运行环境 / 只读检查数据目录                     |
| `burrowl models install` / `burrowl models status`                                                    | 安装 / 查看模型                                     |
| `burrowl backup <包.zip> [--no-external]`                                                               | 备份（默认含被引用的外部原件）                      |
| `burrowl verify-backup <包.zip>`                                                                        | 只校验备份，列出工作根与原件                        |
| `burrowl restore <包.zip> <新目录> [--map 键=新位置] [--default]`                                       | 恢复并重定向原件                                    |
| `burrowl secret set keyring:burrowl/<连接器>`                                                           | 把密钥写入本机凭据管理器                            |
| `burrowl skills install <目录> [--only work]`                                                           | 单独安装 Skill                                      |
| `burrowl call <操作> key=value` / `burrowl ops`                                                       | 直接调用任意操作 / 列出全部操作                     |
| `setup.cmd -List` / `setup.cmd -Use <版本>`                                                           | 列出已安装版本 / 切换或回滚版本（数据目录不受影响） |

**升级**：解压新版本，运行它的 `setup.cmd`，旧版本保留。已登记的 MCP 配置经 `current` 联接自动指向新版本。之后重新运行 `burrowl connect`（或 `burrowl skills install <目录> --force`）更新 Skill。

## 开发与已知边界

- 修改本框架时先读 [AGENTS.md](AGENTS.md) 与 [burrowl-dev-check](skills/burrowl-dev-check/SKILL.md)（`burrowl connect --dev` 会安装它）。六份必读文档的分工见 [docs/README](docs/README.md)，架构见 [ARCHITECTURE](ARCHITECTURE.md)，选择理由见 [DEVELOPMENT_HISTORY](docs/DEVELOPMENT_HISTORY.md)。
- 源项目功能的迁移对照见 [MIGRATION_AUDIT](docs/MIGRATION_AUDIT.md)。
- **已知边界**：
  - 只支持 memory-v4 数据格式，不迁移源项目的旧数据；
  - auto 模式靠 MCP 服务说明引导宿主，宿主是否严格遵循取决于具体 AI 客户端；
  - Qdrant 服务器适配器和检索质量评估集尚未实现；
  - WeKnora 连接器尚未连真实服务实测；
  - 软件测试通过不证明检索质量、AI 实际效果或科学结论成立。
