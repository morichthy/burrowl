import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ReadingDetail } from "./reading/ReadingDetail";
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
  vi.unstubAllGlobals();
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
      stats: { supports: 2, contradicts: 0, open_counterexamples: 0, cited_by: 1, run_inputs: 0 },
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
    handlers["sources.list"] = () => [{ source_id: "SRC-1", path: "a.txt", title: "a", enabled: true, sensitivity: "internal", sha256: "x" }];
    handlers["sources.update"] = () => ({});
    render(<Maintenance />);
    fireEvent.click(await screen.findByRole("button", { name: "预览扫描" }));
    expect(await screen.findByText(/预览（未记录）：1 处变化/)).toBeInTheDocument();
    expect(calls.find((c) => c.name === "monitor.run")!.args).toEqual({ dry_run: true });
    fireEvent.click(await screen.findByRole("button", { name: "停用" }));
    await waitFor(() => expect(calls.some((c) => c.name === "sources.update")).toBe(true));
    expect(calls.find((c) => c.name === "sources.update")!.args).toEqual({ source_id: "SRC-1", enabled: false });
  });
});
