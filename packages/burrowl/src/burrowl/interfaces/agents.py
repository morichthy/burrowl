"""把 burrowl 接入 AI 客户端（agent）：标准 MCP 配置、Skill 安装与升级、项目启用标记。

burrowl 不替各客户端决定配置写到哪里：`burrowl mcp-config` 给出标准的 MCP stdio 服务描述
（command + args + env），由客户端里的 agent 按自己客户端的规范登记；Skill 由 agent 选好自己客户端的
Skill 目录后用 `burrowl skills install <目录>` 安装，只能上传压缩包的客户端用 `burrowl skills pack`。

安装记录（安装根目录下的 burrowl-install.json；开发环境在 %APPDATA%/burrowl/ 下）：
    skill_dirs   装过 Skill 的目录及当时的选项。升级（setup 自动调用 `burrowl upgrade`）时逐个刷新；
    skill_files  本程序写出的每个文件 → sha256。只替换或删除仍与记录一致（用户没改过）的文件。
MCP 配置指向 <安装根目录>/current，升级只切换 current，配置不用改。

项目启用：<项目>/.burrowl.json 写 {"enabled": true}，并在项目说明文件（AGENTS.md，及已存在的
CLAUDE.md / GEMINI.md）中维护一段标记块。MCP 服务启动时读取工作目录下的标记；项目说明文件则让
不经 MCP 说明的客户端同样知道本项目启用了 burrowl。
"""
import hashlib
import json
import os
import re
import sys
import zipfile
from collections.abc import Callable
from pathlib import Path

SKILL_NAMES = ("burrowl-work", "burrowl-read", "burrowl-consolidate", "burrowl-dev-check")
SHARED_DOCS = ("RULES.md", "RECORDING.md")
STATE_FILE = "burrowl-install.json"

MARKER = ".burrowl.json"
BLOCK_BEGIN = "<!-- burrowl:begin -->"
BLOCK_END = "<!-- burrowl:end -->"
BLOCK = f"""{BLOCK_BEGIN}
## burrowl 工作流（本项目已启用）

本项目中的研究、分析、实验、资料整理等实质工作，按 burrowl 的 burrowl-work / burrowl-read / burrowl-consolidate 工作流进行：
成果通过 burrowl MCP 服务的工具存入其数据目录；原始数据和代码留在本项目中，只登记引用（`sources_register`）。

- 工作流说明：已加载 burrowl 的 Skill 时直接使用；否则调用 burrowl MCP 工具 `system_guide`，
  先读 name=rules，再按任务读 burrowl-work / burrowl-read / burrowl-consolidate，写记忆记录前读 recording。
- 简单问答、读源码或配置、闲聊不进入工作流。
- 由 `burrowl enable` 生成，`burrowl disable` 移除。
{BLOCK_END}
"""
NOTE_FILES = ["AGENTS.md", "CLAUDE.md", "GEMINI.md"]   # AGENTS.md 总是写；其余只在已存在时写

Echo = Callable[[str], None]


# ---------- 安装位置与 MCP 配置 ----------
def skills_source() -> Path:
    """仓库内 skills/ 目录（开发时）或安装包内的 skills/。"""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "skills" / "RULES.md").exists():
            return parent / "skills"
    return here.parents[3] / "skills"


def install_root() -> Path | None:
    """安装包内运行时的安装根目录（BURROWL_HOME 是版本目录或经 current 联接到达，resolve() 穿过联接）。"""
    home = os.environ.get("BURROWL_HOME")
    return Path(home).resolve().parent.parent if home else None


def launcher() -> tuple[list[str], dict[str, str]]:
    """客户端拉起本程序的命令与环境。

    安装包内：直接用 <根>/current/python/python.exe，不经 .cmd（客户端经 cmd.exe 启动带空格路径的 .cmd 容易出错），
    切换版本后配置仍然有效。开发环境：当前解释器。
    """
    env = {"PYTHONUTF8": "1"}
    root = install_root()
    if root is not None:
        current = root / "current"
        base = current if (current / "python").is_dir() else Path(os.environ["BURROWL_HOME"]).resolve()
        env["BURROWL_MODELS_DIR"] = str(root / "models")   # 与 model_store.default_models_dir 一致
        return [str(base / "python" / "python.exe"), "-m", "burrowl.interfaces.cli"], env | {"PYTHONNOUSERSITE": "1"}
    return [sys.executable, "-m", "burrowl.interfaces.cli"], env


def server_entry(data: Path, work: Path | None, mode: str | None) -> dict:
    """标准 MCP stdio 服务描述：数据目录、工作目录与运行模式都写成启动参数，改位置只需改这里。"""
    (command, *args), env = launcher()
    args = [*args, "mcp", "--data-dir", str(data)]
    if work:
        args += ["--work-dir", str(work)]
    if mode:
        args += ["--mode", mode]
    return {"command": command, "args": args, "env": env}


# ---------- 安装记录 ----------
def state_path() -> Path:
    root = install_root()
    if root is not None:
        return root / STATE_FILE
    return Path(os.environ.get("APPDATA") or Path.home() / ".config") / "burrowl" / STATE_FILE


def load_state() -> dict:
    path = state_path()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        value = {}
    value.setdefault("skill_dirs", {})
    value.setdefault("skill_files", {})
    return value


def save_state(state: dict) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


# ---------- Skill ----------
def skill_names(dev: bool) -> list[str]:
    return [n for n in SKILL_NAMES if dev or n != "burrowl-dev-check"]


