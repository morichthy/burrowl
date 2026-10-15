// 引用编号是当前阅读视图的派生结果；不改规范正文，不保存独立的编号或参考文献状态。
import { createContext, type ReactNode, useContext, useEffect, useId, useMemo, useState } from "react";
import { op } from "./api";
import { RecordFigure } from "./figures";
import { FIGURE_ANCHOR, href, Markdown, mapWikiLinks } from "./ui";

export interface StoredLink {
  text: string;
  status: string;
  ref: { target_id: string; target_kind?: string; revision?: number | null; sha256?: string | null; locator?: string } | null;
}
export interface RefTarget {
  kind: "record" | "claim" | "file" | "owner";
  owner_id?: string;
  record_id?: string;
  title: string;
  handle?: string | null;
  level?: string | null;
}
export interface CitationSource { owner_id: string; record_id: string; revision?: number }
export interface CitationFragment {
  source: CitationSource;
  texts: string[];
  links: StoredLink[];
  /** 记录页已读取这些名称；文稿与原文展开由 Scope 按来源修订合并查询。 */
  targets?: Record<string, RefTarget>;
}
interface Entry {
  key: string;
  number: number;
  name: string;
  text: string;
  ref: NonNullable<StoredLink["ref"]>;
  source: string;
}
/** 正文提到的概念（R24 D-11）：不编号、不算出处；没有概念页时 ref 为空。 */
interface ConceptEntry { key: string; name: string; ref: StoredLink["ref"]; source: string }
const sourceKey = (source: CitationSource) => JSON.stringify([source.owner_id, source.record_id, source.revision ?? null]);
/** 同一目标的别名共用编号，但不同版本、指纹或块位置不得合并。relation 不属于目标身份。 */
const refKey = (ref: NonNullable<StoredLink["ref"]>) =>
  JSON.stringify([ref.target_kind ?? "record", ref.target_id, ref.revision ?? null, ref.sha256 ?? null, ref.locator ?? "record"]);

export function targetHref(target: RefTarget | undefined): string | undefined {
  if (target?.owner_id && target.record_id) return href("memory", target.owner_id, target.record_id);
  if (target?.kind === "owner" && target.owner_id) return href("memory", target.owner_id);
  return undefined;
}

