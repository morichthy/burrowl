// 共享界面组件：徽标、Markdown、错误、加载、路由。
import "katex/dist/katex.min.css";
import { type ComponentPropsWithoutRef, type ReactNode, useEffect, useMemo, useState } from "react";
import ReactMarkdown, { defaultUrlTransform, type ExtraProps } from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import type { Problem } from "./api";
import { REVIEW_BADGES } from "./labels";

export function ReviewBadge({ value }: { value: string | null | undefined }) {
  if (!value) return null;
  const badge = REVIEW_BADGES[value] ?? { label: value, tone: "muted" };
  return <span className={`badge ${badge.tone}`}>{badge.label}</span>;
}

export function Tag({ children, tone = "muted" }: { children: ReactNode; tone?: string }) {
  return <span className={`badge ${tone}`}>{children}</span>;
}

/** 正文链接 [[…]] 的解析结果：链接原文（竖线前）→ 跳转地址（缺省表示没解析，原样显示）。 */
export type LinkTargets = Record<string, string | undefined>;

// 代码围栏与行内代码里的 [[…]] 不是链接（与服务端 core/links.py 一致）
const CODE = /(^ {0,3}(```|~~~)[\s\S]*?^ {0,3}\2[^\n]*$|`[^`\n]*`)/gm;
const WIKI = /\[\[([^[\]\n]+?)\]\]/g;

/** 只处理代码以外的 [[文字|显示]]；替换策略由普通链接或编号视图提供。 */
export function mapWikiLinks(text: string, replace: (key: string, label: string, whole: string) => string): string {
  return text
    .split(CODE)
    .map((part, i) => {
      if (i % 3 !== 0) return i % 3 === 1 ? part : ""; // split 的捕获组：1 是代码片段，2 是围栏符号（丢弃）
      return part.replace(WIKI, (whole, inner: string) => {
        const [raw, shown] = inner.split("|", 2);
        const key = raw.trim();
        const label = (shown ?? key.replace(/^[@!]/, "")).trim().replace(/[[\]]/g, "");
        return replace(key, label, whole);
      });
    })
    .join("");
}

export function linkify(text: string, targets: LinkTargets): string {
  return mapWikiLinks(text, (key, label, whole) => targets[key] ? `[${label}](${targets[key]})` : whole);
}

export function Markdown({
  text,
  figure,
  anchors,
}: {
  text: string | null | undefined;
  /** 技术单元正文中的 ![图注](figure:N)：在原位置显示第 N 张登记的图 */
  figure?: (index: number, alt: string) => ReactNode;
  /** 仅用于本次渲染生成的引用占位地址；原文中的普通 Markdown 链接仍正常渲染。 */
  anchors?: Record<string, ReactNode>;
}) {
  // 组件表必须稳定：每次渲染换一个新的 img 组件，React 会卸载重建图片（图片的读取请求随之反复发出）
  const components = useMemo(
    () => ({
      // figure 是块级元素，不能嵌在 Markdown 自动生成的 p 内；同段的文字与出处仍保留。
      p: ({ node, children }: ComponentPropsWithoutRef<"p"> & ExtraProps) => {
        const hasFigure = node?.children.some((child) => child.type === "element" && child.tagName === "img" &&
          typeof child.properties.src === "string" && /^figure:\d+$/.test(child.properties.src));
        return hasFigure && figure ? <div className="figure-paragraph">{children}</div> : <p>{children}</p>;
      },
      img: ({ src, alt }: { src?: string | Blob; alt?: string }) => {
        const match = typeof src === "string" ? /^figure:(\d+)$/.exec(src) : null;
        if (match && figure) return <>{figure(Number(match[1]), alt ?? "")}</>;
        return match ? <span className="hint">[图 {Number(match[1]) + 1}：{alt}]</span> : <img src={src as string} alt={alt} />;
      },
    }),
    [figure],
  );
  // 更换引用名称时保留上面的 img 函数身份，避免名称查询完成后再次加载图片。
  const linkedComponents = useMemo(() => ({ ...components,
    a: ({ href, children, title }: ComponentPropsWithoutRef<"a">) =>
      (href && anchors && Object.hasOwn(anchors, href) ? anchors[href] : undefined) ?? <a href={href} title={title}>{children}</a>,
  }), [components, anchors]);
  if (!text) return null;
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        urlTransform={(url) => ((anchors && Object.hasOwn(anchors, url)) || url.startsWith("figure:") || url.startsWith("#/") ? url : defaultUrlTransform(url))}
        components={linkedComponents}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

export function ErrorBox({ error }: { error: Problem | undefined }) {
  if (!error) return null;
  return (
    <div className="error" role="alert">
      <strong>{error.message}</strong> <code>{error.code}</code>
      {error.errors?.length ? (
        <ul>
          {error.errors.map((e, i) => (
            <li key={i}>
              {e.path ? <code>{e.path}</code> : null} {e.message}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function Loading({ show }: { show: boolean }) {
  return show ? <div className="loading">加载中…</div> : null;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Panel({ title, actions, children }: { title: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="panel">
      <header>
        <h2>{title}</h2>
        {actions ? <div className="actions">{actions}</div> : null}
      </header>
      {children}
    </section>
  );
}

export function Time({ value }: { value: string | null | undefined }) {
  if (!value) return <span>—</span>;
  const date = new Date(value);
  return <time title={value}>{Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN")}</time>;
}

/** 输入停顿 delay 毫秒后才更新的值：文本框驱动查询时用，避免每个按键都发一次请求。 */
export function useDebounced<T>(value: T, delay: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return settled;
}

/** 哈希路由：#/reading/RS-1 → ["reading", "RS-1"]。 */
export function useRoute(): string[] {
  const read = () => window.location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const update = () => setRoute(read());
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  return route;
}

export const href = (...parts: string[]) => "#/" + parts.map(encodeURIComponent).join("/");
