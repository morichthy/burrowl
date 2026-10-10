import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ReadingDetail } from "./reading/ReadingDetail";
import { ReadingList } from "./reading/ReadingList";
import { Home } from "./Home";
import { Review } from "./Review";
import { Search } from "./Search";

type Handler = (args: Record<string, unknown>) => unknown;
let handlers: Record<string, Handler>;
let calls: { name: string; args: Record<string, unknown> }[];

beforeEach(() => {
  calls = [];
  handlers = {};
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      const name = url.replace("api/v1/ops/", "");
      const args = JSON.parse(String(init.body ?? "{}"));
      calls.push({ name, args });
      const handler = handlers[name];
      if (!handler) return new Response(JSON.stringify({ code: "NOT_FOUND", message: `no mock for ${name}` }), { status: 404 });
      return new Response(JSON.stringify(handler(args)), { status: 200 });
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const listedSession = {
  session_id: "RS-NEW",
  question: "存储与检索演进讨论",
  status: "active",
  phase: "screening",
  strategy: "standard",
  owner_id: null,
  updated_at: "2026-10-09T00:00:00Z",
  revision: 1,
  pending_requests: 0,
  notes: 0,
  relevant: 0,
};

describe("阅读会话列表刷新", () => {
  it("首页在会话创建后自动显示当前阅读", async () => {
    let created = false;
    handlers["reading.list"] = () => (created ? [listedSession] : []);
    handlers["review.queue"] = () => ({ counts: {} });
    handlers["system.capabilities"] = () => ({ data_dir: "data", vector: { backend: "sqlite", embedder: null, ready: false }, reranker: { ready: false }, connectors: [] });
    handlers["memory.activity"] = () => ({ items: [], next_before: null });
    vi.useFakeTimers();
    render(<Home />);
    await act(async () => Promise.resolve());
    created = true;
    await act(async () => vi.advanceTimersByTimeAsync(5_000));
    expect(screen.getByText("存储与检索演进讨论")).toBeInTheDocument();
  });

  it("首页列出最近关键操作并链接到来源", async () => {
    handlers["reading.list"] = () => [];
    handlers["review.queue"] = () => ({ counts: {} });
    handlers["system.capabilities"] = () => ({ data_dir: "data", vector: { backend: "sqlite", embedder: null, ready: false }, reranker: { ready: false }, connectors: [] });
    handlers["memory.activity"] = () => ({
      items: [
        { at: "2026-10-10T02:00:00Z", kind: "attribution", actor: "AI", owner_id: "RES-X", summary: "归属：新建 RES-X（放弃 RES-SUM）：目标不同", cursor: "c1", ref: { kind: "commit", owner_id: "RES-X", commit_id: "COM-1" } },
        { at: "2026-10-10T01:00:00Z", kind: "publish", actor: "用户", owner_id: null, summary: "发布到 mdvault：新增 2", cursor: "c2", ref: { kind: "hub_run", connector_id: "mdvault", run_id: 1 } },
      ],
      next_before: null,
    });
    render(<Home />);
    const link = await screen.findByText("归属：新建 RES-X（放弃 RES-SUM）：目标不同");
    expect(link.closest("a")?.getAttribute("href")).toBe("#/memory/RES-X");
    expect(screen.getByText("发布到 mdvault：新增 2").closest("a")?.getAttribute("href")).toBe("#/hub");
    expect(calls.some((c) => c.name === "memory.activity" && c.args.limit === 20)).toBe(true);
  });

  it("Run 结束：对象内 Run 链到上级对象，独立 Run 链到维护页（R14）", async () => {
    handlers["reading.list"] = () => [];
    handlers["review.queue"] = () => ({ counts: {} });
    handlers["system.capabilities"] = () => ({ data_dir: "data", vector: { backend: "sqlite", embedder: null, ready: false }, reranker: { ready: false }, connectors: [] });
    handlers["memory.activity"] = () => ({
      items: [
        { at: "2026-10-10T02:00:00Z", kind: "run", actor: "—", owner_id: "RES-X", summary: "RUN-1 完成「实验」", cursor: "c1", ref: { kind: "run", run_id: "RUN-1" } },
        { at: "2026-10-10T01:00:00Z", kind: "run", actor: "—", owner_id: "RUN-2", summary: "RUN-2 失败「临时测试」", cursor: "c2", ref: { kind: "run", run_id: "RUN-2" } },
      ],
      next_before: null,
    });
    render(<Home />);
    expect((await screen.findByText("RUN-1 完成「实验」")).closest("a")?.getAttribute("href")).toBe("#/memory/RES-X");
    expect(screen.getByText("RUN-2 失败「临时测试」").closest("a")?.getAttribute("href")).toBe("#/maintenance");
  });

  it("阅读会话页在会话创建后自动更新列表", async () => {
    let created = false;
    handlers["reading.list"] = () => (created ? [listedSession] : []);
    vi.useFakeTimers();
    render(<ReadingList />);
    await act(async () => Promise.resolve());
    created = true;
    await act(async () => vi.advanceTimersByTimeAsync(5_000));
    expect(screen.getByText("存储与检索演进讨论")).toBeInTheDocument();
  });
});

const claim = {
  claim_id: "CLM-1",
  status: "not-reviewed",
  statement: "排序后求和误差更小",
  scope: "整数输入",
  owner_id: "RES-A",
  record_id: "MEM-1",
  record_title: "顺序实验经过",
  record_kind: "narrative",
  evidence_refs: [{ target_kind: "file", target_id: "SRC-1", revision: null, sha256: "a".repeat(64), locator: "", relation: "input" }],
  review: null,
  problems: [],
};

describe("知识复核", () => {
  it("一键确认先预检再提交，提交后刷新队列", async () => {
    let reviewed = false;
    handlers["review.queue"] = () => ({
      items: reviewed ? [] : [claim],
      total: reviewed ? 0 : 1,
      counts: { "not-reviewed": reviewed ? 0 : 1, accepted: reviewed ? 1 : 0 },
    });
    handlers["review.submit"] = (args) => {
      if (!args.dry_run) reviewed = true;
      return { dry_run: args.dry_run, writes: args.dry_run ? 0 : 1 };
    };
    render(<Review />);
    expect(await screen.findByText("排序后求和误差更小")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "确认" })[0]);
    expect(await screen.findByText("预检通过，可以提交。")).toBeInTheDocument();
    const dry = calls.find((c) => c.name === "review.submit")!;
    expect(dry.args).toMatchObject({ target_claim_id: "CLM-1", state: "accepted", scope: "整数输入", dry_run: true });
    expect((dry.args.evidence_refs as unknown[]).length).toBe(1);
    fireEvent.click(screen.getByRole("button", { name: "提交确认" }));
    await waitFor(() => expect(screen.queryByText("排序后求和误差更小")).not.toBeInTheDocument());
    expect(calls.filter((c) => c.name === "review.submit").map((c) => c.args.dry_run)).toEqual([true, false]);
  });

  it("AI 提出的“已确立”升级由人在工作台确认，必须选择证据类型", async () => {
    const proposed = {
      ...claim,
      status: "maturity-requested",
      maturity: "preliminary",
      requested_maturity: "established",
      stats: { supports: 2, contradicts: 0, open_counterexamples: 0, cited_by: 1 },
      review: { state: "not-reviewed", reason: "两次复算一致", scope: null, updated_at: "2026-10-09T00:00:00Z", revision: 1 },
    };
    handlers["review.queue"] = () => ({ items: [proposed], total: 1, counts: { "maturity-requested": 1 } });
    handlers["review.submit"] = () => ({ writes: 1 });
    render(<Review />);
    expect(await screen.findByText(/提议升级为已确立/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认升级为已确立" }));
    const submitButton = await screen.findByRole("button", { name: "提交确认" });
    expect(submitButton).toBeDisabled();   // 还没选证据类型
    fireEvent.change(screen.getByLabelText(/证据类型/), { target: { value: "replication" } });
    fireEvent.click(screen.getByRole("button", { name: "提交确认" }));
    await waitFor(() => expect(calls.some((c) => c.name === "review.submit")).toBe(true));
    const sent = calls.find((c) => c.name === "review.submit")!;
    expect(sent.args).toMatchObject({ state: "accepted", maturity: "established", evidence_basis: "replication", dry_run: false });
  });

  it("批量质疑把所选结论一次提交", async () => {
    handlers["review.queue"] = () => ({ items: [claim, { ...claim, claim_id: "CLM-2", statement: "另一条" }], total: 2, counts: {} });
    handlers["review.batch"] = (args) => ({ succeeded: (args.items as unknown[]).length, failed: 0 });
    vi.spyOn(window, "prompt").mockReturnValue("样本不足");
    render(<Review />);
    await screen.findByText("另一条");
    fireEvent.click(screen.getByLabelText("选择 CLM-1"));
    fireEvent.click(screen.getByLabelText("选择 CLM-2"));
    fireEvent.click(screen.getByRole("button", { name: "批量质疑" }));
    expect(await screen.findByText("成功 2 条，失败 0 条")).toBeInTheDocument();
    const batch = calls.find((c) => c.name === "review.batch")!;
    expect(batch.args.items).toEqual([
      { target_claim_id: "CLM-1", state: "disputed", reason: "样本不足" },
      { target_claim_id: "CLM-2", state: "disputed", reason: "样本不足" },
    ]);
  });

  it("改筛选后被隐藏的已选结论不计数、不提交；搜索框停顿后才查询", async () => {
    const other = { ...claim, claim_id: "CLM-2", statement: "另一条" };
    handlers["review.queue"] = (args) => {
      const items = [claim, other].filter((i) => !args.query || i.statement.includes(String(args.query)));
      return { items, total: items.length, counts: {} };
    };
    handlers["review.batch"] = (args) => ({ succeeded: (args.items as unknown[]).length, failed: 0 });
    vi.spyOn(window, "prompt").mockReturnValue("样本不足");
    render(<Review />);
    await screen.findByText("另一条");
    fireEvent.click(screen.getByLabelText("选择 CLM-1"));
    fireEvent.click(screen.getByLabelText("选择 CLM-2"));
    expect(screen.getByText(/已选 2 条/)).toBeInTheDocument();
    const before = calls.filter((c) => c.name === "review.queue").length;
    const box = screen.getByPlaceholderText("搜索结论或记录标题");
    for (const text of ["另", "另一", "另一条"]) fireEvent.change(box, { target: { value: text } });
    await waitFor(() => expect(screen.queryByText(claim.statement)).not.toBeInTheDocument());
    // 连续三次输入只在停顿后查询一次
    expect(calls.filter((c) => c.name === "review.queue").length).toBe(before + 1);
    expect(screen.getByText(/已选 1 条/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "批量质疑" }));
    expect(await screen.findByText("成功 1 条，失败 0 条")).toBeInTheDocument();
    expect(calls.find((c) => c.name === "review.batch")!.args.items).toEqual([{ target_claim_id: "CLM-2", state: "disputed", reason: "样本不足" }]);
  });
});

const session = {
  session_id: "RS-1",
  question: "Kahan 误差界",
  conditions: [],
  status: "waiting_user",
  phase: "screening",
  strategy: "standard",
  review_filter: "all",
  revision: 3,
  queue: ["RES-A"],
  owners: {
    "RES-A": {
      title: "浮点求和",
      rank: 1,
      round: "R1",
      verdict: "uncertain",
      reason: "窗口不足以判断",
      judged_by: "agent",
      pages_read: [],
      total_pages: null,
      note: null,
      packet: { windows: [{ record_id: "MEM-1", title: "概览", level: "L4", kind: "overview", review: "accepted", text: "窗口正文" }], extra_windows: [], score: 1 },
    },
  },
  requests: [
    { request_id: "UR-1", kind: "fulltext", prompt: "需要全文补偿吗？", options: [], status: "pending", answer: null, approval_id: null, created_at: "2026-10-08T00:00:00Z" },
  ],
  timeline: [{ at: "2026-10-08T00:00:00Z", action: "recall", summary: "召回 1 个对象" }],
  rounds: [],
  synthesis: null,
  association: { enabled: true, text: "", rounds_used: 0, max_rounds: 3 },
  plan: { variants: [{ language: "zh", question: "Kahan 误差界" }] },
};

describe("阅读会话实时页", () => {
  it("批准全文补偿与改判都写入会话", async () => {
    handlers["reading.view"] = () => session;
    handlers["reading.respond"] = () => ({ session_id: "RS-1", status: "active", revision: 4, approval_id: "AP-1" });
    handlers["reading.override"] = () => ({ session_id: "RS-1", status: "active", revision: 5 });
    vi.spyOn(window, "prompt").mockReturnValue("我读过，确实相关");
    render(<ReadingDetail sessionId="RS-1" />);
    expect(await screen.findByText("需要全文补偿吗？")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "批准全文补偿" }));
    await waitFor(() => expect(calls.some((c) => c.name === "reading.respond")).toBe(true));
    expect(calls.find((c) => c.name === "reading.respond")!.args).toMatchObject({ request_id: "UR-1", decision: "approve" });
    fireEvent.click(screen.getByRole("button", { name: "改为相关" }));
    await waitFor(() => expect(calls.some((c) => c.name === "reading.override")).toBe(true));
    expect(calls.find((c) => c.name === "reading.override")!.args).toMatchObject({
      owner_id: "RES-A",
      verdict: "relevant",
      reason: "我读过，确实相关",
    });
  });
});

