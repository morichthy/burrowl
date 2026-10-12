import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DiscardedRecords, DiscardOwner, DiscardRecords, OwnerTrashPanel } from "./Deletion";

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

const brief = { record_id: "MEM-1", owner_id: "RES-A", kind: "narrative", level: "L2", title: "测试经过" };

describe("删除记录与对象（R22）", () => {
  it("先预检，填写理由后带着预检时的 HEAD 删除", async () => {
    handlers["memory.records_discard"] = (args) =>
      args.dry_run ? { head: "COM-1", records: [brief], blockers: [], allowed: true } : { save_status: "committed" };
    const done = vi.fn();
    render(<DiscardRecords ownerId="RES-A" recordIds={["MEM-1"]} label="删除记录" onDone={done} />);
    fireEvent.click(screen.getByRole("button", { name: "删除记录" }));
    const confirm = await screen.findByRole("button", { name: "删除 1 条记录" });
    expect(confirm).toBeDisabled(); // 没写理由不能删
    fireEvent.change(screen.getByLabelText("删除理由"), { target: { value: "测试数据" } });
    fireEvent.click(confirm);
    await waitFor(() => expect(done).toHaveBeenCalled());
    expect(calls.filter((c) => c.name === "memory.records_discard").at(-1)!.args).toEqual({
      owner_id: "RES-A",
      record_ids: ["MEM-1"],
      reason: "测试数据",
      expected_head: "COM-1",
    });
  });

  it("有阻碍时列出引用者，不给删除按钮", async () => {
    const citer = { ...brief, record_id: "MEM-2", title: "引用它的经过" };
    handlers["memory.records_discard"] = () => ({
      head: "COM-1",
      records: [brief],
      blockers: [{ target_id: "MEM-1", reason: "仍被其他记录的当前修订引用", cited_by: [citer] }],
      allowed: false,
    });
    render(<DiscardRecords ownerId="RES-A" recordIds={["MEM-1"]} label="删除记录" onDone={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: "删除记录" }));
    expect(await screen.findByText("引用它的经过")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "删除 1 条记录" })).toBeNull();
  });

  it("已删除的记录可以恢复", async () => {
    let restored = false;
    handlers["memory.records_discarded"] = () => (restored ? [] : [{ ...brief, reason: "测试数据", removed_at: "2026-10-13T00:00:00Z" }]);
    handlers["memory.records_restore"] = () => {
      restored = true;
      return { save_status: "committed" };
    };
    const onRestored = vi.fn();
    render(<DiscardedRecords ownerId="RES-A" onRestored={onRestored} />);
    fireEvent.click(await screen.findByRole("button", { name: "恢复" }));
    await waitFor(() => expect(onRestored).toHaveBeenCalled());
    expect(calls.find((c) => c.name === "memory.records_restore")!.args).toEqual({ owner_id: "RES-A", record_ids: ["MEM-1"] });
  });

  it("删除对象：预检说明影响，被采用文档的原件保留", async () => {
    handlers["memory.owner_discard"] = () => ({
      owner: { owner_id: "OBJ-1", title: "笔记", owner_type: "knowledge" },
      mode: "memory_home",
      path: "knowledge/x.md.memory",
      head: "COM-9",
      record_count: 3,
      runs: [],
      sources: [],
      kept_original: "knowledge/x.md",
      blockers: [],
      allowed: true,
    });
    render(<DiscardOwner ownerId="OBJ-1" />);
    fireEvent.click(screen.getByRole("button", { name: "删除对象" }));
    expect(await screen.findByText(/原件 knowledge\/x\.md 留在原处/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("删除理由"), { target: { value: "采用错了" } });
    fireEvent.click(screen.getByRole("button", { name: "移入回收站" }));
    await waitFor(() =>
      expect(calls.filter((c) => c.name === "memory.owner_discard").at(-1)!.args).toEqual({
        owner_id: "OBJ-1",
        reason: "采用错了",
        expected_head: "COM-9",
      }),
    );
  });

  it("对象回收站：恢复与清空（清空要确认）", async () => {
    handlers["memory.owner_trash"] = () => [
      { owner_id: "RES-B", owner_type: "research", title: "乙", original_path: "research/b", mode: "folder", record_count: 1, reason: "误建", discarded_at: "2026-10-13T00:00:00Z" },
    ];
    handlers["memory.owner_restore"] = () => ({ status: "restored" });
    handlers["memory.owner_empty_trash"] = () => ({ removed: ["RES-B"] });
    const confirm = vi.fn(() => false);
    vi.stubGlobal("confirm", confirm);
    render(<OwnerTrashPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "恢复" }));
    await waitFor(() => expect(calls.find((c) => c.name === "memory.owner_restore")?.args).toEqual({ owner_id: "RES-B" }));
    fireEvent.click(screen.getByRole("button", { name: "清空回收站（永久删除）" }));
    expect(confirm).toHaveBeenCalled();
    expect(calls.some((c) => c.name === "memory.owner_empty_trash")).toBe(false); // 取消确认就不执行
  });
});
