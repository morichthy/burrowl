// 知识中枢：测试连接 → 预览发布 → 确认执行 → 查看解析状态；浏览 Wiki。
import { useState } from "react";
import { useAction, useOp } from "../api";
import { HUB_OPS, HUB_STATES, label } from "../labels";
import { Empty, ErrorBox, href, Loading, Markdown, Panel, Tag, Time } from "../ui";

interface Connector {
  id: string;
  type: string;
  enabled: boolean;
  collection: string;
  wiki_mode: string;
  published: number;
  available: boolean;
  publish: { review_filter: string; levels: string[]; include_documents: boolean; on_retract: string };
}

interface Plan {
  plan_hash: string;
  counts: Record<string, number>;
  actions: { op: string; local_key: string; channel: string; title: string; reason?: string }[];
}

interface Status {
  counts: Record<string, number>;
  problems: { local_key: string; state: string; detail: string }[];
  runs: { run_id: number; started_at: string; finished_at: string | null; summary: Record<string, number> | null }[];
}

export function Hub() {
  const connectors = useOp<Connector[]>("hub.connectors");
  const [current, setCurrent] = useState<string>();
  const selected = connectors.data?.find((c) => c.id === (current ?? connectors.data?.[0]?.id));
  return (
    <>
      <Panel title="知识中枢">
        <p className="hint">
          本地记忆是唯一的权威数据；中枢（如 WeKnora）只接收按策略挑选的知识，默认只发布已复核的内容。撤回或复核失效的条目会在下次发布时删除或标记。
        </p>
        <ErrorBox error={connectors.error} />
        <Loading show={connectors.loading && !connectors.data} />
        {connectors.data && !connectors.data.length ? (
          <Empty>
            还没有配置连接器。到 <a href={href("settings")}>设置</a> 中添加 WeKnora 或 Markdown 库。
          </Empty>
        ) : null}
        <div className="tabs" role="tablist">
          {connectors.data?.map((c) => (
            <button key={c.id} type="button" role="tab" className={selected?.id === c.id ? "on" : ""} onClick={() => setCurrent(c.id)}>
              {c.id} <span className="hint">{c.type}</span>
            </button>
          ))}
        </div>
      </Panel>
      {selected ? <ConnectorView key={selected.id} connector={selected} /> : null}
    </>
  );
}

