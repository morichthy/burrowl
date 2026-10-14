import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Owners, OwnerView, RecordView } from "./Memory";

let calls: { name: string; args: Record<string, unknown> }[];
let showFigures: boolean;

// 文稿跨对象收录固定修订：图号是技术单元本地索引，不能用文稿对象读取。
const FIGURE_RECORD = {
  owner_id: "RES-SOURCE", record_id: "R-FIG", title: "论文原文", revision: 2,
  body_markdown: "", keywords: [], sources: [], sensitivity: "internal", record_reason: "导入",
  provenance_gap: null, updated_at: "2026-10-01T00:00:00Z",
  links: [{ text: "@原件", status: "resolved", ref: { target_id: "R-SOURCE" } }],
  payload: { figures: [{ caption: "论文 Figure 1" }], blocks: [{ block_id: "results", markdown: "出处：[\u005b@原件|原文\u005d]\n\n![图](figure:0)" }] },
};

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
      if (showFigures) return { record: FIGURE_RECORD };
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
    case "memory.owners":
      return {
        groups: [
          { owner_type: "research", owners: [{ owner_id: "RES-X", owner_type: "research", title: "求和研究", persisted: true, sensitivity: "internal", counts: { L4: 1 } }] },
          { owner_type: "run", owners: [{ owner_id: "RUN-1", owner_type: "run", title: "一次运行", persisted: false, sensitivity: "internal", counts: {} }] },
        ],
      };
    case "review.queue":
      return { items: [], counts: {} };
    case "ui.transcriptions":
      return [{ source_id: "SRC-X", title: "原始说明", has_snapshot: true, counts: { covered: 1 } }];
    case "memory.document":
      if (showFigures && args.view === "full") return {
        document: { title: "论文测试稿", purpose: "固定版本的图片", document_type: "research_process" },
        missing: [], candidates: [], report: { complete: true, sections: [{ section_id: "section", title: "结果", ref: { revision: 4 }, links: FIGURE_RECORD.links,
          source_issues: [], blocks: [{ type: "prose", markdown: "衔接依据：[\u005b@原件|章节出处\u005d]", source_issues: [] }, { type: "unit", source_issues: [], record: FIGURE_RECORD,
            resolved_blocks: FIGURE_RECORD.payload.blocks }] }] },
      };
      return args.view === "coverage" ? {
        segments: [{ key: "方法", status: "covered", ratio: 1, overlap: 1 }],
        assets: [
          { label: "原图一", segment: "方法", status: "missing" },
          { label: "原图二", segment: "方法", status: "unreferenced" },
          { label: "原图三", segment: "方法", status: "linked" },
        ],
        warnings: ["原文包含未抽取的矢量图"],
      } : { file_name: "说明.pdf", sections: [] };
    case "memory.figure":
      return { data_url: "data:image/png;base64,AA==" };
    case "ui.ref_targets":
      return { "R-SOURCE": { kind: "record", owner_id: "RES-SOURCE", record_id: "R-SOURCE", title: "原件" } };
    default:
      return {};
  }
}

beforeEach(() => {
  calls = [];
  showFigures = false;
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
  it("文稿内显示所收录技术单元固定修订的图片，跨对象也用原对象", async () => {
    showFigures = true;
    render(<OwnerView ownerId="RES-X" />);
    fireEvent.click(await screen.findByRole("tab", { name: "文稿" }));
    expect(await screen.findByRole("img", { name: "论文 Figure 1" })).toBeInTheDocument();
    // 章节衔接文字与技术单元引用同一原件，整篇文稿只分配一个编号。
    expect(await screen.findAllByRole("link", { name: "引用 1：原件" })).toHaveLength(2);
    screen.getAllByRole("link", { name: "引用 1：原件" }).forEach((link) =>
      expect(link).toHaveAttribute("href", "#/memory/RES-SOURCE/R-SOURCE"));
    expect(screen.queryByRole("link", { name: "原文" })).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "引用对照" }).querySelectorAll("li")).toHaveLength(1);
    expect(calls.some((c) => c.name === "ui.ref_targets" && c.args.record_id === "section" && c.args.revision === 4)).toBe(true);
    expect(calls.filter((c) => c.name === "memory.figure").map((c) => c.args)).toEqual([
      { owner_id: "RES-SOURCE", record_id: "R-FIG", revision: 2, index: 0 },
    ]);
  });
  it("记录页在原位置只渲染一次图片，无 figure 嵌套 p", async () => {
    showFigures = true;
    const { container } = render(<RecordView ownerId="RES-SOURCE" recordId="R-FIG" />);
    expect(await screen.findByRole("img", { name: "论文 Figure 1" })).toBeInTheDocument();
    expect(screen.getAllByRole("img", { name: "论文 Figure 1" })).toHaveLength(1);
    expect(container.querySelector("p figure")).toBeNull();
    expect(calls.filter((c) => c.name === "memory.figure")).toHaveLength(1);
  });
  it("文字已认领仍展示图片缺口和抽取警告，不将覆盖结果当作完整转录", async () => {
    render(<OwnerView ownerId="RES-X" />);
    fireEvent.click(await screen.findByRole("tab", { name: "原文展开" }));
    expect(await screen.findByText("缺图片附件")).toBeInTheDocument();
    expect(screen.getByText("缺正文引用")).toBeInTheDocument();
    expect(screen.getByText("附件已引用")).toBeInTheDocument();
    expect(screen.getByText("原文包含未抽取的矢量图")).toBeInTheDocument();
    expect(screen.getByText(/文字已认领不代表完整转录/)).toBeInTheDocument();
  });
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

describe("对象列表", () => {
  it("按对象类型筛选，并可隐藏尚无记忆的对象", async () => {
    render(<Owners />);
    expect(await screen.findByText("一次运行")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: /研究/ }));
    expect(screen.queryByText("一次运行")).not.toBeInTheDocument();
    expect(screen.getByText("求和研究")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: "全部" }));
    expect(screen.getByText("一次运行")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: /隐藏尚无记忆/ }));
    expect(screen.queryByText("一次运行")).not.toBeInTheDocument();
  });
});
