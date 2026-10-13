import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Settings } from "./Settings";

// 只列设置页使用的字段，验证用户能看到的保存、预览和日志状态。
const defaults = {
  index: { path: "index.sqlite3", embedding: { model: "test", path: null }, vector: { kind: "none" },
    reranker: { path: null }, ocr_enabled: false },
  query: { review_filter: "current", result_limit: 20, budget: {} },
  reading: { strategy: "standard", review_filter: "current", budget: {},
    context: { max_owners: 10, note_max_tokens: 1000 }, association: { enabled: false, max_rounds: 2 },
    screening: { batch_owners: 3, regular_windows: 2, max_windows: 4 }, reranking: { mode: "off", candidate_limit: 10 } },
  collaboration: { subagents: "off", subagent_requirements: "" },
  mcp: { toolsets: ["memory"], mode: "auto", error_envelope: false },
  diagnostics: { request_log: "full" }, connectors: [],
};
let config: typeof defaults;
let revision: number;
let rejectSave: boolean;
let calls: { name: string; args: Record<string, unknown> }[];

beforeEach(() => {
  config = structuredClone(defaults);
  revision = 1;
  rejectSave = false;
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
    const name = url.replace("api/v1/ops/", "");
    const args = JSON.parse(String(init.body));
    calls.push({ name, args });
    if (name === "config.get") return Response.json({ config, revision: String(revision), secrets: {} });
    if (name === "system.request_log") return Response.json({ level: config.diagnostics.request_log,
      items: config.diagnostics.request_log === "off" ? [] : [{ op: "original_tool_call", status: "ok", request_id: "REQ-1" }] });
    if (name === "config.update") {
      if (rejectSave) return Response.json({ code: "VERSION_CONFLICT", message: "配置已被其他操作修改，请刷新后重试" }, { status: 409 });
      const next = args.patch as typeof defaults;
      const effects = next.index?.path !== config.index.path ? [{ action: "index.rebuild", reason: "索引位置改变，需要重建索引" }] : [];
      if (!args.dry_run) { config = structuredClone(next); revision++; }
      return Response.json({ revision: String(revision), config: next, effects });
    }
    return Response.json({});
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("关闭并保存日志后自动刷新，历史记录不再展示", async () => {
  render(<Settings />);
  expect(await screen.findByText("original_tool_call")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("radio", { name: "关闭" }));
  fireEvent.click(screen.getByRole("button", { name: "保存设置" }));
  expect(await screen.findByText("配置已保存。")).toBeInTheDocument();
  expect(await screen.findByText(/请求日志已关闭：停止记录/)).toBeInTheDocument();
  expect(screen.queryByText("original_tool_call")).not.toBeInTheDocument();
});

it("检查影响只预览，保存后明确提示待重建；再编辑使旧回执失效", async () => {
  render(<Settings />);
  const path = await screen.findByLabelText("索引文件位置（相对数据目录或绝对路径）");
  fireEvent.change(path, { target: { value: "new.sqlite3" } });
  fireEvent.click(screen.getByRole("button", { name: "检查影响" }));
  expect(await screen.findByText("检查通过，尚未保存配置。")).toBeInTheDocument();
  expect(config.index.path).toBe("index.sqlite3");
  expect(screen.getByText("索引位置改变，需要重建索引")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "保存设置" }));
  expect(await screen.findByText("配置已保存。")).toBeInTheDocument();
  await waitFor(() => expect(config.index.path).toBe("new.sqlite3"));
  fireEvent.change(path, { target: { value: "third.sqlite3" } });
  expect(screen.queryByText("配置已保存。")).not.toBeInTheDocument();
  expect(screen.getByText("有未保存的修改。")).toBeInTheDocument();
});

it("保存冲突只显示失败，兼容开关进入配置草稿", async () => {
  render(<Settings />);
  fireEvent.click(await screen.findByRole("checkbox", { name: "开启兼容返回" }));
  rejectSave = true;
  fireEvent.click(screen.getByRole("button", { name: "保存设置" }));
  expect(await screen.findByText("配置已被其他操作修改，请刷新后重试")).toBeInTheDocument();
  expect(screen.queryByText("配置已保存。")).not.toBeInTheDocument();
  const saved = calls.find((c) => c.name === "config.update");
  expect((saved?.args.patch as typeof defaults).mcp.error_envelope).toBe(true);
  expect(config.mcp.error_envelope).toBe(false);
});
