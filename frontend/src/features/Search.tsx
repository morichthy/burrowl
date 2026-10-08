// 检索：本地材料查询（可按复核状态过滤）+ 知识中枢联邦检索。选中结果后组包阅读。
import { type FormEvent, useState } from "react";
import { useAction } from "../api";
import { label, LEVELS, REVIEW_FILTERS } from "../labels";
import { Empty, ErrorBox, href, Markdown, Panel, ReviewBadge, Tag } from "../ui";

interface Candidate {
  candidate_id: string;
  kind: string;
  owner_id: string;
  record_id: string;
  title: string;
  excerpt: string;
  level: string | null;
  review: string;
  score: number;
  stale_revision: boolean;
}

interface SearchResult {
  query_id: string;
  items: Candidate[];
  gaps: { code: string; message: string }[];
  consumed: { wall_ms: number };
}

interface HubHit {
  connector_id: string;
  title: string;
  snippet: string;
  score: number;
  review: string;
  fixed_ref: { target_id: string } | null;
  local_key: string | null;
}

const SOURCES = [
  { value: "overview_experience", label: "概览与经验（L4/L3）" },
  { value: "process", label: "研究经过（L2）" },
  { value: "technical", label: "技术内容（L1）" },
  { value: "all", label: "全部（含原始来源）" },
];

export function Search() {
  const [tab, setTab] = useState<"local" | "hub">("local");
  const [question, setQuestion] = useState("");
  const [source, setSource] = useState("overview_experience");
  const [reviewFilter, setReviewFilter] = useState("all");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const search = useAction<SearchResult>("query.search");
  const hub = useAction<{ hits: HubHit[]; errors: { connector_id: string; error: string }[] }>("hub.search");
  const assemble = useAction<{ parts: { group: string; heading: string; markdown: string }[]; gaps: { message: string }[] }>(
    "query.assemble",
  );
  const [result, setResult] = useState<SearchResult>();
  const [hubResult, setHubResult] = useState<Awaited<ReturnType<typeof hub.run>>>();
  const [packet, setPacket] = useState<Awaited<ReturnType<typeof assemble.run>>>();

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!question.trim()) return;
    setSelected(new Set());
    setPacket(undefined);
    if (tab === "local") {
      setResult(
        await search.run({ question, content_source: source as "all", review_filter: reviewFilter as "all", purpose: "exploration" }),
      );
    } else {
      setHubResult(await hub.run({ query: question, review_filter: reviewFilter as "all", limit: 20 }));
    }
  };

  return (
    <>
      <Panel title="检索">
        <div className="tabs" role="tablist">
          <button type="button" role="tab" aria-selected={tab === "local"} className={tab === "local" ? "on" : ""} onClick={() => setTab("local")}>
            本地记忆
          </button>
          <button type="button" role="tab" aria-selected={tab === "hub"} className={tab === "hub" ? "on" : ""} onClick={() => setTab("hub")}>
            知识中枢
          </button>
        </div>
        <form onSubmit={submit} className="search-form">
          <input value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="用一句话描述要找的问题" aria-label="问题" />
          {tab === "local" ? (
            <select value={source} onChange={(e) => setSource(e.target.value)} aria-label="内容范围">
              {SOURCES.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          ) : null}
          <button type="submit" className="primary" disabled={search.busy || hub.busy}>
            检索
          </button>
        </form>
        <div className="segmented" role="radiogroup" aria-label="复核过滤">
          {REVIEW_FILTERS.map((f) => (
            <label key={f.value} className={reviewFilter === f.value ? "on" : ""}>
              <input type="radio" name="review" checked={reviewFilter === f.value} onChange={() => setReviewFilter(f.value)} />
              {f.label}
            </label>
          ))}
        </div>
        <ErrorBox error={search.error ?? hub.error} />
      </Panel>

      {tab === "local" && result ? (
        <Panel
          title={`结果（${result.items.length}）`}
          actions={
            <button
              type="button"
              disabled={!selected.size || assemble.busy}
              onClick={async () => setPacket(await assemble.run({ query_id: result.query_id, candidate_ids: [...selected] }))}
            >
              组包阅读所选（{selected.size}）
            </button>
          }
        >
          {result.gaps.map((g, i) => (
            <div key={i} className="notice">
              {g.message}
            </div>
          ))}
          {!result.items.length ? <Empty>没有匹配的材料。可以换个说法，或把内容范围改为"全部"。</Empty> : null}
          <ul className="results">
            {result.items.map((c) => (
              <li key={c.candidate_id}>
                <input
                  type="checkbox"
                  aria-label={`选择 ${c.title}`}
                  checked={selected.has(c.candidate_id)}
                  onChange={() =>
                    setSelected((s) => {
                      const next = new Set(s);
                      if (next.has(c.candidate_id)) next.delete(c.candidate_id);
                      else next.add(c.candidate_id);
                      return next;
                    })
                  }
                />
                <div>
                  <a href={href("memory", c.owner_id, c.record_id)}>{c.title}</a> <Tag>{label(LEVELS, c.level)}</Tag>{" "}
                  <ReviewBadge value={c.review} /> {c.stale_revision ? <Tag tone="warn">有新修订</Tag> : null}
                  <div className="hint">{c.owner_id}</div>
                  <p className="excerpt">{c.excerpt}</p>
                </div>
              </li>
            ))}
          </ul>
          <p className="hint">用时 {result.consumed.wall_ms} ms</p>
        </Panel>
      ) : null}

      {tab === "hub" && hubResult ? (
        <Panel title={`知识中枢结果（${hubResult.hits.length}）`}>
          {hubResult.errors.map((e) => (
            <div key={e.connector_id} className="error">
              {e.connector_id}：{e.error}
            </div>
          ))}
          {!hubResult.hits.length ? <Empty>没有命中。确认已配置连接器并完成发布。</Empty> : null}
          <ul className="results">
            {hubResult.hits.map((h, i) => (
              <li key={i}>
                <div>
                  <strong>{h.title}</strong> <Tag>{h.connector_id}</Tag> <ReviewBadge value={h.review} />
                  {h.fixed_ref ? (
                    <div className="hint">
                      本地来源：<code>{h.fixed_ref.target_id}</code>
                    </div>
                  ) : (
                    <div className="hint">外部知识（非本项目发布）</div>
                  )}
                  <p className="excerpt">{h.snippet}</p>
                </div>
              </li>
            ))}
          </ul>
        </Panel>
      ) : null}

      {packet ? (
        <Panel title="阅读包">
          <ErrorBox error={assemble.error} />
          {packet.gaps.map((g, i) => (
            <div key={i} className="notice">
              {g.message}
            </div>
          ))}
          {packet.parts.map((p, i) => (
            <article key={i} className={`part ${p.group}`}>
              <h3>
                {p.heading} {p.group !== "direct" ? <Tag>{p.group === "context" ? "必要依据" : "关联补充"}</Tag> : null}
              </h3>
              <Markdown text={p.markdown} />
            </article>
          ))}
        </Panel>
      ) : null}
    </>
  );
}
