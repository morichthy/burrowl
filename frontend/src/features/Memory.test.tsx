import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { OwnerView, RecordView } from "./Memory";

let calls: { name: string; args: Record<string, unknown> }[];

const ROWS = [
  { record_id: "R-OV", kind: "overview", level: "L4", title: "求和概览", revision: 1, updated_at: "2026-10-01T00:00:00Z", excerpt: "总体结论", claims: [] },
  { record_id: "R-D1", kind: "detail", level: "L1", title: "Kahan 补偿", revision: 3, updated_at: "2026-10-02T00:00:00Z", excerpt: "", claims: [] },
];

function mock(name: string, args: Record<string, unknown>): unknown {
  switch (name) {
    case "memory.records": {
      const kinds = args.kinds as string[] | null;
      return {
        owner: { owner_id: "RES-X", owner_type: "research", title: "求和研究", persisted: true, sensitivity: "internal" },
        head: { generation: 2 },
        items: ROWS.filter((r) => !kinds || kinds.includes(r.kind)),
        total: 2,
        kind_counts: { overview: 1, detail: 1 },
      };
    }
    case "memory.inspect":
      return {
        record: {
          ...ROWS[1],
          revision: (args.revision as number | undefined) ?? 3,
          body_markdown: args.revision ? "旧正文" : "新正文",
          keywords: [],
          sensitivity: "internal",
          record_reason: "保存",
          provenance_gap: null,
          sources: [],
          payload: {},
        },
      };
    case "review.queue":
      return { items: [], counts: {} };
    default:
      return {};
  }
}

beforeEach(() => {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      const name = url.replace("api/v1/ops/", "");
      const args = JSON.parse(String(init.body ?? "{}"));
      calls.push({ name, args });
      return new Response(JSON.stringify(mock(name, args)), { status: 200 });
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("系统记忆", () => {
  it("按类型筛选记录，计数来自 kind_counts，并把类型传给后端", async () => {
    render(<OwnerView ownerId="RES-X" />);
    expect(await screen.findByText("Kahan 补偿")).toBeInTheDocument();
    expect(screen.getByText("总体结论")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: /技术单元/ }));
    await waitFor(() => expect(screen.queryByText("求和概览")).not.toBeInTheDocument());
    const last = calls.filter((c) => c.name === "memory.records").at(-1);
    expect(last?.args.kinds).toEqual(["detail"]);
    expect(screen.getByRole("button", { name: "导出 1 条" })).toBeEnabled();
    fireEvent.click(screen.getByRole("checkbox", { name: "全部" }));
    expect(await screen.findByText("求和概览")).toBeInTheDocument();
  });

  it("可以回看历史修订", async () => {
    render(<RecordView ownerId="RES-X" recordId="R-D1" />);
    expect(await screen.findByText("新正文")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("查看修订"), { target: { value: "1" } });
    expect(await screen.findByText("旧正文")).toBeInTheDocument();
    expect(screen.getByText(/正在查看历史修订 1/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "回到当前版本" }));
    expect(await screen.findByText("新正文")).toBeInTheDocument();
  });
});
