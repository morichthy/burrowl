// 关系图：对象、记录、块、结论、概念、来源，以及按类别区分的关系（来自索引中的图投影，R12 T-P2.8）。
// 颜色区分边的类别，线型区分关联的确定度（实线=有依据，虚线=可能，点线=推测），形状区分节点类型。
// 同一对节点之间的多条边在画布上合并为一条连线，悬停时列出全部关系；这只影响展示，不改变图投影。
// 点击节点查看详情、以它为中心展开、查看关联候选并判断，或从一个节点到另一个节点快速添加关联。
// 关联只是导航，不是证据：采纳一条关联不会让任何结论变成"已确认"。
import cytoscape from "cytoscape";
import Graph from "graphology";
import louvain from "graphology-communities-louvain";
import { useEffect, useMemo, useRef, useState } from "react";
import { useAction, useOp } from "../api";
import { KINDS, label, MATURITY, REVIEW_BADGES } from "../labels";
import { Empty, ErrorBox, href, Loading, Panel, Tag, Time } from "../ui";

interface GraphNode {
  id: string;
  type: string;
  kind: string;
  title: string;
  level: string | null;
  owner_id: string | null;
  record_id?: string | null;
  ref?: FixedRef | null;
  domains?: string[];
  review?: string | null;
  maturity?: string | null;
}
interface GraphEdge {
  source: string;
  target: string;
  type: string;
  origin: string;
  category?: string;
  certainty?: string | null;
  derived?: boolean;
  state?: string | null;
  candidate?: boolean;
}
interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
  truncated: boolean;
}
interface FixedRef {
  target_kind: string;
  target_id: string;
  revision: number | null;
  sha256: string | null;
  locator: string;
  relation: string;
}
interface CandidateLink {
  from: FixedRef;
  to: FixedRef;
  score: number;
  method: string;
  title: string;
}
interface Note {
  id: string;
  kind: string;
  title: string;
  text: string;
  refs: string[];
  status: string;
  created_at: string;
}

// 节点类型色：在浅色和深色画布上都可辨认
const TYPE_COLORS: Record<string, string> = {
  owner: "#6670e8",
  record: "#3aa876",
  source: "#d39b38",
  claim: "#b26ad8",
  concept: "#e0664a",
  block: "#8f9bab",
};
const NODE_TYPE_LABELS: Record<string, string> = { owner: "对象", record: "记录", claim: "结论（Claim）", concept: "概念", source: "来源", block: "内容块（L1）" };
const CATEGORIES: { value: string; label: string; color: string }[] = [
  { value: "structure", label: "结构", color: "#c8c8c8" },
  { value: "evidence", label: "证据", color: "#3d8b5a" },
  { value: "reference", label: "引用", color: "#7a8ca8" },
  { value: "navigation", label: "关联", color: "#d08a2c" },
  { value: "concept", label: "概念", color: "#d0573a" },
  { value: "claim_relation", label: "结论关系", color: "#a05195" },
];
const LINE_STYLE: Record<string, string> = { supported: "solid", plausible: "dashed", speculative: "dotted" };
// 同一对节点之间有多条边时合并成一条连线，颜色取最"有分量"的类别：证据 > 结论关系 > 关联 > 概念 > 引用 > 结构
const CATEGORY_PRIORITY = ["evidence", "claim_relation", "navigation", "concept", "reference", "structure"];
// 边类型（index/graph.py 的 CATEGORY 表）的中文名，用于悬停说明和选中节点时的连线标签
const EDGE_TYPE_LABELS: Record<string, string> = {
  belongs_to: "属于", contains: "包含", part_of: "组成", depends_on: "依赖", produces: "产出", member_of: "成员", precedes: "先于",
  supports: "支持", input: "输入依据", contradicts: "矛盾", supersedes: "替代",
  references: "引用", background: "背景", derived_from: "派生自", prerequisite: "前提", refines: "细化", used_in: "用于",
  same_problem: "同一问题", same_failure_mode: "同一失败模式", analogous_to: "类比", related_to: "相关", similar_structure: "结构相似",
  same_entity: "同一实体", mentions: "提及", subject_of: "主体", object_of: "客体",
};
// 形状区分节点类型（不只靠颜色，色弱也能分辨）；对象另加一圈光环
const NODE_SHAPES: Record<string, string> = { owner: "ellipse", record: "ellipse", block: "ellipse", source: "round-rectangle", claim: "round-diamond", concept: "round-hexagon" };
const UNSAFE_REVIEW = new Set(["disputed", "retracted", "superseded", "stale", "upstream-blocked"]);
const NODE_TYPES =["owner", "record", "block", "claim", "concept", "source"];
// 默认隐藏内容块以免图过密；空数组则明确表示不显示任何节点。
const DEFAULT_NODE_TYPES = NODE_TYPES.filter((type) => type !== "block");
const PALETTE = ["#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f", "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac"];
const KIND_FILTERS = [
  { value: "overview", label: "概览（L4）" },
  { value: "experience", label: "经验（L3）" },
  { value: "narrative", label: "经过（L2）" },
  { value: "detail", label: "技术单元（L1）" },
  { value: "source", label: "原始依据（L0）" },
  { value: "document", label: "文稿" },
  { value: "document_section", label: "章节" },
];
const RELATIONS = [
  { value: "same_problem", label: "同一问题" },
  { value: "same_failure_mode", label: "同一失败模式" },
  { value: "similar_structure", label: "结构相似" },
  { value: "analogous_to", label: "类比（需写共享结构与迁移边界）" },
];
const NOTE_KINDS = { "cluster-name": "分组命名", "bridge-question": "桥接问题", "relation-idea": "关系设想" };

