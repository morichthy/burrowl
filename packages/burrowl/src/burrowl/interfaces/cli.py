"""命令行 `burrowl`。数据目录：--data-dir → 环境变量 BURROWL_DATA_DIR → 已登记的默认数据目录（workspaces.json）。

    burrowl mcp-config -d D:/BurrowlData/main [--mode auto|manual]   准备数据目录并输出标准 MCP 配置（由 agent 登记）
    burrowl skills install <客户端的 Skill 目录> / skills pack <目录>   安装 Skill / 生成供上传的 Skill 压缩包
    burrowl upgrade                                  新版本切换后刷新已安装的 Skill（setup 自动调用）
    burrowl enable / disable <项目目录>             在项目中启用 / 关闭 burrowl 工作流
    burrowl init D:/BurrowlData/main                   新建数据目录并设为默认
    burrowl start / open / stop / restart / status    后台运行工作台（网址固定；进程名 burrowl-workbench）
    burrowl serve [--port 0] [--no-browser]        在前台启动工作台（同时挂载 MCP HTTP）
    burrowl mcp [-d 数据] [-w 工作目录] [--mode auto|manual]   以 stdio 运行 MCP（客户端拉起）
    burrowl backup x.zip / burrowl restore x.zip <新目录> [--map ROOT-X=E:/proj]   迁移（含外部原件与重定向）
    burrowl models install                          安装嵌入/重排模型到本机模型目录
    burrowl call memory.inspect owner_id=RES-X      调用任意操作（也可 --json 文件 / -）
    burrowl ops [--group reading]                   操作目录
    burrowl secret set keyring:burrowl/weknora       把密钥写入本机凭据管理器（交互输入，不回显）
"""
import getpass
import json
import os
import sys
import webbrowser
from pathlib import Path

import typer

from ..app import App
from ..core.errors import BurrowlError
from ..index import model_store
from ..storage import secrets
from . import agents, server
from .agents import server_entry, skills_source
from .ops.registry import Ctx, call, catalog

cli = typer.Typer(add_completion=False, no_args_is_help=True, help="AI 研发工作区（burrowl）")
secret_cli = typer.Typer(help="本机密钥")
skills_cli = typer.Typer(help="工作流 Skill")
models_cli = typer.Typer(help="本机嵌入/重排模型（所有数据目录共用）")
cli.add_typer(secret_cli, name="secret")
cli.add_typer(skills_cli, name="skills")
cli.add_typer(models_cli, name="models")
DataDir = typer.Option(None, "--data-dir", "-d", help="数据目录（默认取 BURROWL_DATA_DIR 或已登记的默认工作区）")
WorkDir = typer.Option(None, "--work-dir", "-w",
                       help="工作目录（原始数据与代码所在）；默认取 BURROWL_WORK_DIR，再退到启动时的当前目录")
Mode = typer.Option(None, "--mode", help="auto：对话默认按工作流进行；manual：用户明确要求时才用。默认取数据目录配置 mcp.mode")


def registry_file() -> Path:
    """已登记的数据目录。安装包内放在安装根目录下：从商店版应用里运行时 AppData 会被重定向，其他程序看不到。"""
    if (root := agents.install_root()) is not None:
        return root / "workspaces.json"
    base = Path(os.environ.get("APPDATA") or Path.home() / ".config")
    return base / "burrowl" / "workspaces.json"


def resolve_data_dir(value: Path | None) -> Path:
    if value:
        return value
    if os.environ.get("BURROWL_DATA_DIR"):
        return Path(os.environ["BURROWL_DATA_DIR"])
    path = registry_file()
    if path.exists():
        default = json.loads(path.read_text(encoding="utf-8")).get("default")
        if default:
            return Path(default)
    raise typer.BadParameter("未指定数据目录：用 --data-dir、环境变量 BURROWL_DATA_DIR，或先运行 burrowl init")


def _fail(exc: BurrowlError) -> None:
    typer.echo(json.dumps(exc.problem(), ensure_ascii=False, indent=2), err=True)
    raise typer.Exit(exc.exit_code)


