// 首页：当前阅读、待处理事项、最近关键操作、复核概况、能力与降级情况。
import { useEffect } from "react";
import { useOp } from "../api";
import { label, SESSION_STATUS } from "../labels";
import { ErrorBox, href, Panel, Tag, Time } from "../ui";
import { useNoteCounts } from "./Notes";
import type { SessionSummary } from "./reading/ReadingList";

// R13：关键操作记录（memory.activity）的一条；ref 指向可追溯的对象、提交、Run 或发布记录。
export type ActivityItem = {
  at: string | null;
  kind: string;
  actor: string;
  owner_id: string | null;
  summary: string;
  cursor: string;
  ref: { kind: string; owner_id?: string; commit_id?: string; run_id?: string | number; connector_id?: string };
};

function activityHref(item: ActivityItem): string {
  const ref = item.ref;
  if (ref.kind === "hub_run") return href("hub");
  // R14：Run 不保存记录。对象内 Run 链到上级对象（时间线里有它）；独立 Run 链到维护页的 Run 列表
  if (ref.kind === "run") return item.owner_id && item.owner_id !== ref.run_id ? href("memory", item.owner_id) : href("maintenance");
  return href("memory", ref.owner_id ?? "");
}

export function Home() {
  const sessions = useOp<SessionSummary[]>("reading.list", { limit: 20 });
  const activity = useOp<{ items: ActivityItem[] }>("memory.activity", { limit: 20 });
  // MCP 创建会话或保存记录时工作台可能一直停留在首页；轮询保证新内容能出现，聚焦时立即补刷。
  useEffect(() => {
    const refresh = () => {
      sessions.reload();
      activity.reload();
    };
    const timer = window.setInterval(refresh, 5_000);
    window.addEventListener("focus", refresh);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", refresh);
    };
  }, [sessions.reload, activity.reload]);
  const review = useOp<{ counts: Record<string, number> }>("review.queue", { limit: 1 });
  const caps = useOp<{
    data_dir: string;
    vector: { backend: string; embedder: string | null; ready: boolean };
    reranker: { ready: boolean };
    connectors: { id: string; type: string; enabled: boolean }[];
  }>("system.capabilities");
  const waiting = sessions.data?.filter((s) => s.pending_requests > 0) ?? [];
  const live = sessions.data?.filter((s) => s.status !== "archived") ?? [];
  const latest = live[0];
  const counts = review.data?.counts;
  return (
    <div className="home">
      <div className="home-main">
        <Panel title="当前阅读" actions={<a href={href("reading")}>全部会话 →</a>}>
          <ErrorBox error={sessions.error} />
          {latest ? (
            <div className="reading-hero">
              <a className="reading-question" href={href("reading", latest.session_id)}>
                {latest.question}
              </a>
              <div className="reading-meta">
                <Tag tone={latest.status === "waiting_user" ? "warn" : latest.status === "active" ? "good" : "muted"}>
                  {label(SESSION_STATUS, latest.status)}
                </Tag>
                <span>相关 {latest.relevant}</span>
                <span>笔记 {latest.notes}</span>
                <Time value={latest.updated_at} />
              </div>
            </div>
          ) : (
            <p className="hint">还没有阅读会话。AI 调用 reading.start 后会出现在这里。</p>
          )}
          {waiting.length ? (
            <div className="notice">
              {waiting.length} 个会话在等你回复：
              <ul>
                {waiting.map((s) => (
                  <li key={s.session_id}>
                    <a href={href("reading", s.session_id)}>{s.question}</a>（{s.pending_requests} 条请求）
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {live.length > 1 ? (
            <ul className="recent-sessions">
              {live.slice(1, 5).map((s) => (
                <li key={s.session_id}>
                  <span className={`status-dot ${s.status}`} title={label(SESSION_STATUS, s.status)} />
                  <a href={href("reading", s.session_id)}>{s.question}</a>
                  <Time value={s.updated_at} />
                </li>
              ))}
            </ul>
          ) : null}
        </Panel>
        <Panel title="最近关键操作">
          <ErrorBox error={activity.error} />
          {activity.data?.items.length ? (
            <ActivityTimeline items={activity.data.items} />
          ) : (
            <p className="hint">还没有关键操作（建立对象、保存、复核、Run 结束、发布）。</p>
          )}
        </Panel>
      </div>
      <div className="home-side">
        <Panel title="复核" actions={<a href={href("review")}>去复核 →</a>}>
          <ErrorBox error={review.error} />
          {counts ? <ReviewSummary counts={counts} /> : null}
        </Panel>
        <NotesCard />
        <Panel title="运行环境" actions={<a href={href("settings")}>设置 →</a>}>
          <ErrorBox error={caps.error} />
          {caps.data ? (
            <ul className="status-list">
              <li>
                <span className={`status-dot ${caps.data.vector.ready ? "good" : "warn"}`} />
                <span>向量检索</span>
                <span className="hint" title={`${caps.data.vector.backend} ${caps.data.vector.embedder ?? ""}`}>
                  {caps.data.vector.ready ? "可用" : "未就绪，仅关键词"}
                </span>
              </li>
              <li>
                <span className={`status-dot ${caps.data.reranker.ready ? "good" : "muted"}`} />
                <span>重排模型</span>
                <span className="hint">{caps.data.reranker.ready ? "可用" : "未安装"}</span>
              </li>
              <li>
                <span className={`status-dot ${caps.data.connectors.length ? "good" : "muted"}`} />
                <span>知识中枢</span>
                <a className="hint" href={href("hub")}>
                  {caps.data.connectors.length ? caps.data.connectors.map((c) => c.id).join("、") : "未配置"}
                </a>
              </li>
              <li className="status-path">
                <span>数据目录</span>
                <code title={caps.data.data_dir}>{caps.data.data_dir}</code>
              </li>
            </ul>
          ) : null}
        </Panel>
      </div>
    </div>
  );
}

// 待处理的复核状态在前；条形图按数量比例分段，颜色与徽标一致
const REVIEW_ROWS = [
  ["not-reviewed", "未复核", "muted"],
  ["stale", "已失效", "bad"],
  ["disputed", "有争议", "warn"],
  ["upstream-blocked", "依据受阻", "bad"],
  ["accepted", "已确认", "good"],
] as const;

function ReviewSummary({ counts }: { counts: Record<string, number> }) {
  const total = REVIEW_ROWS.reduce((sum, [key]) => sum + (counts[key] ?? 0), 0);
  const pending = total - (counts.accepted ?? 0);
  return (
    <>
      <div className="review-total">
        <strong>{pending}</strong>
        <span>条结论待处理 · 共 {total} 条</span>
      </div>
      {total ? (
        <div className="review-bar" aria-hidden="true">
          {REVIEW_ROWS.map(([key, , tone]) =>
            counts[key] ? <span key={key} className={tone} style={{ flexGrow: counts[key] }} /> : null,
          )}
        </div>
      ) : null}
      <ul className="status-list">
        {REVIEW_ROWS.map(([key, text, tone]) => (
          <li key={key}>
            <span className={`status-dot ${tone}`} />
            <span>{text}</span>
            <strong className={counts[key] ? "" : "hint"}>{counts[key] ?? 0}</strong>
          </li>
        ))}
      </ul>
    </>
  );
}

/** 关键操作按日分组的时间线；只显示时刻，日期放在组标题上。 */
function ActivityTimeline({ items }: { items: ActivityItem[] }) {
  const groups = new Map<string, ActivityItem[]>();
  for (const item of items) {
    const key = item.at ? dayLabel(new Date(item.at)) : "时间未知";
    groups.set(key, [...(groups.get(key) ?? []), item]);
  }
  return (
    <div className="activity-timeline">
      {[...groups.entries()].map(([day, rows]) => (
        <section key={day}>
          <h4>{day}</h4>
          <ul className="activity">
            {rows.map((item) => (
              <li key={item.cursor}>
                <time title={item.at ?? ""}>{item.at ? new Date(item.at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }) : "—"}</time>
                <a href={activityHref(item)}>{item.summary}</a>
                {item.actor !== "—" ? <span className="badge muted">{item.actor}</span> : null}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

function dayLabel(date: Date): string {
  if (Number.isNaN(date.getTime())) return "时间未知";
  const today = new Date();
  const days = Math.round((new Date(today.toDateString()).getTime() - new Date(date.toDateString()).getTime()) / 86_400_000);
  if (days === 0) return "今天";
  if (days === 1) return "昨天";
  return date.toLocaleDateString("zh-CN", { year: "numeric", month: "long", day: "numeric" });
}

/** R15：工作台笔记的状态卡。待你确认、需要你回答的排在前面，AI 待处理的放最后。 */
function NotesCard() {
  const counts = useNoteCounts();
  useEffect(() => {
    const timer = window.setInterval(counts.reload, 5_000);
    return () => window.clearInterval(timer);
  }, [counts.reload]);
  const rows = [
    ["待你确认", counts.proposed, "warn"],
    ["需要你回答", counts.needsUser, "warn"],
    ["AI 待处理", counts.queued, "muted"],
  ] as const;
  return (
    <Panel title="工作台笔记" actions={<a href={href("relations")}>去处理 →</a>}>
      <ErrorBox error={counts.error} />
      <ul className="status-list">
        {rows.map(([text, n, tone]) => (
          <li key={text}>
            <span className={`status-dot ${n ? tone : "muted"}`} />
            <span>{text}</span>
            <strong className={n ? "" : "hint"}>{n}</strong>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