interface View {
  center?: string | null;
  hops?: number;
  query?: string;
  kinds?: string[];
  nodeTypes?: string[];
  excluded?: string[];
  communities?: boolean;
  categories?: string[];
}

const defaultView = (): View => ({ hops: 2, query: "", kinds: [], nodeTypes: [...DEFAULT_NODE_TYPES], excluded: [], communities: false, categories: [] });

/** 画布上的一条连线：同一对节点之间的全部边（不分方向）合并而成。 */
interface Link {
  id: string;
  source: string;
  target: string;
  edges: GraphEdge[];
  forward: boolean; // 存在 source→target 方向的边
  backward: boolean; // 存在 target→source 方向的边
}

/**
 * 按无序节点对合并边。图投影里同一对节点常有多条同类边（不同字段各声明一次引用），
 * 逐条画会叠成一束平行线并把连接数虚高；合并后连线数等于实际相连的节点对数。
 */
function mergeEdges(data: GraphData | undefined): Link[] {
  if (!data) return [];
  const ids = new Set(data.nodes.map((n) => n.id));
  const links = new Map<string, Link>();
  for (const e of data.edges) {
    if (!ids.has(e.source) || !ids.has(e.target)) continue;
    const [a, b] = e.source <= e.target ? [e.source, e.target] : [e.target, e.source];
    const id = `${a}|${b}`;
    const link = links.get(id) ?? { id, source: a, target: b, edges: [], forward: false, backward: false };
    link.edges.push(e);
    if (e.source === a) link.forward = true;
    if (e.source === b) link.backward = true;
    links.set(id, link);
  }
  return [...links.values()];
}

const rank = (e: GraphEdge) => {
  const i = CATEGORY_PRIORITY.indexOf(e.category ?? "");
  return (e.state === "invalid" ? 100 : 0) + (i < 0 ? CATEGORY_PRIORITY.length : i);
};
/** 连线的代表边：失效的边排在后面，其余按类别优先级。 */
const mainEdge = (link: Link) => [...link.edges].sort((x, y) => rank(x) - rank(y))[0];
const edgeTypeLabel = (type: string) => EDGE_TYPE_LABELS[type] ?? type;
/** 连线上各种关系类型的中文名，去重后按代表性排序。 */
const linkLabel = (link: Link) => [...new Set([...link.edges].sort((x, y) => rank(x) - rank(y)).map((e) => edgeTypeLabel(e.type)))].join("、");

