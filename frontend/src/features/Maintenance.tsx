// 维护与监测：来源变化监测 → 维护候选 → 语义维护计划（逐项处置 → 预检 → 提交）；来源登记管理。
// 观察和候选不改变任何证据或复核状态；只有维护计划提交后才产生新修订。
import { useState } from "react";
import { useAction, useOp } from "../api";
import { KINDS, label } from "../labels";
import { Empty, ErrorBox, href, Loading, Markdown, Panel, Tag, Time } from "../ui";

interface Observation {
  at: string;
  changes: { kind: string; target_id: string }[];
  errors: { target_id: string; code: string }[];
  maintenance_candidates: string[];
  baseline_updated: boolean;
  dry_run?: boolean;
}

interface Candidate {
  target_id: string;
  status: string;
  first_seen: string;
  last_seen: string;
}

interface PlanItem {
  target: { target_id: string; revision: number | null };
  owner_id: string;
  kind: string;
  title: string;
  content: string;
  formal_dependency: boolean;
  decision: { action: string; reason: string } | null;
}

interface Plan {
  plan_id: string;
  status: string;
  revision: number;
  changed_ids: string[];
  items: PlanItem[];
  applied: Record<string, unknown>;
}

const ACTIONS = [
  { value: "retain", label: "保留（依据变化不影响）" },
  { value: "revise", label: "修订（需提供新草案）" },
  { value: "resynthesize", label: "重新综合（需提供新草案）" },
  { value: "retract", label: "撤回" },
  { value: "defer", label: "暂缓" },
];

export function Maintenance() {
  return (
    <>
      <MonitorPanel />
      <PlanPanel />
      <RootsPanel />
      <SourcesPanel />
    </>
  );
}

function MonitorPanel() {
  const observations = useOp<Observation[]>("monitor.observations", { limit: 10 });
  const candidates = useOp<Candidate[]>("monitor.candidates", { status: "open" });
  const run = useAction<Observation>("monitor.run");
  const resolve = useAction("monitor.resolve");
  const [preview, setPreview] = useState<Observation>();
  const refresh = () => {
    observations.reload();
    candidates.reload();
  };
  return (
    <Panel
      title="来源变化监测"
      actions={
        <>
          <button type="button" disabled={run.busy} onClick={async () => setPreview(await run.run({ dry_run: true }))}>
            预览扫描
          </button>
          <button
            type="button"
            className="primary"
            disabled={run.busy}
            onClick={async () => {
              if (await run.run({ dry_run: false })) {
                setPreview(undefined);
                refresh();
              }
            }}
          >
            扫描并记录
          </button>
        </>
      }
    >
      <p className="hint">比较各对象记忆与所引用原件的指纹。观察只提示“需要关注”，不改动任何记录或复核状态。</p>
      <ErrorBox error={run.error ?? resolve.error ?? observations.error} />
      {preview ? (
        <div className="notice">
          预览（未记录）：{preview.changes.length} 处变化，{preview.maintenance_candidates.length} 个受影响记录
        </div>
      ) : null}
      <h3>待处理的维护候选（{candidates.data?.length ?? 0}）</h3>
      {candidates.data && !candidates.data.length ? <Empty>没有待处理的候选。</Empty> : null}
      <ul className="claims">
        {candidates.data?.map((c) => (
          <li key={c.target_id}>
            <div className="claim-body">
              <code>{c.target_id}</code> <span className="hint">首次发现 <Time value={c.first_seen} /></span>
            </div>
            <div className="actions">
              {(["planned", "dismissed"] as const).map((status) => (
                <button
                  key={status}
                  type="button"
                  className="small"
                  onClick={async () => (await resolve.run({ target_id: c.target_id, status })) && candidates.reload()}
                >
                  {status === "planned" ? "已纳入计划" : "无需处理"}
                </button>
              ))}
            </div>
          </li>
        ))}
      </ul>
      <details>
        <summary>最近观察</summary>
        <Loading show={observations.loading} />
        <ul>
          {observations.data?.map((o) => (
            <li key={o.at}>
              <Time value={o.at} /> 变化 {o.changes.length} · 候选 {o.maintenance_candidates.length} · 错误 {o.errors.length}
              {o.baseline_updated ? "" : "（扫描不完整，未更新基线）"}
            </li>
          ))}
        </ul>
      </details>
    </Panel>
  );
}