@cli.command()
def init(data_dir: Path, make_default: bool = typer.Option(True, help="登记为默认工作区")) -> None:
    """新建（或登记已有的）数据目录。"""
    app = App.open(data_dir, create=True, fake_vectors=True)
    if make_default:
        _register_default(app.root, force=True)
    typer.echo(f"数据目录已就绪：{app.root}")


@cli.command()
def serve(data_dir: Path | None = DataDir, port: int = typer.Option(0, help="0 表示沿用上次的端口（第一次随机选定）"),
          no_browser: bool = typer.Option(False, "--no-browser"),
          static: Path | None = typer.Option(None, help="前端构建目录（默认 frontend/dist）"),
          work_dir: Path | None = typer.Option(None, "--work-dir", "-w", help="经 HTTP 接入的 MCP 使用的工作目录"),
          mode: str | None = Mode,
          takeover: int | None = typer.Option(None, hidden=True, help="重启：等这个进程退出后再监听")) -> None:
    """在前台启动工作台与 MCP HTTP（只监听 127.0.0.1）；后台运行用 burrowl start。"""
    import uvicorn  # noqa: PLC0415

    from .api import create_api  # noqa: PLC0415
    from .mcp import build_server  # noqa: PLC0415

    root = resolve_data_dir(data_dir).resolve()
    if takeover:
        server.wait_exit(takeover, None, 30)
    elif (other := server.running(root)) is not None:
        typer.echo(f"这个数据目录的工作台已在运行：{other['url']}（进程 {other['pid']}）")
        raise typer.Exit(0)
    app = App.open(root)
    app.work_dir = work_dir.resolve() if work_dir else None
    app.mcp_mode, app.skills_dir = _mode(mode), skills_source()
    port, token = server.endpoint(app.root, port)
    static = static or skills_source().parent / "frontend" / "dist"
    api, token = create_api(app, static_dir=static, port=port, token=token,
                            mcp_server=build_server(app, skills_dir=skills_source()))
    url = server.url_of(port, token)
    instance = uvicorn.Server(uvicorn.Config(api, host="127.0.0.1", port=port, log_level="warning"))
    app.server = server.Control(instance, app.root, work_dir=app.work_dir, mode=mode)
    typer.echo(f"工作台：{url}\nMCP（HTTP）：{url}mcp\n关闭浏览器不会停止服务；按 Ctrl+C 退出（后台运行用 burrowl start）。")
    if not no_browser:
        webbrowser.open(url)
    server.mark_running(app.root, port, token)
    try:
        instance.run()
    finally:
        server.mark_stopped(app.root)


def _background(data_dir: Path | None, work_dir: Path | None, mode: str | None) -> dict:
    root = resolve_data_dir(data_dir).resolve()
    if (state := server.running(root)) is not None:
        return state
    App.open(root)  # 先在前台确认数据目录可用，错误直接报给用户
    server.launch(root, work_dir=work_dir.resolve() if work_dir else None, mode=mode)
    state = server.wait_ready(root)
    if state is None:
        typer.echo(f"工作台没有在 60 秒内就绪；查看日志：{root / '.burrowl' / server.LOG_FILE}", err=True)
        raise typer.Exit(1)
    return state


@cli.command()
def start(data_dir: Path | None = DataDir, work_dir: Path | None = typer.Option(None, "--work-dir", "-w"),
          mode: str | None = Mode) -> None:
    """在后台启动工作台（不占用命令行窗口；已在运行时直接给出网址）。进程名 burrowl-workbench。"""
    state = _background(data_dir, work_dir, mode)
    typer.echo(f"工作台：{state['url']}（进程 {state['pid']}）")


@cli.command(name="open")
def open_(data_dir: Path | None = DataDir) -> None:
    """打开工作台网址；没在运行就先在后台启动。"""
    state = _background(data_dir, None, None)
    typer.echo(f"工作台：{state['url']}")
    webbrowser.open(state["url"])


@cli.command()
def stop(data_dir: Path | None = DataDir) -> None:
    """停止后台工作台（先请求正常退出，超时再结束进程）。"""
    result = server.stop(resolve_data_dir(data_dir).resolve())
    typer.echo({"not_running": "工作台未运行", "stopped": "工作台已停止",
                "killed": "工作台没有正常退出，已结束进程"}[result])


