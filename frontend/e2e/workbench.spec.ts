// 工作台端到端：阅读会话双向交互、一键复核后检索过滤生效、知识中枢预览与发布。
import { expect, type APIRequestContext, test } from "@playwright/test";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

interface State {
  data: string;
  vault: string;
  session_id: string;
  owner_id: string;
}
const state = (): State => JSON.parse(readFileSync(new URL("./.state.json", import.meta.url), "utf8"));

async function op(request: APIRequestContext, name: string, args: object) {
  const response = await request.post(`api/v1/ops/${name}`, { data: args });
  expect(response.ok(), await response.text()).toBeTruthy();
  return response.json();
}

test.describe.configure({ mode: "serial" });

test("阅读会话：agent 的判断实时出现；用户批准后 agent 从 inbox 收到 approval_id", async ({ page, request }) => {
  const { session_id } = state();
  await page.goto(`#/reading/${session_id}`);
  await expect(page.locator(".request.pending").getByText("是否允许全文补偿召回？")).toBeVisible();
  const card = page.locator(".owner-card").first();
  await expect(card.getByText("未判断")).toBeVisible();

  // agent 在另一条通道上做判断（这里用 HTTP 操作接口代替 MCP），页面不刷新就应更新。
  const view = await op(request, "reading.view", { session_id });
  const owners = view.owners as Record<string, { rank: number; packet: { digest: string } }>;
  const ownerId = Object.entries(owners).sort((a, b) => a[1].rank - b[1].rank)[0][0];
  await op(request, "reading.assess", {
    session_id,
    judgments: [{ owner_id: ownerId, verdict: "relevant", reason: "概览直接相关", digest: owners[ownerId].packet.digest }],
  });
  await expect(card.locator(".badge.good", { hasText: "相关" })).toBeVisible({ timeout: 10_000 });
  await expect(card.getByText("理由：概览直接相关")).toBeVisible();

  await page.getByRole("button", { name: "批准全文补偿" }).click();
  await expect(page.getByText("AI 的请求")).toBeVisible();
  await expect(page.getByRole("button", { name: "批准全文补偿" })).toHaveCount(0);
  const status = await op(request, "reading.status", { session_id });
  const response = status.inbox.find((m: { kind: string }) => m.kind === "response");
  expect(response.payload.decision).toBe("approve");
  expect(response.payload.approval_id).toMatch(/^AP-/);
});

test("知识复核：确认后只看已复核的检索能找到这条结论所在记录", async ({ page }) => {
  await page.goto("#/search");
  await page.getByLabel("只看已复核").check();
  await page.getByRole("textbox", { name: "问题" }).fill("顺序实验经过");
  await page.getByLabel("内容范围").selectOption("process");
  await page.getByRole("button", { name: "检索" }).click();
  await expect(page.locator(".results")).not.toContainText("顺序实验经过");

  await page.goto("#/review");
  const item = page.locator(".claims li", { hasText: "排序后求和误差更小" });
  await item.getByRole("button", { name: "确认" }).click();
  await expect(page.getByText("预检通过，可以提交。")).toBeVisible();
  await page.getByRole("button", { name: "提交确认" }).click();
  await expect(page.locator(".claims li", { hasText: "排序后求和误差更小" })).toHaveCount(0);

  await page.goto("#/search");
  await page.getByLabel("只看已复核").check();
  await page.getByRole("textbox", { name: "问题" }).fill("顺序实验经过");
  await page.getByLabel("内容范围").selectOption("process");
  await page.getByRole("button", { name: "检索" }).click();
  const hit = page.locator(".results li", { hasText: "顺序实验经过" });
  await expect(hit).toBeVisible();
  await expect(hit.getByText("已确认")).toBeVisible();
});

test("知识中枢：预览只含已复核内容，发布后状态完成并落盘", async ({ page }) => {
  const { vault } = state();
  await page.goto("#/hub");
  await expect(page.getByText("正常")).toBeVisible();
  await page.getByRole("button", { name: "预览发布" }).click();
  const table = page.locator("table");
  await expect(table.getByText("顺序实验经过")).toBeVisible({ timeout: 20_000 });
  await expect(table.locator("tbody tr")).toHaveCount(1);
  await page.getByRole("button", { name: "确认发布" }).click();
  await expect(page.getByText("执行结果：created 1")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("已完成 1")).toBeVisible();
  const files = readdirSync(join(vault, "burrowl")).filter((f) => f.endsWith(".md"));
  expect(files).toHaveLength(1);
  expect(readFileSync(join(vault, "burrowl", files[0]), "utf8")).toContain("burrowl_ref: burrowl://");
  await page.getByRole("button", { name: "预览发布" }).click();
  await expect(page.getByText("已与本地一致，没有需要发布的变化。")).toBeVisible({ timeout: 20_000 });
  expect(existsSync(vault)).toBeTruthy();
});

test("新增页面：维护与监测、原始材料、依据与影响、关系图笔记", async ({ page }) => {
  await page.goto("#/maintenance");
  await page.getByRole("button", { name: "预览扫描" }).click();
  await expect(page.getByText(/预览（未记录）/)).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("SRC-ORDER-DEMO")).toBeVisible();

  await page.goto("#/memory/RES-FLOATING-POINT-SUMMATION");
  await page.getByRole("tab", { name: "原始材料" }).click();
  await page.locator("table button.link").first().click();
  await expect(page.getByText(/指纹已核对/)).toBeVisible({ timeout: 20_000 });

  await page.getByRole("tab", { name: "记录" }).click();
  await page.locator("table a").first().click();
  await page.getByText("依据与影响").click();
  await expect(page.getByText(/追溯到的原件/)).toBeVisible({ timeout: 20_000 });

  await page.goto("#/relations");
  await page.getByPlaceholder("标题", { exact: true }).fill("下一步：对照成对求和");
  await page.getByRole("button", { name: /^添加/ }).click();
  await expect(page.getByText("下一步：对照成对求和")).toBeVisible();
});