function PlanPanel() {
  const [changed, setChanged] = useState("");
  const [planId, setPlanId] = useState("");
  const make = useAction<Plan>("maintenance.plan");
  const plan = useOp<Plan>("maintenance.status", { plan_id: planId }, !!planId);
  const review = useAction<{ revision: number; checks?: unknown }>("maintenance.review");
  const apply = useAction<{ status: string; applied: Record<string, unknown> }>("maintenance.apply");
  const [decisions, setDecisions] = useState<Record<string, { action: string; reason: string; draft: string }>>({});
  const [result, setResult] = useState<unknown>();

  const submitReview = async () => {
    if (!plan.data) return;
    const list = Object.entries(decisions).map(([target_id, d]) => {
      const draft = d.draft.trim() ? JSON.parse(d.draft) : undefined;
      return { target_id, action: d.action, reason: d.reason, ...(draft ? { draft } : {}) };
    });
    const reviewed = await review.run({ plan_id: plan.data.plan_id, decisions: list, expected_revision: plan.data.revision });
    if (reviewed) {
      setResult(reviewed);
      plan.reload();
    }
  };

  return (
    <Panel title="语义维护计划">
      <p className="hint">
        来源变化或出现新反证时，列出受影响记录及其正文，逐项决定：保留、修订、重新综合、撤回或暂缓。修订需要新的记录草案（通常由
        AI 按 consolidate 流程写好）。提交按对象逐个进行，可能部分完成。
      </p>
      <div className="row">
        <input
          placeholder="变化的记录或来源 ID，逗号分隔（可从上方候选复制）"
          value={changed}
          onChange={(e) => setChanged(e.target.value)}
        />
        <button
          type="button"
          className="primary"
          disabled={!changed.trim() || make.busy}
          onClick={async () => {
            const created = await make.run({ changed_ids: changed.split(/[,，\s]+/).filter(Boolean) });
            if (created) setPlanId(created.plan_id);
          }}
        >
          生成计划
        </button>
        <input placeholder="或输入已有计划 ID（MP-…）" value={planId} onChange={(e) => setPlanId(e.target.value.trim())} />
      </div>
      <ErrorBox error={make.error ?? plan.error ?? review.error ?? apply.error} />
      {plan.data ? (
        <>
          <p>
            <Tag>{plan.data.status}</Tag> 第 {plan.data.revision} 版 · 变化：{plan.data.changed_ids.join("、")}
          </p>
          {plan.data.items.map((item) => {
            const id = item.target.target_id;
            const d = decisions[id] ?? { action: item.decision?.action ?? "retain", reason: item.decision?.reason ?? "", draft: "" };
            const set = (patch: Partial<typeof d>) => setDecisions({ ...decisions, [id]: { ...d, ...patch } });
            return (
              <article key={id} className="owner-card">
                <header>
                  <h3>
                    <a href={href("memory", item.owner_id, id)}>{item.title}</a> <Tag>{label(KINDS, item.kind)}</Tag>
                    {item.formal_dependency ? <Tag tone="warn">正式依赖</Tag> : null}
                  </h3>
                </header>
                {item.content ? (
                  <details>
                    <summary>当前正文</summary>
                    <Markdown text={item.content} />
                  </details>
                ) : null}
                <div className="form">
                  <label>
                    处置
                    <select value={d.action} onChange={(e) => set({ action: e.target.value })}>
                      {ACTIONS.map((a) => (
                        <option key={a.value} value={a.value}>
                          {a.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    理由
                    <input value={d.reason} onChange={(e) => set({ reason: e.target.value })} />
                  </label>
                  {d.action === "revise" || d.action === "resynthesize" ? (
                    <label>
                      新草案（JSON，字段见 memory.contract）
                      <textarea value={d.draft} onChange={(e) => set({ draft: e.target.value })} />
                    </label>
                  ) : null}
                </div>
              </article>
            );
          })}
          <div className="actions">
            <button type="button" disabled={review.busy} onClick={submitReview}>
              记录处置并预检
            </button>
            <button
              type="button"
              className="primary"
              disabled={apply.busy || plan.data.status === "planned"}
              onClick={async () => {
                const applied = await apply.run({ plan_id: plan.data!.plan_id, expected_revision: plan.data!.revision });
                if (applied) {
                  setResult(applied);
                  plan.reload();
                }
              }}
            >
              提交修订
            </button>
          </div>
          {result ? <pre className="notice">{JSON.stringify(result, null, 2)}</pre> : null}
        </>
      ) : null}
    </Panel>
  );
}

interface SourceEntry {
  source_id: string;
  root?: string;
  path: string;
  location: string;
  title: string;
  enabled: boolean;
  sensitivity: string;
  sha256: string;
  relocated_from?: string[];
}

interface WorkRoot {
  root_id: string;
  path: string;
  title: string;
  relocated_from?: string[];
}

function RootsPanel() {
  const roots = useOp<{ roots: WorkRoot[]; work_dir: string | null }>("sources.roots");
  const relocate = useAction("sources.relocate_root");
  const [target, setTarget] = useState<Record<string, string>>({});
  const list = roots.data?.roots ?? [];
  return (
    <Panel title={`工作目录（${list.length}）`}>
      <p className="hint">
        原始数据和代码留在工作目录里，记录只引用它们。整个工作目录搬家后，在这里填新位置：程序逐个核对文件指纹，全部一致才改指向，已有引用不变。
      </p>
      <ErrorBox error={roots.error ?? relocate.error} />
      {list.length === 0 ? <p className="hint">还没有登记工作目录（登记工作目录里的文件时会自动建立）。</p> : null}
      <table className="table">
        <tbody>
          {list.map((r) => (
            <tr key={r.root_id}>
              <td>
                <code>{r.root_id}</code>
                <div className="hint">{r.title}</div>
              </td>
              <td className="hint">
                {r.path}
                {r.relocated_from?.length ? <div>原位置：{r.relocated_from.join("、")}</div> : null}
              </td>
              <td>
                <input
                  placeholder="新位置（绝对路径）"
                  value={target[r.root_id] ?? ""}
                  onChange={(e) => setTarget({ ...target, [r.root_id]: e.target.value })}
                />
                <button
                  type="button"
                  className="small"
                  disabled={!target[r.root_id]}
                  onClick={async () =>
                    (await relocate.run({ root_id: r.root_id, new_path: target[r.root_id] })) && roots.reload()
                  }
                >
                  核对并改指向
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}

function SourcesPanel() {
  const sources = useOp<SourceEntry[]>("sources.list");
  const update = useAction("sources.update");
  const [filter, setFilter] = useState("");
  const rows = (sources.data ?? []).filter((s) => !filter || (s.source_id + s.location + s.title).includes(filter));
  return (
    <Panel title={`来源登记（${sources.data?.length ?? 0}）`}>
      <p className="hint">
        原始文件只有在这里登记、启用且未限制时才能被引用和读取。停用或设为 restricted 后，依据它的记录会在读取时被隐去。
      </p>
      <div className="toolbar">
        <input placeholder="按 ID、路径或标题筛选" value={filter} onChange={(e) => setFilter(e.target.value)} />
      </div>
      <ErrorBox error={sources.error ?? update.error} />
      <table className="table">
        <thead>
          <tr>
            <th>来源</th>
            <th>路径</th>
            <th>敏感级别</th>
            <th>状态</th>
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 200).map((s) => (
            <tr key={s.source_id}>
              <td>
                <code>{s.source_id}</code>
                <div className="hint">{s.title}</div>
              </td>
              <td className="hint">
                {s.location}
                {s.root ? <div>工作目录 {s.root} 内</div> : null}
                {s.relocated_from?.length ? <div>原位置：{s.relocated_from.join("、")}</div> : null}
              </td>
              <td>
                <select
                  value={s.sensitivity}
                  onChange={async (e) =>
                    (await update.run({ source_id: s.source_id, sensitivity: e.target.value as "internal" })) && sources.reload()
                  }
                >
                  {["public", "internal", "confidential", "restricted"].map((v) => (
                    <option key={v}>{v}</option>
                  ))}
                </select>
              </td>
              <td>
                <button
                  type="button"
                  className={`small ${s.enabled ? "" : "primary"}`}
                  onClick={async () => (await update.run({ source_id: s.source_id, enabled: !s.enabled })) && sources.reload()}
                >
                  {s.enabled ? "停用" : "启用"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}