export function Relations() {
  const saved = useOp<View>("relations.view");
  const [view, setView] = useState<View>(defaultView);
  const [history, setHistory] = useState<string[]>([]);
  const [focus, setFocus] = useState<GraphNode>();
  const [linkFrom, setLinkFrom] = useState<GraphNode>();
  const [loadedSaved, setLoadedSaved] = useState(false);
  useEffect(() => {
    if (saved.data && !loadedSaved) {
      const stored = saved.data as { center?: string | null; excluded?: string[]; filters?: View };
      const filters = stored.filters;
      // 只读取当前界面的字段，旧 levels/showBlocks 不得成为看不见的筛选条件。
      setView({
        center: stored.center ?? null, excluded: stored.excluded ?? [],
        hops: filters?.hops ?? 2, query: filters?.query ?? "", kinds: filters?.kinds ?? [],
        nodeTypes: filters?.nodeTypes ?? [...DEFAULT_NODE_TYPES],
        communities: filters?.communities ?? false, categories: filters?.categories ?? [],
      });
      setLoadedSaved(true);
    }
  }, [saved.data, loadedSaved]);
  const graph = useOp<GraphData>("relations.graph", {
    query: view.query ?? "",
    center: view.center ?? null,
    hops: view.hops ?? 2,
    kinds: view.kinds?.length ? view.kinds : null,
    excluded: view.excluded?.length ? view.excluded : null,
    categories: view.categories?.length ? view.categories : null,
    node_types: view.nodeTypes ?? DEFAULT_NODE_TYPES,
    limit: 400,
  });
  const candidates = useOp<{ candidates: CandidateLink[] }>(
    "relations.similar",
    { seeds: [focus?.id ?? ""], method: "keyword", limit: 12 },
    !!focus && focus.type === "record",
  );
  const saveView = useAction("relations.view_save");
  const communities = useMemo(() => (view.communities && graph.data ? detect(graph.data) : null), [view.communities, graph.data]);
  const links = useMemo(() => mergeEdges(graph.data), [graph.data]);

  const setCenter = (id: string | null) => {
    if (view.center && view.center !== id) setHistory((h) => [...h, view.center!]);
    setView({ ...view, center: id });
  };
  const toggle = (key: "kinds" | "nodeTypes", value: string) => {
    const current = view[key] ?? [];
    setView({ ...view, [key]: current.includes(value) ? current.filter((v) => v !== value) : [...current, value] });
  };
  // 关系类别按"勾选=显示"呈现：空数组表示全部显示；不允许取消最后一类（后端把空筛选视为全部，会造成误解）
  const allCategories = CATEGORIES.map((c) => c.value);
  const visibleCategories = view.categories?.length ? view.categories : allCategories;
  const toggleCategory = (value: string) => {
    const next = visibleCategories.includes(value) ? visibleCategories.filter((v) => v !== value) : [...visibleCategories, value];
    if (!next.length) return;
    setView({ ...view, categories: next.length === allCategories.length ? [] : allCategories.filter((v) => next.includes(v)) });
  };

  return (
    <div className="split wide-left">
      <Panel
        title="关系图"
        actions={
          <>
            <button type="button" disabled={saveView.busy} onClick={() => {
              const { center, excluded, ...filters } = view;
              saveView.run({ view: { center: center ?? null, excluded: excluded ?? [], filters } });
            }}>
              保存视图
            </button>
            <button type="button" onClick={() => setView(defaultView())}>
              重置
            </button>
          </>
        }
      >
        <div className="toolbar">
          <input placeholder="按标题筛选" value={view.query ?? ""} onChange={(e) => setView({ ...view, query: e.target.value })} />
          <label className="check">
            展开层数{" "}
            <input type="number" className="narrow" min={1} max={4} value={view.hops ?? 2} onChange={(e) => setView({ ...view, hops: Number(e.target.value) })} />
          </label>
          <label className="check">
            <input type="checkbox" checked={!!view.communities} onChange={(e) => setView({ ...view, communities: e.target.checked })} />
            按社区着色
          </label>
          {view.center ? (
            <>
              <button
                type="button"
                onClick={() => {
                  setView({ ...view, center: history.at(-1) ?? null });
                  setHistory((h) => h.slice(0, -1));
                }}
              >
                返回上个中心
              </button>
              <button type="button" onClick={() => setCenter(null)}>
                取消中心（{view.center}）
              </button>
            </>
          ) : null}
        </div>
        <div className="filter-rows">
          <span className="filter-label" title="节点类型控制图中实际显示的节点；结论（Claim）是可以单独引用和复核的判断">
            节点
          </span>
          <div className="chips" role="group" aria-label="节点类型">
            {NODE_TYPES.map((type) => (
              <label key={type} className={view.nodeTypes?.includes(type) ? "on" : ""}>
                <input type="checkbox" checked={!!view.nodeTypes?.includes(type)} onChange={() => toggle("nodeTypes", type)} />
                <i className={`shape shape-${type}`} style={{ background: TYPE_COLORS[type] }} />
                {NODE_TYPE_LABELS[type]}
              </label>
            ))}
          </div>
          <span className="filter-label" title="只筛选记录节点；不选表示全部记录。结论、来源等其他节点由上一行控制">
            记录分类
          </span>
          <div className="chips" role="group" aria-label="记录分类">
            {KIND_FILTERS.map((k) => (
              <label key={k.value} className={view.kinds?.includes(k.value) ? "on" : ""}>
                <input type="checkbox" checked={!!view.kinds?.includes(k.value)} onChange={() => toggle("kinds", k.value)} />
                {k.label}
              </label>
            ))}
            {!view.kinds?.length ? <span className="hint">不选＝全部</span> : null}
          </div>
          <span className="filter-label" title="勾选的类别显示在图中；颜色即连线颜色">
            关系类别
          </span>
          <div className="chips" role="group" aria-label="关系类别">
            {CATEGORIES.map((c) => {
              const on = visibleCategories.includes(c.value);
              return (
                <label key={c.value} className={on ? "on" : ""}>
                  <input type="checkbox" checked={on} disabled={on && visibleCategories.length === 1} onChange={() => toggleCategory(c.value)} />
                  <i className="swatch" style={{ background: c.color }} />
                  {c.label}
                </label>
              );
            })}
          </div>
        </div>
        {view.excluded?.length ? (
          <p className="hint">
            已隐藏：{view.excluded.join("、")}{" "}
            <button type="button" className="link" onClick={() => setView({ ...view, excluded: [] })}>
              全部恢复
            </button>
          </p>
        ) : null}
        <ErrorBox error={graph.error ?? saveView.error} />
        <Loading show={graph.loading} />
        {graph.data?.truncated ? <div className="notice">节点过多，只显示了一部分；请缩小范围或设置中心。</div> : null}
        <GraphCanvas
          data={graph.data}
          links={links}
          communities={communities}
          suggestions={candidates.data?.candidates ?? []}
          selectedId={focus?.id}
          onSelect={setFocus}
          onCenter={(id) => setCenter(id)}
        />
        <p className="hint">
          {graph.data?.nodes.length ?? 0} 个节点、{graph.data?.edges.length ?? 0} 条关系
          {links.length && links.length < (graph.data?.edges.length ?? 0) ? `（合并为 ${links.length} 条连线）` : ""}
          {communities ? `，${new Set(Object.values(communities)).size} 个社区` : ""}。悬停查看名称与关系，双击节点以它为中心展开。连线颜色是关系类别；关联的实线、虚线、点线分别表示有依据、可能、推测；橙色虚线是待判断的候选；节点的红色虚线边框表示复核有问题。
        </p>
      </Panel>
      <aside>
        <Focus
          node={focus}
          candidates={candidates.data?.candidates ?? []}
          onCenter={(id) => setCenter(id)}
          onHide={(id) => setView({ ...view, excluded: [...(view.excluded ?? []), id] })}
          reloadCandidates={candidates.reload}
          linkFrom={linkFrom}
          setLinkFrom={setLinkFrom}
          reloadGraph={graph.reload}
        />
        <Notes focus={focus} />
      </aside>
    </div>
  );
}

function detect(data: GraphData): Record<string, number> {
  const g = new Graph({ type: "undirected", multi: false });
  data.nodes.forEach((n) => g.addNode(n.id));
  data.edges.forEach((e) => {
    if (e.source !== e.target && g.hasNode(e.source) && g.hasNode(e.target) && !g.hasEdge(e.source, e.target)) {
      g.addEdge(e.source, e.target);
    }
  });
  if (!g.size) return {};
  // 固定随机种子：同一张图每次得到相同分组，便于比较
  let seed = 173;
  const rng = () => ((seed = (seed * 16807) % 2147483647) - 1) / 2147483646;
  return louvain(g, { resolution: 1, rng });
}

