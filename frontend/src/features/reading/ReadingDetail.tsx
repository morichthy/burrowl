// 阅读会话实时页：agent 推进会话的同时，用户在这里查看并干预。
// 用户的每个操作都进入会话 inbox，agent 下一次调用 reading.* 时会收到。
import { useState } from "react";
import { useAction, useOp, useReadingEvents } from "../../api";
import { label, LEVELS, REQUEST_KINDS, SESSION_STATUS, STRATEGIES, VERDICTS } from "../../labels";
import { ErrorBox, href, Loading, Markdown, Panel, ReviewBadge, Tag, Time } from "../../ui";

interface Window {
  record_id: string;
  title: string;
  level: string | null;
  kind: string;
  review: string;
  text: string;
}

interface OwnerState {
  title: string;
  rank: number;
  round: string;
  verdict: string | null;
  reason: string;
  judged_by: string | null;
  pages_read: number[];
  total_pages: number | null;
  note: { text: string; sources: string[]; tokens: number; updated_at: string } | null;
  packet: { windows: Window[]; extra_windows: Window[]; score: number };
}

interface UserRequest {
  request_id: string;
  kind: string;
  prompt: string;
  options: string[];
  status: string;
  answer: string | null;
  approval_id: string | null;
  created_at: string;
}

export interface SessionView {
  session_id: string;
  question: string;
  conditions: string[];
  status: string;
  phase: string;
  strategy: string;
  review_filter: string;
  revision: number;
  owners: Record<string, OwnerState>;
  queue: string[];
  requests: UserRequest[];
  timeline: { at: string; action: string; summary: string; round?: string }[];
  rounds: { id: string; kind: string; owners: string[]; at: string }[];
  synthesis: { note: string; owner_ids: string[]; updated_at: string } | null;
  association: { enabled: boolean; text: string; rounds_used: number; max_rounds: number };
  plan: { variants: { language: string; question: string }[] };
}

const CLOSED = new Set(["aborted", "archived"]);

export function ReadingDetail({ sessionId }: { sessionId: string }) {
  const view = useOp<SessionView>("reading.view", { session_id: sessionId });
  useReadingEvents(sessionId, (revision) => {
    if (revision !== view.data?.revision) view.reload();
  });
  const s = view.data;
  return (
    <>
      <p>
        <a href={href("reading")}>← 全部会话</a>
      </p>
      <ErrorBox error={view.error} />
      <Loading show={view.loading && !s} />
      {s ? (
        <>
          <Panel
            title={s.question}
            actions={
              <>
                <Tag tone={s.status === "waiting_user" ? "warn" : "muted"}>{label(SESSION_STATUS, s.status)}</Tag>
                <Tag>第 {s.revision} 版</Tag>
              </>
            }
          >
            <dl className="facts">
              <dt>策略</dt>
              <dd>{label(STRATEGIES, s.strategy)}</dd>
              <dt>复核过滤</dt>
              <dd>{s.review_filter}</dd>
              <dt>条件</dt>
              <dd>{s.conditions.length ? s.conditions.join("；") : "—"}</dd>
              <dt>检索改写</dt>
              <dd>{s.plan?.variants?.map((v) => v.question).join(" ｜ ") || "—"}</dd>
            </dl>
          </Panel>
          <PendingRequests session={s} onDone={view.reload} />
          <Controls session={s} onDone={view.reload} />
          <Owners session={s} onDone={view.reload} />
          {s.synthesis ? (
            <Panel title="跨对象综合">
              <Markdown text={s.synthesis.note} />
              <p className="hint">
                依据：{s.synthesis.owner_ids.join("、")} · <Time value={s.synthesis.updated_at} />
              </p>
            </Panel>
          ) : null}
          <Panel title="时间线">
            <ol className="timeline">
              {s.timeline
                .slice()
                .reverse()
                .map((t, i) => (
                  <li key={i}>
                    <Time value={t.at} /> <code>{t.action}</code> {t.summary}
                  </li>
                ))}
            </ol>
          </Panel>
        </>
      ) : null}
    </>
  );
}

function PendingRequests({ session, onDone }: { session: SessionView; onDone: () => void }) {
  const respond = useAction("reading.respond");
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const pending = session.requests.filter((r) => r.status === "pending");
  const answered = session.requests.filter((r) => r.status !== "pending");
  if (!session.requests.length) return null;
  const send = async (r: UserRequest, decision: "approve" | "deny" | "answer", answer = answers[r.request_id] ?? "") => {
    if (await respond.run({ session_id: session.session_id, request_id: r.request_id, decision, answer })) onDone();
  };
  return (
    <Panel title={`AI 的请求${pending.length ? `（待处理 ${pending.length}）` : ""}`}>
      <ErrorBox error={respond.error} />
      {pending.map((r) => (
        <div key={r.request_id} className="request pending">
          <div>
            <Tag tone="warn">{label(REQUEST_KINDS, r.kind)}</Tag> <Time value={r.created_at} />
          </div>
          <p>{r.prompt}</p>
          {r.options.length ? (
            <div className="options">
              {r.options.map((o) => (
                <button key={o} type="button" disabled={respond.busy} onClick={() => send(r, "answer", o)}>
                  {o}
                </button>
              ))}
            </div>
          ) : null}
          <textarea
            placeholder={r.kind === "fulltext" ? "可选：批准或拒绝的说明" : "回答或补充说明"}
            value={answers[r.request_id] ?? ""}
            onChange={(e) => setAnswers({ ...answers, [r.request_id]: e.target.value })}
          />
          <div className="actions">
            {r.kind === "fulltext" ? (
              <>
                <button type="button" className="primary" disabled={respond.busy} onClick={() => send(r, "approve")}>
                  批准全文补偿
                </button>
                <button type="button" disabled={respond.busy} onClick={() => send(r, "deny")}>
                  拒绝
                </button>
              </>
            ) : (
              <button type="button" className="primary" disabled={respond.busy} onClick={() => send(r, "answer")}>
                回答
              </button>
            )}
          </div>
        </div>
      ))}
      {answered.length ? (
        <details>
          <summary>已处理 {answered.length} 条</summary>
          {answered.map((r) => (
            <div key={r.request_id} className="request">
              <Tag>{label(REQUEST_KINDS, r.kind)}</Tag> {r.prompt} → <strong>{r.status}</strong>
              {r.answer ? `：${r.answer}` : ""}
            </div>
          ))}
        </details>
      ) : null}
    </Panel>
  );
}