def _skill_payload(names: list[str]) -> dict[str, bytes]:
    """目标目录下的相对路径 → 内容。布局与仓库一致：<目录>/RULES.md 与 <目录>/<skill>/SKILL.md。"""
    source = skills_source()
    files = {doc: (source / doc).read_bytes() for doc in SHARED_DOCS}
    for name in names:
        files[f"{name}/SKILL.md"] = (source / name / "SKILL.md").read_bytes()
    return files


def install_skills(target: Path, *, dev: bool, force: bool, state: dict, echo: Echo) -> dict:
    """把 Skill 装进 target 并登记，供升级时刷新。

    已存在的文件：内容相同跳过；与上次本程序写出的一致（用户没改过）就替换；被用户改过则保留并提示（force 时覆盖）。
    上次装过、这次不再需要的文件（如改名或去掉的 Skill），未被修改时删除。
    """
    target = target.resolve()
    recorded = state["skill_files"]
    wanted = _skill_payload(skill_names(dev))
    counts = {"written": 0, "unchanged": 0, "kept_modified": 0, "removed": 0}
    new_keys = set()
    for relative, data in wanted.items():
        destination = target / relative
        key = _key(destination)
        new_keys.add(key)
        if destination.exists():
            existing = destination.read_bytes()
            current = _sha(existing)
            if existing.replace(b"\r\n", b"\n") == data.replace(b"\r\n", b"\n"):   # 只差换行符也算未变
                recorded[key] = current
                counts["unchanged"] += 1
                continue
            if not force and recorded.get(key) != current:
                echo(f"保留（已被修改，--force 覆盖）：{destination}")
                counts["kept_modified"] += 1
                continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        recorded[key] = _sha(data)
        counts["written"] += 1
    for key, digest in list(recorded.items()):
        path = Path(key)
        if key in new_keys or not key.startswith(_key(target) + os.sep):
            continue
        if path.is_file() and _sha(path.read_bytes()) == digest:
            path.unlink()
            if path.parent != target and not any(path.parent.iterdir()):
                path.parent.rmdir()
            counts["removed"] += 1
        recorded.pop(key, None)
    state["skill_dirs"][str(target)] = {"dev": dev}
    echo(f"Skill → {target}：写入 {counts['written']}，未变 {counts['unchanged']}，"
         f"保留用户修改 {counts['kept_modified']}，移除旧文件 {counts['removed']}")
    return counts


def pack_skills(out_dir: Path, *, dev: bool) -> list[Path]:
    """为只能上传压缩包的客户端（如 Claude 桌面版的聊天）生成每个 Skill 的 zip。

    每个 zip 顶层是以 Skill 命名的文件夹，内含 SKILL.md 和公共规则；SKILL.md 中的 ../RULES.md 改为同目录引用。
    """
    source = skills_source()
    out_dir.mkdir(parents=True, exist_ok=True)
    made = []
    for name in skill_names(dev):
        text = (source / name / "SKILL.md").read_text(encoding="utf-8")
        for doc in SHARED_DOCS:
            text = text.replace(f"../{doc}", doc)
        path = out_dir / f"{name}.zip"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(f"{name}/SKILL.md", text)
            for doc in SHARED_DOCS:
                archive.writestr(f"{name}/{doc}", (source / doc).read_bytes())
        made.append(path)
    return made


def upgrade(echo: Echo) -> int:
    """新版本切换后由 setup 调用：刷新所有登记过的 Skill 目录。返回刷新的目录数。"""
    state = load_state()
    done = 0
    for directory, options in list(state["skill_dirs"].items()):
        if not Path(directory).is_dir():
            echo(f"跳过（目录已不存在，移除登记）：{directory}")
            state["skill_dirs"].pop(directory)
            continue
        install_skills(Path(directory), dev=bool(options.get("dev")), force=False, state=state, echo=echo)
        done += 1
    save_state(state)
    return done


# ---------- 项目启用 ----------
def project_state(work_dir: Path | None) -> bool | None:
    """工作目录的项目标记：True 已启用，False 已明确关闭，None 未设置。"""
    if work_dir is None:
        return None
    path = work_dir / MARKER
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return bool(value.get("enabled")) if isinstance(value, dict) and "enabled" in value else None


def _strip_block(text: str) -> str:
    return re.sub(rf"\n*{re.escape(BLOCK_BEGIN)}.*?{re.escape(BLOCK_END)}\n?", "\n", text, flags=re.S).strip()


def enable(project: Path) -> list[Path]:
    """在项目中启用 burrowl 工作流：写标记文件，并在项目说明文件中加入（或更新）标记块。"""
    written = [project / MARKER]
    (project / MARKER).write_text(json.dumps({"enabled": True, "workflows": list(skill_names(False))},
                                             ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for name in NOTE_FILES:
        path = project / name
        if name != "AGENTS.md" and not path.exists():
            continue
        current = _strip_block(path.read_text(encoding="utf-8")) if path.exists() else ""
        path.write_text((current + "\n\n" if current else "") + BLOCK, encoding="utf-8")
        written.append(path)
    return written


def disable(project: Path) -> list[Path]:
    """关闭：标记改为 enabled=false（全局自动模式下也不启用），并移除项目说明中的标记块。"""
    (project / MARKER).write_text(json.dumps({"enabled": False}, indent=2) + "\n", encoding="utf-8")
    changed = [project / MARKER]
    for name in NOTE_FILES:
        path = project / name
        if path.exists() and BLOCK_BEGIN in path.read_text(encoding="utf-8"):
            rest = _strip_block(path.read_text(encoding="utf-8"))
            if rest:
                path.write_text(rest + "\n", encoding="utf-8")
            else:
                path.unlink()
            changed.append(path)
    return changed