// 画布颜色取自页面主题变量，深浅色切换时重新应用样式
function theme() {
  const css = getComputedStyle(document.documentElement);
  const v = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback;
  return {
    text: v("--text", "#1c2230"), muted: v("--muted", "#677085"), bg: v("--panel-2", "#f8f9fb"),
    accent: v("--accent", "#4f5bd5"), line: v("--line-strong", "#cfd4dd"), bad: v("--bad", "#c4283a"),
  };
}

function graphStyle(t: ReturnType<typeof theme>) {
  // cytoscape 的样式类型过窄（不认 data() 映射与过渡），这里整体放宽
  return [
    {
      selector: "node",
      style: {
        label: "data(label)",
        width: "data(size)",
        height: "data(size)",
        shape: "data(shape)",
        "background-color": "data(color)",
        "border-width": 0,
        color: t.text,
        "font-size": 11,
        "text-valign": "bottom",
        "text-margin-y": 5,
        "text-outline-color": t.bg,
        "text-outline-width": 2.5,
        "text-max-width": "160px",
        "text-wrap": "ellipsis",
        // 标签按重要性分级出现：普通节点放大到约 1.2 倍才显示，枢纽和对象更早显示，默认视图不糊成一片
        "min-zoomed-font-size": 13,
        "transition-property": "opacity, border-width, width, height",
        "transition-duration": "0.2s",
      },
    },
    { selector: "node.hub", style: { "min-zoomed-font-size": 8, "font-weight": 500 } },
    // 对象是图的骨架：标签最早出现，外加一圈同色光环
    { selector: "node.major", style: { "font-size": 13, "min-zoomed-font-size": 5, "font-weight": 600, "border-width": 5, "border-color": "data(color)", "border-opacity": 0.28 } },
    { selector: "node.unsafe", style: { "border-width": 2, "border-style": "dashed", "border-color": t.bad, "border-opacity": 1 } },
    {
      selector: "edge",
      style: {
        width: "data(width)",
        "line-color": "data(color)",
        "line-style": "data(line)",
        "line-opacity": 0.55,
        "curve-style": "straight",
        "target-arrow-shape": "data(targetArrow)",
        "source-arrow-shape": "data(sourceArrow)",
        "target-arrow-color": "data(color)",
        "source-arrow-color": "data(color)",
        "arrow-scale": 0.55,
        "transition-property": "opacity, line-opacity, width",
        "transition-duration": "0.2s",
      },
    },
    // 结构边（属于、包含）只是骨架，淡一些，让引用、证据等语义关系更突出
    { selector: "edge.structure", style: { "line-opacity": 0.3 } },
    { selector: "edge.invalid", style: { "line-color": t.line, "target-arrow-color": t.line, "source-arrow-color": t.line } },
    { selector: "edge.candidate", style: { "line-style": "dashed", "line-color": "#d08a2c", width: 1.5, "target-arrow-shape": "none", "source-arrow-shape": "none", "line-opacity": 0.85 } },
    { selector: ".faded", style: { opacity: 0.1 } },
    { selector: "node.highlighted", style: { "min-zoomed-font-size": 0, "font-weight": 600 } },
    { selector: "edge.highlighted", style: { "line-opacity": 0.95 } },
    // 邻居不多时在连线上写出关系名；邻居多时只靠悬停说明，避免文字堆叠
    {
      selector: "edge.labeled",
      style: {
        label: "data(label)",
        "font-size": 9,
        color: t.muted,
        "text-rotation": "autorotate",
        "text-background-color": t.bg,
        "text-background-opacity": 0.9,
        "text-background-padding": "2px",
        "text-background-shape": "round-rectangle",
        "min-zoomed-font-size": 0,
      },
    },
    { selector: "node:selected", style: { "border-width": 3, "border-style": "solid", "border-color": t.accent, "border-opacity": 0.95, "min-zoomed-font-size": 0 } },
  ] as unknown as cytoscape.StylesheetJson;
}

const EDGE_LABEL_LIMIT = 12;

/** 悬停或选中：突出节点（或连线）及其直接相连的部分，其余淡出（不移动任何节点）。 */
function highlight(cy: cytoscape.Core, target: cytoscape.NodeSingular | cytoscape.EdgeSingular | undefined) {
  cy.batch(() => {
    cy.elements().removeClass("faded highlighted labeled");
    if (!target) return;
    const near = target.isNode() ? (target as cytoscape.NodeSingular).closedNeighborhood() : target.union((target as cytoscape.EdgeSingular).connectedNodes());
    cy.elements().not(near).addClass("faded");
    near.addClass("highlighted");
    const edges = near.edges().not(".suggest");
    if (edges.length <= EDGE_LABEL_LIMIT) edges.addClass("labeled");
  });
}

