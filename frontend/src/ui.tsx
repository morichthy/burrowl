// 共享界面组件：徽标、Markdown、错误、加载、路由。
import "katex/dist/katex.min.css";
import { type ReactNode, useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
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

export function Markdown({ text }: { text: string | null | undefined }) {
  if (!text) return null;
  return (
    <div className="markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>
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
