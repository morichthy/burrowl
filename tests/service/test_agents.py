"""接入各类客户端：运行模式与项目启用、system_guide、各客户端配置格式。"""
import json
from pathlib import Path

from typer.testing import CliRunner

from burrowl.app import App
from burrowl.interfaces import agents
from burrowl.interfaces.cli import cli
from burrowl.interfaces.mcp import instructions
from burrowl.interfaces.ops.registry import Ctx, call

SKILLS = Path(__file__).resolve().parents[2] / "skills"


def test_mode_and_project_switch(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "CLAUDE.md").write_text("# 原有说明\n", encoding="utf-8")
    app = App.open(tmp_path / "data", create=True, fake_vectors=True)
    app.work_dir, app.mcp_mode = project, "manual"
    assert "默认不进入 burrowl 工作流" in instructions(app)                 # 手动：默认不启用
    agents.enable(project)
    assert "本项目已启用" in instructions(app)                              # 项目启用后自动
    assert agents.BLOCK_BEGIN in (project / "AGENTS.md").read_text(encoding="utf-8")
    assert "# 原有说明" in (project / "CLAUDE.md").read_text(encoding="utf-8")
    agents.enable(project)                                                  # 重复启用不重复写块
    assert (project / "AGENTS.md").read_text(encoding="utf-8").count(agents.BLOCK_BEGIN) == 1
    app.mcp_mode = "auto"
    agents.disable(project)
    assert "本项目已关闭" in instructions(app)                              # 全局自动下也可单独关闭
    assert not (project / "AGENTS.md").exists()                             # 只有标记块的文件被移除
    assert (project / "CLAUDE.md").read_text(encoding="utf-8").strip() == "# 原有说明"


def test_system_guide_serves_skill_text(tmp_path):
    app = App.open(tmp_path / "data", create=True, fake_vectors=True)
    app.skills_dir = SKILLS
    ctx = Ctx(app=app, actor={"kind": "ai", "id": "t"}, channel="mcp")
    assert call(ctx, "system.guide", {"name": "burrowl-work"})["text"] == (SKILLS / "burrowl-work" / "SKILL.md").read_text(encoding="utf-8")


def test_connect_user_scope_for_several_clients(tmp_path, monkeypatch):
    monkeypatch.delenv("BURROWL_HOME", raising=False)
    home = tmp_path / "home"
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    codex = home / ".codex" / "config.toml"
    codex.parent.mkdir(parents=True)
    codex.write_text('model = "x"\n\n[mcp_servers.burrowl]\ncommand = "old"\n\n[mcp_servers.other]\ncommand = "y"\n',
                     encoding="utf-8")
    result = CliRunner().invoke(cli, ["connect", "-d", str(tmp_path / "数据"), "-c", "codex", "-c", "cursor",
                                      "-c", "vscode", "--mode", "manual"])
    assert result.exit_code == 0, result.output
    text = codex.read_text(encoding="utf-8")
    assert 'model = "x"' in text and "[mcp_servers.other]" in text and '"old"' not in text   # 只替换自己的表
    assert text.count("[mcp_servers.burrowl]") == 1 and '"--mode", "manual"' in text
    assert (home / ".codex" / "skills" / "burrowl-work" / "SKILL.md").exists()
    cursor = json.loads((home / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    assert "--work-dir" not in cursor["mcpServers"]["burrowl"]["args"]                      # 全局：工作目录取启动时目录
    vscode = json.loads((tmp_path / "appdata" / "Code" / "User" / "mcp.json").read_text(encoding="utf-8"))
    assert vscode["servers"]["burrowl"]["type"] == "stdio"
    assert "burrowl enable" in result.output