describe("检索", () => {
  it("复核过滤单选按钮传给后端并显示徽标", async () => {
    handlers["query.search"] = () => ({
      query_id: "Q-1",
      items: [{ candidate_id: "C-1", kind: "experience", owner_id: "RES-A", record_id: "MEM-1", title: "补偿求和经验", excerpt: "正文", level: "L3", review: "accepted", score: 1, stale_revision: false }],
      gaps: [],
      consumed: { wall_ms: 12 },
    });
    render(<Search />);
    fireEvent.click(screen.getByLabelText("只看已复核"));
    fireEvent.change(screen.getByLabelText("问题"), { target: { value: "Kahan" } });
    fireEvent.click(screen.getByRole("button", { name: "检索" }));
    expect(await screen.findByText("补偿求和经验")).toBeInTheDocument();
    expect(screen.getByText("已确认")).toBeInTheDocument();
    expect(calls.find((c) => c.name === "query.search")!.args).toMatchObject({ question: "Kahan", review_filter: "reviewed_only" });
  });
});

describe("维护与监测", () => {
  it("预览扫描不写入；停用来源走用户操作", async () => {
    const { Maintenance } = await import("./Maintenance");
    handlers["monitor.observations"] = () => [];
    handlers["monitor.candidates"] = () => [{ target_id: "MEM-1", status: "open", first_seen: "2026-10-08T00:00:00Z", last_seen: "2026-10-08T00:00:00Z" }];
    handlers["monitor.run"] = (args) => ({ at: "x", changes: [{ kind: "file", target_id: "SRC-1" }], errors: [], maintenance_candidates: ["MEM-1"], baseline_updated: !args.dry_run, dry_run: args.dry_run });
    handlers["sources.list"] = () => [
      { source_id: "SRC-1", path: "a.txt", title: "a", enabled: true, sensitivity: "internal", sha256: "x" },
      { source_id: "SRC-2", path: "b.txt", title: "b", enabled: false, sensitivity: "internal", sha256: "y" },
      { source_id: "SRC-3", path: "c.txt", title: "c", enabled: true, sensitivity: "restricted", sha256: "z" },
    ];
    handlers["sources.update"] = () => ({});
    render(<Maintenance />);
    fireEvent.click(await screen.findByRole("button", { name: "预览扫描" }));
    expect(await screen.findByText(/预览（未记录）：1 处变化/)).toBeInTheDocument();
    expect(calls.find((c) => c.name === "monitor.run")!.args).toEqual({ dry_run: true });
    // 状态与操作分开：标签写当前状态，按钮写要做的事
    const row = (id: string) => within(screen.getByText(id).closest("tr")!);
    expect(await screen.findByText("SRC-2")).toBeInTheDocument();
    expect(row("SRC-1").getByText("可读取")).toBeInTheDocument();
    expect(row("SRC-2").getByText("已停用")).toBeInTheDocument();
    expect(row("SRC-2").getByRole("button", { name: "重新启用 SRC-2" })).toBeInTheDocument();
    expect(row("SRC-3").getByText("受限")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "停用 SRC-1" }));
    await waitFor(() => expect(calls.some((c) => c.name === "sources.update")).toBe(true));
    expect(calls.find((c) => c.name === "sources.update")!.args).toEqual({ source_id: "SRC-1", enabled: false });
  });
});

