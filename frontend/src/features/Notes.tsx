// 工作台笔记（R15）：用户写下的分组名、桥接问题、关系设想，可以交给 AI 处理。
// AI 只能回写方案、提问或说明不处理；方案由用户在这里看预览后执行，服务端按预览原样写入。
// 笔记不是研究记录：不进检索和复核。状态与转换见 docs/design/R15_WORKBENCH_NOTES.md §3。
import { useState } from "react";
import { useAction, useOp } from "../api";
import { label } from "../labels";
import { Empty, ErrorBox, href, Panel, Tag, Time } from "../ui";

export const NOTE_KINDS: Record<string, string> = { "cluster-name": "分组命名", "bridge-question": "桥接问题", "relation-idea": "关系设想" };
const STATUS: Record<string, string> = {
  open: "草稿",
  queued: "AI 处理中",
  proposed: "待你确认",
  needs_user: "需要你回答",
  handled: "已处理",
  done: "已完成",
  dismissed: "已放弃",
};
const EVENT: Record<string, string> = {
  queued: "交给 AI",
  open: "收回或重新打开",
  done: "完成",
  dismissed: "放弃",
  proposal: "AI 提出方案",
  question: "AI 提问",
  answer: "你的回答",
  declined: "AI 说明不处理",
  rejected: "你退回了方案",
  applied: "已执行",
  stale: "依据已变化，未写入",
  failed: "执行失败",
};

interface Write {
  op: string;
  owner_id: string;
  commit_id: string | null;
  record_ids: string[];
}
interface PreviewRecord {
  record_id: string | null;
  expected_revision: number | null;
  kind: string;
  title: string;
  excerpt: string;
  claims: string[];
}
type Preview =
  | { action: "records"; owner_id: string; records: PreviewRecord[] }
  | {
      action: "association";
      owner_id: string;
      from: { id: string; title: string | null };
      to: { id: string; title: string | null };
      relation: string;
      status: string;
      explanation: string;
    };
export interface Note {
  id: string;
  kind: string;
  title: string;
  text: string;
  refs: string[];
  status: string;
  created_at: string;
  updated_at?: string;
  thread: { at: string; by: string; kind: string; text: string; writes?: Write[] }[];
  proposal?: { id: string; message: string; created_at: string; preview: Preview[] };
  result?: { message: string; declined?: boolean; writes: Write[] };
}

/** 等用户处理的笔记数（待你确认 + 需要你回答），供导航和首页显示。 */
export function useNoteCounts() {
  const notes = useOp<Note[]>("relations.notes", { status: ["proposed", "needs_user", "queued"] });
  const count = (status: string) => notes.data?.filter((n) => n.status === status).length ?? 0;
  return { proposed: count("proposed"), needsUser: count("needs_user"), queued: count("queued"), reload: notes.reload, error: notes.error };
}

