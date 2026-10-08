"""把 burrowl 接入各类 AI 客户端（agent）：MCP 配置的位置与格式、Skill 目录、项目启用标记。

MCP 配置是通用的 stdio 服务描述（command + args + env），各客户端只是存放位置和外层格式不同：

    客户端          用户级（全局）                                   项目级
    generic         只打印配置，由用户或 agent 填进自己的客户端
    claude-code     claude mcp add-json --scope user                 <项目>/.mcp.json
    codex           ~/.codex/config.toml  [mcp_servers.<名>]         <项目>/.codex/config.toml
    cursor          ~/.cursor/mcp.json                               <项目>/.cursor/mcp.json
    vscode          %APPDATA%/Code/User/mcp.json（键 servers）       <项目>/.vscode/mcp.json
    gemini          ~/.gemini/settings.json                          <项目>/.gemini/settings.json
    claude-desktop  %APPDATA%/Claude/claude_desktop_config.json      —

Skill（SKILL.md 目录格式）只有原生支持的客户端才装到它们的技能目录；其他客户端由 MCP 工具
system_guide 提供同样的说明全文，所以不装 Skill 也能按工作流工作。

项目启用：<项目>/.burrowl.json 写 {"enabled": true}，并在项目说明文件（AGENTS.md，及已存在的
CLAUDE.md / GEMINI.md）中维护一段标记块。MCP 服务启动时读取工作目录下的标记；项目说明文件则让
不经 MCP 说明的客户端同样知道本项目启用了 burrowl。
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

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


def _appdata() -> Path:
    return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")


def clients() -> dict[str, dict]:
    """客户端 → {user, project, format, skills_user, skills_project}。路径在调用时计算（便于测试改环境变量）。"""
    home = Path.home()
    return {
        "generic": {"format": "print"},
        "claude-code": {"user": "claude-cli", "project": ".mcp.json", "format": "json",
                        "skills_user": home / ".claude" / "skills", "skills_project": ".claude/skills"},
        "codex": {"user": home / ".codex" / "config.toml", "project": ".codex/config.toml", "format": "toml",
                  "skills_user": home / ".codex" / "skills", "skills_project": ".codex/skills"},
        "cursor": {"user": home / ".cursor" / "mcp.json", "project": ".cursor/mcp.json", "format": "json"},
        "vscode": {"user": _appdata() / "Code" / "User" / "mcp.json", "project": ".vscode/mcp.json",
                   "format": "vscode"},
        "gemini": {"user": home / ".gemini" / "settings.json", "project": ".gemini/settings.json",
                   "format": "json"},
        "claude-desktop": {"user": _appdata() / "Claude" / "claude_desktop_config.json", "format": "json"},
    }


def _merge_json(path: Path, key: str, name: str, entry: dict) -> None:
    config = json.loads(path.read_text(encoding="utf-8")) if path.exists() and path.read_text(encoding="utf-8").strip() else {}
    config.setdefault(key, {})[name] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def toml_block(name: str, entry: dict) -> str:
    """Codex 的 [mcp_servers.<名>] 表。json.dumps 生成的字符串同时是合法的 TOML 基本字符串。"""
    lines = [f"[mcp_servers.{name}]", f"command = {json.dumps(entry['command'], ensure_ascii=False)}",
             f"args = {json.dumps(entry['args'], ensure_ascii=False)}", "", f"[mcp_servers.{name}.env]"]
    lines += [f"{k} = {json.dumps(v, ensure_ascii=False)}" for k, v in entry["env"].items()]
    return "\n".join(lines) + "\n"


def _merge_toml(path: Path, name: str, entry: dict) -> None:
    """替换（或追加）本服务的两个表，文件其余内容原样保留。"""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    own = re.compile(rf"^\[mcp_servers\.{re.escape(name)}(\.env)?\]\s*$")
    kept, skipping = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("["):
            skipping = bool(own.match(line.strip()))
        if not skipping:
            kept.append(line)
    body = "\n".join(kept).rstrip()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text((body + "\n\n" if body else "") + toml_block(name, entry), encoding="utf-8")


def write_mcp(client: str, scope: str, project: Path | None, name: str, entry: dict) -> str:
    """按客户端写入 MCP 配置，返回给用户看的一句说明。不认识或无法写入时返回手动配置文本。"""
    spec = clients()[client]
    if spec["format"] == "print":
        return ("通用 MCP 配置（填进你的客户端的 MCP 设置）：\n"
                + json.dumps({"mcpServers": {name: entry}}, ensure_ascii=False, indent=2)
                + "\nTOML 形式（如 Codex）：\n" + toml_block(name, entry))
    target = spec.get(scope)
    if target is None:
        return f"{client} 不支持 {scope} 级配置"
    if target == "claude-cli":
        command = [shutil.which("claude") or "claude", "mcp", "add-json", "--scope", "user", name,
                   json.dumps(entry, ensure_ascii=False)]
        if shutil.which("claude") and subprocess.run(command, check=False).returncode == 0:
            return "Claude Code：已登记为用户级 MCP 服务"
        return "Claude Code：未找到 claude 命令或登记失败，请手动运行：\n  " + subprocess.list2cmdline(command)
    path = target if isinstance(target, Path) else project / target
    if spec["format"] == "toml":
        _merge_toml(path, name, entry)
    elif spec["format"] == "vscode":
        _merge_json(path, "servers", name, {"type": "stdio", **entry})
    else:
        _merge_json(path, "mcpServers", name, entry)
    return f"{client}：已写入 {path}"


def skills_dir(client: str, scope: str, project: Path | None) -> Path | None:
    """客户端原生的 Skill 目录；不支持 Skill 的客户端返回 None（改用 system_guide 工具）。"""
    spec = clients()[client]
    if scope == "user":
        return spec.get("skills_user")
    relative = spec.get("skills_project")
    return project / relative if relative and project else None


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
    (project / MARKER).write_text(json.dumps({"enabled": True, "workflows": ["burrowl-work", "burrowl-read", "burrowl-consolidate"]},
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
