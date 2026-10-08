// 系统记忆：对象列表 → 对象（按层级的记录、文稿、时间线） → 单条记录（正文、结论复核徽标、依据）。
import { useMemo, useState } from "react";
import { useOp } from "../api";
import { KINDS, label, LEVELS, OWNER_TYPES } from "../labels";
import { Empty, ErrorBox, href, Loading, Markdown, Panel, ReviewBadge, Tag, Time } from "../ui";
import type { QueueItem } from "./Review";

interface OwnerSummary {
  owner_id: string;
  owner_type: string;
  title: string;
  persisted: boolean;
  sensitivity: string;
  counts?: Record<string, number>;
}

export function Owners() {
  const [query, setQuery] = useState("");
  const owners = useOp<{ groups: { owner_type: string; owners: OwnerSummary[] }[] }>("memory.owners", { query });
  return (
    <Panel title="系统记忆">
      <div className="toolbar">
        <input placeholder="按名称或 ID 筛选" value={query} onChange={(e) => setQuery(e.target.value)} />
      </div>
      <ErrorBox error={owners.error} />
      <Loading show={owners.loading && !owners.data} />
      {owners.data?.groups.map((g) => (
        <section key={g.owner_type}>
          <h3>{label(OWNER_TYPES, g.owner_type)}</h3>
          <ul className="owners">
            {g.owners.map((o) => (
              <li key={o.owner_id}>
                <a href={href("memory", o.owner_id)}>{o.title}</a> <code>{o.owner_id}</code>
                {!o.persisted ? <Tag>尚无记忆</Tag> : null}
                {o.counts ? (
                  <span className="hint">
                    {Object.entries(o.counts)
                      .filter(([, n]) => n)
                      .map(([k, n]) => `${k} ${n}`)
                      .join(" · ")}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </Panel>
  );
}

interface RecordRow {
  record_id: string;
  kind: string;
  level: string | null;
  title: string;
  revision: number;
  updated_at: string;
  claims: string[];
}

function useClaimBadges(ownerId: string) {
  const queue = useOp<{ items: QueueItem[] }>("review.queue", {
    owner_id: ownerId,
    states: ["not-reviewed", "stale", "disputed", "upstream-blocked", "accepted", "retracted", "superseded"],
    limit: 500,
  });
  return useMemo(() => new Map((queue.data?.items ?? []).map((i) => [i.claim_id, i.status])), [queue.data]);
}

export function OwnerView({ ownerId }: { ownerId: string }) {
  const [tab, setTab] = useState<"records" | "document" | "raw" | "timeline">("records");
  const [mainOnly, setMainOnly] = useState(true);
  const records = useOp<{ owner: OwnerSummary; head: { generation: number } | null; items: RecordRow[]; total: number }>(
    "memory.records",
    { owner_id: ownerId, main_only: mainOnly, limit: 500 },
  );
  const badges = useClaimBadges(ownerId);
  const owner = records.data?.owner;
  return (
    <>
      <p>
        <a href={href("memory")}>← 全部对象</a>
      </p>
      <Panel
        title={owner ? owner.title : ownerId}
        actions={
          <>
            {owner ? <Tag>{label(OWNER_TYPES, owner.owner_type)}</Tag> : null}
            {records.data?.head ? <Tag>第 {records.data.head.generation} 次提交</Tag> : null}
          </>
        }
      >
        <div className="tabs" role="tablist">
          {(
            [
              ["records", "记录"],
              ["document", "文稿"],
              ["raw", "原始材料"],
              ["timeline", "时间线"],
            ] as const
          ).map(([key, text]) => (
            <button key={key} type="button" role="tab" aria-selected={tab === key} className={tab === key ? "on" : ""} onClick={() => setTab(key)}>
              {text}
            </button>
          ))}
        </div>
        <ErrorBox error={records.error} />
        {tab === "records" ? (
          <>
            <label className="hint">
              <input type="checkbox" checked={!mainOnly} onChange={() => setMainOnly(!mainOnly)} /> 显示目标、检查点、复核等工作流记录
            </label>
            <Loading show={records.loading && !records.data} />
            {records.data && !records.data.items.length ? <Empty>这个对象还没有记忆记录。</Empty> : null}
            <table className="table">
              <thead>
                <tr>
                  <th>层级</th>
                  <th>标题</th>
                  <th>类型</th>
                  <th>结论</th>
                  <th>更新</th>
                </tr>
              </thead>
              <tbody>
                {records.data?.items.map((r) => (
                  <tr key={r.record_id}>
                    <td>{r.level ?? "—"}</td>
                    <td>
                      <a href={href("memory", ownerId, r.record_id)}>{r.title}</a>
                    </td>
                    <td>{label(KINDS, r.kind)}</td>
                    <td>
                      {r.claims.map((c) => (
                        <ReviewBadge key={c} value={badges.get(c) ?? "not-reviewed"} />
                      ))}
                    </td>
                    <td>
                      <Time value={r.updated_at} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        ) : null}
        {tab === "document" ? <DocumentView ownerId={ownerId} /> : null}
        {tab === "raw" ? <RawMaterials ownerId={ownerId} /> : null}
        {tab === "timeline" ? <Timeline ownerId={ownerId} /> : null}
      </Panel>
    </>
  );
}

interface Block {
  type: "prose" | "unit";
  markdown?: string;
  record?: { title: string; record_id: string } | null;
  resolved_blocks?: { block_id: string; markdown: string }[];
  source_issues: { code: string }[];
}

function DocumentView({ ownerId }: { ownerId: string }) {
  const [type, setType] = useState("research_process");
  const [mode, setMode] = useState<"full" | "outline" | "impact">("full");
  const doc = useOp<{
    document: { title: string; purpose: string; document_type: string } | null;
    report: { sections: { section_id: string; title: string; blocks: Block[]; source_issues: { code: string }[] }[]; complete: boolean } | null;
    missing: { message: string }[];
    candidates: { record_id: string; document_type: string; title: string }[];
  }>("memory.document", { owner_id: ownerId, view: "full", document_type: type as "research_process" }, mode === "full");
  return (
    <div>
      <div className="toolbar">
        <select value={mode} onChange={(e) => setMode(e.target.value as "full")} aria-label="文稿视图">
          <option value="full">全文</option>
          <option value="outline">目录</option>
          <option value="impact">依据变化影响</option>
        </select>
        <select value={type} onChange={(e) => setType(e.target.value)} aria-label="文稿类型">
          <option value="research_process">研究过程稿</option>
          <option value="research_report">研究报告</option>
        </select>
        {doc.data?.report && !doc.data.report.complete ? <Tag tone="warn">部分依据不可用</Tag> : null}
      </div>
      {mode !== "full" ? <DocumentAux ownerId={ownerId} type={type} mode={mode} /> : null}
      <ErrorBox error={mode === "full" ? doc.error : undefined} />
      <Loading show={doc.loading} />
      {mode === "full" && doc.data?.missing.map((m, i) => (
        <div key={i} className="notice">
          {m.message}
        </div>
      ))}
      {mode === "full" && doc.data?.document ? (
        <article className="document">
          <h2>{doc.data.document.title}</h2>
          <p className="hint">{doc.data.document.purpose}</p>
          {doc.data.report?.sections.map((s) => (
            <section key={s.section_id}>
              <h3>{s.title}</h3>
              {s.source_issues.length ? <Tag tone="bad">依据不可用：{s.source_issues.map((i) => i.code).join("、")}</Tag> : null}
              {s.blocks.map((b, i) =>
                b.type === "prose" ? (
                  <Markdown key={i} text={b.markdown} />
                ) : (
                  <div key={i} className="unit">
                    {b.record ? (
                      <div className="hint">
                        技术单元：<a href={href("memory", ownerId, b.record.record_id)}>{b.record.title}</a>
                      </div>
                    ) : null}
                    {b.resolved_blocks?.map((rb) => <Markdown key={rb.block_id} text={rb.markdown} />)}
                  </div>
                ),
              )}
            </section>
          ))}
        </article>
      ) : null}
    </div>
  );
}

function Timeline({ ownerId }: { ownerId: string }) {
  const timeline = useOp<{ items: { id: string; kind: string; level: string | null; title: string; occurred_at: string; status?: string }[] }>(
    "memory.timeline",
    { owner_id: ownerId },
  );
  return (
    <>
      <ErrorBox error={timeline.error} />
      <ol className="timeline">
        {timeline.data?.items.map((t) => (
          <li key={t.id}>
            <Time value={t.occurred_at} /> <Tag>{label(KINDS, t.kind)}</Tag> {t.title} {t.status ? <code>{t.status}</code> : null}
          </li>
        ))}
      </ol>
    </>
  );
}

interface FullRecord {
  record_id: string;
  kind: string;
  level: string | null;
  title: string;
  revision: number;
  body_markdown: string;
  keywords: string[];
  sensitivity: string;
  record_reason: string;
  provenance_gap: string | null;
  updated_at: string;
  sources: { target_kind: string; target_id: string; revision: number | null; locator: string; relation: string }[];
  payload: {
    claims?: { claim_id: string; statement: string; scope: string; kind: string }[];
    blocks?: { block_id: string; markdown: string; kind?: string }[];
    [key: string]: unknown;
  };
}

export function RecordView({ ownerId, recordId }: { ownerId: string; recordId: string }) {
  const record = useOp<{ record: FullRecord }>("memory.inspect", { owner_id: ownerId, record_id: recordId });
  const badges = useClaimBadges(ownerId);
  const [exported, setExported] = useState(false);
  // 交换格式视图（R12 T-P3.1）：与导出包、Markdown 库同一个渲染器
  const rendered = useOp<{ markdown: string; path: string }>("memory.render", { owner_id: ownerId, record_id: recordId }, exported);
  const r = record.data?.record;
  const links = (r as unknown as { links?: { text: string; status: string; ref: { target_id: string } | null }[] })?.links ?? [];
  const handle = (r as unknown as { handle?: string })?.handle;
  return (
    <>
      <p>
        <a href={href("memory", ownerId)}>← 返回对象</a>
      </p>
      <ErrorBox error={record.error} />
      <Loading show={record.loading && !r} />
      {r ? (
        <Panel
          title={r.title}
          actions={
            <>
              <Tag>{label(LEVELS, r.level) !== "—" ? label(LEVELS, r.level) : label(KINDS, r.kind)}</Tag>
              <Tag>修订 {r.revision}</Tag>
              {handle ? <Tag>@{handle}</Tag> : null}
              <button type="button" className="small" onClick={() => setExported(!exported)}>
                {exported ? "返回正文" : "导出视图"}
              </button>
            </>
          }
        >
          {exported ? (
            <>
              <ErrorBox error={rendered.error} />
              <Loading show={rendered.loading} />
              {rendered.data ? (
                <>
                  <p className="hint">交换包中的路径：{rendered.data.path}（链接只指向本对象内的记录；完整导出用 exchange.export）</p>
                  <pre className="raw">{rendered.data.markdown}</pre>
                </>
              ) : null}
            </>
          ) : null}
          <p className="hint">
            {r.record_reason} · <Time value={r.updated_at} /> · <code>{r.record_id}</code>
          </p>
          {r.provenance_gap ? <div className="notice">依据缺口：{r.provenance_gap}</div> : null}
          <Markdown text={r.body_markdown} />
          {r.payload.blocks?.map((b) => <Markdown key={b.block_id} text={b.markdown} />)}
          {r.payload.claims?.length ? (
            <>
              <h3>结论</h3>
              <ul className="claims">
                {r.payload.claims.map((c) => (
                  <li key={c.claim_id}>
                    <div className="claim-body">
                      <ReviewBadge value={badges.get(c.claim_id) ?? "not-reviewed"} /> {c.statement}
                      <div className="hint">
                        适用范围：{c.scope} · <a href={href("review")}>去复核</a>
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            </>
          ) : null}
          {links.length ? (
            <>
              <h3>正文链接（{links.length}）</h3>
              <ul className="sources">
                {links.map((l) => (
                  <li key={l.text}>
                    <code>[[{l.text}]]</code> →{" "}
                    {l.ref ? <code>{l.ref.target_id}</code> : <span className="hint">概念库中还没有这个概念</span>}
                  </li>
                ))}
              </ul>
            </>
          ) : null}
          <Figures ownerId={ownerId} record={r} />
          <Provenance recordId={r.record_id} />
          <h3>来源（{r.sources.length}）</h3>
          <ol className="sources">
            {r.sources.map((s, i) => (
              <li key={i}>
                <Tag>{s.relation}</Tag> <code>{s.target_id}</code>
                {s.revision != null ? `@${s.revision}` : ""} {s.locator}
              </li>
            ))}
          </ol>
          {r.keywords.length ? <p className="hint">关键词：{r.keywords.join("、")}</p> : null}
        </Panel>
      ) : null}
    </>
  );
}

function DocumentAux({ ownerId, type, mode }: { ownerId: string; type: string; mode: "outline" | "impact" }) {
  const data = useOp<{
    sections?: { section_id: string; title: string; role: string; source_issues: { code: string }[] }[];
    changes?: { code: string; target_id: string; message: string; requires_review: boolean; section_ids: string[] }[];
    affected_section_ids?: string[];
    uncovered_unit_ids?: string[];
    scientific_review?: string;
  }>("memory.document", { owner_id: ownerId, view: mode, document_type: type as "research_process" });
  return (
    <div>
      <ErrorBox error={data.error} />
      <Loading show={data.loading} />
      {mode === "outline" ? (
        <ol>
          {data.data?.sections?.map((s) => (
            <li key={s.section_id}>
              {s.title} <Tag>{s.role}</Tag> {s.source_issues.length ? <Tag tone="bad">依据不可用</Tag> : null}
            </li>
          ))}
        </ol>
      ) : (
        <>
          <p className="hint">
            列出文稿固定引用的依据此后发生的变化（新修订、失效、新增未纳入的技术单元）。这只是审阅提示，文稿不会被自动修改，
            也不代表科学结论需要改变。
          </p>
          {data.data && !data.data.changes?.length ? <Empty>文稿依据没有变化。</Empty> : null}
          <ul className="problems">
            {data.data?.changes?.map((c, i) => (
              <li key={i}>
                <code>{c.code}</code> {c.message} · <code>{c.target_id}</code> · 影响 {c.section_ids.length} 个章节
              </li>
            ))}
          </ul>
          {data.data?.uncovered_unit_ids?.length ? (
            <p className="hint">未纳入文稿的技术单元：{data.data.uncovered_unit_ids.join("、")}</p>
          ) : null}
        </>
      )}
    </div>
  );
}

interface RawItem {
  material_id: string;
  title: string;
  path: string;
  sha256: string | null;
  status: string;
  origins: { record_id: string; title: string; role: string }[];
}

function RawMaterials({ ownerId }: { ownerId: string }) {
  const list = useOp<{ items: RawItem[]; total: number; missing: { message: string }[] }>("memory.raw_materials", {
    owner_id: ownerId,
  });
  const [open, setOpen] = useState<string>();
  const detail = useOp<{ text?: string; truncated?: boolean; media_type: string; verified_sha256: string; bytes: number }>(
    "memory.raw_material",
    { owner_id: ownerId, material_id: open ?? "" },
    !!open,
  );
  return (
    <>
      <p className="hint">L0 原始材料：已登记的原件清单。打开时会核对固定指纹；未登记的文件不会出现在这里。</p>
      <ErrorBox error={list.error} />
      <Loading show={list.loading} />
      <div className="split">
        <table className="table">
          <tbody>
            {list.data?.items.map((m) => (
              <tr key={m.material_id}>
                <td>
                  <button type="button" className={`link ${open === m.material_id ? "on" : ""}`} onClick={() => setOpen(m.material_id)}>
                    {m.title}
                  </button>
                  <div className="hint">{m.path}</div>
                </td>
                <td>
                  <Tag tone={m.status === "registered" ? "muted" : "bad"}>{m.status}</Tag>
                </td>
                <td className="hint">被 {m.origins.length} 条记录引用</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div>
          <ErrorBox error={detail.error} />
          {open && detail.data ? (
            <>
              <p className="hint">
                {detail.data.media_type} · {detail.data.bytes} 字节 · 指纹已核对 {detail.data.verified_sha256.slice(0, 12)}
              </p>
              {detail.data.text != null ? <pre className="raw">{detail.data.text}</pre> : <Empty>非文本材料，未显示内容。</Empty>}
              {detail.data.truncated ? <p className="hint">内容过长，已截断显示。</p> : null}
            </>
          ) : null}
        </div>
      </div>
    </>
  );
}

function Figures({ ownerId, record }: { ownerId: string; record: FullRecord }) {
  const figures = (record.payload.figures as { caption: string }[] | undefined) ?? [];
  if (!figures.length) return null;
  return (
    <>
      <h3>图（{figures.length}）</h3>
      {figures.map((f, i) => (
        <Figure key={i} ownerId={ownerId} record={record} index={i} caption={f.caption} />
      ))}
    </>
  );
}

function Figure({ ownerId, record, index, caption }: { ownerId: string; record: FullRecord; index: number; caption: string }) {
  const figure = useOp<{ data_url: string }>("memory.figure", {
    owner_id: ownerId,
    record_id: record.record_id,
    revision: record.revision,
    index,
  });
  return (
    <figure className="figure">
      <ErrorBox error={figure.error} />
      {figure.data ? <img src={figure.data.data_url} alt={caption} /> : <Loading show />}
      <figcaption>
        图 {index + 1}：{caption}
      </figcaption>
    </figure>
  );
}

function Provenance({ recordId }: { recordId: string }) {
  const [open, setOpen] = useState(false);
  const lineage = useOp<{ source_count: number; sources: { source_ids: string[]; paths: string[] }[]; missing: unknown[]; meaning: string }>(
    "provenance.lineage",
    { target_ids: [recordId] },
    open,
  );
  const impact = useOp<{ affected: { canonical_id: string; formal_dependency: boolean; paths: string[][] }[]; note: string }>(
    "provenance.impact",
    { changed_ids: [recordId] },
    open,
  );
  return (
    <details className="provenance" onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary>依据与影响</summary>
      <ErrorBox error={lineage.error ?? impact.error} />
      {lineage.data ? (
        <>
          <h4>追溯到的原件（{lineage.data.source_count}）</h4>
          <p className="hint">{lineage.data.meaning}</p>
          <ul>
            {lineage.data.sources.map((s, i) => (
              <li key={i}>
                <code>{s.source_ids.join("、")}</code> <span className="hint">{s.paths[0]}</span>
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {impact.data ? (
        <>
          <h4>如果这条记录变化，需要重新审阅的下游（{impact.data.affected.length}）</h4>
          <p className="hint">{impact.data.note}</p>
          <ul>
            {impact.data.affected.map((a) => (
              <li key={a.canonical_id}>
                <code>{a.canonical_id}</code> {a.formal_dependency ? <Tag tone="warn">正式依赖</Tag> : <Tag>导航/引用</Tag>}
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </details>
  );
}
