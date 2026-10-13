// 设置：存储与检索、查询与阅读默认值、MCP 工具组、知识中枢连接器、本机密钥。
// 与 MCP 的 config.update 使用同一套契约；保存时带 revision，防止覆盖别处的修改。
import { useEffect, useState } from "react";
import { useAction, useOp } from "../api";
import { REVIEW_FILTERS, STRATEGIES } from "../labels";
import { ErrorBox, Loading, Panel, Tag } from "../ui";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Config = Record<string, any>;
type ConfigEffect = { action: string; reason: string };

const TOOLSETS = ["memory", "review", "query", "reading", "provenance", "relations", "runs", "sources", "maintenance",
  "monitor", "hub", "wiki", "index", "config", "system"];

const BUDGET_LABELS: Record<string, string> = {
  wall_ms: "执行时间（毫秒）",
  read_bytes: "读取字节",
  output_chars: "输出字符",
  candidates: "候选数",
  rerank_items: "重排条数",
};

const NEW_CONNECTOR = {
  id: "weknora",
  type: "weknora",
  enabled: true,
  base_url: "http://localhost:8080",
  tenant_id: null,
  secret_ref: "keyring:burrowl/weknora",
  collection: "burrowl",
  options: {},
  publish: { levels: ["L4", "L3"], include_documents: true, review_filter: "reviewed_only", on_retract: "delete" },
  wiki: { mode: "both", synthesis_model_id: null },
};

