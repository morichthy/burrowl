// R22 删除记录与对象：先预检（dry_run）显示影响与阻碍，填写理由后才执行；删除的记录可在对象页恢复，
// 删除的对象在"维护与监测"的回收站里恢复或由用户清空（永久删除）。
import { type ReactNode, useState } from "react";
import { useAction, useOp } from "../api";
import { label, OWNER_TYPES } from "../labels";
import { ErrorBox, href, Panel, Time } from "../ui";

interface Brief {
  record_id: string;
  owner_id: string;
  kind: string;
  level: string | null;
  title: string;
}

interface Blocker {
  target_id: string;
  reason: string;
  cited_by?: Brief[];
  nested?: string[];
  missing?: string[];
}

interface RecordsPreview {
  head: string | null;
  records: Brief[];
  blockers: Blocker[];
  allowed: boolean;
}

interface OwnerPreview {
  owner: { owner_id: string; title: string; owner_type: string };
  mode: "folder" | "memory_home";
  path: string;
  head: string | null;
  record_count: number;
  runs: string[];
  sources: string[];
  kept_original: string | null;
  blockers: Blocker[];
  allowed: boolean;
}

function Blockers({ blockers }: { blockers: Blocker[] }) {
  if (!blockers.length) return null;
  return (
    <div className="notice">
      <strong>不能执行：</strong>
      <ul>
        {blockers.map((b, i) => (
          <li key={i}>
            <code>{b.target_id}</code> {b.reason}
            {b.cited_by?.length ? (
              <>
                ：
                {b.cited_by.map((c, j) => (
                  <span key={c.record_id}>
                    {j ? "、" : ""}
                    <a href={href("memory", c.owner_id, c.record_id)}>{c.title}</a>
                    <span className="hint">（{c.owner_id}）</span>
                  </span>
                ))}
              </>
            ) : null}
            {b.nested?.length ? <>：{b.nested.join("、")}</> : null}
            {b.missing?.length ? <>：{b.missing.join("、")}</> : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** 确认框：预检结果 + 理由 + 确认按钮。 */
function Confirm({
  children,
  blockers,
  busy,
  action,
  onConfirm,
  onCancel,
}: {
  children: ReactNode;
  blockers: Blocker[];
  busy: boolean;
  action: string;
  onConfirm: (reason: string) => void;
  onCancel: () => void;
}) {
  const [reason, setReason] = useState("");
  return (
    <div className="confirm-box" role="dialog" aria-label={action}>
      {children}
      <Blockers blockers={blockers} />
      {!blockers.length ? (
        <div className="toolbar">
          <input
            placeholder="理由（例如：测试数据、重复、建错对象）"
            aria-label="删除理由"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
          <button type="button" className="danger" disabled={busy || !reason.trim()} onClick={() => onConfirm(reason)}>
            {action}
          </button>
          <button type="button" onClick={onCancel}>
            取消
          </button>
        </div>
      ) : (
        <button type="button" onClick={onCancel}>
          关闭
        </button>
      )}
    </div>
  );
}

/** 删除一条或多条记录（墓碑提交，可恢复）。 */
export function DiscardRecords({ ownerId, recordIds, label: text, onDone }: { ownerId: string; recordIds: string[]; label: string; onDone: () => void }) {
  const check = useAction<RecordsPreview>("memory.records_discard");
  const discard = useAction("memory.records_discard");
  const [preview, setPreview] = useState<RecordsPreview>();
  return (
    <>
      <button
        type="button"
        className="small danger"
        disabled={!recordIds.length || check.busy}
        onClick={async () => setPreview(await check.run({ owner_id: ownerId, record_ids: recordIds, reason: "预检", dry_run: true }))}
      >
        {text}
      </button>
      <ErrorBox error={check.error ?? discard.error} />
      {preview ? (
        <Confirm
          blockers={preview.blockers}
          busy={discard.busy}
          action={`删除 ${preview.records.length} 条记录`}
          onCancel={() => setPreview(undefined)}
          onConfirm={async (reason) => {
            const done = await discard.run({ owner_id: ownerId, record_ids: recordIds, reason, expected_head: preview.head });
            if (done) {
              setPreview(undefined);
              onDone();
            }
          }}
        >
          <p>
            将从当前记录中移除：{preview.records.map((r) => r.title).join("、")}。旧修订留在历史里，可以在对象页"已删除的记录"中恢复。
            结论错了请用复核撤回，不要删除。
          </p>
        </Confirm>
      ) : null}
    </>
  );
}

/** 对象页：已删除的记录与恢复。 */
export function DiscardedRecords({ ownerId, onRestored }: { ownerId: string; onRestored: () => void }) {
  const list = useOp<(Brief & { reason: string; removed_at: string })[]>("memory.records_discarded", { owner_id: ownerId });
  const restore = useAction("memory.records_restore");
  const rows = list.data ?? [];
  if (!rows.length && !list.error) return null;
  return (
    <details className="discarded">
      <summary>已删除的记录（{rows.length}）</summary>
      <ErrorBox error={list.error ?? restore.error} />
      <table className="table">
        <tbody>
          {rows.map((r) => (
            <tr key={r.record_id}>
              <td>{r.level ? <span className={`level ${r.level}`}>{r.level}</span> : "—"}</td>
              <td>
                {r.title}
                <div className="hint">
                  {r.reason} · <Time value={r.removed_at} />
                </div>
              </td>
              <td>
                <button
                  type="button"
                  className="small"
                  disabled={restore.busy}
                  onClick={async () => {
                    if (await restore.run({ owner_id: ownerId, record_ids: [r.record_id] })) {
                      list.reload();
                      onRestored();
                    }
                  }}
                >
                  恢复
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

/** 删除对象到回收站。 */
export function DiscardOwner({ ownerId }: { ownerId: string }) {
  const check = useAction<OwnerPreview>("memory.owner_discard");
  const discard = useAction("memory.owner_discard");
  const [preview, setPreview] = useState<OwnerPreview>();
  return (
    <>
      <button
        type="button"
        className="small danger"
        disabled={check.busy}
        onClick={async () => setPreview(await check.run({ owner_id: ownerId, reason: "预检", dry_run: true }))}
      >
        删除对象
      </button>
      {check.error ?? discard.error ? <ErrorBox error={check.error ?? discard.error} /> : null}
      {preview ? (
        <Confirm
          blockers={preview.blockers}
          busy={discard.busy}
          action="移入回收站"
          onCancel={() => setPreview(undefined)}
          onConfirm={async (reason) => {
            if (await discard.run({ owner_id: ownerId, reason, expected_head: preview.head })) window.location.hash = href("memory");
          }}
        >
          <p>
            「{preview.owner.title}」（{label(OWNER_TYPES, preview.owner.owner_type)}）将移入回收站：{preview.record_count} 条记录
            {preview.runs.length ? `、${preview.runs.length} 个 Run` : ""}
            {preview.mode === "folder" ? `，整个目录 ${preview.path}` : `；原件 ${preview.kept_original} 留在原处，只移走它的记忆`}。
            可以在"维护与监测"的回收站里恢复。
          </p>
        </Confirm>
      ) : null}
    </>
  );
}

interface TrashedOwner {
  owner_id: string;
  owner_type: string;
  title: string;
  original_path: string;
  mode: string;
  record_count: number;
  reason: string;
  discarded_at: string;
}

/** 维护与监测：对象回收站。清空是永久删除，只能由用户在这里执行。 */
export function OwnerTrashPanel() {
  const trash = useOp<TrashedOwner[]>("memory.owner_trash");
  const restore = useAction("memory.owner_restore");
  const empty = useAction("memory.owner_empty_trash");
  const rows = trash.data ?? [];
  return (
    <Panel title={`已删除的对象（${rows.length}）`}>
      <p className="hint">删除的对象在这里，可以恢复到原位置。清空回收站会永久删除这些对象的全部记录与文件。</p>
      <ErrorBox error={trash.error ?? restore.error ?? empty.error} />
      {rows.length === 0 ? <p className="hint">回收站是空的。</p> : null}
      <table className="table">
        <tbody>
          {rows.map((t) => (
            <tr key={t.owner_id}>
              <td>
                {t.title} <code>{t.owner_id}</code>
                <div className="hint">
                  {label(OWNER_TYPES, t.owner_type)} · {t.record_count} 条记录 · {t.original_path}
                </div>
              </td>
              <td className="hint">
                {t.reason} · <Time value={t.discarded_at} />
              </td>
              <td>
                <button
                  type="button"
                  className="small"
                  disabled={restore.busy}
                  onClick={async () => (await restore.run({ owner_id: t.owner_id })) && trash.reload()}
                >
                  恢复
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length ? (
        <button
          type="button"
          className="small danger"
          disabled={empty.busy}
          onClick={async () => {
            if (window.confirm(`永久删除回收站中的 ${rows.length} 个对象及其全部记录？此操作不能撤销。`) && (await empty.run({})))
              trash.reload();
          }}
        >
          清空回收站（永久删除）
        </button>
      ) : null}
    </Panel>
  );
}