export function Notes({ focus }: { focus?: { id: string; title: string } }) {
  const notes = useOp<Note[]>("relations.notes");
  const add = useAction("relations.note_add");
  const resolve = useAction("relations.note_resolve");
  const answer = useAction("relations.note_answer");
  const apply = useAction<Note>("relations.note_apply");
  const reject = useAction("relations.note_reject");
  const clear = useAction("relations.note_clear");
  const [kind, setKind] = useState("bridge-question");
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const all = notes.data ?? [];
  const pick = (...states: string[]) => all.filter((n) => states.includes(n.status));
  const mine = pick("proposed", "needs_user");
  const drafts = pick("open");
  const queued = pick("queued");
  const closed = pick("handled", "done", "dismissed");
  // 等写操作完成后再刷新列表：传进来的是请求的 Promise，提前刷新会读到旧状态
  const after = async (pending: Promise<unknown>) => {
    if (await pending) notes.reload();
  };
  const move = (id: string, status: "open" | "queued" | "done" | "dismissed") => after(resolve.run({ note_id: id, status }));
  const error = notes.error ?? add.error ?? resolve.error ?? answer.error ?? apply.error ?? reject.error ?? clear.error;
  return (
    <Panel title={`工作台笔记（${drafts.length + queued.length + mine.length}）`}>
      <p className="hint">
        看图时的工作便签：给一组节点起名、记下想验证的桥接问题或关系设想。不是研究记录，不进检索和复核。
        点"交给 AI"后，AI 在下次对话里处理：要写入记忆时先给出方案，由你在这里看预览后执行。
      </p>
      <ErrorBox error={error} />
      <div className="form">
        <select value={kind} onChange={(e) => setKind(e.target.value)} aria-label="笔记类型">
          {Object.entries(NOTE_KINDS).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
        <input placeholder="标题" value={title} onChange={(e) => setTitle(e.target.value)} />
        <textarea placeholder="内容（可选）" value={text} onChange={(e) => setText(e.target.value)} />
        <button
          type="button"
          disabled={!title.trim() || add.busy}
          onClick={async () => {
            if (await add.run({ kind: kind as "bridge-question", title, text, refs: focus ? [focus.id] : [] })) {
              setTitle("");
              setText("");
              notes.reload();
            }
          }}
        >
          添加{focus ? `（关联 ${focus.title.slice(0, 12)}）` : ""}
        </button>
      </div>

      {mine.length ? (
        <section className="note-group">
          <h4>等你处理（{mine.length}）</h4>
          {mine.map((n) =>
            n.status === "proposed" ? (
              <ProposalCard
                key={n.id}
                note={n}
                busy={apply.busy || reject.busy}
                onApply={() => after(apply.run({ note_id: n.id, proposal_id: n.proposal!.id }))}
                onReject={(comment) => after(reject.run({ note_id: n.id, proposal_id: n.proposal!.id, comment }))}
                onDrop={() => move(n.id, "dismissed")}
              />
            ) : (
              <QuestionCard key={n.id} note={n} busy={answer.busy} onAnswer={(t) => after(answer.run({ note_id: n.id, text: t }))} onDrop={() => move(n.id, "dismissed")} />
            ),
          )}
        </section>
      ) : null}

      {drafts.length ? (
        <section className="note-group">
          <h4>草稿（{drafts.length}）</h4>
          <ul className="claims">
            {drafts.map((n) => (
              <li key={n.id}>
                <NoteHead note={n} />
                <div className="actions">
                  <button type="button" className="small primary" onClick={() => move(n.id, "queued")}>
                    交给 AI
                  </button>
                  <button type="button" className="small" onClick={() => move(n.id, "done")}>
                    完成（无需 AI）
                  </button>
                  <button type="button" className="small" onClick={() => move(n.id, "dismissed")}>
                    放弃
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {queued.length ? (
        <section className="note-group">
          <h4>AI 处理中（{queued.length}）</h4>
          <p className="hint">AI 会在下次对话中处理；你也可以直接对 AI 说"处理工作台笔记"。</p>
          <ul className="claims">
            {queued.map((n) => (
              <li key={n.id}>
                <NoteHead note={n} />
                <button type="button" className="small" onClick={() => move(n.id, "open")}>
                  收回
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {!all.length && notes.data ? <Empty>还没有笔记。</Empty> : null}

      {closed.length ? (
        <details className="closed-notes">
          <summary>已处理（{closed.length}）</summary>
          <ul className="claims">
            {closed.map((n) => (
              <li key={n.id}>
                <div className="claim-body">
                  <NoteHead note={n} />
                  {n.result ? <div className="hint">{n.result.message}</div> : null}
                  {n.result?.writes.length ? <Writes writes={n.result.writes} /> : null}
                </div>
                <button type="button" className="small" onClick={() => move(n.id, "open")}>
                  重新打开
                </button>
              </li>
            ))}
          </ul>
          <button
            type="button"
            className="small danger"
            disabled={clear.busy}
            onClick={() => {
              if (window.confirm(`删除这 ${closed.length} 条已处理的笔记？删除后不能恢复；已写入记忆的记录不受影响。`)) after(clear.run({}));
            }}
          >
            清除已处理
          </button>
        </details>
      ) : null}
    </Panel>
  );
}

function NoteHead({ note }: { note: Note }) {
  const last = note.thread.at(-1);
  return (
    <div className="claim-body">
      <Tag>{NOTE_KINDS[note.kind] ?? note.kind}</Tag>
      {note.status !== "open" ? <Tag tone={note.status === "handled" || note.status === "done" ? "good" : "muted"}>{label(STATUS, note.status)}</Tag> : null}
      <strong>{note.title}</strong> <code>{note.id}</code>
      {note.text ? <div className="hint">{note.text}</div> : null}
      {last && ["stale", "failed", "rejected"].includes(last.kind) ? (
        <div className="hint">
          {EVENT[last.kind]}：{last.text}
        </div>
      ) : null}
      <div className="hint">
        <Time value={note.updated_at ?? note.created_at} />
        {note.refs.length ? ` · 关联 ${note.refs.join("、")}` : ""}
      </div>
    </div>
  );
}

function ProposalCard(props: { note: Note; busy: boolean; onApply: () => void; onReject: (comment: string) => void; onDrop: () => void }) {
  const { note } = props;
  const proposal = note.proposal!;
  return (
    <div className="note-card">
      <NoteHead note={note} />
      <div className="note-ai">
        <strong>AI 的方案：</strong>
        {proposal.message}
      </div>
      <ol className="note-preview" aria-label="写入预览">
        {proposal.preview.map((p, i) => (
          <li key={i}>{p.action === "records" ? <RecordsPreview preview={p} /> : <AssociationPreview preview={p} />}</li>
        ))}
      </ol>
      <p className="hint">点"执行"后按上面的预览原样写入；如果这些对象在此期间有了新提交，不会写入，笔记会退回给 AI 重新出方案。</p>
      <div className="actions">
        <button type="button" className="small primary" disabled={props.busy} onClick={props.onApply}>
          执行
        </button>
        <button
          type="button"
          className="small"
          disabled={props.busy}
          onClick={() => {
            const comment = window.prompt("退回给 AI 的意见（可选）", "");
            if (comment !== null) props.onReject(comment);
          }}
        >
          退回
        </button>
        <button type="button" className="small" onClick={props.onDrop}>
          放弃
        </button>
      </div>
    </div>
  );
}

function RecordsPreview({ preview }: { preview: Extract<Preview, { action: "records" }> }) {
  return (
    <>
      {preview.records.map((r, i) => (
        <div key={i} className="note-preview-item">
          <Tag tone={r.record_id ? "warn" : "good"}>{r.record_id ? `修订 ${r.record_id}（第 ${r.expected_revision} 版 → 新版）` : "新建"}</Tag>
          <Tag>{r.kind}</Tag> <strong>{r.title}</strong> <span className="hint">→ {preview.owner_id}</span>
          {r.excerpt ? <div className="excerpt">{r.excerpt}</div> : null}
          {r.claims.length ? (
            <ul className="hint">
              {r.claims.map((c, j) => (
                <li key={j}>结论（未复核）：{c}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ))}
    </>
  );
}

function AssociationPreview({ preview }: { preview: Extract<Preview, { action: "association" }> }) {
  return (
    <div className="note-preview-item">
      <Tag tone="good">关联</Tag> {preview.from.title ?? preview.from.id} ↔ {preview.to.title ?? preview.to.id}{" "}
      <span className="hint">
        （{preview.relation} · {preview.status} · 写进 {preview.owner_id}）
      </span>
      {preview.explanation ? <div className="excerpt">{preview.explanation}</div> : null}
    </div>
  );
}

function QuestionCard(props: { note: Note; busy: boolean; onAnswer: (text: string) => void; onDrop: () => void }) {
  const [reply, setReply] = useState("");
  const question = [...props.note.thread].reverse().find((e) => e.kind === "question");
  return (
    <div className="note-card">
      <NoteHead note={props.note} />
      <div className="note-ai">
        <strong>AI 问：</strong>
        {question?.text}
      </div>
      <textarea placeholder="你的回答" value={reply} onChange={(e) => setReply(e.target.value)} aria-label={`回答 ${props.note.id}`} />
      <div className="actions">
        <button type="button" className="small primary" disabled={!reply.trim() || props.busy} onClick={() => props.onAnswer(reply)}>
          回答
        </button>
        <button type="button" className="small" onClick={props.onDrop}>
          放弃
        </button>
      </div>
    </div>
  );
}

function Writes({ writes }: { writes: Write[] }) {
  return (
    <div className="hint">
      已写入：
      {writes.map((w, i) => (
        <span key={i}>
          {w.record_ids.length
            ? w.record_ids.map((id) => (
                <a key={id} href={href("memory", w.owner_id, id)}>
                  {id}{" "}
                </a>
              ))
            : `${w.owner_id} `}
        </span>
      ))}
    </div>
  );
}
