// 系统记忆：对象列表 → 对象（按层级的记录、文稿、时间线） → 单条记录（正文、结论复核徽标、依据）。
import { useCallback, useMemo, useState } from "react";
import { useAction, useOp } from "../api";
import { KINDS, label, LEVELS, OWNER_TYPES } from "../labels";
import { Empty, ErrorBox, href, type LinkTargets, linkify, Loading, Markdown, Panel, ReviewBadge, Tag, Time, useDebounced } from "../ui";
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
  // 对象类型多选取并集；空数组表示全部。在已分组的结果上筛选，类型计数不随勾选变化。
  const [types, setTypes] = useState<string[]>([]);
  const [persistedOnly, setPersistedOnly] = useState(false);
  const owners = useOp<{ groups: { owner_type: string; owners: OwnerSummary[] }[] }>("memory.owners", { query: useDebounced(query, 300) });
  const groups = (owners.data?.groups ?? [])
    .filter((g) => !types.length || types.includes(g.owner_type))
    .map((g) => ({ ...g, owners: persistedOnly ? g.owners.filter((o) => o.persisted) : g.owners }))
    .filter((g) => g.owners.length);
  const toggleType = (type: string) => setTypes((now) => (now.includes(type) ? now.filter((t) => t !== type) : [...now, type]));
  return (
    <Panel title="系统记忆">
      <div className="toolbar">
        <input placeholder="按名称或 ID 筛选" value={query} onChange={(e) => setQuery(e.target.value)} />
        <label className="check hint">
          <input type="checkbox" checked={persistedOnly} onChange={() => setPersistedOnly(!persistedOnly)} /> 隐藏尚无记忆的对象
        </label>
      </div>
      {owners.data?.groups.length ? (
        <div className="chips" role="group" aria-label="按对象类型筛选">
          <label className={types.length ? "" : "on"}>
            <input type="checkbox" checked={!types.length} onChange={() => setTypes([])} />
            全部
          </label>
          {owners.data.groups.map((g) => (
            <label key={g.owner_type} className={types.includes(g.owner_type) ? "on" : ""}>
              <input type="checkbox" checked={types.includes(g.owner_type)} onChange={() => toggleType(g.owner_type)} />
              {label(OWNER_TYPES, g.owner_type)} <span className="count">{g.owners.length}</span>
            </label>
          ))}
        </div>
      ) : null}
      <ErrorBox error={owners.error} />
      <Loading show={owners.loading && !owners.data} />
      {owners.data && !groups.length ? <Empty>没有符合筛选条件的对象。</Empty> : null}
      {groups.map((g) => (
        <section key={g.owner_type}>
          <h3>
            {label(OWNER_TYPES, g.owner_type)} <span className="count">{g.owners.length}</span>
          </h3>
          <ul className="owners">
            {g.owners.map((o) => (
              <li key={o.owner_id}>
                <a href={href("memory", o.owner_id)}>{o.title}</a>
                <code>{o.owner_id}</code>
                <div className="counts">
                  {!o.persisted ? <Tag>尚无记忆</Tag> : null}
                  {Object.entries(o.counts ?? {})
                    .filter(([, n]) => n)
                    // 层级 L4→L0 在前，其余类型在后
                    .sort(([a], [b]) => (LEVELS[b] ? 1 : 0) - (LEVELS[a] ? 1 : 0) || (LEVELS[a] ? b.localeCompare(a) : a.localeCompare(b)))
                    .map(([k, n]) => (
                      <span key={k} className="badge muted" title={label(LEVELS, k) !== k ? label(LEVELS, k) : label(KINDS, k)}>
                        {LEVELS[k] ? k : label(KINDS, k)} {n}
                      </span>
                    ))}
                </div>
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
  excerpt: string;
  claims: string[];
}

// 类型筛选项按知识层级从高到低排列，与记录表的排序一致；工作流记录排在后面
const KIND_ORDER = ["overview", "experience", "narrative", "detail", "source", "document", "document_section"];
const kindOrder = (kind: string) => (KIND_ORDER.includes(kind) ? KIND_ORDER.indexOf(kind) : KIND_ORDER.length);

/** 导出当前筛选出的记录（只读，不产生新记录）。recordIds 为空时不可用。 */
function ExportButton({ ownerId, recordIds }: { ownerId: string; recordIds: string[] | undefined }) {
  const [fmt, setFmt] = useState<"markdown" | "json" | "html">("markdown");
  const exporter = useAction<{ filename: string; content: string }>("memory.export");
  const save = async () => {
    const result = await exporter.run({ owner_id: ownerId, fmt, record_ids: recordIds });
    if (!result) return;
    const url = URL.createObjectURL(new Blob([result.content], { type: "text/plain;charset=utf-8" }));
    const link = Object.assign(document.createElement("a"), { href: url, download: `${ownerId}-${result.filename}` });
    link.click();
    URL.revokeObjectURL(url);
  };
  return (
    <span className="actions">
      <select value={fmt} onChange={(e) => setFmt(e.target.value as "markdown")} aria-label="导出格式">
        <option value="markdown">Markdown</option>
        <option value="json">JSON</option>
        <option value="html">HTML</option>
      </select>
      <button type="button" disabled={!recordIds?.length || exporter.busy} onClick={save} title="导出当前列表中的记录">
        导出 {recordIds?.length ?? 0} 条
      </button>
      {exporter.error ? <span className="hint">{exporter.error.message}</span> : null}
    </span>
  );
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
  const [tab, setTab] = useState<"records" | "document" | "expansion" | "raw" | "timeline">("records");
  const [mainOnly, setMainOnly] = useState(true);
  const [query, setQuery] = useState("");
  // 多选取并集；空数组表示不按类型筛选（全部）
  const [kinds, setKinds] = useState<string[]>([]);
  const recordQuery = useDebounced(query, 300); // 停顿后再查，避免每个按键都重取记录列表
  const records = useOp<{
    owner: OwnerSummary;
    head: { generation: number } | null;
    items: RecordRow[];
    total: number;
    kind_counts: Record<string, number>;
  }>("memory.records", { owner_id: ownerId, main_only: mainOnly, kinds: kinds.length ? kinds : null, query: recordQuery, limit: 500 });
  const kindOptions = Object.entries(records.data?.kind_counts ?? {}).sort(([a], [b]) => kindOrder(a) - kindOrder(b));
  const toggleKind = (kind: string) => setKinds((now) => (now.includes(kind) ? now.filter((k) => k !== kind) : [...now, kind]));
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
              ["expansion", "原文展开"],
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
            <div className="toolbar">
              <input placeholder="按标题或 ID 筛选" aria-label="筛选记录" value={query} onChange={(e) => setQuery(e.target.value)} />
              <label className="check hint">
                <input type="checkbox" checked={!mainOnly} onChange={() => setMainOnly(!mainOnly)} /> 显示目标、检查点、复核等工作流记录
              </label>
              <span className="spacer" />
              <ExportButton ownerId={ownerId} recordIds={records.data?.items.map((r) => r.record_id)} />
            </div>
            {kindOptions.length ? (
              <div className="chips" role="group" aria-label="按类型筛选">
                <label className={kinds.length ? "" : "on"}>
                  <input type="checkbox" checked={!kinds.length} onChange={() => setKinds([])} />
                  全部
                </label>
                {kindOptions.map(([kind, n]) => (
                  <label key={kind} className={kinds.includes(kind) ? "on" : ""}>
                    <input type="checkbox" checked={kinds.includes(kind)} onChange={() => toggleKind(kind)} />
                    {label(KINDS, kind)} <span className="count">{n}</span>
                  </label>
                ))}
              </div>
            ) : null}
            <Loading show={records.loading && !records.data} />
            {records.data && !records.data.items.length ? (
              <Empty>{kinds.length || query ? "没有符合筛选条件的记录。" : "这个对象还没有记忆记录。"}</Empty>
            ) : null}
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
                    <td>{r.level ? <span className={`level ${r.level}`}>{r.level}</span> : "—"}</td>
                    <td>
                      <a href={href("memory", ownerId, r.record_id)}>{r.title}</a>
                      {r.excerpt ? <div className="excerpt">{r.excerpt}</div> : null}
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
        {tab === "expansion" ? <Expansion ownerId={ownerId} /> : null}
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
  const current = useOp<{ record: FullRecord }>("memory.inspect", { owner_id: ownerId, record_id: recordId });
  // 历史修订：只读回看固定版本；undefined 表示当前版本
  const [revision, setRevision] = useState<number>();
  const past = useOp<{ record: FullRecord }>("memory.inspect", { owner_id: ownerId, record_id: recordId, revision }, revision != null);
  const record = revision != null ? past : current;
  const latest = current.data?.record.revision ?? 0;
  const badges = useClaimBadges(ownerId);
  const [exported, setExported] = useState(false);
  // 交换格式视图（R12 T-P3.1）：与导出包、Markdown 库同一个渲染器
  const rendered = useOp<{ markdown: string; path: string }>("memory.render", { owner_id: ownerId, record_id: recordId }, exported);
  const r = record.data?.record;
  const links = (r as unknown as { links?: { text: string; status: string; ref: { target_id: string } | null }[] })?.links ?? [];
  const handle = (r as unknown as { handle?: string })?.handle;
  // R21 D-11：引用的记录、结论、原件解析为名称与跳转地址；读不到的退回显示 ID
  const targets = useOp<Record<string, RefTarget>>("ui.ref_targets", { owner_id: ownerId, record_id: recordId, revision }).data ?? {};
  const linkUrls: LinkTargets = Object.fromEntries(links.map((l) => [l.text, l.ref ? targetHref(targets[l.ref.target_id]) : undefined]));
  const blocks = r?.payload.blocks ?? [];
  const inline = new Set(blocks.flatMap((b) => [...b.markdown.matchAll(/\]\(figure:(\d+)\)/g)].map((m) => Number(m[1]))));
  const figureAt = useCallback(
    (index: number, alt: string) =>
      r ? <Figure ownerId={ownerId} record={r} index={index} caption={figureCaption(r, index) || alt} /> : null,
    [ownerId, r],
  );
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
              {latest > 1 ? (
                <select
                  aria-label="查看修订"
                  value={revision ?? latest}
                  onChange={(e) => setRevision(Number(e.target.value) === latest ? undefined : Number(e.target.value))}
                >
                  {Array.from({ length: latest }, (_, i) => latest - i).map((n) => (
                    <option key={n} value={n}>
                      修订 {n}
                      {n === latest ? "（当前）" : ""}
                    </option>
                  ))}
                </select>
              ) : (
                <Tag>修订 {r.revision}</Tag>
              )}
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
          {revision != null ? (
            <div className="notice">
              正在查看历史修订 {revision}（当前为修订 {latest}）。结论徽标显示的是当前复核状态。{" "}
              <button type="button" className="link" onClick={() => setRevision(undefined)}>
                回到当前版本
              </button>
            </div>
          ) : null}
          {r.provenance_gap ? <div className="notice">依据缺口：{r.provenance_gap}</div> : null}
          <Markdown text={linkify(r.body_markdown, linkUrls)} />
          {blocks.map((b) => (
            <div key={b.block_id} id={`block-${b.block_id}`}>
              <Markdown text={linkify(b.markdown, linkUrls)} figure={figureAt} />
            </div>
          ))}
          {r.payload.claims?.length ? (
            <>
              <h3>结论</h3>
              <ul className="claims">
                {r.payload.claims.map((c) => (
                  <li key={c.claim_id}>
                    <div className="claim-body">
                      <ReviewBadge value={badges.get(c.claim_id) ?? "not-reviewed"} /> <Markdown text={linkify(c.statement, linkUrls)} />
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
                    {l.ref ? <RefName id={l.ref.target_id} targets={targets} /> : <span className="hint">概念库中还没有这个概念</span>}
                  </li>
                ))}
              </ul>
            </>
          ) : null}
          <Figures ownerId={ownerId} record={r} skip={inline} />
          <Provenance recordId={r.record_id} />
          <h3>来源（{r.sources.length}）</h3>
          <ol className="sources">
            {r.sources.map((s, i) => (
              <li key={i}>
                <Tag>{s.relation}</Tag> <RefName id={s.target_id} targets={targets} />
                {s.revision != null ? <span className="hint"> 修订 {s.revision}</span> : null}{" "}
                {s.locator && s.locator !== "record" ? <span className="hint">{s.locator}</span> : null}
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

function figureCaption(record: FullRecord, index: number): string {
  return ((record.payload.figures as { caption: string }[] | undefined) ?? [])[index]?.caption ?? "";
}

function Figures({ ownerId, record, skip }: { ownerId: string; record: FullRecord; skip: Set<number> }) {
  const figures = ((record.payload.figures as { caption: string }[] | undefined) ?? []).map((f, i) => ({ ...f, i }));
  const rest = figures.filter((f) => !skip.has(f.i));
  if (!rest.length) return null;
  return (
    <>
      <h3>{skip.size ? `其他图（${rest.length}）` : `图（${rest.length}）`}</h3>
      {rest.map((f) => (
        <Figure key={f.i} ownerId={ownerId} record={record} index={f.i} caption={f.caption} />
      ))}
    </>
  );
}

function Figure({ ownerId, record, index, caption }: { ownerId: string; record: FullRecord; index: number; caption: string }) {
  return <RecordFigure ownerId={ownerId} recordId={record.record_id} revision={record.revision} index={index} caption={caption} />;
}

function RecordFigure({
  ownerId,
  recordId,
  revision,
  index,
  caption,
}: {
  ownerId: string;
  recordId: string;
  revision: number;
  index: number;
  caption: string;
}) {
  const figure = useOp<{ data_url: string }>("memory.figure", { owner_id: ownerId, record_id: recordId, revision, index });
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

// ---------- R21：引用的名称与跳转 ----------
interface RefTarget {
  kind: "record" | "claim" | "file" | "owner";
  owner_id?: string;
  record_id?: string;
  title: string;
  handle?: string | null;
  level?: string | null;
}

function targetHref(target: RefTarget | undefined): string | undefined {
  if (!target) return undefined;
  if (target.owner_id && target.record_id) return href("memory", target.owner_id, target.record_id);
  if (target.kind === "owner" && target.owner_id) return href("memory", target.owner_id);
  return undefined;
}

/** 显示引用目标的标题（短名）而不是长编号；完整 ID 在悬停提示里。 */
function RefName({ id, targets }: { id: string; targets: Record<string, RefTarget> }) {
  const target = targets[id];
  if (!target) return <code>{id}</code>;
  const url = targetHref(target);
  const text = target.kind === "claim" && target.title.length > 60 ? target.title.slice(0, 60) + "…" : target.title;
  const prefix = target.kind === "file" ? "原件：" : target.kind === "claim" ? "结论：" : target.level ? `${target.level} ` : "";
  const body = (
    <>
      {prefix}
      {text}
      {target.handle ? <span className="hint"> @{target.handle}</span> : null}
    </>
  );
  return url ? (
    <a href={url} title={id}>
      {body}
    </a>
  ) : (
    <span title={id}>{body}</span>
  );
}

// ---------- R21 D-07：原文展开与覆盖 ----------
interface Transcription {
  source_id: string;
  title: string;
  has_snapshot: boolean;
  counts?: Record<string, number>;
}

interface ExpansionSection {
  key: string;
  path: string;
  status: "covered" | "omitted" | "gap";
  reason?: string;
  excerpt?: string;
  blocks: {
    record_id: string;
    revision: number;
    title: string;
    handle: string | null;
    block_id: string;
    markdown?: string;
    see_above?: boolean;
  }[];
}

const COVERAGE_TONES: Record<string, { label: string; tone: string }> = {
  covered: { label: "已转写", tone: "good" },
  thin: { label: "偏短", tone: "warn" },
  uncovered: { label: "未覆盖", tone: "bad" },
  omitted: { label: "已省略", tone: "muted" },
};

function Expansion({ ownerId }: { ownerId: string }) {
  const list = useOp<Transcription[]>("ui.transcriptions", { owner_id: ownerId });
  const [picked, setPicked] = useState<string>();
  const sources = list.data ?? [];
  const current = picked ?? sources.find((s) => s.has_snapshot)?.source_id;
  return (
    <>
      <p className="hint">
        按原文顺序展开转写它的技术单元（L1），用来核对导入是否完整。只有导入时保存了原文快照的原件才能展开；“偏短”“未覆盖”只是提示，
        觉得哪里简略，可以在关系图页写笔记交给 AI 补写。
      </p>
      <ErrorBox error={list.error} />
      <Loading show={list.loading} />
      {list.data && !sources.length ? <Empty>这个对象没有登记或转写过原件。</Empty> : null}
      {sources.length ? (
        <div className="toolbar">
          <select aria-label="选择原件" value={current ?? ""} onChange={(e) => setPicked(e.target.value)}>
            {sources.map((s) => (
              <option key={s.source_id} value={s.source_id} disabled={!s.has_snapshot}>
                {s.title}
                {s.has_snapshot ? "" : "（没有快照）"}
              </option>
            ))}
          </select>
          {Object.entries(sources.find((s) => s.source_id === current)?.counts ?? {}).map(([status, n]) =>
            n ? (
              <Tag key={status} tone={COVERAGE_TONES[status]?.tone}>
                {COVERAGE_TONES[status]?.label ?? status} {n}
              </Tag>
            ) : null,
          )}
        </div>
      ) : null}
      {current ? <ExpansionView ownerId={ownerId} sourceId={current} /> : null}
    </>
  );
}

function ExpansionView({ ownerId, sourceId }: { ownerId: string; sourceId: string }) {
  const doc = useOp<{ file_name: string; sections: ExpansionSection[] }>("memory.document", {
    owner_id: ownerId,
    view: "expansion",
    source_id: sourceId,
  });
  const coverage = useOp<{ segments: { key: string; status: string; ratio?: number; overlap?: number }[] }>("memory.document", {
    owner_id: ownerId,
    view: "coverage",
    source_id: sourceId,
  });
  const status = new Map((coverage.data?.segments ?? []).map((s) => [s.key, s]));
  return (
    <>
      <ErrorBox error={doc.error ?? coverage.error} />
      <Loading show={doc.loading} />
      <article className="document">
        {doc.data?.sections.map((s) => {
          const row = status.get(s.key);
          const tone = COVERAGE_TONES[row?.status ?? s.status] ?? COVERAGE_TONES.uncovered;
          return (
            <section key={s.key}>
              <div className="hint">
                <Tag tone={tone.tone}>{tone.label}</Tag> 原文：{s.key}
                {row?.ratio != null ? ` · 字数比 ${row.ratio} · 重合度 ${row.overlap}` : ""}
              </div>
              {s.status === "omitted" ? <p className="hint">省略理由：{s.reason}</p> : null}
              {s.status === "gap" ? <blockquote className="hint">原文开头：{s.excerpt}</blockquote> : null}
              {s.blocks.map((b) => (
                <div key={`${b.record_id}#${b.block_id}`} className="unit">
                  <div className="hint">
                    <a href={href("memory", ownerId, b.record_id)}>{b.title}</a>
                    {b.handle ? ` @${b.handle}` : ""} · {b.block_id}
                    {b.see_above ? "（同一块已在上文显示）" : ""}
                  </div>
                  {b.markdown ? <BlockMarkdown ownerId={ownerId} block={b} /> : null}
                </div>
              ))}
            </section>
          );
        })}
      </article>
    </>
  );
}

function BlockMarkdown({ ownerId, block }: { ownerId: string; block: ExpansionSection["blocks"][number] }) {
  const figure = useCallback(
    (index: number, alt: string) => (
      <RecordFigure ownerId={ownerId} recordId={block.record_id} revision={block.revision} index={index} caption={alt} />
    ),
    [ownerId, block.record_id, block.revision],
  );
  return <Markdown text={block.markdown} figure={figure} />;
}
