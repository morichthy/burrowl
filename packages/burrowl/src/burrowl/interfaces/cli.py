"""命令行 `burrowl`。数据目录：--data-dir → 环境变量 BURROWL_DATA_DIR → %APPDATA%/burrowl/workspaces.json 的默认项。

    burrowl connect -d D:/BurrowlData/main -c codex [--mode auto|manual]   接入 AI 客户端：MCP 配置 + Skill
    burrowl enable / disable <项目目录>             在项目中启用 / 关闭 burrowl 工作流
    burrowl init D:/BurrowlData/main                   新建数据目录并设为默认
    burrowl serve [--port 0] [--no-browser]        启动工作台（同时挂载 MCP HTTP）
    burrowl mcp [-d 数据] [-w 工作目录] [--mode auto|manual]   以 stdio 运行 MCP（客户端拉起）
    burrowl backup x.zip / burrowl restore x.zip <新目录> [--map ROOT-X=E:/proj]   迁移（含外部原件与重定向）
    burrowl models install                          安装嵌入/重排模型到本机模型目录
    burrowl call memory.inspect owner_id=RES-X      调用任意操作（也可 --json 文件 / -）
    burrowl ops [--group reading]                   操作目录
    burrowl secret set keyring:burrowl/weknora       把密钥写入本机凭据管理器（交互输入，不回显）
    burrowl skills install --target .claude/skills  安装工作流 Skill（只增不覆盖用户修改）
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


def skills_source() -> Path:
    """仓库内 skills/ 目录（开发时）或安装包旁的 skills/。"""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "skills").is_dir() and (parent / "skills" / "RULES.md").exists():
            return parent / "skills"
    return here.parents[3] / "skills"


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
def serve(data_dir: Path | None = DataDir, port: int = typer.Option(0, help="0 表示随机端口"),
          no_browser: bool = typer.Option(False, "--no-browser"),
          static: Path | None = typer.Option(None, help="前端构建目录（默认 frontend/dist）"),
          work_dir: Path | None = typer.Option(None, "--work-dir", "-w", help="经 HTTP 接入的 MCP 使用的工作目录"),
          mode: str | None = Mode) -> None:
    """启动工作台与 MCP HTTP（只监听 127.0.0.1）。"""
    import socket  # noqa: PLC0415

    import uvicorn  # noqa: PLC0415

    from .api import create_api  # noqa: PLC0415
    from .mcp import build_server  # noqa: PLC0415

    app = App.open(resolve_data_dir(data_dir))
    app.work_dir = work_dir.resolve() if work_dir else None
    app.mcp_mode, app.skills_dir = _mode(mode), skills_source()
    if port == 0:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
    static = static or skills_source().parent / "frontend" / "dist"
    api, token = create_api(app, static_dir=static, port=port,
                            mcp_server=build_server(app, skills_dir=skills_source()))
    url = f"http://127.0.0.1:{port}/{token}/"
    typer.echo(f"工作台：{url}\nMCP（HTTP）：{url}mcp\n关闭浏览器不会停止服务；按 Ctrl+C 退出。")
    if not no_browser:
        webbrowser.open(url)
    uvicorn.run(api, host="127.0.0.1", port=port, log_level="warning")


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
def mcp(data_dir: Path | None = DataDir, work_dir: Path | None = WorkDir, mode: str | None = Mode) -> None:
    """以 stdio 运行 MCP 服务（由宿主客户端拉起）。"""
    from .mcp import run_stdio  # noqa: PLC0415

    app = App.open(resolve_data_dir(data_dir))
    app.work_dir, app.mcp_mode = resolve_work_dir(work_dir, app.root), _mode(mode)
    app.skills_dir = skills_source()
    run_stdio(app, skills_dir=skills_source())


def launcher() -> tuple[list[str], dict[str, str]]:
    """宿主拉起本程序的命令与环境。

    安装包内（burrowl.cmd 设置 BURROWL_HOME=版本目录）：直接用 <根>/current/python/python.exe，
    不经 .cmd（宿主经 cmd.exe 启动带空格路径的 .cmd 容易出错），切换版本后配置仍然有效。
    开发环境：当前解释器。
    """
    env = {"PYTHONUTF8": "1"}
    if os.environ.get("BURROWL_HOME"):
        version_dir = Path(os.environ["BURROWL_HOME"]).resolve()
        root = version_dir.parent.parent
        current = root / "current"
        base = current if (current / "python").is_dir() else version_dir
        if (root / "models" / "multilingual-minilm").is_dir():
            env["BURROWL_MODELS_DIR"] = str(root / "models")
        return [str(base / "python" / "python.exe"), "-m", "burrowl.interfaces.cli"], env | {"PYTHONNOUSERSITE": "1"}
    return [sys.executable, "-m", "burrowl.interfaces.cli"], env


def server_entry(data: Path, work: Path | None, mode: str | None) -> dict:
    """宿主 MCP 配置中的一项：数据目录、工作目录与运行模式都写成启动参数，改位置只需改这里。"""
    (command, *args), env = launcher()
    args = [*args, "mcp", "--data-dir", str(data)]
    if work:
        args += ["--work-dir", str(work)]
    if mode:
        args += ["--mode", mode]
    return {"command": command, "args": args, "env": env}


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


@cli.command()
def connect(data_dir: Path = typer.Option(..., "--data-dir", "-d", help="数据目录；不存在时新建"),
            client: list[str] = typer.Option(["generic"], "--client", "-c", help=(
                "要接入的客户端，可重复：generic（只打印通用配置）、claude-code、codex、cursor、vscode、gemini、"
                "claude-desktop")),
            scope: str = typer.Option("user", help="user：本机所有项目（全局）；project：只对 --project 指定的项目"),
            project: Path | None = typer.Option(None, "--project", "-p", help="项目目录（scope=project 时必填，也是工作目录）"),
            mode: str = typer.Option("auto", "--mode", help=(
                "auto：任何项目中的实质工作默认按 burrowl-work / burrowl-read / burrowl-consolidate 进行；"
                "manual：默认不启用，只在用 burrowl enable 启用的项目中或用户明确要求时使用")),
            work_dir: Path | None = typer.Option(None, "--work-dir", "-w", help=(
                "固定的工作目录；默认 project 级为项目目录，user 级为客户端启动 MCP 时的当前目录")),
            skills_dir: Path | None = typer.Option(None, "--skills-dir", help=(
                "另把 Skill 装到这个目录（客户端支持 SKILL.md 但不在内置列表中时）")),
            dev: bool = typer.Option(False, help="同时安装框架开发用的 burrowl-dev-check Skill"),
            name: str = typer.Option("burrowl", help="MCP 服务名")) -> None:
    """一步接入 AI 客户端：准备数据目录 → 写入各客户端的 MCP 配置 → 安装 Skill（客户端支持时）。可重复运行。"""
    from . import agents  # noqa: PLC0415

    _mode(mode)
    known = agents.clients()
    unknown = [c for c in client if c not in known]
    if unknown:
        raise typer.BadParameter(f"未知客户端：{'、'.join(unknown)}；可选：{'、'.join(known)}")
    if scope not in ("project", "user"):
        raise typer.BadParameter("--scope 只能是 user 或 project")
    if scope == "project":
        if project is None or not project.is_dir():
            raise typer.BadParameter("scope=project 时需要用 --project 指定已存在的项目目录")
        project = project.resolve()
    app = App.open(data_dir, create=True, fake_vectors=True)
    if _register_default(app.root, force=False):
        typer.echo(f"已登记为默认数据目录：{app.root}")
    work = (work_dir or project).resolve() if (work_dir or project) else None
    entry = server_entry(app.root, work, mode)
    typer.echo(f"数据目录：{app.root}\n工作目录：{work or '客户端启动 MCP 时的当前目录'}\n运行模式：{mode}")
    skills = [s for s in ("burrowl-work", "burrowl-read", "burrowl-consolidate", "burrowl-dev-check") if dev or s != "burrowl-dev-check"]
    for item in client:
        typer.echo(agents.write_mcp(item, scope, project, name, entry))
        target = agents.skills_dir(item, scope, project)
        if target is not None:
            install_skills(target, skills, force=False)
        elif item != "generic":
            typer.echo(f"{item}：没有内置的 Skill 目录；agent 通过 MCP 工具 system_guide 读取同样的工作流说明")
    if skills_dir:
        install_skills(skills_dir, skills, force=False)
    if mode == "manual":
        typer.echo("manual 模式：在需要的项目中运行 `burrowl enable <项目目录>` 启用，或在对话中明确要求使用 burrowl。")
    typer.echo("完成。重启客户端（或新开对话）后生效。")


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
def skills_install(target: Path, force: bool = typer.Option(False, help="覆盖已存在且内容不同的文件"),
                   only: list[str] = typer.Option([], help="只安装这些 Skill（可重复）；默认全部")) -> None:
    """把工作流 Skill 和公共文档（RULES.md、RECORDING.md）复制到宿主的技能目录。

    布局与仓库一致：<target>/<skill>/SKILL.md 与 <target>/RULES.md，Skill 中的 ../RULES.md 链接可直接打开。
    已存在且被修改过的文件默认不覆盖。
    """
    install_skills(target, only or None, force=force)


def install_skills(target: Path, names: list[str] | None, *, force: bool) -> None:
    source = skills_source()
    files = [(p, target / p.name) for p in sorted(source.glob("*.md")) if p.name != "README.md"]
    files += [(p / "SKILL.md", target / p.name / "SKILL.md") for p in sorted(source.iterdir())
              if (p / "SKILL.md").exists() and (names is None or p.name in names)]
    for origin, destination in files:
        text = origin.read_text(encoding="utf-8")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and destination.read_text(encoding="utf-8") != text and not force:
            typer.echo(f"跳过（已被修改，--force 覆盖）：{destination}")
            continue
        destination.write_text(text, encoding="utf-8")
        typer.echo(f"已安装：{destination}")


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
    params = {name for item in ops.values() for name in item["input"].get("properties", {})}
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
def models_install(embedding: bool = typer.Option(True, help="下载嵌入模型（约 220 MB，需要联网）"),
                   reranker: bool = typer.Option(True, help="安装重排模型"),
                   reranker_from: Path | None = typer.Option(None, help="从本机已有的重排模型目录复制（含清单校验）")) -> None:
    """安装模型到本机模型目录（BURROWL_MODELS_DIR 或 %LOCALAPPDATA%/burrowl/models）。已安装的跳过。"""
    models = model_store.default_models_dir()
    current = model_store.status(models)
    try:
        if embedding and not current["embedding"]["installed"]:
            typer.echo("下载嵌入模型…")
            model_store.install_embedding(models)
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
