// 知识复核：待复核队列 + 一键复核 + 批量 + 历史。复核结果立即影响检索的复核过滤与中枢发布。
import { useState } from "react";
import { useAction, useOp } from "../api";
import { label, KINDS, REVIEW_BADGES } from "../labels";
import { Empty, ErrorBox, href, Loading, Panel, ReviewBadge, Time } from "../ui";

interface FixedRef {
  target_kind: string;
  target_id: string;
  revision: number | null;
  sha256: string | null;
  locator: string;
  relation: string;
}

export interface QueueItem {
  claim_id: string;
  status: string;
  statement: string;
  scope: string;
  owner_id: string;
  record_id: string;
  record_title: string;
  record_kind: string;
  evidence_refs: FixedRef[];
  review: { state: string; reason: string; scope: string | null; updated_at: string; revision: number } | null;
  problems: { code: string; message: string; target_id: string }[];
}

interface Queue {
  items: QueueItem[];
  total: number;
  counts: Record<string, number>;
}

type Action = "accepted" | "disputed" | "retracted" | "superseded";
const ACTIONS: { state: Action; label: string; tone: string }[] = [
  { state: "accepted", label: "确认", tone: "primary" },
  { state: "disputed", label: "质疑", tone: "" },
  { state: "retracted", label: "撤回", tone: "danger" },
  { state: "superseded", label: "替代", tone: "" },
];
const TEMPLATES: Record<Action, string[]> = {
  accepted: ["依据可复算，结论在所述范围内成立", "已核对原始数据与方法"],
  disputed: ["样本或条件不足以支持结论", "与其他结果冲突，需要复查"],
  retracted: ["依据有误，结论不成立", "数据或计算错误"],
  superseded: ["已有更准确的替代结论"],
};
const QUEUE_STATES = ["not-reviewed", "stale", "disputed", "upstream-blocked", "accepted", "retracted", "superseded"];
const DEFAULT_STATES = ["not-reviewed", "stale", "disputed", "upstream-blocked"];

interface Draft {
  item: QueueItem;
  state: Action;
  reason: string;
  scope: string;
  replacement: string;
  preview?: unknown;
}

