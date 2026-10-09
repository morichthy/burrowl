import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useOp } from "./api";
import { useDebounced } from "./ui";

let pending: Map<string, (value: unknown) => void>;

beforeEach(() => {
  pending = new Map();
  // 每个请求挂起，由测试按参数逐个放行，模拟"新参数的结果尚未返回"
  vi.stubGlobal(
    "fetch",
    vi.fn(
      (_url: string, init: RequestInit) =>
        new Promise((resolve) => pending.set(String(init.body), (value) => resolve(new Response(JSON.stringify(value), { status: 200 })))),
    ),
  );
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("useOp 的 stale 标记", () => {
  it("参数改变后、新结果到达前，旧数据标记为 stale；禁用时也是 stale", async () => {
    const { result, rerender } = renderHook(({ seed, enabled }) => useOp("relations.similar", { seeds: [seed] } as never, enabled), {
      initialProps: { seed: "A", enabled: true },
    });
    expect(result.current.stale).toBe(true);
    await act(async () => pending.get(JSON.stringify({ seeds: ["A"] }))!({ for: "A" }));
    await waitFor(() => expect(result.current.data).toEqual({ for: "A" }));
    expect(result.current.stale).toBe(false);

    rerender({ seed: "B", enabled: true });
    // 仍保留旧数据（列表不闪烁），但明确标记不属于当前参数
    expect(result.current.data).toEqual({ for: "A" });
    expect(result.current.stale).toBe(true);
    await act(async () => pending.get(JSON.stringify({ seeds: ["B"] }))!({ for: "B" }));
    await waitFor(() => expect(result.current.stale).toBe(false));

    rerender({ seed: "B", enabled: false });
    expect(result.current.stale).toBe(true);
  });
});

describe("useDebounced", () => {
  it("停顿满 delay 后才更新为最后一次的值", () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(({ value }) => useDebounced(value, 300), { initialProps: { value: "" } });
    rerender({ value: "a" });
    act(() => vi.advanceTimersByTime(200));
    rerender({ value: "ab" });
    act(() => vi.advanceTimersByTime(200));
    expect(result.current).toBe("");
    act(() => vi.advanceTimersByTime(100));
    expect(result.current).toBe("ab");
  });
});