describe("维护计划交给 AI（R16）", () => {
  it("勾选候选生成计划；待你确认时显示修订稿，执行与退回走用户操作", async () => {
    const { Maintenance } = await import("./Maintenance");
    let status = "planned";
    const item = {
      target: { target_id: "MEM-1", revision: 1 }, owner_id: "RES-A", kind: "narrative", title: "第一轮经过", content: "旧正文",
      formal_dependency: true,
      get decision() {
        return status === "planned" ? null : { action: "revise", reason: "来源数据更正", draft: { title: "第一轮经过", body_markdown: "更正后的正文" } };
      },
    };
    handlers["monitor.observations"] = () => [];
    handlers["monitor.candidates"] = () => [{ target_id: "MEM-1", status: "open", first_seen: "2026-10-11T00:00:00Z", last_seen: "2026-10-11T00:00:00Z", reasons: [["SRC-1", "MEM-1"]] }];
    handlers["sources.list"] = () => [];
    handlers["sources.roots"] = () => ({ roots: [], work_dir: null });
    handlers["runs.search"] = () => [];
    handlers["runs.trash"] = () => [];
    handlers["maintenance.plans"] = () => [{ plan_id: "MP-1", status, changed_ids: ["SRC-1"], items: 1, updated_at: "2026-10-11T00:00:00Z" }];
    handlers["maintenance.plan_from_candidates"] = () => ({ plan_id: "MP-1" });
    handlers["maintenance.status"] = () => ({
      plan_id: "MP-1", status, revision: 3, changed_ids: ["SRC-1"], items: [item], applied: {}, thread: [],
      preview: status === "reviewed" ? [{ owner_id: "RES-A", ok: true }] : undefined,
    });
    handlers["maintenance.handoff"] = () => {
      status = "queued";
      return {};
    };
    handlers["maintenance.apply"] = () => {
      status = "applied";
      return { status: "applied", applied: {} };
    };
    vi.stubGlobal("prompt", vi.fn(() => "理由再具体些"));
    render(<Maintenance />);
    fireEvent.click(await screen.findByRole("checkbox", { name: "选择 MEM-1" }));
    expect(screen.getByText(/原因：SRC-1 → MEM-1/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "为所选生成维护计划" }));
    await waitFor(() => expect(calls.find((c) => c.name === "maintenance.plan_from_candidates")?.args).toEqual({ target_ids: ["MEM-1"] }));
    fireEvent.click(await screen.findByRole("button", { name: "交给 AI" }));
    await waitFor(() => expect(calls.find((c) => c.name === "maintenance.handoff")?.args).toEqual({ plan_id: "MP-1", note: "" }));

    status = "reviewed"; // AI 写好处置
    fireEvent.click(screen.getByRole("button", { name: "SRC-1" }));
    expect(await screen.findByText("更正后的正文")).toBeInTheDocument();
    expect(screen.getByText("理由：来源数据更正")).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: /JSON/ })).not.toBeInTheDocument(); // 不再手写 JSON 草稿
    fireEvent.click(screen.getByRole("button", { name: "退回" }));
    await waitFor(() => expect(calls.filter((c) => c.name === "maintenance.handoff").at(-1)?.args).toEqual({ plan_id: "MP-1", note: "理由再具体些" }));
    status = "reviewed";
    fireEvent.click(screen.getByRole("button", { name: "SRC-1" }));
    fireEvent.click(await screen.findByRole("button", { name: "执行" }));
    await waitFor(() => expect(calls.find((c) => c.name === "maintenance.apply")?.args).toEqual({ plan_id: "MP-1", expected_revision: 3 }));
  });
});

