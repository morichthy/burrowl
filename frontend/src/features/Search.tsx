// 检索：本地材料查询（可按复核状态过滤）+ 知识中枢联邦检索。选中结果后组包阅读。
import { type FormEvent, useRef, useState } from "react";
import { useAction } from "../api";
import type { OpArgs } from "../generated/ops";
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
  gaps: Gap[];
  consumed: { wall_ms: number };
}

// 查询缺口是结构化诊断（reason/channel/owner_id）；组包缺口则通常含 message。
// 两种返回都保留原始原因，不能把缺少 message 的诊断渲染成空黄色行。
interface Gap {
  message?: string;
  reason?: string;
  code?: string;
  owner_id?: string;
  target_id?: string | null;
  channel?: string;
  connector_id?: string;
}

const GAP_REASONS: Record<string, string> = {
  not_indexed: "尚未建立检索索引，结果可能不完整",
  pending: "索引更新尚未完成，结果可能不完整",
  failed: "索引更新失败，结果可能不完整",
  stale: "索引落后于当前内容，结果可能不完整",
  disabled: "该检索通道未启用",
  access_denied: "材料不可访问，已从结果中省略",
  ACCESS_DENIED: "材料不可访问，已从结果中省略",
  NOT_FOUND: "材料不存在或已不可用",
  embedder_unavailable: "向量模型不可用，本次使用其他检索通道",
  model_budget_insufficient: "本次预算不足以使用向量模型",
  query_truncated: "问题超出向量模型输入上限，向量检索使用了截短的问题",
  graph_budget_truncated: "关系扩展达到预算上限，关联补充可能不完整",
  index_behind_current_revision: "命中索引落后于记录当前修订，已省略该条材料",
  timeout: "外部检索超时，本次使用本地结果",
  unavailable: "外部检索不可用，本次使用本地结果",
  external_only: "部分外部命中没有对应的本地材料，未加入结果",
};
const GAP_CHANNELS: Record<string, string> = { lexical: "关键词", vector: "向量", dense: "向量", graph: "关系", hub: "知识中枢" };
const REPAIRABLE_INDEX_REASONS = new Set(["not_indexed", "pending", "stale", "failed", "index_behind_current_revision"]);

