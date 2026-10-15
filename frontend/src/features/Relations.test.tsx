import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import { EdgeFocus, Focus, Relations } from "./Relations";

let stored: Record<string, unknown>;
let notes: { id: string; kind: string; title: string; text: string; refs: string[]; status: string; created_at: string }[];
let calls: { name: string; args: Record<string, unknown> }[];
const defaults = ["owner", "record", "claim", "concept"];
const defaultCategories = ["structure", "evidence", "reference", "navigation", "claim_part"];
const latestGraph = () => calls.filter((call) => call.name === "relations.graph").at(-1)!.args;

beforeEach(() => {
  stored = {};
  notes = [];
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
    const name = url.replace("api/v1/ops/", "");
    const args = JSON.parse(String(init.body));
    calls.push({ name, args });
    let body: unknown = [];
    if (name === "relations.view") body = stored;
    if (name === "relations.view_save") body = stored = args.view as Record<string, unknown>;
    if (name === "relations.notes") body = notes;
    if (name === "relations.note_resolve") {
      notes = notes.map((n) => (n.id === args.note_id ? { ...n, status: args.status as string } : n));
      body = notes.find((n) => n.id === args.note_id);
    }
    // 用各类型一个节点模拟后端可见性，断言用户能看到的节点计数。
    if (name === "relations.graph") body = {
      nodes: (args.node_types as string[]).map((type) => ({ id: type, type, kind: type, title: type, owner_id: null, level: null })),
      edges: [], truncated: false,
    };
    return new Response(JSON.stringify(body), { status: 200 });
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("关系图筛选（R12 T-P2.8）", () => {
  it("结论默认显示、来源默认关闭，L1 块在记录内部细节中独立控制（R23 D-12）", async () => {
    render(<Relations />);
    await screen.findByText(/4 个节点、0 条关系/);
    const kinds = within(screen.getByRole("group", { name: "记录分类" }));
    // 七个分类外加一个显式的"全部"（替代原先的"不选＝全部"提示），未选分类时"全部"处于选中态
    expect(kinds.getAllByRole("checkbox")).toHaveLength(8);
    expect(kinds.getByRole("checkbox", { name: "全部" })).toBeChecked();
    for (const name of ["概览（L4）", "经验（L3）", "经过（L2）", "技术单元（L1）", "原始依据（L0）", "文稿", "章节"]) {
      expect(kinds.getByRole("checkbox", { name })).toBeInTheDocument();
    }
    fireEvent.click(kinds.getByRole("checkbox", { name: "经验（L3）" }));
    await waitFor(() => expect(latestGraph().kinds).toEqual(["experience"]));
    expect(kinds.getByRole("checkbox", { name: "全部" })).not.toBeChecked();
    fireEvent.click(kinds.getByRole("checkbox", { name: "全部" }));
    await waitFor(() => expect(latestGraph().kinds ?? null).toBeNull());
    fireEvent.click(kinds.getByRole("checkbox", { name: "经验（L3）" }));
    await waitFor(() => expect(latestGraph().kinds).toEqual(["experience"]));
    expect(latestGraph()).not.toHaveProperty("levels");
    expect(latestGraph().node_types).toEqual(defaults);
    const types = within(screen.getByRole("group", { name: "节点类型" }));
    expect(types.getAllByRole("checkbox")).toHaveLength(5);
    expect(types.getByRole("checkbox", { name: "结论（独立判断）" })).toBeChecked();
    expect(types.getByRole("checkbox", { name: "来源" })).not.toBeChecked();
    const details = within(screen.getByRole("group", { name: "记录内部细节" }));
    expect(details.getByRole("checkbox", { name: "展开 L1 记录内的块与图" })).not.toBeChecked();
    fireEvent.click(details.getByRole("checkbox", { name: "展开 L1 记录内的块与图" }));
    await screen.findByText(/6 个节点、0 条关系/);  // R24 D-12：块与图一起展开
    for (const name of ["对象", "记录", "结论（独立判断）", "概念"]) fireEvent.click(types.getByRole("checkbox", { name }));
    fireEvent.click(details.getByRole("checkbox", { name: "展开 L1 记录内的块与图" }));
    await screen.findByText(/0 个节点、0 条关系/);
    expect(latestGraph().node_types).toEqual([]);
  });

  it("旧隐藏字段不生效，当前筛选保存后重载保持空选，重置恢复默认", async () => {
    stored = { filters: { levels: ["L4"], showBlocks: true, kinds: ["experience"], nodeTypes: ["claim"] } };
    const first = render(<Relations />);
    await screen.findByText(/1 个节点、0 条关系/);
    expect(latestGraph()).toMatchObject({ kinds: ["experience"], node_types: ["claim"] });
    expect(latestGraph()).not.toHaveProperty("levels");
    fireEvent.click(screen.getByRole("checkbox", { name: "结论（独立判断）" }));
    await screen.findByText(/0 个节点、0 条关系/);
    fireEvent.click(screen.getByRole("button", { name: "保存视图" }));
    await waitFor(() => expect(calls.some((call) => call.name === "relations.view_save")).toBe(true));
    expect(stored.filters).toMatchObject({ nodeTypes: [], kinds: ["experience"] });
    expect(stored.filters).not.toHaveProperty("levels");
    expect(stored.filters).not.toHaveProperty("showBlocks");
    first.unmount();
    render(<Relations />);
    await waitFor(() => expect(latestGraph().node_types).toEqual([]));
    await screen.findByText(/0 个节点、0 条关系/);
    fireEvent.click(screen.getByRole("button", { name: "重置" }));
    await screen.findByText(/4 个节点、0 条关系/);
    expect(latestGraph()).toMatchObject({ node_types: defaults, kinds: null, categories: defaultCategories, center: null });
  });

  it("同一对节点之间的多条边合并为一条连线计数", async () => {
    const edge = (source: string, target: string, type: string, category: string) => ({ source, target, type, category, origin: "x" });
    vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
      const name = url.replace("api/v1/ops/", "");
      calls.push({ name, args: JSON.parse(String(init.body)) });
      const body = name !== "relations.graph" ? [] : {
        nodes: ["a", "b", "c"].map((id) => ({ id, type: "record", kind: "detail", title: id, owner_id: null, level: null })),
        // a-b 之间三条（含反向），b-c 一条：4 条关系、2 条连线
        edges: [edge("a", "b", "references", "reference"), edge("a", "b", "references", "reference"), edge("b", "a", "input", "evidence"), edge("b", "c", "belongs_to", "structure")],
        truncated: false,
      };
      return new Response(JSON.stringify(body), { status: 200 });
    }));
    render(<Relations />);
    await screen.findByText(/3 个节点、4 条关系（合并为 2 条连线）/);
  });

  it("关系类别勾选即显示：提及默认关闭，全选不过滤，取消一类只请求其余类别，最后一类不能取消（R23 D-11）", async () => {
    render(<Relations />);
    await screen.findByText(/4 个节点、0 条关系/);
    const cats = within(screen.getByRole("group", { name: "关系类别" }));
    expect(cats.getAllByRole("checkbox", { checked: true })).toHaveLength(5);
    expect(cats.getByRole("checkbox", { name: "提及" })).not.toBeChecked();
    expect(latestGraph().categories).toEqual(defaultCategories);
    fireEvent.click(cats.getByRole("checkbox", { name: "提及" }));
    await waitFor(() => expect(latestGraph().categories).toBeNull());
    fireEvent.click(cats.getByRole("checkbox", { name: "结构" }));
    await waitFor(() => expect(latestGraph().categories).toEqual(["evidence", "reference", "navigation", "claim_part", "mention"]));
    for (const name of ["依据", "引用", "关联", "提及"]) fireEvent.click(cats.getByRole("checkbox", { name }));
    await waitFor(() => expect(latestGraph().categories).toEqual(["claim_part"]));
    expect(cats.getByRole("checkbox", { name: "结论要素" })).toBeDisabled();
  });

  it("已保存视图里改名前的类别不再生效，退回默认类别", async () => {
    stored = { filters: { categories: ["concept", "claim_relation"] } };
    render(<Relations />);
    await screen.findByText(/4 个节点、0 条关系/);
    expect(latestGraph().categories).toEqual(defaultCategories);
  });

  it("重点关系有单独的图例，样式相同的依赖与前提合并为一项", async () => {
    render(<Relations />);
    const legend = within(await screen.findByLabelText("重点关系图例"));
    for (const name of ["矛盾", "替代", "支持", "输入依据", "依赖/前提"]) expect(legend.getByText(name)).toBeInTheDocument();
  });

  it("只有旧筛选字段的已保存视图使用当前默认节点类型", async () => {
    stored = { filters: { levels: ["L0"], showBlocks: true } };
    render(<Relations />);
    await screen.findByText(/4 个节点、0 条关系/);
    expect(latestGraph().node_types).toEqual(defaults);
    expect(latestGraph()).not.toHaveProperty("levels");
  });
});

