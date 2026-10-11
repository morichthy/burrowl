// 维护与监测：来源变化监测 → 勾选候选生成维护计划 → 交给 AI 起草处置 → 预览后执行或退回（R16）；来源登记管理。
// 观察和候选不改变任何证据或复核状态；只有维护计划提交后才产生新修订。
import { useEffect, useState } from "react";
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
  reasons?: string[][];
  // R16：候选是什么、能否放进计划修订（结论落到包含它的记录上）
  kind?: string | null;
  title?: string;
  record_id?: string | null;
  owner_id?: string | null;
  editable?: boolean;
}

interface PlanItem {
  target: { target_id: string; revision: number | null };
  owner_id: string;
  kind: string;
  title: string;
  content: string;
  formal_dependency: boolean;
  decision: { action: string; reason: string; draft?: unknown } | null;
}

interface Plan {
  plan_id: string;
  status: string;
  revision: number;
  changed_ids: string[];
  note?: string;
  items: PlanItem[];
  applied: Record<string, { ok: boolean; error?: { message: string } }>;
  thread?: { at: string; by: string; kind: string; text: string }[];
  preview?: { owner_id: string; ok: boolean; error?: { message: string } }[];
}

interface PlanSummary {
  plan_id: string;
  status: string;
  changed_ids: string[];
  note?: string;
  items: number;
  updated_at: string;
}

export function Maintenance() {
  // 候选生成计划后直接在下方打开这份计划
  const [planId, setPlanId] = useState("");
  return (
    <>
      <MonitorPanel onPlan={setPlanId} />
      <VisibilityPanel onPlan={setPlanId} />
      <PlanPanel planId={planId} setPlanId={setPlanId} />
      <RunsPanel />
      <RootsPanel />
      <SourcesPanel />
    </>
  );
}