export function Settings() {
  const loaded = useOp<{ config: Config; revision: string; secrets: Record<string, { set: boolean; error?: string }> }>("config.get");
  const update = useAction<{ config: Config; revision: string; effects: ConfigEffect[] }>("config.update");
  const [config, setConfig] = useState<Config>();
  const [baseline, setBaseline] = useState<{ config: Config; revision: string }>();
  const [saved, setSaved] = useState<{ dryRun: boolean; effects: ConfigEffect[] }>();
  useEffect(() => {
    if (loaded.data) {
      setConfig(structuredClone(loaded.data.config));
      setBaseline({ config: structuredClone(loaded.data.config), revision: loaded.data.revision });
    }
  }, [loaded.data]);

  if (!config || !loaded.data) return <Loading show={loaded.loading} />;
  const set = (path: string[], value: unknown) => {
    // 草稿再次编辑时，旧的“保存成功 / 检查通过”不能代表当前内容。
    setSaved(undefined);
    setConfig((current) => {
      const next = structuredClone(current!);
      let node = next;
      for (const key of path.slice(0, -1)) node = node[key] ??= {};
      node[path[path.length - 1]] = value;
      return next;
    });
  };
  const save = async (dryRun: boolean) => {
    setSaved(undefined);
    const { schema_version: _ignored, ...patch } = config;
    const result = await update.run({ patch, expected_revision: baseline!.revision, dry_run: dryRun });
    if (result) {
      setSaved({ dryRun, effects: result.effects });
      // 直接采用已保存的回执基线，避免异步重新加载覆盖用户紧接着输入的新草稿。
      if (!dryRun) setBaseline({ config: structuredClone(result.config), revision: result.revision });
    }
  };

  return (
    <fieldset disabled={update.busy} style={{ border: 0, padding: 0, minWidth: 0 }}>
      <ErrorBox error={loaded.error} />
      <Panel title="存储与检索">
        <div className="form">
          <label>
            索引文件位置（相对数据目录或绝对路径）
            <input value={config.index.path} onChange={(e) => set(["index", "path"], e.target.value)} />
          </label>
          <label>
            嵌入模型
            <input value={config.index.embedding.model} onChange={(e) => set(["index", "embedding", "model"], e.target.value)} />
          </label>
          <label>
            嵌入模型目录（留空使用 BURROWL_MODELS_DIR）
            <input value={config.index.embedding.path ?? ""} onChange={(e) => set(["index", "embedding", "path"], e.target.value || null)} />
          </label>
          <label>
            向量后端
            <select value={config.index.vector.kind} onChange={(e) => set(["index", "vector", "kind"], e.target.value)}>
              <option value="sqlite-vec">sqlite-vec（默认，与关键词同一文件）</option>
              <option value="qdrant-server">Qdrant 服务器（大规模时）</option>
              <option value="none">不使用向量</option>
            </select>
          </label>
          {config.index.vector.kind === "qdrant-server" ? (
            <label>
              Qdrant 地址
              <input value={config.index.vector.url ?? ""} onChange={(e) => set(["index", "vector", "url"], e.target.value || null)} />
            </label>
          ) : null}
          <label>
            重排模型目录（留空使用 BURROWL_MODELS_DIR）
            <input value={config.index.reranker.path ?? ""} onChange={(e) => set(["index", "reranker", "path"], e.target.value || null)} />
          </label>
          <label className="check">
            <input type="checkbox" checked={config.index.ocr_enabled} onChange={(e) => set(["index", "ocr_enabled"], e.target.checked)} />
            对图片和扫描件做文字识别（OCR）
          </label>
        </div>
      </Panel>

      <Panel title="查询与阅读默认值">
        <div className="form">
          <label>
            材料查询默认复核过滤
            <select value={config.query.review_filter} onChange={(e) => set(["query", "review_filter"], e.target.value)}>
              {REVIEW_FILTERS.map((f) => (
                <option key={f.value} value={f.value}>
                  {f.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            材料查询结果数
            <input type="number" min={1} max={100} value={config.query.result_limit} onChange={(e) => set(["query", "result_limit"], Number(e.target.value))} />
          </label>
          <label>
            阅读默认策略
            <select value={config.reading.strategy} onChange={(e) => set(["reading", "strategy"], e.target.value)}>
              {Object.entries(STRATEGIES).map(([v, t]) => (
                <option key={v} value={v}>
                  {t}
                </option>
              ))}
            </select>
          </label>
          <label>
            阅读默认复核过滤
            <select value={config.reading.review_filter} onChange={(e) => set(["reading", "review_filter"], e.target.value)}>
              {REVIEW_FILTERS.map((f) => (
                <option key={f.value} value={f.value}>
                  {f.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            每次最多完整阅读的对象数
            <input type="number" min={1} max={50} value={config.reading.context.max_owners} onChange={(e) => set(["reading", "context", "max_owners"], Number(e.target.value))} />
          </label>
          <label className="check">
            <input type="checkbox" checked={config.reading.association.enabled} onChange={(e) => set(["reading", "association", "enabled"], e.target.checked)} />
            允许联想补查
          </label>
          <label>
            交接笔记最多 token（超出的笔记整篇省略）
            <input type="number" min={500} max={100000} value={config.reading.context.note_max_tokens} onChange={(e) => set(["reading", "context", "note_max_tokens"], Number(e.target.value))} />
          </label>
          <label>
            联想补查最多轮数
            <input type="number" min={0} max={10} value={config.reading.association.max_rounds} onChange={(e) => set(["reading", "association", "max_rounds"], Number(e.target.value))} />
          </label>
          <label>
            子 Agent
            <select value={config.collaboration.subagents} onChange={(e) => set(["collaboration", "subagents"], e.target.value)}>
              <option value="auto">允许（适合拆分时）</option>
              <option value="off">关闭</option>
            </select>
          </label>
          <label>
            子 Agent 要求
            <textarea value={config.collaboration.subagent_requirements} onChange={(e) => set(["collaboration", "subagent_requirements"], e.target.value)} />
          </label>
        </div>
      </Panel>

      <Panel title="高级：筛选、重排与预算">
        <p className="hint">一般不需要修改。预算是工程保护上限（防止一次操作读取过多），不是宿主模型的 token 计费。</p>
        <div className="form">
          <label>
            每批筛选的对象数
            <input type="number" min={1} max={50} value={config.reading.screening.batch_owners} onChange={(e) => set(["reading", "screening", "batch_owners"], Number(e.target.value))} />
          </label>
          <label>
            每个对象常规窗口数
            <input type="number" min={1} max={4} value={config.reading.screening.regular_windows} onChange={(e) => set(["reading", "screening", "regular_windows"], Number(e.target.value))} />
          </label>
          <label>
            每个对象最多窗口数（含 peek）
            <input type="number" min={1} max={8} value={config.reading.screening.max_windows} onChange={(e) => set(["reading", "screening", "max_windows"], Number(e.target.value))} />
          </label>
          <label>
            重排模式
            <select value={config.reading.reranking.mode} onChange={(e) => set(["reading", "reranking", "mode"], e.target.value)}>
              <option value="auto">auto（有模型就用）</option>
              <option value="off">off（不重排）</option>
              <option value="required">required（没有模型就报错）</option>
            </select>
          </label>
          <label>
            重排候选窗
            <input type="number" min={1} max={200} value={config.reading.reranking.candidate_limit} onChange={(e) => set(["reading", "reranking", "candidate_limit"], Number(e.target.value))} />
          </label>
          {(["query", "reading"] as const).map((section) =>
            (["wall_ms", "read_bytes", "output_chars", "candidates", "rerank_items"] as const).map((key) => (
              <label key={section + key}>
                {section === "query" ? "材料查询" : "阅读会话"}预算 · {BUDGET_LABELS[key]}
                <input type="number" min={0} value={config[section].budget[key]} onChange={(e) => set([section, "budget", key], Number(e.target.value))} />
              </label>
            )),
          )}
        </div>
      </Panel>

      <Panel title="MCP 运行模式与工具组（向 AI 暴露哪些能力）">
        <div className="segmented">
          {(
            [
              ["auto", "自动：接入的对话默认按工作流工作，成果存入本数据目录"],
              ["manual", "手动：只在用户明确要求时使用"],
            ] as const
          ).map(([value, label]) => (
            <label key={value} className={config.mcp.mode === value ? "on" : ""}>
              <input type="radio" name="mcp-mode" checked={config.mcp.mode === value} onChange={() => set(["mcp", "mode"], value)} />
              {label}
            </label>
          ))}
        </div>
        <p className="hint">宿主配置里的 --mode 参数优先于这里；修改后重启 MCP 服务（或新开对话）生效。</p>
        <div className="chips">
          {TOOLSETS.map((t) => (
            <label key={t} className={config.mcp.toolsets.includes(t) ? "on" : ""}>
              <input
                type="checkbox"
                checked={config.mcp.toolsets.includes(t)}
                onChange={(e) =>
                  set(["mcp", "toolsets"], e.target.checked ? [...config.mcp.toolsets, t] : config.mcp.toolsets.filter((x: string) => x !== t))
                }
              />
              {t}
            </label>
          ))}
        </div>
      </Panel>

      <Panel title="诊断：请求日志">
        <div className="segmented">
          {(
            [
              ["off", "关闭"],
              ["summary", "摘要：工具名、耗时、结果与参数结构（不含内容）"],
              ["full", "完整：另记参数原文与返回内容（长字段截断）"],
            ] as const
          ).map(([value, text]) => (
            <label key={value} className={(config.diagnostics?.request_log ?? "off") === value ? "on" : ""}>
              <input
                type="radio"
                name="request-log"
                checked={(config.diagnostics?.request_log ?? "off") === value}
                onChange={() => set(["diagnostics", "request_log"], value)}
              />
              {text}
            </label>
          ))}
        </div>
        <p className="hint">
          排查“AI 传来的参数和服务看到的不一样”时打开，用完关闭。写在数据目录的 .burrowl/logs/requests.jsonl，不进备份包。保存后立即生效；
          MCP 启动参数 --request-log（或环境变量 BURROWL_REQUEST_LOG）优先于这里。
        </p>
        <RequestLog key={baseline!.revision} configuredLevel={baseline!.config.diagnostics?.request_log ?? "off"} />
      </Panel>

      <Panel title="MCP 错误兼容返回">
        <label className="check">
          <input type="checkbox" checked={config.mcp.error_envelope ?? false}
            onChange={(e) => set(["mcp", "error_envelope"], e.target.checked)} />
          开启兼容返回
        </label>
        <p className="hint">
          客户端隐藏工具报错详情时开启。成功返回 ok=true 与结果，业务失败返回 ok=false 与简短错误码、参数路径和处理提示；
          AI 无需翻查堆积的日志。仅改变 MCP 返回格式，错误仍按失败记录。保存后下一次调用生效，无需重启；
          客户端修复后可关闭，恢复标准报错方式。
        </p>
      </Panel>

      <Panel
        title="知识中枢连接器"
        actions={
          <button type="button" onClick={() => set(["connectors"], [...config.connectors, structuredClone(NEW_CONNECTOR)])}>
            添加连接器
          </button>
        }
      >
        {config.connectors.map((c: Config, i: number) => (
          <ConnectorForm
            key={i}
            connector={c}
            secret={loaded.data!.secrets[c.id]}
            onChange={(value) => set(["connectors", String(i)], value)}
            onRemove={() => set(["connectors"], config.connectors.filter((_: unknown, j: number) => j !== i))}
          />
        ))}
      </Panel>

      <div className="savebar">
        <ErrorBox error={update.error} />
        {saved ? (
          <div className="notice" role="status">
            <strong>{saved.dryRun ? "检查通过，尚未保存配置。" : "配置已保存。"}</strong>
            {saved.effects.length ? (
              <><p>{saved.dryRun ? "保存后还需要完成以下动作：" : "以下设置还需要后续动作才会生效："}</p>
                <ul>{saved.effects.map((effect, i) => <li key={i}>{effect.reason}</li>)}</ul>
                <p>这些动作不会由保存按钮自动执行。</p></>
            ) : <p>{saved.dryRun ? "未发现需要重启或重建索引的后续动作。" : "无需重启或重建索引；启动参数覆盖的设置以实际生效值为准。"}</p>}
          </div>
        ) : null}
        {!saved && JSON.stringify(config) !== JSON.stringify(baseline!.config) ? <p className="hint">有未保存的修改。</p> : null}
        <button type="button" onClick={() => save(true)} disabled={update.busy} title="只校验草稿并预览后续动作，不保存，也不执行这些动作">
          检查影响
        </button>
        <button type="button" className="primary" onClick={() => save(false)} disabled={update.busy}>
          保存设置
        </button>
        <button type="button" disabled={update.busy} onClick={() => { setSaved(undefined); setConfig(structuredClone(baseline!.config)); }}>
          放弃修改
        </button>
        <button
          type="button"
          onClick={async () => {
            // 用预检（不保存）取得各节的出厂默认值，只放进草稿；连接器配置保持不变
            const result = await update.run({
              patch: { index: null, query: null, reading: null, collaboration: null, mcp: null },
              expected_revision: baseline!.revision,
              dry_run: true,
            });
            if (result) { setSaved(undefined); setConfig({ ...(result as unknown as { config: Config }).config, connectors: config.connectors }); }
          }}
        >
          恢复默认（仅草稿）
        </button>
      </div>
    </fieldset>
  );
}

function ConnectorForm(props: { connector: Config; secret?: { set: boolean; error?: string }; onChange: (c: Config) => void; onRemove: () => void }) {
  const c = props.connector;
  const edit = (path: string[], value: unknown) => {
    const next = structuredClone(c);
    let node = next;
    for (const key of path.slice(0, -1)) node = node[key] ??= {};
    node[path[path.length - 1]] = value;
    props.onChange(next);
  };
  const [optionsText, setOptionsText] = useState(JSON.stringify(c.options ?? {}));
  return (
    <fieldset className="connector">
      <legend>
        {c.id} <Tag>{c.type}</Tag> {c.enabled ? null : <Tag>已停用</Tag>}
      </legend>
      <div className="form">
        <label>
          ID（小写字母、数字、连字符）
          <input value={c.id} onChange={(e) => edit(["id"], e.target.value)} />
        </label>
        <label>
          类型
          <select value={c.type} onChange={(e) => edit(["type"], e.target.value)}>
            <option value="weknora">weknora</option>
            <option value="mdvault">mdvault（Markdown 库）</option>
          </select>
        </label>
        <label className="check">
          <input type="checkbox" checked={c.enabled} onChange={(e) => edit(["enabled"], e.target.checked)} /> 启用
        </label>
        {c.type === "weknora" ? (
          <>
            <label>
              服务地址
              <input value={c.base_url ?? ""} onChange={(e) => edit(["base_url"], e.target.value || null)} />
            </label>
            <label>
              租户 ID（可选）
              <input value={c.tenant_id ?? ""} onChange={(e) => edit(["tenant_id"], e.target.value || null)} />
            </label>
          </>
        ) : null}
        <label>
          知识库（名称或 ID）
          <input value={c.collection ?? ""} onChange={(e) => edit(["collection"], e.target.value || null)} />
        </label>
        <label>
          其他选项（JSON，如 Markdown 库的 {'{"path": "D:/Vault"}'}）
          <input
            value={optionsText}
            onChange={(e) => {
              setOptionsText(e.target.value);
              try {
                edit(["options"], JSON.parse(e.target.value || "{}"));
              } catch {
                /* 输入未完成时不更新 */
              }
            }}
          />
        </label>
        <label>
          发布范围
          <select value={c.publish.review_filter} onChange={(e) => edit(["publish", "review_filter"], e.target.value)}>
            {REVIEW_FILTERS.map((f) => (
              <option key={f.value} value={f.value}>
                {f.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          发布层级（逗号分隔）
          <input value={c.publish.levels.join(",")} onChange={(e) => edit(["publish", "levels"], e.target.value.split(",").map((s) => s.trim()).filter(Boolean))} />
        </label>
        <label className="check">
          <input type="checkbox" checked={c.publish.include_documents} onChange={(e) => edit(["publish", "include_documents"], e.target.checked)} />
          同时发布依据完整的成稿文稿
        </label>
        <label>
          撤回或复核失效时
          <select value={c.publish.on_retract} onChange={(e) => edit(["publish", "on_retract"], e.target.value)}>
            <option value="delete">删除外部条目</option>
            <option value="mark">保留并标记为已撤回</option>
          </select>
        </label>
        <label>
          Wiki 模式
          <select value={c.wiki.mode} onChange={(e) => edit(["wiki", "mode"], e.target.value)}>
            <option value="both">自动生成 + AI 编写</option>
            <option value="auto">只用自动生成</option>
            <option value="authored">只发布 AI 编写的页面</option>
            <option value="off">关闭</option>
          </select>
        </label>
        <label>
          自动 Wiki 使用的模型 ID（WeKnora 中配置的模型）
          <input value={c.wiki.synthesis_model_id ?? ""} onChange={(e) => edit(["wiki", "synthesis_model_id"], e.target.value || null)} />
        </label>
        <label>
          密钥引用
          <input value={c.secret_ref ?? ""} onChange={(e) => edit(["secret_ref"], e.target.value || null)} placeholder="keyring:burrowl/weknora" />
        </label>
      </div>
      {c.secret_ref?.startsWith("keyring:") ? <SecretInput secretRef={c.secret_ref} status={props.secret} /> : null}
      <button type="button" className="danger small" onClick={props.onRemove}>
        移除此连接器
      </button>
    </fieldset>
  );
}

function SecretInput({ secretRef, status }: { secretRef: string; status?: { set: boolean; error?: string } }) {
  const store = useAction<{ set: boolean }>("config.secret_set");
  const [value, setValue] = useState("");
  const [done, setDone] = useState(false);
  return (
    <div className="row">
      <span>
        密钥：{done || status?.set ? <Tag tone="good">已设置</Tag> : <Tag tone="warn">未设置</Tag>}
      </span>
      <input type="password" autoComplete="off" placeholder="粘贴 API Key（只保存在本机凭据管理器）" value={value} onChange={(e) => setValue(e.target.value)} />
      <button
        type="button"
        disabled={!value || store.busy}
        onClick={async () => {
          if (await store.run({ secret_ref: secretRef, value })) {
            setValue("");
            setDone(true);
          }
        }}
      >
        保存密钥
      </button>
      <ErrorBox error={store.error} />
    </div>
  );
}

interface RequestRow {
  at: string;
  request_id: string;
  op: string;
  channel: string;
  ms: number;
  status: string;
  json_decoded?: string[];
  [key: string]: unknown;
}

/** 最近的请求（新的在前）；点开一行看收到的参数、预处理后的参数与返回。 */
function RequestLog({ configuredLevel }: { configuredLevel: string }) {
  const [errorsOnly, setErrorsOnly] = useState(false);
  const log = useOp<{ level: string; path: string; items: RequestRow[] }>("system.request_log", { limit: 50, errors_only: errorsOnly });
  const [open, setOpen] = useState<string>();
  return (
    <>
      <div className="toolbar">
        <label className="check hint">
          <input type="checkbox" checked={errorsOnly} onChange={() => setErrorsOnly(!errorsOnly)} /> 只看出错的调用
        </label>
        <button type="button" className="small" onClick={log.reload}>
          刷新
        </button>
        {log.data ? <span className="hint">当前生效：{log.data.level}</span> : null}
      </div>
      <ErrorBox error={log.error} />
      {log.data && log.data.level !== configuredLevel ? <p className="notice">
        工作台保存的日志级别为 {configuredLevel}，实际生效为 {log.data.level}；启动参数或环境变量覆盖了这里的设置。
      </p> : null}
      {log.data?.level === "off" ? <p className="hint">请求日志已关闭：停止记录并隐藏历史内容；已有日志文件保留，重新开启后可查看。</p> : null}
      {log.data && log.data.level !== "off" && !log.data.items.length ? <p className="hint">还没有记录。</p> : null}
      <table className="table">
        <tbody>
          {(log.data?.level === "off" ? [] : log.data?.items)?.map((r) => (
            <tr key={r.request_id} onClick={() => setOpen(open === r.request_id ? undefined : r.request_id)} style={{ cursor: "pointer" }}>
              <td>
                <code>{r.op}</code> <Tag tone={r.status === "ok" ? "muted" : "bad"}>{r.status}</Tag>
                {r.json_decoded?.length ? <Tag tone="warn">字符串转回：{r.json_decoded.join("、")}</Tag> : null}
                <div className="hint">
                  {r.at} · {r.channel} · {r.ms} ms · {r.request_id}
                </div>
                {open === r.request_id ? <pre className="raw">{JSON.stringify(r, null, 2)}</pre> : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