describe("关系图交互修复", () => {
  it("合并连线逐条显示声明记录、方向、结论范围和依据", async () => {
    const nodes = [
      { id: "a", type: "record", kind: "detail", title: "记录甲", owner_id: "RES-1", level: "L1" },
      { id: "b", type: "record", kind: "detail", title: "记录乙", owner_id: "RES-1", level: "L1" },
    ];
    const ref = { target_kind: "record", target_id: "MEM-3", revision: 1, sha256: "hash", locator: "block:k", relation: "supports", note: "表 2 的满载组" };
    const relation = { predicate: "causes", participants: [{ role: "cause", value: "[[温漂|温度漂移]]" }, { role: "cause", value: "[[噪声]]" }, { role: "effect", value: "瞬态误差" }] };
    const edges = [
      { id: "e1", source: "a", target: "b", type: "supports", category: "evidence", origin: "CLM-1", declared_by: "CLM-1", declared_owner: "RES-1", declared_record: { owner_id: "RES-1", record_id: "MEM-1", revision: 2 }, declared_rev: 2, field: "claims.evidence_refs" },
      { id: "e2", source: "b", target: "a", type: "related_to", category: "navigation", state: "accepted", origin: "MEM-2", declared_by: "MEM-2", declared_owner: "RES-1", declared_record: { owner_id: "RES-1", record_id: "MEM-2", revision: 1 }, declared_rev: 1, field: "association" },
    ];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
      const name = url.replace("api/v1/ops/", "");
      const args = JSON.parse(String(init.body));
      calls.push({ name, args });
      if (name === "ui.ref_targets") return new Response(JSON.stringify({ "MEM-3": { kind: "record", owner_id: "RES-1", record_id: "MEM-3", title: "隐藏的依据记录" } }), { status: 200 });
      const record = args.record_id === "MEM-1"
        ? { record_id: "MEM-1", title: "结论原记录", revision: 2, payload: { claims: [{ claim_id: "CLM-1", statement: "实验表明甲支持乙", scope: "温度 20°C", evidence_refs: [ref], relation }] } }
        : { record_id: "MEM-2", title: "关联原记录", revision: 1, payload: { explanation: "同一个失效模式", basis_refs: [ref], transfer_limits: ["只能比较小样本"] } };
      return new Response(JSON.stringify({ record }), { status: 200 });
    }));
    render(<EdgeFocus link={{ id: "a|b", source: "a", target: "b", edges, forward: true, backward: true }} nodes={nodes} />);
    const entries = screen.getAllByRole("listitem");
    expect(entries).toHaveLength(2);
    expect(within(entries[0]).getByText(/记录甲 → 记录乙：支持/)).toBeInTheDocument();
    expect(within(entries[1]).getByText(/记录乙 → 记录甲：相关/)).toBeInTheDocument();
    // 采纳关联只是导航决定，不得显示为经过科学复核的“已确认”。
    expect(within(entries[1]).getByText(/状态：已采纳/)).toBeInTheDocument();
    expect(within(entries[1]).queryByText(/状态：已确认/)).not.toBeInTheDocument();
    await screen.findByText("实验表明甲支持乙");
    expect(screen.getByText(/适用范围：温度 20°C/)).toBeInTheDocument();
    // R23 D-12：结构与陈述并排，按角色列出参与者；依据显示 note
    expect(screen.getByText(/自动生成，以陈述为准/)).toBeInTheDocument();
    expect(screen.getByText("原因：温度漂移、噪声")).toBeInTheDocument();
    expect(screen.getByText("结果：瞬态误差")).toBeInTheDocument();
    expect(screen.getAllByText(/表 2 的满载组/).length).toBeGreaterThan(0);
    expect(screen.getByText("同一个失效模式")).toBeInTheDocument();
    expect(screen.getByText(/迁移边界：只能比较小样本/)).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "隐藏的依据记录" })[0]).toHaveAttribute("href", "#/memory/RES-1/MEM-3");
    expect(calls.filter((call) => call.name === "memory.inspect").map((call) => call.args)).toEqual([
      { owner_id: "RES-1", record_id: "MEM-1", revision: 2 },
      { owner_id: "RES-1", record_id: "MEM-2", revision: 1 },
    ]);
  });

  it("声明记录拒读时明确展示读取错误，不把图边当完整依据", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ code: "ACCESS_DENIED", message: "声明记录不可读取" }), { status: 403 })));
    render(<EdgeFocus link={{ id: "a|b", source: "a", target: "b", forward: true, backward: false,
      edges: [{ source: "a", target: "b", type: "supports", category: "evidence", origin: "MEM-1",
        declared_record: { owner_id: "RES-1", record_id: "MEM-1", revision: 1 } }] }} nodes={[]} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("声明记录不可读取");
    expect(screen.queryByText("原记录：")).not.toBeInTheDocument();
  });

  it("再次点击“从这里添加关联”取消关联起点", () => {
    const node = { id: "MEM-1", type: "record", kind: "detail", title: "求和记录", level: "L1", owner_id: "RES-1", ref: { target_kind: "record", target_id: "MEM-1", revision: 1, sha256: null, locator: "", relation: "navigation" } };
    function Harness() {
      const [linkFrom, setLinkFrom] = useState<typeof node>();
      return (
        <Focus node={node} candidates={[]} onCenter={() => {}} onHide={() => {}} reloadCandidates={() => {}} candidatesLoading={false}
          linkFrom={linkFrom} setLinkFrom={(n) => setLinkFrom(n as typeof node | undefined)} reloadGraph={() => {}} />
      );
    }
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "从这里添加关联" }));
    expect(screen.getByRole("button", { name: "取消关联起点" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "取消关联起点" }));
    expect(screen.getByRole("button", { name: "从这里添加关联" })).toHaveAttribute("aria-pressed", "false");
  });

  it("隐藏的节点可以逐个恢复", async () => {
    stored = { excluded: ["claim", "concept"], filters: {} };
    render(<Relations />);
    const bar = within(await screen.findByRole("group", { name: "已隐藏的节点" }));
    fireEvent.click(bar.getByRole("button", { name: /^claim/ }));
    await waitFor(() => expect(latestGraph().excluded).toEqual(["concept"]));
  });

});