function MonitorPanel({ onPlan }: { onPlan: (planId: string) => void }) {
  const observations = useOp<Observation[]>("monitor.observations", { limit: 10 });
  const candidates = useOp<Candidate[]>("monitor.candidates", { status: "open" });
  const run = useAction<Observation>("monitor.run");
  const resolve = useAction("monitor.resolve");
  const fromCandidates = useAction<Plan>("maintenance.plan_from_candidates");
  const [preview, setPreview] = useState<Observation>();
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const refresh = () => {
    observations.reload();
    candidates.reload();
  };
  const toggle = (id: string) =>
    setPicked((now) => {
      const next = new Set(now);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  return (
    <Panel
      title="来源变化监测"
      actions={
        <>
          <button type="button" disabled={run.busy} onClick={async () => setPreview(await run.run({ dry_run: true }))} title="只比较，不记录、不更新基线">
            预览扫描
          </button>
          <button
            type="button"
            className="primary"
            disabled={run.busy}
            title="记录本次观察、更新基线，并登记受影响的记录为维护候选"
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
      <p className="hint">
        比较各对象记忆与所引用原件的指纹。"预览扫描"只看有几处变化，不记录；"扫描并记录"会更新基线，并把受原件变化影响的记录登记为维护候选。
        观察和候选都不改动任何记录或复核状态。
      </p>
      <ErrorBox error={run.error ?? resolve.error ?? observations.error ?? fromCandidates.error} />
      {preview ? (
        <div className="notice">
          预览（未记录）：{preview.changes.length} 处变化，{preview.maintenance_candidates.length} 个受影响记录
        </div>
      ) : null}
      <h3>待处理的维护候选（{candidates.data?.length ?? 0}）</h3>
      {candidates.data && !candidates.data.length ? <Empty>没有待处理的候选。</Empty> : null}
      {candidates.data?.length ? (
        <div className="batchbar">
          <span>已选 {picked.size} 个</span>
          <button
            type="button"
            className="small primary"
            disabled={!picked.size || fromCandidates.busy}
            onClick={async () => {
              const plan = await fromCandidates.run({ target_ids: [...picked] });
              if (plan) {
                setPicked(new Set());
                candidates.reload();
                onPlan(plan.plan_id);
              }
            }}
          >
            为所选生成维护计划
          </button>
        </div>
      ) : null}
      <ul className="claims">
        {candidates.data?.map((c) => (
          <li key={c.target_id}>
            <input
              type="checkbox"
              aria-label={`选择 ${c.target_id}`}
              checked={picked.has(c.target_id)}
              disabled={c.editable === false}
              onChange={() => toggle(c.target_id)}
            />
            <div className="claim-body">
              <Tag>{c.kind === "claim" ? "结论" : label(KINDS, c.kind ?? "")}</Tag>
              {c.record_id && c.owner_id ? <a href={href("memory", c.owner_id, c.record_id)}>{c.title}</a> : <span>{c.title}</span>}{" "}
              <code>{c.target_id}</code>
              <div className="hint">
                首次发现 <Time value={c.first_seen} />
                {c.reasons?.[0]?.length ? ` · 原因：${c.reasons[0].join(" → ")}` : ""}
              </div>
              {c.editable === false ? <div className="hint">这一项不能修订（如 L0 原件记录），确认后点"无需处理"即可。</div> : null}
            </div>
            <button
              type="button"
              className="small"
              onClick={async () => (await resolve.run({ target_id: c.target_id, status: "dismissed" })) && candidates.reload()}
            >
              无需处理
            </button>
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

interface VisibilityCandidate {
  record_id: string;
  owner_id: string;
  kind: string;
  level: string | null;
  title: string;
  updated_at: string;
}

// R19 §8：之前按旧默认存成"只在本对象可见"的知识记录。勾选后生成计划（处置已写好：改为全库可见、正文不变），
// 在下方预览后执行；没勾选的保持原样。
function VisibilityPanel({ onPlan }: { onPlan: (planId: string) => void }) {
  const candidates = useOp<VisibilityCandidate[]>("maintenance.visibility_candidates", {});
  const generate = useAction<Plan>("maintenance.plan_visibility");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const rows = candidates.data ?? [];
  if (!rows.length && !candidates.error) return null;
  const toggle = (id: string) =>
    setPicked((now) => {
      const next = new Set(now);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  return (
    <Panel title={`可见范围补齐（${rows.length}）`}>
      <p className="hint">
        这些知识记录保存时取了旧的默认值"只在本对象可见"，在跨项目的检索和联想里找不到。现在知识记录默认全库可见。
        勾选要公开的记录生成计划，预览后执行；需要保密的不要勾选。对象整体设为保密的不会列在这里。
      </p>
      <ErrorBox error={candidates.error ?? generate.error} />
      <div className="batchbar">
        <span>已选 {picked.size} 个</span>
        <button type="button" className="small" onClick={() => setPicked(new Set(rows.map((r) => r.record_id)))}>
          全选
        </button>
        <button
          type="button"
          className="small primary"
          disabled={!picked.size || generate.busy}
          onClick={async () => {
            const plan = await generate.run({ target_ids: [...picked] });
            if (plan) {
              setPicked(new Set());
              candidates.reload();
              onPlan(plan.plan_id);
            }
          }}
        >
          为所选生成补齐计划
        </button>
      </div>
      <ul className="claims">
        {rows.map((r) => (
          <li key={r.record_id}>
            <input
              type="checkbox"
              aria-label={`选择 ${r.record_id}`}
              checked={picked.has(r.record_id)}
              onChange={() => toggle(r.record_id)}
            />
            <div className="claim-body">
              <Tag>{label(KINDS, r.kind)}</Tag> <a href={href("memory", r.owner_id, r.record_id)}>{r.title}</a>{" "}
              <code>{r.owner_id}</code>
              <div className="hint">
                更新于 <Time value={r.updated_at} />
              </div>
            </div>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

// R16 D-02：计划状态的中文名
const PLAN_STATUS: Record<string, { label: string; tone: string }> = {
  planned: { label: "已生成", tone: "muted" },
  queued: { label: "AI 处理中", tone: "muted" },
  reviewed: { label: "待你确认", tone: "warn" },
  applied: { label: "已执行", tone: "good" },
  partial: { label: "部分完成", tone: "bad" },
};
const ACTION_LABELS: Record<string, { label: string; tone: string }> = {
  retain: { label: "保留", tone: "good" },
  revise: { label: "修订", tone: "warn" },
  resynthesize: { label: "重新综合", tone: "warn" },
  retract: { label: "撤回", tone: "bad" },
  defer: { label: "暂缓", tone: "muted" },
};

function PlanPanel({ planId, setPlanId }: { planId: string; setPlanId: (id: string) => void }) {
  const plans = useOp<PlanSummary[]>("maintenance.plans", {});
  const plan = useOp<Plan>("maintenance.status", { plan_id: planId }, !!planId);
  const make = useAction<Plan>("maintenance.plan");
  const handoff = useAction("maintenance.handoff");
  const apply = useAction<{ status: string; applied: Record<string, { ok: boolean; error?: { message: string } }> }>("maintenance.apply");
  const [changed, setChanged] = useState("");
  const refresh = () => {
    plans.reload();
    plan.reload();
  };
  // AI 在对话中写好处置后，计划会从"AI 处理中"变为"待你确认"：与其他页面一样每 5 秒刷新
  const reloadPlans = plans.reload;
  const reloadPlan = plan.reload;
  useEffect(() => {
    const timer = window.setInterval(() => {
      reloadPlans();
      if (planId) reloadPlan();
    }, 5_000);
    return () => window.clearInterval(timer);
  }, [reloadPlans, reloadPlan, planId]);
  const p = plan.data;
  return (
    <Panel title="语义维护计划">
      <p className="hint">
        原件变化或出现新反证时，列出受影响记录，逐条决定保留、修订、重新综合、撤回或暂缓。处置和修订稿由 AI 起草：把计划"交给 AI"后，AI
        在下次对话中写好处置，计划变为"待你确认"；你在这里看过预览后"执行"，或附意见"退回"。只有执行后才写入记忆。
      </p>
      <ErrorBox error={plans.error ?? make.error ?? plan.error ?? handoff.error ?? apply.error} />
      {plans.data?.length ? (
        <ul className="plan-list">
          {plans.data.map((s) => (
            <li key={s.plan_id} className={s.plan_id === planId ? "on" : ""}>
              <button type="button" className="link" onClick={() => setPlanId(s.plan_id)}>
                {s.changed_ids.length ? s.changed_ids.join("、") : s.note || s.plan_id}
              </button>
              <Tag tone={PLAN_STATUS[s.status]?.tone}>{PLAN_STATUS[s.status]?.label ?? s.status}</Tag>
              <span className="hint">
                {s.items} 条 · <Time value={s.updated_at} />
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <Empty>还没有维护计划。可以在上方勾选维护候选生成，也可以手动输入变化的 ID。</Empty>
      )}
      <details>
        <summary>手动输入变化的记录或来源 ID 生成计划</summary>
        <div className="row">
          <input placeholder="逗号分隔" value={changed} onChange={(e) => setChanged(e.target.value)} aria-label="变化的 ID" />
          <button
            type="button"
            disabled={!changed.trim() || make.busy}
            onClick={async () => {
              const created = await make.run({ changed_ids: changed.split(/[,，\s]+/).filter(Boolean) });
              if (created) {
                setChanged("");
                setPlanId(created.plan_id);
                plans.reload();
              }
            }}
          >
            生成计划
          </button>
        </div>
      </details>

      {p ? (
        <section className="plan-detail">
          <h3>
            <Tag tone={PLAN_STATUS[p.status]?.tone}>{PLAN_STATUS[p.status]?.label ?? p.status}</Tag>{" "}
            {p.changed_ids.length ? `变化：${p.changed_ids.join("、")}` : p.note}{" "}
            <code>{p.plan_id}</code>
          </h3>
          {(p.thread ?? []).slice(-3).map((e, i) => (
            <div key={i} className="hint">
              <Time value={e.at} /> {e.by === "ai" ? "AI" : e.by === "human" ? "你" : "系统"}：{e.text}
            </div>
          ))}
          {p.items.map((item) => (
            <PlanItemView key={item.target.target_id} item={item} />
          ))}
          {p.preview?.some((r) => !r.ok) ? (
            <div className="error">
              预检未通过：
              {p.preview
                .filter((r) => !r.ok)
                .map((r) => `${r.owner_id}：${r.error?.message ?? "未知错误"}`)
                .join("；")}
            </div>
          ) : null}
          {Object.entries(p.applied ?? {}).some(([, v]) => !v.ok) ? (
            <div className="error">
              写入失败：
              {Object.entries(p.applied)
                .filter(([, v]) => !v.ok)
                .map(([k, v]) => `${k}：${v.error?.message ?? ""}`)
                .join("；")}
            </div>
          ) : null}
          <div className="actions">
            {p.status === "planned" ? (
              <button type="button" className="primary" disabled={handoff.busy} onClick={async () => (await handoff.run({ plan_id: p.plan_id, note: "" })) && refresh()}>
                交给 AI
              </button>
            ) : null}
            {p.status === "queued" ? <span className="hint">AI 会在下次对话中写好处置；也可以直接对 AI 说"处理维护计划"。</span> : null}
            {p.status === "reviewed" || p.status === "partial" ? (
              <>
                <button
                  type="button"
                  className="primary"
                  disabled={apply.busy || p.preview?.some((r) => !r.ok)}
                  onClick={async () => (await apply.run({ plan_id: p.plan_id, expected_revision: p.revision })) && refresh()}
                >
                  {p.status === "partial" ? "重试执行" : "执行"}
                </button>
                <button
                  type="button"
                  disabled={handoff.busy}
                  onClick={async () => {
                    const note = window.prompt("退回给 AI 的意见", "");
                    if (note !== null && (await handoff.run({ plan_id: p.plan_id, note }))) refresh();
                  }}
                >
                  退回
                </button>
              </>
            ) : null}
          </div>
        </section>
      ) : null}
    </Panel>
  );
}

function PlanItemView({ item }: { item: PlanItem }) {
  const decision = item.decision;
  const action = decision ? ACTION_LABELS[decision.action] : undefined;
  const draft = decision?.draft as { title?: string; body_markdown?: string } | undefined;
  return (
    <article className="plan-item">
      <header>
        {action ? <Tag tone={action.tone}>{action.label}</Tag> : <Tag>待处置</Tag>}
        <a href={href("memory", item.owner_id, item.target.target_id)}>{item.title}</a> <Tag>{label(KINDS, item.kind)}</Tag>
        {item.formal_dependency ? <Tag tone="warn">正式依赖</Tag> : null}
      </header>
      {decision ? <div className="note-ai">理由：{decision.reason}</div> : null}
      {draft ? (
        <div className="plan-draft">
          <div className="hint">修订后{draft.title && draft.title !== item.title ? `（标题改为「${draft.title}」）` : ""}：</div>
          <Markdown text={draft.body_markdown || "（正文为空，内容在结构化字段中）"} />
        </div>
      ) : null}
      {item.content ? (
        <details>
          <summary>当前正文</summary>
          <Markdown text={item.content} />
        </details>
      ) : null}
    </article>
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

interface RunRow {
  run_id: string;
  title: string;
  status: string | null;
  owner_id: string | null;
  ended_at: string | null;
  path: string;
}

interface TrashedRun {
  run_id: string;
  original_path: string;
  reason: string;
  discarded_at: string;
}

// R14：Run 是执行证据，不进检索。这里列出还没被任何 L1–L4 记录引用的 Run（知识尚未写成记录），
// 以及回收站里已丢弃的 Run。丢弃与恢复可撤销；清空回收站是永久删除，只能在这里由用户执行。
function RunsPanel() {
  const unfixed = useOp<RunRow[]>("runs.search", { unfixed_only: true, limit: 200 });
  const trash = useOp<TrashedRun[]>("runs.trash");
  const discard = useAction("runs.discard");
  const restore = useAction("runs.restore");
  const empty = useAction("runs.empty_trash");
  const reload = () => {
    unfixed.reload();
    trash.reload();
  };
  const rows = unfixed.data ?? [];
  const trashed = trash.data ?? [];
  return (
    <Panel title={`未写成记录的 Run（${rows.length}）`}>
      <p className="hint">
        Run 只是执行证据：执行得到的知识要写进对象的记录并引用它。下面的 Run 还没被任何 L1–L4 记录引用；没有知识的可以丢弃到回收站（可恢复）。
      </p>
      <ErrorBox error={unfixed.error ?? trash.error ?? discard.error ?? restore.error ?? empty.error} />
      <Loading show={unfixed.loading} />
      {!unfixed.loading && unfixed.data && rows.length === 0 ? <Empty>所有 Run 都已被记录引用。</Empty> : null}
      <table className="table">
        <tbody>
          {rows.map((r) => (
            <tr key={r.run_id}>
              <td>
                <code>{r.run_id}</code>
                <div>{r.title}</div>
              </td>
              <td className="hint">
                {r.owner_id ? <a href={href("memory", r.owner_id)}>{r.owner_id}</a> : "独立 Run"} · {r.status ?? "—"}
                {r.ended_at ? (
                  <>
                    {" "}
                    · <Time value={r.ended_at} />
                  </>
                ) : null}
              </td>
              <td>
                <button
                  type="button"
                  className="small"
                  disabled={discard.busy}
                  onClick={async () => {
                    const reason = window.prompt(`丢弃 ${r.run_id} 的理由（例如：临时测试，没有可复用的内容）`);
                    if (reason?.trim() && (await discard.run({ run_id: r.run_id, reason }))) reload();
                  }}
                >
                  丢弃到回收站
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <h4>回收站（{trashed.length}）</h4>
      {trashed.length === 0 ? <p className="hint">回收站是空的。</p> : null}
      <table className="table">
        <tbody>
          {trashed.map((t) => (
            <tr key={t.run_id}>
              <td>
                <code>{t.run_id}</code>
                <div className="hint">{t.original_path}</div>
              </td>
              <td className="hint">
                {t.reason} · <Time value={t.discarded_at} />
              </td>
              <td>
                <button
                  type="button"
                  className="small"
                  disabled={restore.busy}
                  onClick={async () => (await restore.run({ run_id: t.run_id })) && reload()}
                >
                  恢复
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {trashed.length ? (
        <button
          type="button"
          className="small danger"
          disabled={empty.busy}
          onClick={async () => {
            if (window.confirm(`永久删除回收站中的 ${trashed.length} 个 Run？此操作不能撤销。`) && (await empty.run({})))
              reload();
          }}
        >
          清空回收站（永久删除）
        </button>
      ) : null}
    </Panel>
  );
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

// 敏感级别的中文说明；restricted 与停用效果相同：不能读取，依据它的记录被隐去
const SENSITIVITY = [
  ["public", "public 公开"],
  ["internal", "internal 内部"],
  ["confidential", "confidential 机密"],
  ["restricted", "restricted 受限（不可读取）"],
] as const;

function SourcesPanel() {
  const sources = useOp<SourceEntry[]>("sources.list");
  const update = useAction("sources.update");
  const [filter, setFilter] = useState("");
  const rows = (sources.data ?? []).filter((s) => !filter || (s.source_id + s.location + s.title).includes(filter));
  return (
    <Panel title={`来源登记（${sources.data?.length ?? 0}）`}>
      <p className="hint">
        原始文件只有在这里登记、启用且未限制时才能被引用和读取。停用或设为 restricted 后，读取时被隐去的记录在检索和关系图中也会同时隐去；恢复后重新出现。
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
                  {SENSITIVITY.map(([v, text]) => (
                    <option key={v} value={v}>
                      {text}
                    </option>
                  ))}
                </select>
              </td>
              <td>
                {/* 先写当前状态，再给操作按钮：按钮上的字是"要做的事"，单独出现时容易被读成状态 */}
                <div className="source-state">
                  {!s.enabled ? (
                    <Tag tone="bad">已停用</Tag>
                  ) : s.sensitivity === "restricted" ? (
                    <Tag tone="warn">受限</Tag>
                  ) : (
                    <Tag tone="good">可读取</Tag>
                  )}
                  <button
                    type="button"
                    className="small"
                    aria-label={`${s.enabled ? "停用" : "重新启用"} ${s.source_id}`}
                    title={s.enabled ? "停用后不能读取，依据它的记录在检索和关系图中隐去" : "恢复读取，依据它的记录重新出现"}
                    onClick={async () => (await update.run({ source_id: s.source_id, enabled: !s.enabled })) && sources.reload()}
                  >
                    {s.enabled ? "停用" : "重新启用"}
                  </button>
                </div>
                {s.enabled && s.sensitivity === "restricted" ? <div className="hint">敏感级别为 restricted，不可读取</div> : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}