/** 正文中提到的概念，按出现顺序去重；同一概念页的不同写法合并。 */
export function conceptEntries(fragments: CitationFragment[]): ConceptEntry[] {
  const out = new Map<string, ConceptEntry>();
  for (const fragment of fragments) {
    const links = new Map(fragment.links.map((link) => [link.text, link]));
    for (const text of fragment.texts) mapWikiLinks(text, (raw, name, whole) => {
      if (!/^[@!#]/.test(raw)) {
        const ref = links.get(raw)?.ref ?? null;
        const key = ref ? `${ref.target_id}` : `name:${raw}`;
        if (!out.has(key)) out.set(key, { key, name, ref, source: sourceKey(fragment.source) });
      }
      return whole;
    });
  }
  return [...out.values()];
}

/** 引用定位的可读写法：块 p15、图 fig-1。 */
function locatorLabel(locator: string | undefined): string | null {
  if (!locator || locator === "record") return null;
  const [kind, name] = locator.split(/:(.*)/s);
  return kind === "block" ? `块 ${name}` : kind === "figure" ? `图 ${name}` : locator;
}

/** 按实际显示顺序扫描；代码、普通 [1]、数学及概念链接不参加出处编号。 */
export function citationEntries(fragments: CitationFragment[]): Entry[] {
  const entries = new Map<string, Entry>();
  for (const fragment of fragments) {
    const links = new Map(fragment.links.map((link) => [link.text, link]));
    for (const text of fragment.texts) mapWikiLinks(text, (raw, name, whole) => {
      const link = links.get(raw);
      if (/^[@!]/.test(raw) && link?.ref) {
        const key = refKey(link.ref);
        if (!entries.has(key)) entries.set(key, { key, number: entries.size + 1, name, text: raw,
          ref: link.ref, source: sourceKey(fragment.source) });
      }
      return whole;
    });
  }
  return [...entries.values()];
}

interface CitationView {
  id: string;
  entries: Entry[];
  concepts: ConceptEntry[];
  targets: Record<string, Record<string, RefTarget>>;
  errors: boolean;
  retry: () => void;
}
const Context = createContext<CitationView | null>(null);

export function CitationScope({ fragments, children }: { fragments: CitationFragment[]; children: ReactNode }) {
  const id = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const entries = useMemo(() => citationEntries(fragments), [fragments]);
  const concepts = useMemo(() => conceptEntries(fragments), [fragments]);
  // 一个来源记录只查一次，包含固定修订；异步名称不会决定编号，失败时仍可读原引用名。
  const sources = [...new Map(fragments.filter((f) => !f.targets && f.links.length)
    .map((f) => [sourceKey(f.source), f.source])).values()];
  const queryKey = JSON.stringify(sources);
  const [attempt, setAttempt] = useState(0);
  const [loaded, setLoaded] = useState<{ key: string; targets: CitationView["targets"]; errors: boolean }>();
  useEffect(() => {
    let alive = true;
    const requested: CitationSource[] = JSON.parse(queryKey);
    void Promise.allSettled(requested.map(async (source) =>
      [sourceKey(source), await op<Record<string, RefTarget>>("ui.ref_targets", source)] as const,
    )).then((results) => {
      if (!alive) return;
      setLoaded({ key: queryKey, targets: Object.fromEntries(results.flatMap((result) =>
        result.status === "fulfilled" ? [result.value] : [])), errors: results.some((r) => r.status === "rejected") });
    });
    return () => { alive = false; };
  }, [queryKey, attempt]);
  const targets = { ...(loaded?.key === queryKey ? loaded.targets : {}),
    ...Object.fromEntries(fragments.filter((f) => f.targets).map((f) => [sourceKey(f.source), f.targets!])) };
  return <Context.Provider value={{ id, entries, concepts, targets, errors: loaded?.key === queryKey && loaded.errors,
    retry: () => setAttempt((n) => n + 1) }}>{children}</Context.Provider>;
}

function description(entry: Entry, target: RefTarget | undefined): string {
  return [target?.title ?? entry.name, `引用名：${entry.text}`,
    entry.ref.revision != null ? `固定修订 ${entry.ref.revision}` : null,
    locatorLabel(entry.ref.locator) ? `位置：${locatorLabel(entry.ref.locator)}` : null,
    entry.ref.target_id].filter(Boolean).join("\n");
}

function CitationAnchor({ entry }: { entry: Entry }) {
  const view = useContext(Context)!;
  const tooltipId = useId();
  const target = view.targets[entry.source]?.[entry.ref.target_id];
  const url = targetHref(target);
  const summary = description(entry, target);
  const label = `引用 ${entry.number}：${target?.title ?? entry.name}`;
  return <sup className="citation-marker">
    {url ? <a href={url} aria-label={label} aria-describedby={tooltipId}>〔引{entry.number}〕</a> :
      <button type="button" className="link" aria-label={label} aria-describedby={tooltipId}
        onClick={() => document.getElementById(`citation-${view.id}-${entry.number}`)?.scrollIntoView({ block: "nearest" })}>
        〔引{entry.number}〕</button>}
    <span role="tooltip" id={tooltipId} className="citation-tooltip">{summary}</span>
  </sup>;
}

/** 引用别的记录的图：按引用方固定的修订读图，图注后附引用编号。 */
function CitedFigure({ entry }: { entry: Entry }) {
  const view = useContext(Context)!;
  const target = view.targets[entry.source]?.[entry.ref.target_id];
  const label = entry.ref.locator!.slice("figure:".length);
  if (!target?.owner_id || entry.ref.revision == null) return <CitationAnchor entry={entry} />;
  return <RecordFigure ownerId={target.owner_id} recordId={entry.ref.target_id} revision={entry.ref.revision}
    figureKey={label} fallback={entry.name} marker={<> <CitationAnchor entry={entry} /></>} />;
}

/** 只把规范 links 中有固定依据的 @ / ! 写法转换成视图占位符，其他引用格式原样交给 Markdown。 */
export function CitedMarkdown({ text, source, links, figure }: {
  text?: string | null; source: CitationSource; links: StoredLink[]; figure?: (key: number | string, alt: string) => ReactNode;
}) {
  const view = useContext(Context)!;
  const targets = view.targets[sourceKey(source)] ?? {};
  const stored = new Map(links.map((link) => [link.text, link]));
  const entries = new Map(view.entries.map((entry) => [entry.key, entry]));
  const anchors: Record<string, ReactNode> = {};
  const rendered = mapWikiLinks(text ?? "", (raw, name, whole, embed) => {
    const link = stored.get(raw);
    const entry = /^[@!]/.test(raw) && link?.ref ? entries.get(refKey(link.ref)) : undefined;
    if (entry && embed && entry.ref.locator?.startsWith("figure:")) {  // R24 D-05：显示被引用记录的图并计入编号
      const placeholder = `${FIGURE_ANCHOR}${view.id}/${entry.number}`;
      anchors[placeholder] = <CitedFigure entry={entry} />;
      return `[引${entry.number}](${placeholder})`;
    }
    if (entry) {
      const placeholder = `burrowl-citation:${view.id}/${entry.number}`;
      anchors[placeholder] = <CitationAnchor entry={entry} />;
      return `[引${entry.number}](${placeholder})`;
    }
    const url = link?.ref ? targetHref(targets[link.ref.target_id]) : undefined;
    return url ? `[${name}](${url})` : whole;
  });
  return <Markdown text={rendered} figure={figure} anchors={anchors} />;
}

/** 在整条记录、整篇文稿或整份原文展开底部集中显示当前视图实际使用的依据。 */
export function CitationList() {
  const view = useContext(Context)!;
  if (!view.entries.length && !view.concepts.length) return null;
  return <section className="citation-list" aria-label="引用对照">
    <h3>引用对照</h3>
    {view.entries.length ? <h4>依据</h4> : null}
    <ol>{view.entries.map((entry) => {
      const target = view.targets[entry.source]?.[entry.ref.target_id];
      const url = targetHref(target);
      const title = target?.title ?? entry.name;
      return <li key={entry.key} id={`citation-${view.id}-${entry.number}`}>
        <span className="citation-number">〔引{entry.number}〕</span>{" "}
        {url ? <a href={url} title={description(entry, target)}>{title}</a> : <span title={description(entry, target)}>{title}</span>}
        {entry.ref.revision != null ? <span className="hint"> · 修订 {entry.ref.revision}</span> : null}
        {locatorLabel(entry.ref.locator) ? <span className="hint"> · {locatorLabel(entry.ref.locator)}</span> : null}
      </li>;
    })}</ol>
    {view.concepts.length ? <>
      <h4>提及的概念</h4>
      <p className="hint">概念链接表示正文讨论了它，不算出处；以概念页内容为依据时写 [[@概念库/名称]]。</p>
      <ul className="concept-list">{view.concepts.map((concept) => {
        const target = concept.ref ? view.targets[concept.source]?.[concept.ref.target_id] : undefined;
        const url = targetHref(target);
        return <li key={concept.key}>{url ? <a href={url}>{target?.title ?? concept.name}</a> : concept.name}
          {concept.ref ? null : <span className="hint">（尚未建概念页）</span>}</li>;
      })}</ul>
    </> : null}
    {view.errors ? <p className="hint">部分引用名称未能加载，暂用正文引用名。<button type="button" className="link" onClick={view.retry}>重试</button></p> : null}
    <p className="hint">编号仅用于当前视图，不写入正文，也不改现有导出。</p>
  </section>;
}
