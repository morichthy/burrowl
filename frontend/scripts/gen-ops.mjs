// 由 src/generated/ops.json（tools/gen_reference.py 从操作注册表导出）生成带类型的操作参数表。
// 改了后端操作后：uv run python tools/gen_reference.py && npm run ops
import { readFile, writeFile } from "node:fs/promises";
import { compile } from "json-schema-to-typescript";

const here = new URL("../src/generated/", import.meta.url);
const ops = JSON.parse(await readFile(new URL("ops.json", here), "utf8"));
const pascal = (name) => name.split(/[._]/).map((p) => p[0].toUpperCase() + p.slice(1)).join("");

// 字段级 title 会被生成为独立的类型别名，在多个操作间重名；去掉后字段类型直接内联。
function stripTitles(node, root = true) {
  if (Array.isArray(node)) return node.map((n) => stripTitles(n, false));
  if (node && typeof node === "object") {
    const copy = {};
    for (const [key, value] of Object.entries(node)) {
      if (key === "title" && !root && typeof value === "string") continue;
      copy[key] = stripTitles(value, false);
    }
    return copy;
  }
  return node;
}

let out = "// 自动生成，勿手改（npm run ops）。\n\n";
const entries = [];
for (const op of ops) {
  const space = pascal(op.name);
  const schema = { ...stripTitles(op.input), title: "Args", additionalProperties: false };
  const body = await compile(schema, "Args", { bannerComment: "", additionalProperties: false });
  out += `export declare namespace ${space} {\n${body.replace(/^/gm, "  ").trimEnd()}\n}\n\n`;
  entries.push(`  "${op.name}": ${space}.Args;`);
}
out += `export interface OpArgs {\n${entries.join("\n")}\n}\n\nexport type OpName = keyof OpArgs;\n`;
out += `\nexport const OP_EFFECTS: Record<OpName, string> = {\n${ops.map((o) => `  "${o.name}": "${o.effect}",`).join("\n")}\n};\n`;
await writeFile(new URL("ops.ts", here), out, "utf8");
console.log(`generated ${ops.length} ops`);