@cli.command()
def restart(data_dir: Path | None = DataDir, work_dir: Path | None = typer.Option(None, "--work-dir", "-w"),
            mode: str | None = Mode) -> None:
    """重启后台工作台（加载当前安装的版本）。"""
    server.stop(resolve_data_dir(data_dir).resolve())
    state = _background(data_dir, work_dir, mode)
    typer.echo(f"工作台：{state['url']}（进程 {state['pid']}，版本 {state.get('version')}）")


@cli.command()
def status(data_dir: Path | None = DataDir) -> None:
    """工作台是否在运行、网址、进程号与版本。"""
    typer.echo(server.describe(resolve_data_dir(data_dir).resolve()))


def resolve_work_dir(value: Path | None, data: Path) -> Path | None:
    """工作目录：参数 → BURROWL_WORK_DIR → 当前目录。当前目录在数据目录或安装目录内时不当作工作目录。"""
    if value or os.environ.get("BURROWL_WORK_DIR"):
        path = Path(value or os.environ["BURROWL_WORK_DIR"]).resolve()
        if not path.is_dir():
            raise typer.BadParameter(f"工作目录不存在：{path}")
        return path
    cwd, data = Path.cwd().resolve(), data.resolve()
    install = skills_source().parent.resolve()
    if cwd.is_relative_to(data) or data.is_relative_to(cwd) or cwd.is_relative_to(install) or cwd == Path(cwd.anchor):
        return None
    return cwd


def _mode(value: str | None) -> str | None:
    if value not in (None, "auto", "manual"):
        raise typer.BadParameter("--mode 只能是 auto 或 manual")
    return value


@cli.command()
def mcp(data_dir: Path | None = DataDir, work_dir: Path | None = WorkDir, mode: str | None = Mode,
        request_log: str | None = typer.Option(None, "--request-log", help=(
            "请求日志：off / summary / full，写到数据目录的 .burrowl/logs/requests.jsonl；"
            "默认取环境变量 BURROWL_REQUEST_LOG，再取配置 diagnostics.request_log（默认 off）"))) -> None:
    """以 stdio 运行 MCP 服务（由宿主客户端拉起）。"""
    from .mcp import run_stdio  # noqa: PLC0415

    if request_log not in (None, "off", "summary", "full"):
        raise typer.BadParameter("--request-log 只能是 off、summary 或 full")
    app = App.open(resolve_data_dir(data_dir))
    app.work_dir, app.mcp_mode = resolve_work_dir(work_dir, app.root), _mode(mode)
    app.request_log = request_log
    app.skills_dir = skills_source()
    run_stdio(app, skills_dir=skills_source())


def _register_default(root: Path, *, force: bool) -> bool:
    path = registry_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    known = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"workspaces": []}
    known["workspaces"] = sorted(set(known.get("workspaces", [])) | {str(root)})
    changed = force or not known.get("default")
    if changed:
        known["default"] = str(root)
    path.write_text(json.dumps(known, ensure_ascii=False, indent=2), encoding="utf-8")
    return changed


@cli.command("mcp-config")
def mcp_config(data_dir: Path = typer.Option(..., "--data-dir", "-d", help="数据目录；不存在时新建"),
               mode: str = typer.Option("auto", "--mode", help=(
                   "auto：任何项目中的实质工作默认按 burrowl-work / burrowl-read / burrowl-consolidate 进行；"
                   "manual：默认不启用，只在用 burrowl enable 启用的项目中或用户明确要求时使用")),
               work_dir: Path | None = typer.Option(None, "--work-dir", "-w", help=(
                   "固定的工作目录；不写时为客户端启动 MCP 时的当前目录（全局接入时不要写）")),
               name: str = typer.Option("burrowl", help="MCP 服务名")) -> None:
    """准备数据目录，并向标准输出打印标准 MCP 配置（JSON），由客户端里的 agent 按自己客户端的规范登记。"""
    _mode(mode)
    app = App.open(data_dir, create=True, fake_vectors=True)
    if _register_default(app.root, force=False):
        typer.echo(f"已登记为默认数据目录：{app.root}", err=True)
    entry = server_entry(app.root, work_dir.resolve() if work_dir else None, mode)
    typer.echo(json.dumps({"mcpServers": {name: entry}}, ensure_ascii=False, indent=2))