// 力导向布局参数：斥力大、边长适中，接近 Obsidian 图谱的疏朗效果；只在节点集合变化时运行。
// 结构边（记录属于对象等）短而硬，让记录围绕所属对象成团；引用、证据等语义边长而软，把各团松散地连起来。
// cose 的 edgeElasticity 是除数，越大弹簧越弱；标签不计入节点尺寸（否则整图过散、缩放过小）。
// 只连一个节点的叶子（多为来源）也用短边，贴在引用它的记录旁边，而不是被斥力甩到画面边缘。
const isTight = (edge: cytoscape.EdgeSingular) => edge.data("category") === "structure" || edge.source().degree(false) <= 1 || edge.target().degree(false) <= 1;
const LAYOUT = {
  name: "cose",
  animate: "end",
  animationEasing: "ease-out-cubic",
  fit: false,
  nodeRepulsion: (node: cytoscape.NodeSingular) => 200000 + (node.data("size") ?? 14) * 10000,
  idealEdgeLength: (edge: cytoscape.EdgeSingular) => (isTight(edge) ? 40 : 100),
  edgeElasticity: (edge: cytoscape.EdgeSingular) => (isTight(edge) ? 60 : 300),
  nodeOverlap: 20,
  componentSpacing: 100,
  gravity: 1,
  numIter: 1500,
};

/** 平滑地把全部节点放进画面；节点很少时不放大到 1.6 倍以上，避免标签过大。 */
function smoothFit(cy: cytoscape.Core, duration = 450) {
  const eles = cy.elements();
  if (eles.empty()) return;
  const box = eles.boundingBox();
  const pad = 40;
  const zoom = Math.min(1.6, (cy.width() - 2 * pad) / Math.max(box.w, 1), (cy.height() - 2 * pad) / Math.max(box.h, 1));
  const pan = { x: cy.width() / 2 - zoom * (box.x1 + box.w / 2), y: cy.height() / 2 - zoom * (box.y1 + box.h / 2) };
  if (duration) cy.animate({ zoom, pan }, { duration, easing: "ease-in-out-cubic" });
  else cy.viewport({ zoom, pan });
}

const CERTAINTY_LABELS: Record<string, string> = { supported: "有依据", plausible: "可能", speculative: "推测" };

/** 悬停说明卡：画布坐标（相对画布左上角）、标题和若干行说明。 */
interface Tip {
  x: number;
  y: number;
  title: string;
  lines: string[];
}

