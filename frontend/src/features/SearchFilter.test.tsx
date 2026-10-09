import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Search } from "./Search";

let calls: { name: string; args: Record<string, unknown> }[];
const ITEMS = [
  { candidate_id: "C-1", kind: "overview", owner_id: "RES-A", record_id: "M-1", title: "已确认的结论", excerpt: "", level: "L4", review: "accepted", score: 1, stale_revision: false },
  { candidate_id: "C-2", kind: "detail", owner_id: "RES-A", record_id: "M-2", title: "未复核的结论", excerpt: "", level: "L1", review: "unreviewed", score: 1, stale_revision: false },
];

beforeEach(() => {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      const name = url.replace("api/v1/ops/", "");
      const args = JSON.parse(String(init.body));
      calls.push({ name, args });
      const items = args.review_filter === "reviewed_only" ? ITEMS.filter((i) => i.review === "accepted") : ITEMS;
      return new Response(JSON.stringify({ query_id: "Q", items, gaps: [], consumed: { wall_ms: 1 } }), { status: 200 });
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("结果出来后切换复核过滤会用已提交的问题重新检索", async () => {
  render(<Search />);
  fireEvent.change(screen.getByLabelText("问题"), { target: { value: "存储与检索" } });
  fireEvent.click(screen.getByRole("button", { name: /^检索$/ }));
  expect(await screen.findByText("未复核的结论")).toBeInTheDocument();
  // 输入框里改了但没提交的问题不应被带进重查
  fireEvent.change(screen.getByLabelText("问题"), { target: { value: "另一个问题" } });
  fireEvent.click(screen.getByRole("radio", { name: "只看已复核" }));
  await waitFor(() => expect(screen.queryByText("未复核的结论")).not.toBeInTheDocument());
  expect(screen.getByText("已确认的结论")).toBeInTheDocument();
  expect(calls.at(-1)?.args).toMatchObject({ question: "存储与检索", review_filter: "reviewed_only" });
});
