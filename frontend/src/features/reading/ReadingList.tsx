import { useState } from "react";
import { useOp } from "../../api";
import { label, SESSION_STATUS, STRATEGIES } from "../../labels";
import { Empty, ErrorBox, href, Loading, Panel, Tag, Time } from "../../ui";

export interface SessionSummary {
  session_id: string;
  question: string;
  status: string;
  phase: string;
  strategy: string;
  owner_id: string | null;
  updated_at: string;
  revision: number;
  pending_requests: number;
  notes: number;
  relevant: number;
}

const FILTERS = [
  { value: "", label: "全部" },
  { value: "waiting_user", label: "等待用户" },
  { value: "active", label: "进行中" },
  { value: "finished", label: "已完成" },
  { value: "archived", label: "已归档" },
];

export function ReadingList() {
  const [status, setStatus] = useState("");
  const [owner, setOwner] = useState("");
  const list = useOp<SessionSummary[]>("reading.list", {
    status: status || null,
    owner_id: owner.trim() || null,
    limit: 200,
  });
  return (
    <Panel
      title="阅读会话"
      actions={
        <button type="button" onClick={list.reload}>
          刷新
        </button>
      }
    >
      <div className="toolbar">
        <div className="segmented" role="radiogroup" aria-label="状态">
          {FILTERS.map((f) => (
            <label key={f.value} className={status === f.value ? "on" : ""}>
              <input type="radio" name="status" checked={status === f.value} onChange={() => setStatus(f.value)} />
              {f.label}
            </label>
          ))}
        </div>
        <input placeholder="按归属对象筛选（Owner ID）" value={owner} onChange={(e) => setOwner(e.target.value)} />
      </div>
      <ErrorBox error={list.error} />
      <Loading show={list.loading && !list.data} />
      {list.data && list.data.length === 0 ? <Empty>没有符合条件的阅读会话。AI 调用 reading.start 后会出现在这里。</Empty> : null}
      <table className="table">
        <thead>
          <tr>
            <th>问题</th>
            <th>状态</th>
            <th>策略</th>
            <th>相关 / 笔记</th>
            <th>更新时间</th>
          </tr>
        </thead>
        <tbody>
          {list.data?.map((s) => (
            <tr key={s.session_id}>
              <td>
                <a href={href("reading", s.session_id)}>{s.question}</a>
                {s.pending_requests ? <Tag tone="warn">待处理 {s.pending_requests}</Tag> : null}
              </td>
              <td>{label(SESSION_STATUS, s.status)}</td>
              <td>{label(STRATEGIES, s.strategy).split("（")[0]}</td>
              <td>
                {s.relevant} / {s.notes}
              </td>
              <td>
                <Time value={s.updated_at} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}