function ConnectorView({ connector }: { connector: Connector }) {
  const enabled = connector.enabled && connector.available;
  const health = useOp<{ ok: boolean; error?: string; server_version?: string; secret: { set: boolean; secret_ref: string | null } }>(
    "hub.health",
    { connector_id: connector.id },
    enabled,
  );
  const status = useOp<Status>("hub.status", { connector_id: connector.id, refresh: true }, enabled);
  const plan = useAction<Plan>("hub.plan");
  const apply = useAction<{ summary: Record<string, number>; results: { local_key: string; result: string; error?: string; hint?: string }[] }>(
    "hub.apply",
  );
  const configure = useAction<{ auto: boolean; note?: string }>("wiki.configure");
  const [preview, setPreview] = useState<Plan>();
  const [outcome, setOutcome] = useState<Awaited<ReturnType<typeof apply.run>>>();
  const [note, setNote] = useState<string>();

  if (!connector.available) return <div className="error">未安装类型为 {connector.type} 的连接器包。</div>;
  if (!connector.enabled) return <div className="notice">该连接器已停用。</div>;

  const execute = async (force = false) => {
    if (!preview) return;
    const result = await apply.run({ connector_id: connector.id, plan_hash: preview.plan_hash, force_wiki: force });
    if (result) {
      setOutcome(result);
      setPreview(undefined);
      status.reload();
    }
  };

  return (
    <>
      <Panel title="连接">
        <dl className="facts">
          <dt>状态</dt>
          <dd>
            {health.loading ? "检查中…" : health.data?.ok ? <Tag tone="good">正常</Tag> : <Tag tone="bad">不可用</Tag>}{" "}
            {health.data?.error ?? health.data?.server_version ?? ""}
          </dd>
          <dt>密钥</dt>
          <dd>{!health.data?.secret?.secret_ref ? "不需要" : health.data.secret.set ? "已设置" : "未设置（在设置页填写）"}</dd>
          <dt>知识库</dt>
          <dd>{connector.collection}</dd>
          <dt>发布策略</dt>
          <dd>
            {connector.publish.review_filter === "reviewed_only" ? "只发布已复核" : connector.publish.review_filter} ·{" "}
            {connector.publish.levels.join("/")} {connector.publish.include_documents ? "+ 成稿文稿" : ""} · 撤回时
            {connector.publish.on_retract === "delete" ? "删除" : "标记"}
          </dd>
          <dt>Wiki</dt>
          <dd>
            {connector.wiki_mode}{" "}
            <button
              type="button"
              className="small"
              onClick={async () => {
                const result = await configure.run({ connector_id: connector.id });
                if (result) setNote(result.note ?? (result.auto ? "已开启自动 Wiki" : "已关闭自动 Wiki"));
              }}
            >
              应用 Wiki 设置
            </button>
            {note ? <span className="hint"> {note}</span> : null}
          </dd>
        </dl>
        <ErrorBox error={configure.error} />
      </Panel>

      <Panel
        title="发布"
        actions={
          <button
            type="button"
            className="primary"
            disabled={plan.busy}
            onClick={async () => {
              setOutcome(undefined);
              setPreview(await plan.run({ connector_id: connector.id }));
            }}
          >
            预览发布
          </button>
        }
      >
        <ErrorBox error={plan.error ?? apply.error} />
        {preview ? (
          <>
            <p>
              {Object.entries(preview.counts)
                .map(([op, n]) => `${label(HUB_OPS, op)} ${n}`)
                .join(" · ")}
            </p>
            {preview.actions.length ? (
              <>
                <table className="table">
                  <thead>
                    <tr>
                      <th>动作</th>
                      <th>标题</th>
                      <th>类型</th>
                      <th>说明</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.actions.map((a) => (
                      <tr key={a.local_key}>
                        <td>
                          <Tag tone={a.op === "delete" ? "bad" : a.op === "create" ? "good" : "muted"}>{label(HUB_OPS, a.op)}</Tag>
                        </td>
                        <td>{a.title}</td>
                        <td>{a.channel === "wiki" ? "Wiki 页面" : "知识条目"}</td>
                        <td className="hint">{a.reason ?? ""}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <button type="button" className="primary" disabled={apply.busy} onClick={() => execute(false)}>
                  确认发布
                </button>
              </>
            ) : (
              <Empty>已与本地一致，没有需要发布的变化。</Empty>
            )}
          </>
        ) : null}
        {outcome ? (
          <div className="notice">
            执行结果：
            {Object.entries(outcome.summary)
              .map(([k, n]) => `${k} ${n}`)
              .join(" · ")}
            <ul>
              {outcome.results
                .filter((r) => r.result === "failed" || r.result === "drift")
                .map((r) => (
                  <li key={r.local_key}>
                    <code>{r.local_key}</code> {r.result}：{r.error ?? r.hint}
                  </li>
                ))}
            </ul>
          </div>
        ) : null}
      </Panel>

      <Panel
        title="同步状态"
        actions={
          <button type="button" onClick={status.reload}>
            刷新
          </button>
        }
      >
        <ErrorBox error={status.error} />
        {status.data ? (
          <>
            <p>
              {Object.entries(status.data.counts)
                .map(([state, n]) => `${label(HUB_STATES, state)} ${n}`)
                .join(" · ") || "尚未发布任何条目"}
            </p>
            {status.data.problems.length ? (
              <ul className="problems">
                {status.data.problems.map((p) => (
                  <li key={p.local_key}>
                    <Tag tone="bad">{label(HUB_STATES, p.state)}</Tag> <code>{p.local_key}</code> {p.detail}
                  </li>
                ))}
              </ul>
            ) : null}
            <details>
              <summary>最近执行</summary>
              <ul>
                {status.data.runs.map((r) => (
                  <li key={r.run_id}>
                    <Time value={r.started_at} />{" "}
                    {Object.entries(r.summary ?? {})
                      .map(([k, n]) => `${k} ${n}`)
                      .join(" · ")}
                  </li>
                ))}
              </ul>
            </details>
          </>
        ) : null}
      </Panel>
      <Wiki connectorId={connector.id} />
    </>
  );
}

function Wiki({ connectorId }: { connectorId: string }) {
  const [query, setQuery] = useState("");
  const [slug, setSlug] = useState<string>();
  const pages = useOp<{ pages: { slug: string; title: string; page_type: string; edited_by: string; authored_here: boolean }[] }>(
    "wiki.list",
    { connector_id: connectorId, query, limit: 100 },
  );
  const page = useOp<{ title: string; markdown: string; version: number | null; edited_by: string }>(
    "wiki.read",
    { connector_id: connectorId, slug: slug ?? "" },
    !!slug,
  );
  if (pages.error?.code === "CAPABILITY_UNAVAILABLE") return null;
  return (
    <Panel title="Wiki">
      <div className="toolbar">
        <input placeholder="搜索页面" value={query} onChange={(e) => setQuery(e.target.value)} />
      </div>
      <ErrorBox error={pages.error} />
      <div className="split">
        <ul className="wiki-list">
          {pages.data?.pages.map((p) => (
            <li key={p.slug}>
              <button type="button" className={`link ${slug === p.slug ? "on" : ""}`} onClick={() => setSlug(p.slug)}>
                {p.title}
              </button>{" "}
              <Tag>{p.authored_here ? "本项目发布" : p.edited_by === "user" ? "人工维护" : "自动生成"}</Tag>
            </li>
          ))}
          {pages.data && !pages.data.pages.length ? <Empty>还没有 Wiki 页面。</Empty> : null}
        </ul>
        <div>
          <ErrorBox error={page.error} />
          {slug && page.data ? (
            <article>
              <h3>{page.data.title}</h3>
              <p className="hint">
                <code>{slug}</code> · 版本 {page.data.version ?? "—"} · 最后编辑：{page.data.edited_by || "—"}
              </p>
              <Markdown text={page.data.markdown} />
            </article>
          ) : null}
        </div>
      </div>
    </Panel>
  );
}