@cli.command()
def upgrade() -> None:
    """新版本切换后刷新所有登记过的 Skill 目录（setup.cmd 自动调用）。MCP 配置经 current 联接自动指向新版本。"""
    count = agents.upgrade(typer.echo)
    typer.echo(f"已刷新 {count} 个 Skill 目录。" if count else "没有登记过的 Skill 目录，无需刷新。")


@cli.command()
def enable(project: Path = typer.Argument(Path("."), help="项目目录")) -> None:
    """在项目中启用 burrowl 工作流：写 .burrowl.json，并在 AGENTS.md（及已有的 CLAUDE.md / GEMINI.md）加入说明块。"""
    from . import agents  # noqa: PLC0415

    for path in agents.enable(project.resolve()):
        typer.echo(f"已写入：{path}")


@cli.command()
def disable(project: Path = typer.Argument(Path("."), help="项目目录")) -> None:
    """在项目中关闭 burrowl 工作流（全局 auto 模式下也不启用）；移除项目说明中的说明块。"""
    from . import agents  # noqa: PLC0415

    for path in agents.disable(project.resolve()):
        typer.echo(f"已更新：{path}")


@cli.command("call")
def call_op(name: str, args: list[str] = typer.Argument(None, help="key=value（值按 JSON 解析，失败则当字符串）"),
            json_input: str | None = typer.Option(None, "--json",
                                                  help="JSON 参数：直接写 {...}，或文件路径，- 表示标准输入"),
            data_dir: Path | None = DataDir,
            actor_kind: str = typer.Option("human", help="human / ai / workflow"),
            actor_id: str = typer.Option("cli-user")) -> None:
    """调用一个操作并输出 JSON。"""
    arguments: dict = {}
    if json_input:
        if json_input == "-":
            raw = sys.stdin.read()
        elif json_input.lstrip().startswith("{"):
            raw = json_input
        else:
            raw = Path(json_input).read_text(encoding="utf-8")
        arguments = json.loads(raw)
    for item in args or []:
        key, _, value = item.partition("=")
        try:
            arguments[key] = json.loads(value)
        except ValueError:
            arguments[key] = value
    try:
        app = App.open(resolve_data_dir(data_dir))
        app.skills_dir = skills_source()
        result = call(Ctx(app=app, actor={"kind": actor_kind, "id": actor_id}, channel="cli"), name, arguments)
    except BurrowlError as exc:
        _fail(exc)
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@cli.command()
def ops(group: str | None = typer.Option(None)) -> None:
    """列出全部操作。"""
    for item in catalog([group] if group else None):
        typer.echo(f"{item['name']:<32} {item['effect']:<6} {item['summary']}")


@secret_cli.command("set")
def secret_set(ref: str) -> None:
    """交互输入密钥并写入凭据管理器（如 keyring:burrowl/weknora）。"""
    try:
        secrets.store(ref, getpass.getpass("密钥（不回显）："))
    except BurrowlError as exc:
        _fail(exc)
    typer.echo(json.dumps(secrets.status(ref), ensure_ascii=False))


@skills_cli.command("install")
def skills_install(target: Path = typer.Argument(..., help="客户端的 Skill 目录（由 agent 按自己客户端的规范确定）"),
                   dev: bool = typer.Option(False, help="同时安装框架开发用的 burrowl-dev-check"),
                   force: bool = typer.Option(False, help="覆盖被用户改过的文件")) -> None:
    """把 burrowl Skill 和公共规则（RULES.md、RECORDING.md）装进 target，并登记以便升级时自动刷新。

    布局：<target>/RULES.md、<target>/<skill>/SKILL.md（Skill 中的 ../RULES.md 链接可直接打开）。
    被用户改过的文件默认保留；上一版本装过、这一版本不再有的文件（未修改时）会被移除。
    """
    state = agents.load_state()
    agents.install_skills(target, dev=dev, force=force, state=state, echo=typer.echo)
    agents.save_state(state)


@skills_cli.command("pack")
def skills_pack(out_dir: Path = typer.Argument(..., help="输出目录"),
                dev: bool = typer.Option(False, help="同时打包 burrowl-dev-check")) -> None:
    """为只能上传 Skill 压缩包的客户端（如 Claude 桌面版的聊天）生成每个 Skill 的 zip（内含公共规则）。"""
    for path in agents.pack_skills(out_dir.resolve(), dev=dev):
        typer.echo(f"已生成：{path}")
    typer.echo("这类上传的 Skill 不会随升级自动更新；新版本后重新生成并上传。")