function GraphCanvas(props: {
  data: GraphData | undefined;
  links: Link[];
  communities: Record<string, number> | null;
  suggestions: CandidateLink[];
  selectedId: string | undefined;
  onSelect: (node: GraphNode | undefined) => void;
  onCenter: (id: string) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const cyRef = useRef<cytoscape.Core>(undefined);
  const nodesRef = useRef(new Map<string, GraphNode>());
  const linksRef = useRef(new Map<string, Link>());
  const onSelect = useRef(props.onSelect);
  onSelect.current = props.onSelect;
  const onCenter = useRef(props.onCenter);
  onCenter.current = props.onCenter;
  const [tip, setTip] = useState<Tip>();
  const { data, links, communities, suggestions, selectedId } = props;
  const inJsdom = typeof navigator !== "undefined" && navigator.userAgent.includes("jsdom");

  // 画布只创建一次：数据、着色、候选、选中都在原实例上增量更新，位置和视野不会被重置
  useEffect(() => {
    if (!container.current || inJsdom) return;
    const cy = cytoscape({ container: container.current, style: graphStyle(theme()), minZoom: 0.1, maxZoom: 4, wheelSensitivity: 0.3 });
    const selected = () => {
      const node = cy.nodes(":selected").first();
      return node.nonempty() ? node : undefined;
    };
    // 说明卡放在画布内，靠右时向左收，避免超出画布
    const place = (p: cytoscape.Position) => ({ x: Math.min(p.x + 14, cy.width() - 290), y: p.y + 14 });
    const nodeTip = (el: cytoscape.NodeSingular): Tip | undefined => {
      const n = nodesRef.current.get(el.id());
      if (!n) return undefined;
      const kind = n.type === "record" ? label(KINDS, n.kind) || n.kind : "";
      const lines = [[NODE_TYPE_LABELS[n.type] ?? n.type, kind, n.level].filter(Boolean).join(" · "), `${el.neighborhood().nodes().length} 个相连节点`];
      if (n.review) lines.push(`复核：${REVIEW_BADGES[n.review]?.label ?? n.review}`);
      if (n.maturity) lines.push(`成熟度：${label(MATURITY, n.maturity) || n.maturity}`);
      return { ...place(el.renderedPosition()), title: n.title, lines };
    };
    const edgeTip = (el: cytoscape.EdgeSingular, at: cytoscape.Position): Tip | undefined => {
      const link = linksRef.current.get(el.id());
      if (!link) return undefined;
      const name = (id: string) => (nodesRef.current.get(id)?.title ?? id).slice(0, 14);
      // 每种"方向＋关系类型"列一行，并注明确定度、失效与候选状态
      const rows = new Map<string, string>();
      for (const e of [...link.edges].sort((x, y) => rank(x) - rank(y))) {
        const notes = [e.category === "navigation" && e.certainty ? CERTAINTY_LABELS[e.certainty] : "", e.state === "invalid" ? "已失效" : "", e.candidate ? "候选" : ""].filter(Boolean);
        rows.set(`${e.source}>${e.target}:${e.type}`, `${name(e.source)} → ${name(e.target)}：${edgeTypeLabel(e.type)}${notes.length ? `（${notes.join("，")}）` : ""}`);
      }
      const lines = [...rows.values()];
      return { ...place(at), title: linkLabel(link), lines: lines.length > 6 ? [...lines.slice(0, 6), `……另有 ${lines.length - 6} 条`] : lines };
    };
    const rest = () => {
      highlight(cy, selected());
      setTip(undefined);
    };
    cy.on("tap", "node", (event) => onSelect.current(nodesRef.current.get(event.target.id())));
    cy.on("tap", (event) => {
      if (event.target === cy) onSelect.current(undefined); // 点空白处取消选中
    });
    cy.on("dbltap", "node", (event) => onCenter.current(event.target.id()));
    cy.on("select", "node", (event) => highlight(cy, event.target));
    cy.on("unselect", "node", () => highlight(cy, selected()));
    cy.on("mouseover", "node", (event) => {
      highlight(cy, event.target);
      setTip(nodeTip(event.target));
    });
    cy.on("mouseover", "edge", (event) => {
      if (event.target.hasClass("suggest")) return;
      highlight(cy, event.target);
      setTip(edgeTip(event.target, event.renderedPosition));
    });
    cy.on("mouseout", "node, edge", rest);
    cy.on("viewport", () => setTip(undefined));
    // 鼠标直接移出画布时 cytoscape 不一定触发 mouseout，补一次复位
    const box = container.current;
    box.addEventListener("mouseleave", rest);
    const media = window.matchMedia?.("(prefers-color-scheme: dark)");
    const restyle = () => cy.style(graphStyle(theme()));
    media?.addEventListener("change", restyle);
    // 容器尺寸变化（窗口缩放、侧栏展开）时同步画布尺寸，否则点击位置和适应画面会错位
    const resize = typeof ResizeObserver === "undefined" ? undefined : new ResizeObserver(() => cy.resize());
    resize?.observe(box);
    cyRef.current = cy;
    return () => {
      box.removeEventListener("mouseleave", rest);
      media?.removeEventListener("change", restyle);
      resize?.disconnect();
      cy.destroy();
      cyRef.current = undefined;
    };
  }, [inJsdom]);

  // 数据变化：按 ID 增删，已有节点保持原位；只对新节点做布局，并以动画过渡
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || !data) return;
    nodesRef.current = new Map(data.nodes.map((n) => [n.id, n]));
    linksRef.current = new Map(links.map((l) => [l.id, l]));
    // 连接数按相连的节点数计（合并后的连线），重复声明的边不会让节点虚大
    const degree = new Map<string, number>();
    links.forEach((l) => {
      if (l.source === l.target) return;
      degree.set(l.source, (degree.get(l.source) ?? 0) + 1);
      degree.set(l.target, (degree.get(l.target) ?? 0) + 1);
    });
    // 枢纽：连接数排在前 15% 且至少 4 个的节点，标签在较小缩放下就显示
    const ranked = [...degree.values()].sort((a, b) => b - a);
    const hubMin = Math.max(4, ranked[Math.floor(ranked.length * 0.15)] ?? Infinity);
    const fresh = new Set<string>();
    let removedNodes = 0;
    cy.batch(() => {
      const stale = cy.elements().filter((el) => (el.isNode() ? !nodesRef.current.has(el.id()) : !el.hasClass("suggest") && !linksRef.current.has(el.id())));
      removedNodes = stale.nodes().length;
      stale.remove();
      for (const n of data.nodes) {
        const d = degree.get(n.id) ?? 0;
        // 节点大小随连接数增长（开方压缩，避免枢纽节点过大）；内容块本身更小
        const fields = { label: n.title.slice(0, 28), size: (n.type === "block" ? 10 : 14) + Math.min(30, Math.sqrt(d) * 5), shape: NODE_SHAPES[n.type] ?? "ellipse" };
        const existing = cy.getElementById(n.id);
        if (existing.nonempty()) existing.data(fields);
        else {
          fresh.add(n.id);
          cy.add({ group: "nodes", data: { id: n.id, ...fields } });
        }
        cy.getElementById(n.id)
          .toggleClass("major", n.type === "owner")
          .toggleClass("hub", d >= hubMin)
          .toggleClass("unsafe", UNSAFE_REVIEW.has(n.review ?? ""));
      }
      for (const link of links) {
        const main = mainEdge(link);
        const fields = {
          color: CATEGORIES.find((c) => c.value === main.category)?.color ?? "#bbb",
          line: main.category === "navigation" ? (LINE_STYLE[main.certainty ?? "plausible"] ?? "solid") : "solid",
          // 一对节点之间同时有多类关系时线稍粗
          width: 1.2 + 0.5 * Math.min(3, new Set(link.edges.map((e) => e.category)).size - 1),
          category: main.category ?? "reference",
          label: linkLabel(link),
          targetArrow: link.forward ? "triangle" : "none",
          sourceArrow: link.backward ? "triangle" : "none",
        };
        const existing = cy.getElementById(link.id);
        if (existing.nonempty()) existing.data(fields);
        else cy.add({ group: "edges", data: { id: link.id, source: link.source, target: link.target, ...fields } });
        cy.getElementById(link.id)
          .toggleClass("structure", main.category === "structure")
          .toggleClass("candidate", link.edges.every((e) => e.candidate))
          .toggleClass("invalid", link.edges.every((e) => e.state === "invalid"));
      }
    });
    if (!fresh.size) {
      // 只删不增（如设置中心、加过滤）：剩余节点从当前位置平滑舒展开，不随机重排
      if (removedNodes && cy.nodes().nonempty()) {
        const relax = cy.layout({ ...LAYOUT, randomize: false, animationDuration: 450 } as cytoscape.LayoutOptions);
        relax.one("layoutstop", () => smoothFit(cy));
        relax.run();
      }
      return;
    }
    const added = cy.nodes().filter((n) => fresh.has(n.id()));
    const kept = cy.nodes().not(added);
    const first = kept.empty();
    // 新节点从已有邻居附近出现，而不是从画面原点飞入
    const box = kept.boundingBox();
    const middle = first ? { x: 0, y: 0 } : { x: (box.x1 + box.x2) / 2, y: (box.y1 + box.y2) / 2 };
    added.forEach((n) => {
      const anchor = n.neighborhood().nodes().filter((m) => !fresh.has(m.id()))[0] as cytoscape.NodeSingular | undefined;
      const base = anchor ? anchor.position() : middle;
      n.position({ x: base.x + (Math.random() - 0.5) * 60, y: base.y + (Math.random() - 0.5) * 60 });
    });
    kept.lock();
    // 首次加载没有旧布局可保持，直接一次排好；之后的增量变化才用动画过渡
    const layout = cy.layout({ ...LAYOUT, randomize: first, animate: first ? false : "end", animationDuration: 450 } as cytoscape.LayoutOptions);
    layout.one("layoutstop", () => {
      kept.unlock();
      smoothFit(cy, first ? 0 : 450);
    });
    layout.run();
  }, [data, links]);

  // 社区着色只改颜色
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || !data) return;
    cy.batch(() =>
      data.nodes.forEach((n) =>
        cy.getElementById(n.id).data("color", communities ? PALETTE[(communities[n.id] ?? 0) % PALETTE.length] : (TYPE_COLORS[n.type] ?? "#888")),
      ),
    );
  }, [data, communities]);

  // 关联候选是叠加的虚线，增删时不触发布局
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.batch(() => {
      cy.$(".suggest").remove();
      suggestions
        .filter((l) => cy.getElementById(l.from.target_id).nonempty() && cy.getElementById(l.to.target_id).nonempty())
        .forEach((l, i) =>
          cy.add({
            group: "edges",
            data: { id: `suggest-${i}`, source: l.from.target_id, target: l.to.target_id, color: "#d08a2c", line: "dashed", width: 1.5, category: "navigation", label: "候选", targetArrow: "none", sourceArrow: "none" },
            classes: "candidate suggest",
          }),
        );
    });
  }, [suggestions, data]);

  // 侧栏选中的节点与画布同步（只改选中态，不平移、不缩放）
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    const target = selectedId ? cy.getElementById(selectedId) : cy.collection();
    if (target.nonempty() && target.selected()) return;
    cy.$("node:selected").unselect();
    target.select();
  }, [selectedId, data]);

  const fit = () => cyRef.current && smoothFit(cyRef.current, 400);
  const focusSelected = () => {
    const cy = cyRef.current;
    const node = selectedId ? cy?.getElementById(selectedId) : undefined;
    if (cy && node?.nonempty()) cy.animate({ center: { eles: node }, zoom: Math.max(cy.zoom(), 1.2) }, { duration: 450, easing: "ease-in-out-cubic" });
  };
  return (
    <div className="graph-wrap">
      <div ref={container} className="graph" aria-label="关系图" />
      <div className="graph-controls">
        <button type="button" className="small" onClick={fit} title="把全部节点放进画面">
          适应画面
        </button>
        <button type="button" className="small" disabled={!selectedId} onClick={focusSelected} title="平滑移动到选中的节点">
          定位选中
        </button>
      </div>
      {tip ? (
        <div className="graph-tip" style={{ left: tip.x, top: tip.y }}>
          <strong>{tip.title}</strong>
          {tip.lines.map((line, i) => (
            <div key={i}>{line}</div>
          ))}
        </div>
      ) : null}
      {/* 形状始终表示节点类型；按社区着色时颜色改表示社区，图例改为中性色 */}
      <div className="graph-legend">
        {NODE_TYPES.map((t) => (
          <span key={t}>
            <i className={`shape shape-${t}`} style={{ background: communities ? "var(--muted)" : TYPE_COLORS[t] }} />
            {NODE_TYPE_LABELS[t]}
          </span>
        ))}
      </div>
    </div>
  );
}

