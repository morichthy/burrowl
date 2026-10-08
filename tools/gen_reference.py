"""从操作注册表与配置模型生成 docs/reference/（工具参考与配置参考）和前端的 ops.json。改了操作或配置后重新运行。

用法：uv run python tools/gen_reference.py
"""
import json
from pathlib import Path

from burrowl.core.config import BurrowlConfig
from burrowl.interfaces.ops.registry import catalog

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "reference"
FRONTEND = ROOT / "frontend" / "src" / "generated"
EFFECTS = {"read": "只读", "write": "写入", "admin": "管理", "user": "仅用户（不暴露给 agent）"}


def _params(schema: dict) -> str:
    props, required = schema.get("properties", {}), set(schema.get("required", []))
    if not props:
        return "无"
    parts = []
    for name, spec in props.items():
        kind = spec.get("type") or ("enum" if "enum" in spec else "any")
        if "anyOf" in spec:
            kind = "|".join(s.get("type", "?") for s in spec["anyOf"])
        parts.append(f"`{name}`{'*' if name in required else ''}: {kind}")
    return "，".join(parts)


def ops_md() -> str:
    lines = ["# 工具参考（自动生成，勿手改）", "",
             "HTTP：`POST /api/v1/ops/<name>`；MCP：工具名把 `.` 换成 `_`；CLI：`burrowl call <name> --json '{...}'`。",
             "带 * 的参数必填。", ""]
    groups: dict[str, list[dict]] = {}
    for item in catalog():
        groups.setdefault(item["group"], []).append(item)
    for group, items in sorted(groups.items()):
        lines += [f"## {group}", "", "| 操作 | 类型 | 说明 | 参数 |", "|---|---|---|---|"]
        for item in sorted(items, key=lambda i: i["name"]):
            summary = (item["summary"] or "").replace("|", "\\|").replace("\n", " ")
            lines.append(f"| `{item['name']}` | {EFFECTS.get(item['effect'], item['effect'])} | {summary} | "
                         f"{_params(item['input'])} |")
        lines.append("")
    return "\n".join(lines)


def config_md() -> str:
    defaults = BurrowlConfig().model_dump(mode="json")
    return "\n".join(["# 配置参考（自动生成，勿手改）", "",
                      "配置文件：`<数据目录>/.burrowl/config.json`。通过工作台设置页、MCP `config_update` 或 "
                      "`burrowl call config.update` 修改（JSON Merge Patch + expected_revision）。密钥只存引用。", "",
                      "## 默认值", "", "```json", json.dumps(defaults, ensure_ascii=False, indent=2), "```", "",
                      "## JSON Schema", "", "```json",
                      json.dumps(BurrowlConfig.model_json_schema(), ensure_ascii=False, indent=2), "```", ""])


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ops.md").write_text(ops_md(), encoding="utf-8", newline="\n")
    (OUT / "config.md").write_text(config_md(), encoding="utf-8", newline="\n")
    FRONTEND.mkdir(parents=True, exist_ok=True)
    (FRONTEND / "ops.json").write_text(json.dumps(catalog(), ensure_ascii=False, indent=1), encoding="utf-8",
                                       newline="\n")
    print(f"wrote {OUT} and {FRONTEND / 'ops.json'}")


if __name__ == "__main__":
    main()
