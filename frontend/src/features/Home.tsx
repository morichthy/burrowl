// 首页：当前阅读、待处理事项、复核概况、能力与降级情况。
import { useEffect } from "react";
import { useOp } from "../api";
import { label, SESSION_STATUS } from "../labels";
import { ErrorBox, href, Panel, Tag, Time } from "../ui";
import type { SessionSummary } from "./reading/ReadingList";

export function Home() {
  const sessions = useOp<SessionSummary[]>("reading.list", { limit: 20 });
  // MCP 创建会话时工作台可能一直停留在首页；轮询保证新会话能出现，聚焦时立即补刷。
  useEffect(() => {
    const timer = window.setInterval(sessions.reload, 5_000);
    window.addEventListener("focus", sessions.reload);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", sessions.reload);
    };
  }, [sessions.reload]);
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