function GapNotices({ gaps }: { gaps: Gap[] }) {
  const notices = new Map<string, { text: string; channels: Set<string> }>();
  for (const gap of gaps) {
    const reason = gap.reason || gap.code || "unknown";
    const identity = gap.owner_id || gap.target_id || gap.connector_id;
    const text = gap.message?.trim() || `${identity ? `${identity}：` : ""}${GAP_REASONS[reason] || `检索或阅读存在缺口（${reason}）`}`;
    // 同一对象同时缺关键词和向量索引时合并为一行，仍显示两条通道。
    const key = JSON.stringify([identity, reason, text]);
    const notice = notices.get(key) || { text, channels: new Set<string>() };
    if (gap.channel) notice.channels.add(GAP_CHANNELS[gap.channel] || gap.channel);
    notices.set(key, notice);
  }
  return <>{[...notices.entries()].map(([key, notice]) => <div key={key} className="notice">
    {notice.text}{notice.channels.size ? `（${[...notice.channels].join("、")}）` : ""}
  </div>)}</>;
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
  const reconcile = useAction("index.reconcile");
  const hub = useAction<{ hits: HubHit[]; errors: { connector_id: string; error: string }[] }>("hub.search");
  const assemble = useAction<{ parts: { group: string; heading: string; markdown: string }[]; gaps: Gap[] }>(
    "query.assemble",
  );
  const [result, setResult] = useState<SearchResult>();
  const [hubResult, setHubResult] = useState<Awaited<ReturnType<typeof hub.run>>>();
  const [packet, setPacket] = useState<Awaited<ReturnType<typeof assemble.run>>>();
  const requestVersion = useRef(0);
  const submittedQuery = useRef<OpArgs["query.search"] | undefined>(undefined);
  const submittedHubQuery = useRef<string | undefined>(undefined);

  const repairAndRetry = async () => {
    const args = submittedQuery.current;
    if (!args || search.busy || hub.busy || reconcile.busy) return;
    const version = ++requestVersion.current;
    setSelected(new Set());
    setPacket(undefined);
    assemble.clear();
    // 只在用户点击后补派生索引；重试沿用原查询，不能偷偷换成输入框中的未提交问题。
    const repaired = await reconcile.run({});
    if (version !== requestVersion.current || repaired === undefined) return;
    setResult(undefined);
    setResult(await search.run(args));
  };

  // 复核过滤在结果出来后切换时立即生效：沿用已提交的问题与范围重查，不读取输入框里未提交的新问题。
  const changeReviewFilter = async (value: string) => {
    setReviewFilter(value);
    if (search.busy || hub.busy || reconcile.busy) return;
    if (tab === "local" && submittedQuery.current) {
      const version = ++requestVersion.current;
      const args = { ...submittedQuery.current, review_filter: value as "all" };
      submittedQuery.current = args;
      setSelected(new Set());
      setPacket(undefined);
      assemble.clear();
      const next = await search.run(args);
      if (version === requestVersion.current) setResult(next);
    } else if (tab === "hub" && submittedHubQuery.current) {
      const version = ++requestVersion.current;
      const next = await hub.run({ query: submittedHubQuery.current, review_filter: value as "all", limit: 20 });
      if (version === requestVersion.current) setHubResult(next);
    }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!question.trim() || search.busy || hub.busy || reconcile.busy) return;
    ++requestVersion.current;
    setSelected(new Set());
    setPacket(undefined);
    assemble.clear();
    reconcile.clear();
    if (tab === "local") {
      // 新问题提交后旧命中已不属于当前查询，等待或失败时都不保留旧结果。
      setResult(undefined);
      const args: OpArgs["query.search"] = { question, content_source: source as "all", review_filter: reviewFilter as "all", purpose: "exploration" };
      submittedQuery.current = args;
      setResult(await search.run(args));
    } else {
      setHubResult(undefined);
      submittedHubQuery.current = question;
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
          <button type="submit" className="primary" disabled={search.busy || hub.busy || reconcile.busy}>
            {reconcile.busy ? "正在补齐索引…" : search.busy || hub.busy ? "检索中…" : "检索"}
          </button>
        </form>
        <div className="segmented" role="radiogroup" aria-label="复核过滤">
          {REVIEW_FILTERS.map((f) => (
            <label key={f.value} className={reviewFilter === f.value ? "on" : ""}>
              <input type="radio" name="review" checked={reviewFilter === f.value} disabled={search.busy || hub.busy || reconcile.busy} onChange={() => changeReviewFilter(f.value)} />
              {f.label}
            </label>
          ))}
        </div>
        <ErrorBox error={tab === "local" ? search.error : hub.error} />
        {tab === "local" ? <ErrorBox error={reconcile.error} /> : null}
      </Panel>

      {tab === "local" && result ? (
        <Panel
          title={`结果（${result.items.length}）`}
          actions={
            <button
              type="button"
              disabled={!selected.size || assemble.busy || reconcile.busy}
              onClick={async () => {
                const version = requestVersion.current;
                const value = await assemble.run({ query_id: result.query_id, candidate_ids: [...selected] });
                // 组包可能晚于下一次搜索返回；旧查询的阅读包不能回写新查询。
                if (version === requestVersion.current) setPacket(value);
                else assemble.clear(); // 旧组包失败也不能把错误挂到新查询结果上。
              }}
            >
              组包阅读所选（{selected.size}）
            </button>
          }
        >
          <GapNotices gaps={result.gaps} />
          {result.gaps.some((gap) => REPAIRABLE_INDEX_REASONS.has(gap.reason || gap.code || "")) ? (
            <button type="button" disabled={reconcile.busy || search.busy || hub.busy} onClick={repairAndRetry}>
              {reconcile.busy ? "正在补齐索引…" : "补齐索引并重试"}
            </button>
          ) : null}
          <ErrorBox error={assemble.error} />
          {!result.items.length ? <Empty>{reviewFilter === "all" ? "没有匹配的材料。可以换个说法，或把内容范围改为\"全部\"。" : "当前复核过滤下没有匹配的材料。可以切回\"全部\"查看未复核的结果。"}</Empty> : null}
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
          <GapNotices gaps={packet.gaps} />
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
