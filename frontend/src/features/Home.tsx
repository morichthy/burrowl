// 首页：当前阅读、待处理事项、最近关键操作、复核概况、能力与降级情况。
import { useEffect } from "react";
import { useOp } from "../api";
import { label, SESSION_STATUS } from "../labels";
import { ErrorBox, href, Panel, Tag, Time } from "../ui";
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
  const latest = sessions.data?.find((s) => !["archived"].includes(s.status));
  const counts = review.data?.counts;
  return (
    <div className="grid">
      <Panel title="当前阅读">
        <ErrorBox error={sessions.error} />
        {latest ? (
          <>
            <a href={href("reading", latest.session_id)}>{latest.question}</a>
            <p>
              <Tag>{label(SESSION_STATUS, latest.status)}</Tag> 相关 {latest.relevant} · 笔记 {latest.notes} · <Time value={latest.updated_at} />
            </p>
          </>
        ) : (
          <p className="hint">还没有阅读会话。</p>
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
      </Panel>
      <Panel title="最近关键操作">
        <ErrorBox error={activity.error} />
        {activity.data?.items.length ? (
          <ul className="activity">
            {activity.data.items.map((item) => (
              <li key={item.cursor}>
                <Time value={item.at} /> <a href={activityHref(item)}>{item.summary}</a>
                {item.actor !== "—" ? <span className="hint"> · {item.actor}</span> : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="hint">还没有关键操作（建立对象、保存、复核、Run 结束、发布）。</p>
        )}
      </Panel>
      <Panel title="复核">
        <ErrorBox error={review.error} />
        {counts ? (
          <div className="stats">
            {(
              [
                ["not-reviewed", "未复核", ""],
                ["stale", "已失效", "bad"],
                ["disputed", "有争议", "warn"],
                ["upstream-blocked", "依据受阻", "bad"],
                ["accepted", "已确认", "good"],
              ] as const
            ).map(([key, text, tone]) => (
              <div key={key} className={`stat ${counts[key] ? tone : ""}`}>
                <strong>{counts[key] ?? 0}</strong>
                <span>{text}</span>
              </div>
            ))}
          </div>
        ) : null}
        <a href={href("review")}>进入知识复核 →</a>
      </Panel>
      <Panel title="运行环境">
        <ErrorBox error={caps.error} />
        {caps.data ? (
          <dl className="facts">
            <dt>数据目录</dt>
            <dd>
              <code>{caps.data.data_dir}</code>
            </dd>
            <dt>向量检索</dt>
            <dd>
              {caps.data.vector.ready ? <Tag tone="good">可用</Tag> : <Tag tone="warn">未就绪，仅关键词检索</Tag>} {caps.data.vector.backend}{" "}
              {caps.data.vector.embedder ?? ""}
            </dd>
            <dt>重排模型</dt>
            <dd>{caps.data.reranker.ready ? <Tag tone="good">可用</Tag> : <Tag>未安装</Tag>}</dd>
            <dt>知识中枢</dt>
            <dd>
              {caps.data.connectors.length ? caps.data.connectors.map((c) => <Tag key={c.id}>{c.id}</Tag>) : "未配置"}{" "}
              <a href={href("hub")}>管理 →</a>
            </dd>
          </dl>
        ) : null}
      </Panel>
    </div>
  );
}