describe("维护页的 Run（R14）", () => {
  it("列出未写成记录的 Run，丢弃需写理由，回收站可恢复与清空", async () => {
    const { Maintenance } = await import("./Maintenance");
    let trashed = false;
    handlers["monitor.observations"] = () => [];
    handlers["monitor.candidates"] = () => [];
    handlers["sources.list"] = () => [];
    handlers["sources.roots"] = () => ({ roots: [], work_dir: null });
    handlers["runs.search"] = () =>
      trashed ? [] : [{ run_id: "RUN-9", title: "临时测试", status: "succeeded", owner_id: null, ended_at: null, path: "runs/run-9/run.json" }];
    handlers["runs.trash"] = () =>
      trashed ? [{ run_id: "RUN-9", original_path: "runs/run-9", reason: "无可复用内容", discarded_at: "2026-10-10T00:00:00Z" }] : [];
    handlers["runs.discard"] = () => {
      trashed = true;
      return { status: "discarded" };
    };
    handlers["runs.restore"] = () => ({ status: "restored" });
    handlers["runs.empty_trash"] = () => ({ removed: ["run-9"] });
    vi.stubGlobal("prompt", vi.fn(() => "无可复用内容"));
    vi.stubGlobal("confirm", vi.fn(() => true));
    render(<Maintenance />);
    expect(await screen.findByText("临时测试")).toBeInTheDocument();
    expect(calls.find((c) => c.name === "runs.search")!.args).toMatchObject({ unfixed_only: true });
    fireEvent.click(screen.getByRole("button", { name: "丢弃到回收站" }));
    await waitFor(() => expect(calls.some((c) => c.name === "runs.discard")).toBe(true));
    expect(calls.find((c) => c.name === "runs.discard")!.args).toEqual({ run_id: "RUN-9", reason: "无可复用内容" });
    fireEvent.click(await screen.findByRole("button", { name: "恢复" }));
    await waitFor(() => expect(calls.find((c) => c.name === "runs.restore")?.args).toEqual({ run_id: "RUN-9" }));
    fireEvent.click(screen.getByRole("button", { name: "清空回收站（永久删除）" }));
    await waitFor(() => expect(calls.some((c) => c.name === "runs.empty_trash")).toBe(true));
  });
});
