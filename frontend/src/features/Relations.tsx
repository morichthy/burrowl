// 关系图：对象、记录、块、结论、概念、来源，以及按类别区分的关系（来自索引中的图投影，R12 T-P2.8）。
// 颜色区分边的类别，线型区分关联的确定度（实线=有依据，虚线=可能，点线=推测）。
// 点击节点查看详情、以它为中心展开、查看关联候选并判断，或从一个节点到另一个节点快速添加关联。
// 关联只是导航，不是证据：采纳一条关联不会让任何结论变成"已确认"。
import cytoscape from "cytoscape";
import Graph from "graphology";
import louvain from "graphology-communities-louvain";
import { useEffect, useMemo, useRef, useState } from "react";
import { useAction, useOp } from "../api";
import { KINDS, label } from "../labels";
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
const NODE_TYPES = ["owner", "record", "block", "claim", "concept", "source"];
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

  const setCenter = (id: string | null) => {
    if (view.center) setHistory((h) => [...h, view.center!]);
    setView({ ...view, center: id });
  };
  const toggle = (key: "kinds" | "nodeTypes" | "categories", value: string) => {
    const current = view[key] ?? [];
    setView({ ...view, [key]: current.includes(value) ? current.filter((v) => v !== value) : [...current, value] });
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
        <p className="hint">记录分类仅筛选记录，其他节点由节点类型控制。</p>
        <div className="chips" role="group" aria-label="记录分类">
          {KIND_FILTERS.map((k) => (
            <label key={k.value} className={view.kinds?.includes(k.value) ? "on" : ""}>
              <input type="checkbox" checked={!!view.kinds?.includes(k.value)} onChange={() => toggle("kinds", k.value)} />
              {k.label}
            </label>
          ))}
        </div>
        <p className="hint">节点类型控制图中实际显示的节点；结论（Claim）是可以单独引用和复核的判断。</p>
        <div className="chips" role="group" aria-label="节点类型">
          {NODE_TYPES.map((type) => (
            <label key={type} className={view.nodeTypes?.includes(type) ? "on" : ""}>
              <input type="checkbox" checked={!!view.nodeTypes?.includes(type)} onChange={() => toggle("nodeTypes", type)} />
              {NODE_TYPE_LABELS[type]}
            </label>
          ))}
        </div>
        <div className="chips" aria-label="边的类别">
          {CATEGORIES.map((c) => (
            <label key={c.value} className={view.categories?.includes(c.value) ? "on" : ""}>
              <input type="checkbox" checked={!!view.categories?.includes(c.value)} onChange={() => toggle("categories", c.value)} />
              <span style={{ color: c.color }}>■</span> {c.label}
            </label>
          ))}
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
        <GraphCanvas data={graph.data} communities={communities} links={candidates.data?.candidates ?? []} selectedId={focus?.id} onSelect={setFocus} />
        <p className="hint">
          {graph.data?.nodes.length ?? 0} 个节点、{graph.data?.edges.length ?? 0} 条关系
          {communities ? `，${new Set(Object.values(communities)).size} 个社区` : ""}。颜色是关系类别；关联的实线、虚线、点线分别表示有依据、可能、推测；浅色是待判断的候选。
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
  return { text: v("--text", "#1c2230"), muted: v("--muted", "#677085"), bg: v("--panel-2", "#f8f9fb"), accent: v("--accent", "#4f5bd5"), line: v("--line-strong", "#cfd4dd") };
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
        "background-color": "data(color)",
        "border-width": 0,
        color: t.text,
        "font-size": 10,
        "text-valign": "bottom",
        "text-margin-y": 4,
        "text-outline-color": t.bg,
        "text-outline-width": 2,
        "text-max-width": "140px",
        "text-wrap": "ellipsis",
        // 标签随放大逐渐出现（约 1.3 倍起），默认视图只看结构，避免文字糊成一片
        "min-zoomed-font-size": 13,
        "transition-property": "opacity, border-width, width, height",
        "transition-duration": "0.2s",
      },
    },
    {
      selector: "edge",
      style: {
        width: 1.5,
        "line-color": "data(color)",
        "line-style": "data(line)",
        "line-opacity": 0.45,
        "curve-style": "straight",
        "target-arrow-shape": "triangle",
        "target-arrow-color": "data(color)",
        "arrow-scale": 0.5,
        "transition-property": "opacity, line-opacity, width",
        "transition-duration": "0.2s",
      },
    },
    { selector: "edge.invalid", style: { "line-color": t.line, "target-arrow-color": t.line } },
    { selector: "edge.candidate", style: { "line-style": "dashed", "line-color": "#d08a2c", width: 1.5, "target-arrow-shape": "none", "line-opacity": 0.8 } },
    { selector: "node.major", style: { "font-size": 13, "min-zoomed-font-size": 5, "font-weight": 600 } },
    { selector: ".faded", style: { opacity: 0.12 } },
    { selector: "node.highlighted", style: { "min-zoomed-font-size": 0, "font-weight": 600 } },
    { selector: "edge.highlighted", style: { "line-opacity": 0.95, width: 1.6 } },
    { selector: "node:selected", style: { "border-width": 3, "border-color": t.accent, "border-opacity": 0.9, "min-zoomed-font-size": 0 } },
  ] as unknown as cytoscape.StylesheetJson;
}

/** 悬停或选中：突出节点及其直接邻居，其余淡出（不移动任何节点）。 */
function highlight(cy: cytoscape.Core, node: cytoscape.NodeSingular | undefined) {
  cy.batch(() => {
    cy.elements().removeClass("faded highlighted");
    if (!node) return;
    const near = node.closedNeighborhood();
    cy.elements().not(near).addClass("faded");
    near.addClass("highlighted");
  });
}