export function Review() {
  const [states, setStates] = useState<string[]>(DEFAULT_STATES);
  const [owner, setOwner] = useState("");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [draft, setDraft] = useState<Draft>();
  const [historyOf, setHistoryOf] = useState<string>();
  const queue = useOp<Queue>("review.queue", {
    states: states as never,
    owner_id: owner.trim() || null,
    query,
    limit: 200,
  });
  const submit = useAction("review.submit");
  const batch = useAction("review.batch");
  const [batchResult, setBatchResult] = useState<string>();

  const toggle = (state: string) =>
    setStates((current) => (current.includes(state) ? current.filter((s) => s !== state) : [...current, state]));

  const open = async (item: QueueItem, state: Action) => {
    const next: Draft = { item, state, reason: TEMPLATES[state][0], scope: item.scope, replacement: "" };
    setDraft(next);
    submit.clear();
    if (state === "accepted") {
      // 一键确认：先自动预检，显示依据与适用范围后再提交。
      const preview = await submit.run(argsOf(next, true));
      setDraft({ ...next, preview });
    }
  };

  const confirm = async () => {
    if (!draft) return;
    if (await submit.run(argsOf(draft, false))) {
      setDraft(undefined);
      queue.reload();
    }
  };

  const runBatch = async (state: Action) => {
    const reason = window.prompt(`批量"${ACTIONS.find((a) => a.state === state)?.label}"的理由`, TEMPLATES[state][0]);
    if (!reason) return;
    const items = (queue.data?.items ?? [])
      .filter((i) => selected.has(i.claim_id))
      .map((i) => ({
        target_claim_id: i.claim_id,
        state,
        reason,
        ...(state === "accepted" ? { scope: i.scope, evidence_refs: i.evidence_refs } : {}),
      }));
    const result = await batch.run({ items, dry_run: false });
    if (result) {
      setBatchResult(`成功 ${result.succeeded} 条，失败 ${result.failed} 条`);
      setSelected(new Set());
      queue.reload();
    }
  };

  const items = queue.data?.items ?? [];
  return (
    <>
      <Panel
        title="知识复核"
        actions={
          <button type="button" onClick={queue.reload}>
            刷新
          </button>
        }
      >
        <p className="hint">
          复核只认可在所述"适用范围"内成立的结论。记录内容一旦修改，原复核自动失效并重新出现在队列中。
        </p>
        <div className="toolbar">
          <div className="chips">
            {QUEUE_STATES.map((state) => (
              <label key={state} className={states.includes(state) ? "on" : ""}>
                <input type="checkbox" checked={states.includes(state)} onChange={() => toggle(state)} />
                {REVIEW_BADGES[state]?.label ?? state} <span className="count">{queue.data?.counts[state] ?? 0}</span>
              </label>
            ))}
          </div>
          <input placeholder="对象 ID" value={owner} onChange={(e) => setOwner(e.target.value)} />
          <input placeholder="搜索结论或记录标题" value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
        {selected.size ? (
          <div className="batchbar">
            已选 {selected.size} 条：
            {ACTIONS.filter((a) => a.state !== "superseded").map((a) => (
              <button key={a.state} type="button" className={a.tone} disabled={batch.busy} onClick={() => runBatch(a.state)}>
                批量{a.label}
              </button>
            ))}
            <button type="button" onClick={() => setSelected(new Set())}>
              取消选择
            </button>
          </div>
        ) : null}
        {batchResult ? <div className="notice">{batchResult}</div> : null}
        <ErrorBox error={queue.error ?? batch.error} />
        <Loading show={queue.loading && !queue.data} />
        {queue.data && !items.length ? <Empty>当前筛选下没有需要处理的结论。</Empty> : null}
        <ul className="claims">
          {items.map((item) => (
            <li key={item.claim_id}>
              <input
                type="checkbox"
                aria-label={`选择 ${item.claim_id}`}
                checked={selected.has(item.claim_id)}
                onChange={() =>
                  setSelected((current) => {
                    const next = new Set(current);
                    if (next.has(item.claim_id)) next.delete(item.claim_id);
                    else next.add(item.claim_id);
                    return next;
                  })
                }
              />
              <div className="claim-body">
                <div>
                  <ReviewBadge value={item.status} /> <strong>{item.statement}</strong>
                </div>
                <div className="hint">
                  适用范围：{item.scope} · 来自{label(KINDS, item.record_kind)}「
                  <a href={href("memory", item.owner_id, item.record_id)}>{item.record_title}</a>」 · <code>{item.claim_id}</code>
                </div>
                {item.review ? (
                  <div className="hint">
                    上次复核：{REVIEW_BADGES[item.review.state]?.label ?? item.review.state}（{item.review.reason}）
                    <Time value={item.review.updated_at} />
                  </div>
                ) : null}
                {item.problems.length ? (
                  <ul className="problems">
                    {item.problems.map((p, i) => (
                      <li key={i}>
                        <code>{p.code}</code> {p.message}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>
              <div className="actions">
                {ACTIONS.map((a) => (
                  <button key={a.state} type="button" className={`small ${a.tone}`} onClick={() => open(item, a.state)}>
                    {a.label}
                  </button>
                ))}
                <button type="button" className="small" onClick={() => setHistoryOf(item.claim_id)}>
                  历史
                </button>
              </div>
            </li>
          ))}
        </ul>
      </Panel>
      {draft ? (
        <ReviewDialog
          draft={draft}
          setDraft={setDraft}
          busy={submit.busy}
          error={submit.error}
          onConfirm={confirm}
          onCancel={() => setDraft(undefined)}
        />
      ) : null}
      {historyOf ? <History claimId={historyOf} onClose={() => setHistoryOf(undefined)} /> : null}
    </>
  );
}

function argsOf(draft: Draft, dryRun: boolean) {
  return {
    target_claim_id: draft.item.claim_id,
    state: draft.state,
    reason: draft.reason,
    scope: draft.state === "accepted" ? draft.scope : null,
    evidence_refs: draft.state === "accepted" ? draft.item.evidence_refs : null,
    replacement_claim_id: draft.state === "superseded" ? draft.replacement || null : null,
    dry_run: dryRun,
  };
}

function ReviewDialog(props: {
  draft: Draft;
  setDraft: (d: Draft) => void;
  busy: boolean;
  error: ReturnType<typeof useAction>["error"];
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const { draft, setDraft } = props;
  const action = ACTIONS.find((a) => a.state === draft.state)!;
  return (
    <div className="dialog" role="dialog" aria-label="复核">
      <div className="dialog-body">
        <h3>
          {action.label}：{draft.item.statement}
        </h3>
        <label>
          理由
          <select value="" onChange={(e) => e.target.value && setDraft({ ...draft, reason: e.target.value })}>
            <option value="">从模板选择…</option>
            {TEMPLATES[draft.state].map((t) => (
              <option key={t}>{t}</option>
            ))}
          </select>
          <textarea value={draft.reason} onChange={(e) => setDraft({ ...draft, reason: e.target.value })} />
        </label>
        {draft.state === "accepted" ? (
          <>
            <label>
              适用范围（必须与结论声明的范围一致）
              <input value={draft.scope} onChange={(e) => setDraft({ ...draft, scope: e.target.value })} />
            </label>
            <div>
              依据（{draft.item.evidence_refs.length} 条）：
              <ul>
                {draft.item.evidence_refs.map((r, i) => (
                  <li key={i}>
                    <code>{r.target_id}</code> {r.revision != null ? `@${r.revision}` : ""} {r.locator}
                  </li>
                ))}
              </ul>
            </div>
            {draft.preview ? <div className="notice">预检通过，可以提交。</div> : null}
          </>
        ) : null}
        {draft.state === "superseded" ? (
          <label>
            替代结论 ID
            <input value={draft.replacement} onChange={(e) => setDraft({ ...draft, replacement: e.target.value })} />
          </label>
        ) : null}
        <ErrorBox error={props.error} />
        <div className="actions">
          <button type="button" className={action.tone || "primary"} disabled={props.busy || !draft.reason.trim()} onClick={props.onConfirm}>
            提交{action.label}
          </button>
          <button type="button" onClick={props.onCancel}>
            取消
          </button>
        </div>
      </div>
    </div>
  );
}

function History({ claimId, onClose }: { claimId: string; onClose: () => void }) {
  const history = useOp<{ revisions: { state: string; reason: string; revision: number; updated_at: string; actor?: { id: string } }[] }>(
    "review.history",
    { claim_id: claimId },
  );
  return (
    <div className="dialog" role="dialog" aria-label="复核历史">
      <div className="dialog-body">
        <h3>复核历史 · {claimId}</h3>
        <ErrorBox error={history.error} />
        <Loading show={history.loading} />
        {history.data && !history.data.revisions.length ? <Empty>还没有复核记录。</Empty> : null}
        <ol>
          {history.data?.revisions.map((r) => (
            <li key={r.revision}>
              <ReviewBadge value={r.state} /> {r.reason} <Time value={r.updated_at} />
            </li>
          ))}
        </ol>
        <button type="button" onClick={onClose}>
          关闭
        </button>
      </div>
    </div>
  );
}