@skills_cli.command("check")
def skills_check() -> None:
    """检查 Skill 与公共文档里引用的工具：都必须存在，且在 MCP 默认工具组内。

    检查范围：frontmatter 的 requires_tools，以及正文中出现的 `组名_操作名` 形式的工具名。
    """
    import re  # noqa: PLC0415 - 仅本命令使用

    from ..core.config import DEFAULT_TOOLSETS  # noqa: PLC0415

    ops = {item["name"].replace(".", "_"): item for item in catalog()}
    groups = {item["group"] for item in ops.values()}
    # 正文中用反引号写出的工具名，如 `reading_start` 或 `reading_start{...}`
    pattern = re.compile(rf"`((?:{'|'.join(sorted(groups))})_[a-z_]+)[`{{(]")
    from ..core.schema import defs as record_defs  # noqa: PLC0415

    params = {name for item in ops.values() for name in item["input"].get("properties", {})}
    # 记录草案的字段名（如 provenance_gap）也不是工具，只是恰好以工具组名开头
    params |= {name for definition in record_defs().values() for name in definition.get("properties", {})}
    problems = []
    source = skills_source()
    for path in sorted([*source.glob("*/SKILL.md"), *source.glob("*.md")]):
        text = path.read_text(encoding="utf-8")
        named = set(pattern.findall(text)) - params  # 形如 review_filter 的是参数名，不是工具
        for line in text.splitlines():
            if line.startswith("requires_tools:"):
                named |= {t.strip() for t in line.split(":", 1)[1].strip(" []").split(",") if t.strip()}
        where = path.relative_to(source).as_posix()
        for tool in sorted(named):
            if tool not in ops:
                problems.append(f"{where}: 未知工具 {tool}")
            elif ops[tool]["effect"] == "user":
                problems.append(f"{where}: {tool} 只能由用户在工作台操作，agent 无法调用")
            elif ops[tool]["group"] not in DEFAULT_TOOLSETS and ops[tool]["group"] != "system":
                problems.append(f"{where}: {tool} 所在工具组 {ops[tool]['group']} 不在 MCP 默认工具组")
    typer.echo("\n".join(problems) if problems else "全部 Skill 引用的工具都存在且可由 agent 调用")
    raise typer.Exit(1 if problems else 0)


@cli.command()
def doctor(data_dir: Path | None = DataDir,
           require: list[str] = typer.Option([], help="必须就绪的能力：vector / rerank；不就绪时退出码 1")) -> None:
    """检查运行环境：数据目录、模型、向量与重排能力、连接器。"""
    app = App.open(resolve_data_dir(data_dir))
    caps = app.capabilities() | {"models": model_store.status(model_store.default_models_dir())}
    typer.echo(json.dumps(caps, ensure_ascii=False, indent=2))
    ready = {"vector": caps["vector"]["ready"], "rerank": caps["reranker"]["ready"]}
    missing = [r for r in require if not ready.get(r, False)]
    if missing:
        typer.echo(f"未就绪：{'、'.join(missing)}", err=True)
        raise typer.Exit(1)


@cli.command()
def validate(data_dir: Path | None = DataDir) -> None:
    """全工作区只读校验（提交链与指纹、残留、来源文件、索引）。发现问题时退出码 1。"""
    from ..services import health  # noqa: PLC0415 - 仅本命令使用

    app = App.open(resolve_data_dir(data_dir))
    result = health.validate(app.storage, app.index)
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    raise typer.Exit(0 if result["ok"] else 1)


