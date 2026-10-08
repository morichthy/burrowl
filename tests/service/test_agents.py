"""接入各类客户端：运行模式与项目启用、system_guide、各客户端配置格式。"""
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


def test_skills_install_upgrade_keeps_user_edits(tmp_path, monkeypatch):
    monkeypatch.delenv("BURROWL_HOME", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    target = tmp_path / "客户端 skills"
    result = CliRunner().invoke(cli, ["skills", "install", str(target), "--dev"])
    assert result.exit_code == 0, result.output
    assert (target / "RULES.md").exists() and (target / "burrowl-dev-check" / "SKILL.md").exists()
    state = agents.load_state()
    assert str(target.resolve()) in state["skill_dirs"]                     # 登记了目录，升级时会刷新

    # 模拟"上一版本"装过的文件：旧内容（用户没改，指纹与记录一致）、用户改过的文件、新版本已删除的旧 Skill
    work = target / "burrowl-work" / "SKILL.md"
    read = target / "burrowl-read" / "SKILL.md"
    old = target / "burrowl-old" / "SKILL.md"
    for path, text in ((work, "旧版本内容"), (old, "已改名的旧 Skill")):
        path.parent.mkdir(exist_ok=True)
        path.write_text(text, encoding="utf-8")
        state["skill_files"][agents._key(path)] = agents._sha(path.read_bytes())
    read.write_text("用户自己改过", encoding="utf-8")
    agents.save_state(state)

    assert agents.upgrade(lambda _: None) == 1
    assert work.read_text(encoding="utf-8") == (SKILLS / "burrowl-work" / "SKILL.md").read_text(encoding="utf-8")
    assert read.read_text(encoding="utf-8") == "用户自己改过"                 # 用户修改保留
    assert not old.exists() and not old.parent.exists()                      # 旧 Skill 被移除
    assert (target / "burrowl-dev-check" / "SKILL.md").exists()              # 沿用当时的 --dev 选项


def test_skills_pack_for_upload(tmp_path):
    import zipfile

    made = agents.pack_skills(tmp_path / "zips", dev=False)
    assert [p.name for p in made] == ["burrowl-work.zip", "burrowl-read.zip", "burrowl-consolidate.zip"]
    with zipfile.ZipFile(made[0]) as archive:
        names = set(archive.namelist())
        assert names == {"burrowl-work/SKILL.md", "burrowl-work/RULES.md", "burrowl-work/RECORDING.md"}
        assert "../RULES.md" not in archive.read("burrowl-work/SKILL.md").decode("utf-8")