function Controls({ session, onDone }: { session: SessionView; onDone: () => void }) {
  const direct = useAction("reading.direct");
  const configure = useAction("reading.configure");
  const abort = useAction("reading.abort");
  const archive = useAction("reading.archive");
  const [direction, setDirection] = useState("");
  const closed = CLOSED.has(session.status);
  return (
    <Panel title="干预">
      <ErrorBox error={direct.error ?? configure.error ?? abort.error ?? archive.error} />
      <div className="row">
        <input
          placeholder={session.association.text ? `当前联想方向：${session.association.text}` : "补充联想方向（下一轮补查使用）"}
          value={direction}
          onChange={(e) => setDirection(e.target.value)}
          disabled={closed}
        />
        <button
          type="button"
          disabled={closed || !direction.trim() || direct.busy}
          onClick={async () => {
            if (await direct.run({ session_id: session.session_id, text: direction.trim() })) {
              setDirection("");
              onDone();
            }
          }}
        >
          发送方向
        </button>
      </div>
      <div className="row">
        <label>
          策略{" "}
          <select
            value={session.strategy}
            disabled={closed || configure.busy}
            onChange={async (e) =>
              (await configure.run({ session_id: session.session_id, strategy: e.target.value as "standard" })) && onDone()
            }
          >
            {Object.entries(STRATEGIES).map(([value, text]) => (
              <option key={value} value={value}>
                {text}
              </option>
            ))}
          </select>
        </label>
        {closed ? (
          session.status === "archived" ? (
            <button
              type="button"
              onClick={async () => (await archive.run({ session_id: session.session_id, archived: false })) && onDone()}
            >
              恢复
            </button>
          ) : null
        ) : (
          <button
            type="button"
            className="danger"
            onClick={async () => {
              if (window.confirm("中止后 AI 不能再推进这个会话，确定吗？")) {
                if (await abort.run({ session_id: session.session_id, reason: "用户在工作台中止" })) onDone();
              }
            }}
          >
            中止会话
          </button>
        )}
        {session.status === "finished" || session.status === "aborted" ? (
          <button
            type="button"
            onClick={async () => (await archive.run({ session_id: session.session_id, archived: true })) && onDone()}
          >
            归档
          </button>
        ) : null}
      </div>
    </Panel>
  );
}

function Owners({ session, onDone }: { session: SessionView; onDone: () => void }) {
  const override = useAction("reading.override");
  const owners = Object.entries(session.owners).sort((a, b) => a[1].rank - b[1].rank);
  const closed = CLOSED.has(session.status);
  return (
    <Panel title={`筛选与阅读（${owners.length} 个对象）`}>
      <ErrorBox error={override.error} />
      {owners.map(([ownerId, o]) => (
        <article key={ownerId} className="owner-card">
          <header>
            <h3>
              <a href={href("memory", ownerId)}>{o.title}</a> <code>{ownerId}</code>
            </h3>
            <div className="actions">
              {o.verdict ? (
                <Tag tone={o.verdict === "relevant" ? "good" : o.verdict === "uncertain" ? "warn" : "muted"}>
                  {label(VERDICTS, o.verdict)}
                  {o.judged_by === "user" ? "（用户）" : ""}
                </Tag>
              ) : (
                <Tag>未判断</Tag>
              )}
              {o.pages_read.length ? (
                <Tag>
                  已读 {o.pages_read.length}/{o.total_pages ?? "?"} 页
                </Tag>
              ) : null}
              {!closed
                ? Object.entries(VERDICTS)
                    .filter(([v]) => v !== o.verdict)
                    .map(([v, text]) => (
                      <button
                        key={v}
                        type="button"
                        className="small"
                        disabled={override.busy}
                        onClick={async () => {
                          const reason = window.prompt(`改判为"${text}"的理由（可留空）`) ?? undefined;
                          if (reason === undefined) return;
                          if (await override.run({ session_id: session.session_id, owner_id: ownerId, verdict: v as "relevant", reason }))
                            onDone();
                        }}
                      >
                        改为{text}
                      </button>
                    ))
                : null}
            </div>
          </header>
          {o.reason ? <p className="hint">理由：{o.reason}</p> : null}
          <details>
            <summary>命中窗口（{o.packet.windows.length + o.packet.extra_windows.length}）</summary>
            {[...o.packet.windows, ...o.packet.extra_windows].map((w, i) => (
              <div key={i} className="window">
                <div>
                  <strong>{w.title}</strong> <Tag>{label(LEVELS, w.level) || w.kind}</Tag> <ReviewBadge value={w.review} />
                </div>
                <Markdown text={w.text} />
              </div>
            ))}
          </details>
          {o.note ? (
            <div className="note">
              <h4>
                研究笔记 <span className="hint">约 {o.note.tokens} token · <Time value={o.note.updated_at} /></span>
              </h4>
              <Markdown text={o.note.text} />
            </div>
          ) : null}
        </article>
      ))}
    </Panel>
  );
}