@cli.command()
def backup(target: Path = typer.Argument(..., help="备份文件路径（.zip，不能在数据目录内，不能已存在）"),
           data_dir: Path | None = DataDir,
           external: bool = typer.Option(True, "--external/--no-external",
                                         help="连同数据目录外已登记的原件一起打包（默认是）")) -> None:
    """把整个数据目录（默认连同已登记的外部原件）打成 burrowl-bundle（逐文件 sha256；不含可重建的索引）。"""
    from ..portable import bundle  # noqa: PLC0415

    try:
        result = bundle.pack(resolve_data_dir(data_dir), target, include_external=external)
    except BurrowlError as exc:
        _fail(exc)
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@cli.command()
def restore(source: Path = typer.Argument(..., help="burrowl-bundle 文件"),
            target: Path = typer.Argument(..., help="恢复到的目录（必须不存在或为空）"),
            map_: list[str] = typer.Option([], "--map", help=(
                "外部原件的新位置，可重复：工作根ID=新目录（如 ROOT-PROJ=E:/proj），或 旧路径前缀=新路径前缀")),
            make_default: bool = typer.Option(False, "--default", help="恢复后登记为默认数据目录")) -> None:
    """校验 burrowl-bundle 后恢复到新目录（绝不覆盖已有数据），外部原件随之迁移，来源登记自动重定向。

    外部原件的去向：有 --map → 新位置；原位置仍有同一文件 → 不动；否则放到与新数据目录并列的 <目录名>-work/ 下。
    """
    from ..portable import bundle  # noqa: PLC0415

    mapping = {}
    for item in map_:
        key, sep, value = item.partition("=")
        if not sep or not key.strip() or not value.strip():
            raise typer.BadParameter(f"--map 格式应为 键=新位置：{item}")
        mapping[key.strip()] = value.strip()
    try:
        result = bundle.unpack(source, target, mapping=mapping)
    except BurrowlError as exc:
        _fail(exc)
    if make_default:
        _register_default(Path(result["restored_to"]), force=True)
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@cli.command("verify-backup")
def verify_backup(source: Path) -> None:
    """只校验 burrowl-bundle，不写任何文件。"""
    from ..portable import bundle  # noqa: PLC0415

    try:
        typer.echo(json.dumps(bundle.verify(source), ensure_ascii=False, indent=2))
    except BurrowlError as exc:
        _fail(exc)


@models_cli.command("status")
def models_status() -> None:
    """显示模型目录与安装情况。"""
    typer.echo(json.dumps(model_store.status(model_store.default_models_dir()), ensure_ascii=False, indent=2))


@models_cli.command("install")
def models_install(embedding: bool = typer.Option(True, help="下载嵌入模型（需要联网）"),
                   embedding_model: str = typer.Option(model_store.DEFAULT_EMBEDDING, help=(
                       "嵌入模型：jina-v2-base-zh（默认，约 640 MB）、minilm（0.6 之前的默认，约 220 MB）、"
                       "bge-small-zh、bge-base-zh、qwen3-0.6b-q")),
                   reranker: bool = typer.Option(True, help="安装重排模型"),
                   reranker_from: Path | None = typer.Option(None, help="从本机已有的重排模型目录复制（含清单校验）")) -> None:
    """安装模型到本机模型目录（BURROWL_MODELS_DIR 或 %LOCALAPPDATA%/burrowl/models）。已安装的跳过。
    换用非默认嵌入模型后，还要在配置里改 index.embedding 的 model、dim、query_prefix（见输出提示）。"""
    models = model_store.default_models_dir()
    current = model_store.status(models)
    try:
        spec = model_store.EMBEDDING_MODELS.get(embedding_model)
        if spec is None:
            raise BurrowlError("INVALID_ARGUMENT", "未知的嵌入模型",
                               {"model": embedding_model, "known": sorted(model_store.EMBEDDING_MODELS)})
        if embedding and not current["embedding_models"][embedding_model]["installed"]:
            typer.echo(f"下载嵌入模型 {embedding_model}（{spec['size']}）…")
            model_store.install_embedding(models, embedding_model)
            if embedding_model != model_store.DEFAULT_EMBEDDING:
                typer.echo("启用方法：config_update index.embedding = " + json.dumps(
                    {"model": spec["model"], "dim": spec["dim"], "query_prefix": spec["query_prefix"]},
                    ensure_ascii=False) + "，然后 burrowl call index.reconcile")
        if reranker and not current["reranker"]["installed"]:
            typer.echo("安装重排模型…")
            model_store.install_reranker(models, reranker_from)
    except BurrowlError as exc:
        _fail(exc)
    typer.echo(json.dumps(model_store.status(models), ensure_ascii=False, indent=2))


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
