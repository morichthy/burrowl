import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { type Note, Notes } from "./Notes";

let notes: Note[];
let calls: { name: string; args: Record<string, unknown> }[];

const base = (id: string, status: string, extra: Partial<Note> = {}): Note => ({
  id, kind: "bridge-question", title: `笔记 ${id}`, text: "", refs: ["RES-A"], status, created_at: "2026-10-10T00:00:00Z", thread: [], ...extra,
});

beforeEach(() => {
  calls = [];
  notes = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
    const name = url.replace("api/v1/ops/", "");
    const args = JSON.parse(String(init.body));
    calls.push({ name, args });
    const find = () => notes.find((n) => n.id === args.note_id)!;
    let body: unknown = {};
    if (name === "relations.notes") body = notes;
    if (name === "relations.note_resolve") body = Object.assign(find(), { status: args.status });
    if (name === "relations.note_edit") {
      const note = find();
      body = Object.assign(note, { title: args.title, text: args.text, kind: args.kind },
        note.status === "proposed" ? { status: "queued", proposal: undefined } : {});
    }
    if (name === "relations.note_answer") body = Object.assign(find(), { status: "queued" });
    if (name === "relations.note_reject") body = Object.assign(find(), { status: "queued", proposal: undefined });
    if (name === "relations.note_apply") {
      body = Object.assign(find(), { status: "handled", result: { message: "建关联", writes: [{ op: "relations.decide", owner_id: "RES-A", commit_id: "C-1", record_ids: ["MEM-9"] }] } });
    }
    if (name === "relations.note_clear") {
      notes = notes.filter((n) => !["handled", "done", "dismissed"].includes(n.status));
      body = { removed: [] };
    }
    return new Response(JSON.stringify(body), { status: 200 });
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

const lastCall = (name: string) => calls.filter((c) => c.name === name).at(-1)?.args;

describe("工作台笔记（R15）", () => {
  it("草稿可以交给 AI，AI 处理中可以收回", async () => {
    notes = [base("WN-1", "open")];
    render(<Notes />);
    fireEvent.click(await screen.findByRole("button", { name: "交给 AI" }));
    expect(await screen.findByText("AI 处理中（1）")).toBeInTheDocument();
    expect(lastCall("relations.note_resolve")).toEqual({ note_id: "WN-1", status: "queued" });
    fireEvent.click(screen.getByRole("button", { name: "收回" }));
    expect(await screen.findByText("草稿（1）")).toBeInTheDocument();
  });

  it("方案显示服务端预览，执行后移到已处理并链到写入的记录", async () => {
    notes = [base("WN-2", "proposed", { proposal: { id: "P-1", message: "建一条关联", created_at: "", preview: [
      { action: "association", owner_id: "RES-A", from: { id: "MEM-1", title: "顺序实验" }, to: { id: "MEM-2", title: "补偿求和" }, relation: "related_to", status: "accepted", explanation: "都在讨论误差" },
      { action: "records", owner_id: "RES-A", records: [{ record_id: "MEM-3", expected_revision: 2, kind: "experience", title: "补偿经验", excerpt: "", claims: ["补偿项可能被吞没"] }] },
    ] } })];
    render(<Notes />);
    const preview = within(await screen.findByRole("list", { name: "写入预览" }));
    expect(preview.getByText(/顺序实验 ↔ 补偿求和/)).toBeInTheDocument();
    expect(preview.getByText(/修订 MEM-3/)).toBeInTheDocument();          // 修订已有记录要标明
    expect(preview.getByText("结论（未复核）：补偿项可能被吞没")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "执行" }));
    await waitFor(() => expect(lastCall("relations.note_apply")).toEqual({ note_id: "WN-2", proposal_id: "P-1" }));
    expect(await screen.findByText("已处理（1）")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /MEM-9/ })).toHaveAttribute("href", "#/memory/RES-A/MEM-9");
  });

  it("退回方案带意见；回答 AI 的提问", async () => {
    notes = [
      base("WN-3", "proposed", { proposal: { id: "P-2", message: "补目标", created_at: "", preview: [] } }),
      base("WN-4", "needs_user", { thread: [{ at: "", by: "ai", kind: "question", text: "写进哪个对象？" }] }),
    ];
    vi.spyOn(window, "prompt").mockReturnValue("目标太宽");
    render(<Notes />);
    expect(await screen.findByText("写进哪个对象？")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "退回" }));
    await waitFor(() => expect(lastCall("relations.note_reject")).toEqual({ note_id: "WN-3", proposal_id: "P-2", comment: "目标太宽" }));
    fireEvent.change(screen.getByLabelText("回答 WN-4"), { target: { value: "求和研究" } });
    fireEvent.click(screen.getByRole("button", { name: "回答" }));
    await waitFor(() => expect(lastCall("relations.note_answer")).toEqual({ note_id: "WN-4", text: "求和研究" }));
  });

  it("清除已处理需要确认", async () => {
    notes = [base("WN-5", "done"), base("WN-6", "open")];
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    render(<Notes />);
    fireEvent.click(await screen.findByRole("button", { name: "清除已处理" }));
    expect(lastCall("relations.note_clear")).toBeUndefined();
    fireEvent.click(screen.getByRole("button", { name: "清除已处理" }));
    await waitFor(() => expect(lastCall("relations.note_clear")).toEqual({}));
    await waitFor(() => expect(screen.queryByText("已处理（1）")).not.toBeInTheDocument());
    expect(confirm).toHaveBeenCalledTimes(2);
  });
  it("已添加的笔记可以编辑；改动待确认的方案会先确认并作废方案", async () => {
    notes = [base("WN-7", "open"), base("WN-8", "proposed", { proposal: { id: "P-3", message: "补目标", created_at: "", preview: [] } })];
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<Notes />);
    fireEvent.click(await screen.findByRole("button", { name: "编辑 WN-7" }));
    fireEvent.change(screen.getByLabelText("笔记标题"), { target: { value: "改过的标题" } });
    fireEvent.change(screen.getByLabelText("笔记内容"), { target: { value: "补充说明" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(lastCall("relations.note_edit")).toMatchObject({ note_id: "WN-7", title: "改过的标题", text: "补充说明" }));
    expect(await screen.findByText("改过的标题")).toBeInTheDocument();
    expect(confirm).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "编辑 WN-8" }));
    expect(screen.getByText(/保存后当前方案作废/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(lastCall("relations.note_edit")).toMatchObject({ note_id: "WN-8" }));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("AI 处理中（1）")).toBeInTheDocument();
  });
});
