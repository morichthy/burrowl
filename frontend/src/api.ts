// 工作台唯一的后端入口：POST api/v1/ops/<name>（相对路径，页面位于 /<token>/ 下）。
import { useCallback, useEffect, useRef, useState } from "react";
import type { OpArgs, OpName } from "./generated/ops";

export interface Problem {
  code: string;
  message: string;
  details?: Record<string, unknown>;
  errors?: { path?: string; message: string }[];
  retryable?: boolean;
}

export class OpError extends Error {
  constructor(public problem: Problem) {
    super(problem.message);
  }
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export async function op<T = any>(name: OpName, args?: OpArgs[OpName]): Promise<T> {
  const response = await fetch(`api/v1/ops/${name}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(args ?? {}),
  });
  const body = await response.json().catch(() => ({ code: "BAD_RESPONSE", message: "服务返回了无法解析的内容" }));
  if (!response.ok) throw new OpError(body as Problem);
  return body as T;
}

export interface OpState<T> {
  data: T | undefined;
  error: Problem | undefined;
  loading: boolean;
  /**
   * data 不属于当前参数：参数已变而新结果未到，或 enabled 为 false。
   * 列表筛选可以照常显示旧数据避免闪烁；但按对象执行操作的地方（如对某个节点的候选做判断）必须先看它。
   */
  stale: boolean;
  reload: () => void;
}

/** 读取类操作：参数变化时自动重新加载；reload() 手动刷新。 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function useOp<T = any>(name: OpName, args?: OpArgs[OpName], enabled = true): OpState<T> {
  const [data, setData] = useState<T>();
  const [dataKey, setDataKey] = useState<string>();
  const [error, setError] = useState<Problem>();
  const [loading, setLoading] = useState(false);
  const [tick, setTick] = useState(0);
  const key = JSON.stringify(args ?? {});
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    setLoading(true);
    op<T>(name, JSON.parse(key))
      .then((value) => alive && (setData(value), setDataKey(key), setError(undefined)))
      .catch((exc) => alive && setError(exc instanceof OpError ? exc.problem : { code: "NETWORK", message: String(exc) }))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [name, key, tick, enabled]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, stale: !enabled || dataKey !== key, reload };
}

/** 写操作：返回 [run, 状态]；run 失败时把 Problem 放进 error，不抛出。 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function useAction<T = any>(name: OpName) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Problem>();
  const run = useCallback(
    async (args: OpArgs[OpName]): Promise<T | undefined> => {
      setBusy(true);
      setError(undefined);
      try {
        return await op<T>(name, args);
      } catch (exc) {
        setError(exc instanceof OpError ? exc.problem : { code: "NETWORK", message: String(exc) });
        return undefined;
      } finally {
        setBusy(false);
      }
    },
    [name],
  );
  return { run, busy, error, clear: () => setError(undefined) };
}

/** 阅读会话的修订推送（SSE）；每次新修订调用 onRevision。浏览器断线后自动重连。 */
export function useReadingEvents(sessionId: string | undefined, onRevision: (revision: number) => void) {
  const callback = useRef(onRevision);
  callback.current = onRevision;
  useEffect(() => {
    if (!sessionId || typeof EventSource === "undefined") return;
    const source = new EventSource(`api/v1/events/reading/${encodeURIComponent(sessionId)}`);
    source.addEventListener("revision", (event) => {
      callback.current(JSON.parse((event as MessageEvent).data).revision);
    });
    return () => source.close();
  }, [sessionId]);
}