/** 平滑地把全部节点放进画面；节点很少时不放大到 1.6 倍以上，避免标签过大。 */
// 力导向布局参数：斥力大、边长适中，接近 Obsidian 图谱的疏朗效果；只在节点集合变化时运行
const LAYOUT = {
  name: "cose",
  animate: "end",
  animationEasing: "ease-out-cubic",
  fit: false,
  // cose 的弹簧在小图上偏硬：加大斥力、减弱弹簧（edgeElasticity 越大越弱），并把标签算进节点尺寸
  // 标签不计入节点尺寸（否则整图过散、缩放过小）；较强的 gravity 让孤立分量靠拢
  nodeRepulsion: () => 400000,
  edgeElasticity: () => 200,
  idealEdgeLength: () => 80,
  nodeOverlap: 20,
  componentSpacing: 100,
  gravity: 1,
  numIter: 1200,
};

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

const edgeKey = (e: GraphEdge) => `${e.source}→${e.target}:${e.type}`;

function GraphCanvas(props: {
  data: GraphData | undefined;
  communities: Record<string, number> | null;
  links: CandidateLink[];
  selectedId: string | undefined;
  onSelect: (node: GraphNode | undefined) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const cyRef = useRef<cytoscape.Core>(undefined);
  const nodesRef = useRef(new Map<string, GraphNode>());
  const onSelect = useRef(props.onSelect);
  onSelect.current = props.onSelect;
  const { data, communities, links, selectedId } = props;
  const inJsdom = typeof navigator !== "undefined" && navigator.userAgent.includes("jsdom");

  // 画布只创建一次：数据、着色、候选、选中都在原实例上增量更新，位置和视野不会被重置
  useEffect(() => {
    if (!container.current || inJsdom) return;
    const cy = cytoscape({ container: container.current, style: graphStyle(theme()), minZoom: 0.1, maxZoom: 4, wheelSensitivity: 0.3 });
    const selected = () => cy.nodes(":selected").first();
    cy.on("tap", "node", (event) => onSelect.current(nodesRef.current.get(event.target.id())));
    cy.on("select", "node", (event) => highlight(cy, event.target));
    cy.on("unselect", "node", () => highlight(cy, selected().nonempty() ? selected() : undefined));
    cy.on("mouseover", "node", (event) => highlight(cy, event.target));
    cy.on("mouseout", "node", () => highlight(cy, selected().nonempty() ? selected() : undefined));
    const media = window.matchMedia?.("(prefers-color-scheme: dark)");
    const restyle = () => cy.style(graphStyle(theme()));
    media?.addEventListener("change", restyle);
    cyRef.current = cy;
    return () => {
      media?.removeEventListener("change", restyle);
      cy.destroy();
      cyRef.current = undefined;
    };
  }, [inJsdom]);

  // 数据变化：按 ID 增删，已有节点保持原位；只对新节点做布局，并以动画过渡
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || !data) return;
    nodesRef.current = new Map(data.nodes.map((n) => [n.id, n]));
    const degree = new Map<string, number>();
    data.edges.forEach((e) => {
      degree.set(e.source, (degree.get(e.source) ?? 0) + 1);
      degree.set(e.target, (degree.get(e.target) ?? 0) + 1);
    });
    const edgeIds = new Map<string, GraphEdge>();
    data.edges.forEach((e) => {
      if (!nodesRef.current.has(e.source) || !nodesRef.current.has(e.target)) return;
      let id = edgeKey(e);
      for (let i = 1; edgeIds.has(id); i++) id = `${edgeKey(e)}#${i}`;
      edgeIds.set(id, e);
    });
    const fresh = new Set<string>();
    let removedNodes = 0;
    cy.batch(() => {
      const stale = cy.elements().filter((el) => (el.isNode() ? !nodesRef.current.has(el.id()) : !el.hasClass("suggest") && !edgeIds.has(el.id())));
      removedNodes = stale.nodes().length;
      stale.remove();
      for (const n of data.nodes) {
        // 节点大小随连接数增长（开方压缩，避免枢纽节点过大）
        const fields = { label: n.title.slice(0, 28), size: 14 + Math.min(30, Math.sqrt(degree.get(n.id) ?? 0) * 5) };
        const existing = cy.getElementById(n.id);
        if (existing.nonempty()) existing.data(fields);
        else {
          fresh.add(n.id);
          cy.add({ group: "nodes", data: { id: n.id, ...fields } });
        }
        // 对象节点是图的骨架，标签在较小缩放下也显示
        cy.getElementById(n.id).toggleClass("major", n.type === "owner");
      }
      for (const [id, e] of edgeIds) {
        const fields = {
          color: CATEGORIES.find((c) => c.value === e.category)?.color ?? "#bbb",
          line: e.category === "navigation" ? (LINE_STYLE[e.certainty ?? "plausible"] ?? "solid") : "solid",
        };
        const existing = cy.getElementById(id);
        if (existing.nonempty()) existing.data(fields);
        else cy.add({ group: "edges", data: { id, source: e.source, target: e.target, ...fields } });
        cy.getElementById(id)
          .toggleClass("candidate", !!e.candidate)
          .toggleClass("invalid", e.state === "invalid");
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
  }, [data]);

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
      links
        .filter((l) => cy.getElementById(l.from.target_id).nonempty() && cy.getElementById(l.to.target_id).nonempty())
        .forEach((l, i) => cy.add({ group: "edges", data: { id: `suggest-${i}`, source: l.from.target_id, target: l.to.target_id, color: "#d08a2c", line: "dashed" }, classes: "candidate suggest" }));
    });
  }, [links, data]);

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
      <div className="graph-legend" hidden={!!communities}>
        {NODE_TYPES.map((t) => (
          <span key={t}>
            <i style={{ background: TYPE_COLORS[t] }} />
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