function Focus(props: {
  node: GraphNode | undefined;
  candidates: CandidateLink[];
  onCenter: (id: string) => void;
  onHide: (id: string) => void;
  reloadCandidates: () => void;
  linkFrom: GraphNode | undefined;
  setLinkFrom: (node: GraphNode | undefined) => void;
  reloadGraph: () => void;
}) {
  const { node, candidates, linkFrom } = props;
  const decide = useAction("relations.decide");
  const quickAdd = async (from: GraphNode, to: GraphNode) => {
    // 快速添加：只给两端和一句说明，服务端自动设为 related_to + 推测（speculative）+ 已采纳
    const explanation = window.prompt(`为什么把「${from.title.slice(0, 20)}」和「${to.title.slice(0, 20)}」放在一起看？`, "");
    if (!explanation || !from.ref || !to.ref || !from.owner_id) return;
    if (await decide.run({ owner_id: from.owner_id, payload: { from: from.ref, to: to.ref, explanation }, reason: explanation })) {
      props.setLinkFrom(undefined);
      props.reloadGraph();
    }
  };
  const [relation, setRelation] = useState("same_problem");
  if (!node) return <Panel title="节点">{<p className="hint">点击图中的节点查看详情和关联候选。</p>}</Panel>;
  const judge = async (link: CandidateLink, status: "accepted" | "rejected") => {
    const explanation = window.prompt(status === "accepted" ? "说明两者的关系（会保存为导航关联）" : "不相关的原因", "") ?? undefined;
    if (explanation === undefined) return;
    let shared: string | null = null;
    let limits: string[] = [];
    if (status === "accepted" && relation === "analogous_to") {
      shared = window.prompt("两者共享的问题结构") || null;
      limits = (window.prompt("不能迁移的边界（逗号分隔）") || "").split(/[,，]/).filter(Boolean);
    }
    const payload = {
      from: { ...link.from, relation },
      to: { ...link.to, relation },
      relation,
      explanation: explanation || "（未说明）",
      shared_structure: shared,
      transfer_limits: limits,
      differences: [],
      basis_refs: [link.from, link.to],
      status,
      missing_refs: [],
    };
    if (await decide.run({ owner_id: node.owner_id!, payload, reason: explanation || status })) props.reloadCandidates();
  };
  return (
    <Panel title={node.title}>
      <p>
        <Tag>{label(KINDS, node.kind) || node.type}</Tag> {node.level ? <Tag>{node.level}</Tag> : null} <code>{node.id}</code>
      </p>
      <div className="actions">
        <button type="button" onClick={() => props.onCenter(node.id)}>
          以此为中心
        </button>
        <button type="button" onClick={() => props.onHide(node.id)}>
          隐藏
        </button>
        {node.owner_id ? (
          <a href={node.type === "owner" ? href("memory", node.id) : href("memory", node.owner_id, node.record_id ?? node.id)}>打开</a>
        ) : null}
      </div>
      {node.maturity || node.review ? (
        <p className="hint">
          {node.review ? `复核：${node.review}` : ""} {node.maturity ? `成熟度：${node.maturity}` : ""}
        </p>
      ) : null}
      {node.domains?.length ? <p className="hint">领域：{node.domains.join("、")}</p> : null}
      <div className="actions">
        {linkFrom && linkFrom.id !== node.id ? (
          <>
            <button type="button" className="small primary" disabled={!node.ref || decide.busy} onClick={() => quickAdd(linkFrom, node)}>
              与「{linkFrom.title.slice(0, 12)}」建立关联
            </button>
            <button type="button" className="small" onClick={() => props.setLinkFrom(undefined)}>
              取消
            </button>
          </>
        ) : (
          <button type="button" className="small" disabled={!node.ref || !node.owner_id} onClick={() => props.setLinkFrom(node)}>
            {linkFrom?.id === node.id ? "已选为关联起点（再点另一个节点）" : "从这里添加关联"}
          </button>
        )}
      </div>
      <ErrorBox error={decide.error} />
      {node.type === "record" ? (
        <>
          <h4>关联候选（关键词重合）</h4>
          <ErrorBox error={decide.error} />
          {!candidates.length ? <Empty>没有候选。</Empty> : null}
          <label className="hint">
            采纳为{" "}
            <select value={relation} onChange={(e) => setRelation(e.target.value)}>
              {RELATIONS.map((r) => (
                <option key={r.value} value={r.value}>
                  {r.label}
                </option>
              ))}
            </select>
          </label>
          <ul className="claims">
            {candidates.map((c, i) => (
              <li key={i}>
                <div className="claim-body">
                  {c.title} <span className="hint">得分 {c.score.toFixed(2)}</span>
                </div>
                <div className="actions">
                  <button type="button" className="small" disabled={!node.owner_id} onClick={() => judge(c, "accepted")}>
                    采纳
                  </button>
                  <button type="button" className="small" disabled={!node.owner_id} onClick={() => judge(c, "rejected")}>
                    不相关
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </Panel>
  );
}

function Notes({ focus }: { focus: GraphNode | undefined }) {
  const notes = useOp<Note[]>("relations.notes");
  const add = useAction("relations.note_add");
  const resolve = useAction("relations.note_resolve");
  const [kind, setKind] = useState<keyof typeof NOTE_KINDS>("bridge-question");
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const open = notes.data?.filter((n) => n.status === "open") ?? [];
  return (
    <Panel title={`随手笔记（${open.length}）`}>
      <p className="hint">记下分组名、想验证的桥接问题或关系设想；只保存在本机，不是研究记录。</p>
      <ErrorBox error={notes.error ?? add.error ?? resolve.error} />
      <div className="form">
        <select value={kind} onChange={(e) => setKind(e.target.value as keyof typeof NOTE_KINDS)}>
          {Object.entries(NOTE_KINDS).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
        <input placeholder="标题" value={title} onChange={(e) => setTitle(e.target.value)} />
        <textarea placeholder="内容（可选）" value={text} onChange={(e) => setText(e.target.value)} />
        <button
          type="button"
          disabled={!title.trim() || add.busy}
          onClick={async () => {
            if (await add.run({ kind, title, text, refs: focus ? [focus.id] : [] })) {
              setTitle("");
              setText("");
              notes.reload();
            }
          }}
        >
          添加{focus ? `（关联 ${focus.title.slice(0, 12)}）` : ""}
        </button>
      </div>
      <ul className="claims">
        {open.map((n) => (
          <li key={n.id}>
            <div className="claim-body">
              <Tag>{NOTE_KINDS[n.kind as keyof typeof NOTE_KINDS] ?? n.kind}</Tag> <strong>{n.title}</strong>
              {n.text ? <div className="hint">{n.text}</div> : null}
              <div className="hint">
                <Time value={n.created_at} /> {n.refs.join("、")}
              </div>
            </div>
            <div className="actions">
              {(["done", "dismissed"] as const).map((status) => (
                <button key={status} type="button" className="small" onClick={async () => (await resolve.run({ note_id: n.id, status })) && notes.reload()}>
                  {status === "done" ? "完成" : "放弃"}
                </button>
              ))}
            </div>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
