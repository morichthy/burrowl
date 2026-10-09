import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Relations } from "./Relations";

let stored: Record<string, unknown>;
let calls: { name: string; args: Record<string, unknown> }[];
const defaults = ["owner", "record", "claim", "concept", "source"];
const nodeLabels = ["对象", "记录", "内容块（L1）", "结论（Claim）", "概念", "来源"];
const latestGraph = () => calls.filter((call) => call.name === "relations.graph").at(-1)!.args;

beforeEach(() => {
  stored = {};
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
    const name = url.replace("api/v1/ops/", "");
    const args = JSON.parse(String(init.body));
    calls.push({ name, args });
    let body: unknown = [];
    if (name === "relations.view") body = stored;
    if (name === "relations.view_save") body = stored = args.view as Record<string, unknown>;
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
  it("分类合并层级标签，六类节点独立控制实际可见性，全部取消显示零节点", async () => {
    render(<Relations />);
    await screen.findByText(/5 个节点、0 条关系/);
    const kinds = within(screen.getByRole("group", { name: "记录分类" }));
    expect(kinds.getAllByRole("checkbox")).toHaveLength(7);
    for (const name of ["概览（L4）", "经验（L3）", "经过（L2）", "技术单元（L1）", "原始依据（L0）", "文稿", "章节"]) {
      expect(kinds.getByRole("checkbox", { name })).toBeInTheDocument();
    }
    fireEvent.click(kinds.getByRole("checkbox", { name: "经验（L3）" }));
    await waitFor(() => expect(latestGraph().kinds).toEqual(["experience"]));
    expect(latestGraph()).not.toHaveProperty("levels");
    expect(latestGraph().node_types).toEqual(defaults);
    const types = within(screen.getByRole("group", { name: "节点类型" }));
    expect(types.getAllByRole("checkbox")).toHaveLength(6);
    expect(types.getByRole("checkbox", { name: "内容块（L1）" })).not.toBeChecked();
    fireEvent.click(types.getByRole("checkbox", { name: "内容块（L1）" }));
    await screen.findByText(/6 个节点、0 条关系/);
    for (const name of nodeLabels) fireEvent.click(types.getByRole("checkbox", { name }));
    await screen.findByText(/0 个节点、0 条关系/);
    expect(latestGraph().node_types).toEqual([]);
  });

  it("旧隐藏字段不生效，当前筛选保存后重载保持空选，重置恢复默认", async () => {
    stored = { filters: { levels: ["L4"], showBlocks: true, kinds: ["experience"], nodeTypes: ["claim"] } };
    const first = render(<Relations />);
    await screen.findByText(/1 个节点、0 条关系/);
    expect(latestGraph()).toMatchObject({ kinds: ["experience"], node_types: ["claim"] });
    expect(latestGraph()).not.toHaveProperty("levels");
    fireEvent.click(screen.getByRole("checkbox", { name: "结论（Claim）" }));
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
    await screen.findByText(/5 个节点、0 条关系/);
    expect(latestGraph()).toMatchObject({ node_types: defaults, kinds: null, categories: null, center: null });
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

  it("关系类别勾选即显示：默认全选不过滤，取消一类只请求其余类别，最后一类不能取消", async () => {
    render(<Relations />);
    await screen.findByText(/5 个节点、0 条关系/);
    const cats = within(screen.getByRole("group", { name: "关系类别" }));
    expect(cats.getAllByRole("checkbox", { checked: true })).toHaveLength(6);
    fireEvent.click(cats.getByRole("checkbox", { name: "结构" }));
    await waitFor(() => expect(latestGraph().categories).toEqual(["evidence", "reference", "navigation", "concept", "claim_relation"]));
    fireEvent.click(cats.getByRole("checkbox", { name: "结构" }));
    await waitFor(() => expect(latestGraph().categories).toBeNull());
    for (const name of ["结构", "证据", "引用", "关联", "概念"]) fireEvent.click(cats.getByRole("checkbox", { name }));
    await waitFor(() => expect(latestGraph().categories).toEqual(["claim_relation"]));
    expect(cats.getByRole("checkbox", { name: "结论关系" })).toBeDisabled();
  });

  it("重点关系有单独的图例，样式相同的依赖与前提合并为一项", async () => {
    render(<Relations />);
    const legend = within(await screen.findByLabelText("重点关系图例"));
    for (const name of ["矛盾", "替代", "支持", "输入依据", "依赖/前提"]) expect(legend.getByText(name)).toBeInTheDocument();
  });

  it("只有旧筛选字段的已保存视图使用当前默认节点类型", async () => {
    stored = { filters: { levels: ["L0"], showBlocks: true } };
    render(<Relations />);
    await screen.findByText(/5 个节点、0 条关系/);
    expect(latestGraph().node_types).toEqual(defaults);
    expect(latestGraph()).not.toHaveProperty("levels");
  });
});
