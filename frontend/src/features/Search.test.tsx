import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Search } from "./Search";

type Reply = { body: unknown; status?: number };
let handlers: Record<string, (args: Record<string, unknown>) => Reply | Promise<Reply>>;
let calls: { name: string; args: Record<string, unknown> }[];
const item = { candidate_id: "C-1", kind: "overview", owner_id: "RES-VENV", record_id: "MEM-1", title: "虚拟环境验证", excerpt: "正文", level: "L4", review: "not-reviewed", score: 1, stale_revision: false };
const result = (gaps: unknown[] = [], items = [item]) => ({ query_id: "Q-1", items, gaps, consumed: { wall_ms: 12 } });
const missing = [{ owner_id: "RES-VENV", channel: "lexical", reason: "not_indexed" }, { owner_id: "RES-VENV", channel: "vector", reason: "not_indexed" }];

beforeEach(() => {
  handlers = {};
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
    const name = url.replace("api/v1/ops/", "");
    const args = JSON.parse(String(init.body));
    calls.push({ name, args });
    const reply = await handlers[name](args);
    return new Response(JSON.stringify(reply.body), { status: reply.status ?? 200 });
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function submit(question = "虚拟环境") {
  fireEvent.change(screen.getByLabelText("问题"), { target: { value: question } });
  fireEvent.click(screen.getByRole("button", { name: /^检索$/ }));
}

describe("检索缺口与重试", () => {
  it("真实结构化缺口合并为可读提示，未知原因和组包信息也不会变成空行", async () => {
    handlers["query.search"] = () => ({ body: result([...missing, { channel: "dense", reason: "future_reason" }]) });
    handlers["query.assemble"] = () => ({ body: { parts: [], gaps: [{ code: "CONTEXT_MISSING", message: "该材料的必要依据未能加入" }] } });
    const { container } = render(<Search />);
    submit();
    expect(await screen.findByText(/RES-VENV：尚未建立检索索引/)).toHaveTextContent("关键词、向量");
    expect(container.querySelectorAll(".notice")).toHaveLength(2);
    expect(screen.getByText(/future_reason/)).toBeVisible();
    fireEvent.click(screen.getByRole("checkbox", { name: "选择 虚拟环境验证" }));
    fireEvent.click(screen.getByRole("button", { name: "组包阅读所选（1）" }));
    expect(await screen.findByText("该材料的必要依据未能加入")).toBeVisible();
    for (const notice of container.querySelectorAll(".notice")) expect(notice.textContent?.trim()).toBeTruthy();
  });

  it("补齐后重试冻结的原问题与过滤条件，不使用未提交的输入，也不创建阅读会话", async () => {
    let repaired = false;
    handlers["query.search"] = () => ({ body: result(repaired ? [] : missing, repaired ? [item] : []) });
    handlers["index.reconcile"] = () => { repaired = true; return { body: { items: [] } }; };
    render(<Search />);
    fireEvent.change(screen.getByLabelText("内容范围"), { target: { value: "technical" } });
    fireEvent.click(screen.getByLabelText("只看已复核"));
    submit();
    await screen.findByRole("button", { name: "补齐索引并重试" });
    fireEvent.change(screen.getByLabelText("问题"), { target: { value: "存储与检索" } });
    fireEvent.change(screen.getByLabelText("内容范围"), { target: { value: "all" } });
    fireEvent.click(screen.getByRole("button", { name: "补齐索引并重试" }));
    expect(await screen.findByRole("link", { name: "虚拟环境验证" })).toBeVisible();
    expect(calls.map((call) => call.name)).toEqual(["query.search", "index.reconcile", "query.search"]);
    expect(calls[2].args).toEqual(calls[0].args);
    expect(calls[2].args).toMatchObject({ question: "虚拟环境", content_source: "technical", review_filter: "reviewed_only" });
    expect(screen.queryByText(/尚未建立检索索引/)).not.toBeInTheDocument();
  });

  it("模型缺失不提供补齐索引入口，索引修复失败显示真实错误而不假装成功", async () => {
    handlers["query.search"] = () => ({ body: result([{ channel: "dense", reason: "embedder_unavailable" }]) });
    render(<Search />);
    submit();
    expect(await screen.findByText(/向量模型不可用/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "补齐索引并重试" })).not.toBeInTheDocument();
    handlers["query.search"] = () => ({ body: result(missing) });
    handlers["index.reconcile"] = () => ({ status: 500, body: { code: "STORAGE_ERROR", message: "索引数据库不可用" } });
    submit();
    fireEvent.click(await screen.findByRole("button", { name: "补齐索引并重试" }));
    expect(await screen.findByText("索引数据库不可用")).toBeVisible();
    expect(calls.filter((call) => call.name === "query.search")).toHaveLength(2);
  });

  it("新检索等待和失败时隐藏旧结果，旧组包请求不会回写到新查询", async () => {
    handlers["query.search"] = () => ({ body: result() });
    let finishPacket!: (reply: Reply) => void;
    handlers["query.assemble"] = () => new Promise((resolve) => { finishPacket = resolve; });
    render(<Search />);
    submit();
    fireEvent.click(await screen.findByRole("checkbox", { name: "选择 虚拟环境验证" }));
    fireEvent.click(screen.getByRole("button", { name: "组包阅读所选（1）" }));
    let finishSearch!: (reply: Reply) => void;
    handlers["query.search"] = () => new Promise((resolve) => { finishSearch = resolve; });
    submit("存储与检索");
    expect(screen.queryByRole("link", { name: "虚拟环境验证" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "检索中…" })).toBeDisabled();
    await act(async () => { finishPacket({ body: { gaps: [], parts: [{ group: "direct", heading: "过期阅读包", markdown: "旧内容" }] } }); });
    expect(screen.queryByText("过期阅读包")).not.toBeInTheDocument();
    await act(async () => { finishSearch({ status: 500, body: { code: "STORAGE_ERROR", message: "查询失败" } }); });
    await waitFor(() => expect(screen.getByText("查询失败")).toBeVisible());
    expect(screen.queryByRole("link", { name: "虚拟环境验证" })).not.toBeInTheDocument();
  });

  it("旧组包失败晚到时不在新查询下显示错误", async () => {
    handlers["query.search"] = () => ({ body: result() });
    let finishPacket!: (reply: Reply) => void;
    handlers["query.assemble"] = () => new Promise((resolve) => { finishPacket = resolve; });
    render(<Search />);
    submit();
    fireEvent.click(await screen.findByRole("checkbox", { name: "选择 虚拟环境验证" }));
    fireEvent.click(screen.getByRole("button", { name: "组包阅读所选（1）" }));
    submit("存储与检索");
    await screen.findByRole("link", { name: "虚拟环境验证" });
    await act(async () => { finishPacket({ status: 500, body: { code: "STORAGE_ERROR", message: "过期组包错误" } }); });
    expect(screen.queryByText("过期组包错误")).not.toBeInTheDocument();
  });
});
