// 关系图：对象、记录、来源与作者保存的类型化关系。
// 点击节点查看详情、以它为中心展开、查看关联候选（虚线）并判断；视图与随手笔记保存在本机。
// 关联只是导航，不是证据：采纳一条关联不会让任何结论变成"已确认"。
import cytoscape from "cytoscape";
import Graph from "graphology";
import louvain from "graphology-communities-louvain";
import { useEffect, useMemo, useRef, useState } from "react";
import { useAction, useOp } from "../api";
import { KINDS, label, LEVELS } from "../labels";
import { Empty, ErrorBox, href, Loading, Panel, Tag, Time } from "../ui";

interface GraphNode {
  id: string;
  type: string;
  kind: string;
  title: string;
  level: string | null;
  owner_id: string | null;
}
interface GraphData {
  nodes: GraphNode[];
  edges: { source: string; target: string; type: string; origin: string }[];
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

const TYPE_COLORS: Record<string, string> = { owner: "#3b6ea5", record: "#6a9955", source: "#b5852b", claim: "#a05195" };
const PALETTE = ["#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f", "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac"];
const KIND_FILTERS = ["overview", "experience", "narrative", "detail", "source", "document", "document_section"];
const LEVEL_FILTERS = ["L0", "L1", "L2", "L3", "L4"];
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
  levels?: string[];
  excluded?: string[];
  communities?: boolean;
}

export function Relations() {
  const saved = useOp<View>("relations.view");
  const [view, setView] = useState<View>({ hops: 2, query: "", kinds: [], levels: [], excluded: [], communities: false });
  const [history, setHistory] = useState<string[]>([]);
  const [focus, setFocus] = useState<GraphNode>();
  const [loadedSaved, setLoadedSaved] = useState(false);
  useEffect(() => {
    if (saved.data && !loadedSaved) {
      const stored = saved.data as { center?: string | null; excluded?: string[]; filters?: View };
      setView((v) => ({ ...v, ...(stored.filters ?? {}), center: stored.center ?? null, excluded: stored.excluded ?? [] }));
      setLoadedSaved(true);
    }
  }, [saved.data, loadedSaved]);
  const graph = useOp<GraphData>("relations.graph", {
    query: view.query ?? "",
    center: view.center ?? null,
    hops: view.hops ?? 2,
    kinds: view.kinds?.length ? view.kinds : null,
    levels: view.levels?.length ? view.levels : null,
    excluded: view.excluded?.length ? view.excluded : null,
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
  const toggle = (key: "kinds" | "levels", value: string) => {
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
            <button type="button" onClick={() => setView({ hops: 2, query: "", kinds: [], levels: [], excluded: [], communities: false })}>
              重置
            </button>
          </>
        }
      >
        <div className="toolbar">
          <input placeholder="按标题筛选" value={view.query ?? ""} onChange={(e) => setView({ ...view, query: e.target.value })} />
          <label>
            展开层数{" "}
            <input type="number" min={1} max={4} value={view.hops ?? 2} onChange={(e) => setView({ ...view, hops: Number(e.target.value) })} />
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
        <div className="chips">
          {KIND_FILTERS.map((k) => (
            <label key={k} className={view.kinds?.includes(k) ? "on" : ""}>
              <input type="checkbox" checked={!!view.kinds?.includes(k)} onChange={() => toggle("kinds", k)} />
              {label(KINDS, k)}
            </label>
          ))}
          {LEVEL_FILTERS.map((l) => (
            <label key={l} className={view.levels?.includes(l) ? "on" : ""}>
              <input type="checkbox" checked={!!view.levels?.includes(l)} onChange={() => toggle("levels", l)} />
              {label(LEVELS, l)}
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
        <GraphCanvas data={graph.data} communities={communities} links={candidates.data?.candidates ?? []} onSelect={setFocus} />
        <p className="hint">
          {graph.data?.nodes.length ?? 0} 个节点、{graph.data?.edges.length ?? 0} 条关系
          {communities ? `，${new Set(Object.values(communities)).size} 个社区` : ""}。实线是作者保存的关系，虚线是待判断的候选。
        </p>
      </Panel>
      <aside>
        <Focus
          node={focus}
          candidates={candidates.data?.candidates ?? []}
          onCenter={(id) => setCenter(id)}
          onHide={(id) => setView({ ...view, excluded: [...(view.excluded ?? []), id] })}
          reloadCandidates={candidates.reload}
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

function GraphCanvas(props: {
  data: GraphData | undefined;
  communities: Record<string, number> | null;
  links: CandidateLink[];
  onSelect: (node: GraphNode | undefined) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const { data, communities, links, onSelect } = props;
  useEffect(() => {
    if (!container.current || !data || navigator.userAgent.includes("jsdom")) return;
    const known = new Set(data.nodes.map((n) => n.id));
    const cy = cytoscape({
      container: container.current,
      elements: [
        ...data.nodes.map((n) => ({
          data: {
            id: n.id,
            label: n.title.slice(0, 24),
            color: communities ? PALETTE[(communities[n.id] ?? 0) % PALETTE.length] : (TYPE_COLORS[n.type] ?? "#888"),
          },
        })),
        ...data.edges.map((e, i) => ({ data: { id: `e${i}`, source: e.source, target: e.target } })),
        ...links
          .filter((l) => known.has(l.from.target_id) && known.has(l.to.target_id))
          .map((l, i) => ({ data: { id: `c${i}`, source: l.from.target_id, target: l.to.target_id }, classes: "candidate" })),
      ],
      style: [
        { selector: "node", style: { label: "data(label)", "font-size": 9, "min-zoomed-font-size": 7, "background-color": "data(color)", width: 14, height: 14 } },
        { selector: "edge", style: { width: 1, "line-color": "#bbb", "curve-style": "bezier", "target-arrow-shape": "triangle", "arrow-scale": 0.6 } },
        { selector: "edge.candidate", style: { "line-style": "dashed", "line-color": "#d08a2c", width: 1.5, "target-arrow-shape": "none" } },
        { selector: "node:selected", style: { "border-width": 3, "border-color": "#d08a2c" } },
      ],
      layout: { name: "cose", animate: false },
    });
    cy.on("tap", "node", (event) => onSelect(data.nodes.find((n) => n.id === event.target.id())));
    return () => cy.destroy();
  }, [data, communities, links, onSelect]);
  return <div ref={container} className="graph" aria-label="关系图" />;
}

function Focus(props: {
  node: GraphNode | undefined;
  candidates: CandidateLink[];
  onCenter: (id: string) => void;
  onHide: (id: string) => void;
  reloadCandidates: () => void;
}) {
  const { node, candidates } = props;
  const decide = useAction("relations.decide");
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
          <a href={node.type === "owner" ? href("memory", node.id) : href("memory", node.owner_id, node.id)}>打开</a>
        ) : null}
      </div>
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
