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

test("关系图筛选：分类不重复，节点可见性、空选与保存重载一致", async ({ page, request }) => {
  await page.goto("#/relations");
  const types = page.getByRole("group", { name: "节点类型" });
  const kinds = page.getByRole("group", { name: "记录分类" });
  await expect(types.getByRole("checkbox")).toHaveCount(6);
  // 七个分类外加显式的"全部"；它替代了原先的"不选＝全部"文字提示
  await expect(kinds.getByRole("checkbox")).toHaveCount(8);
  await expect(types.getByRole("checkbox", { name: "内容块（L1）" })).not.toBeChecked();
  // 筛选区为"组名＋选项"两列：分类只筛选记录的说明放在组名的悬停提示中；未选任何分类时"全部"处于选中态
  await expect(page.locator(".filter-label", { hasText: "记录分类" })).toHaveAttribute("title", /只筛选记录节点/);
  await expect(kinds.getByRole("checkbox", { name: "全部" })).toBeChecked();
  await page.screenshot({ path: "test-results/relations-filters.png", fullPage: true });

  // 浏览器请求走真实服务；检查分类只限制记录，结论仍保留导航上下文。
  const [classified] = await Promise.all([
    page.waitForResponse((response) => response.url().endsWith("/relations.graph") && response.request().postDataJSON().kinds?.includes("experience")),
    kinds.getByRole("checkbox", { name: "经验（L3）" }).check(),
  ]);
  const classifiedNodes = (await classified.json()).nodes as { type: string; kind: string }[];
  expect(classifiedNodes.some((node) => node.type === "claim")).toBeTruthy();
  expect(classifiedNodes.filter((node) => node.type === "record").every((node) => node.kind === "experience")).toBeTruthy();
  expect(classified.request().postDataJSON()).not.toHaveProperty("levels");

  // 选块后再全部取消，最后一个请求必须保留 []，不能变成代表全选的 null。
  await types.getByRole("checkbox", { name: "内容块（L1）" }).check();
  for (const name of ["对象", "记录", "内容块（L1）", "结论（Claim）", "概念"]) {
    await types.getByRole("checkbox", { name, exact: true }).uncheck();
  }
  const [empty] = await Promise.all([
    page.waitForResponse((response) => response.url().endsWith("/relations.graph") && response.request().postDataJSON().node_types?.length === 0),
    types.getByRole("checkbox", { name: "来源", exact: true }).uncheck(),
  ]);
  expect((await empty.json()).nodes).toEqual([]);
  await expect(page.getByText(/0 个节点、0 条关系/)).toBeVisible();
  const [saved] = await Promise.all([
    page.waitForResponse((response) => response.url().endsWith("/relations.view_save")),
    page.getByRole("button", { name: "保存视图" }).click(),
  ]);
  expect(saved.ok()).toBeTruthy();
  expect(saved.request().postDataJSON().view.filters).toMatchObject({ nodeTypes: [], kinds: ["experience"] });
  expect((await op(request, "relations.view", {})).filters.nodeTypes).toEqual([]);
  await page.reload();
  await expect(types.getByRole("checkbox", { checked: true })).toHaveCount(0);
  await expect(page.getByText(/0 个节点、0 条关系/)).toBeVisible();
  const [reset] = await Promise.all([
    page.waitForResponse((response) => response.url().endsWith("/relations.graph") && response.request().postDataJSON().node_types?.length === 5),
    page.getByRole("button", { name: "重置", exact: true }).click(),
  ]);
  expect(reset.request().postDataJSON()).toMatchObject({ node_types: ["owner", "record", "claim", "concept", "source"], kinds: null });
  await expect(types.getByRole("checkbox", { checked: true })).toHaveCount(5);
  expect((await reset.json()).nodes.some((node: { type: string }) => node.type === "block")).toBeFalsy();
});

test("检索：未索引材料显示非空提示，补齐后原问题恢复命中", async ({ page }) => {
  await page.goto("#/search");
  await page.getByRole("textbox", { name: "问题" }).fill("虚拟环境");
  await page.getByRole("button", { name: "检索", exact: true }).click();
  const notice = page.locator(".notice");
  await expect(notice).toHaveCount(1);
  await expect(notice).toContainText("尚未建立检索索引");
  await expect(notice).toContainText("关键词、向量");
  await expect(page.locator(".results")).not.toContainText("虚拟环境验证概览");
  await page.screenshot({ path: "test-results/search-index-gap.png", fullPage: true });
  // 输入框已改但尚未提交：修复按钮重试的仍是原来的“虚拟环境”。
  await page.getByRole("textbox", { name: "问题" }).fill("存储与检索");
  const [retried] = await Promise.all([
    page.waitForResponse((response) => response.url().endsWith("/query.search")),
    page.getByRole("button", { name: "补齐索引并重试" }).click(),
  ]);
  expect(retried.request().postDataJSON()).toMatchObject({ question: "虚拟环境", content_source: "overview_experience" });
  expect((await retried.json()).items.map((item: { title: string }) => item.title)).toContain("虚拟环境验证概览");
  await expect(page.locator(".results")).toContainText("虚拟环境验证概览");
  await expect(notice).toHaveCount(0);
  await page.screenshot({ path: "test-results/search-index-restored.png", fullPage: true });
});

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

test("阅读列表：首页和会话页会显示工作台打开后新建的会话", async ({ page, request }) => {
  const { session_id: seededSession } = state();

  // 归档 e2e 种子会话，让首页和“进行中”列表都从空状态开始。
  await op(request, "reading.archive", { session_id: seededSession });
  await page.goto("#/");
  await expect(page.getByText("还没有阅读会话。")).toBeVisible();

  const homeQuestion = "存储与检索演进讨论：首页自动刷新";
  const homeSession = await op(request, "reading.start", { question: homeQuestion });
  await expect(page.getByRole("link", { name: homeQuestion })).toBeVisible({ timeout: 12_000 });

  // 清掉首页用例新建的活跃会话，确保列表页也从空结果开始。
  await op(request, "reading.archive", { session_id: homeSession.session_id });
  await page.goto("#/reading");
  await page.getByLabel("状态").getByText("进行中").click();
  await expect(page.getByText("没有符合条件的阅读会话。")).toBeVisible();

  const listQuestion = "存储与检索演进讨论：列表自动刷新";
  await op(request, "reading.start", { question: listQuestion });
  await expect(page.getByRole("link", { name: listQuestion })).toBeVisible({ timeout: 12_000 });
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
  // mdvault 按交换格式写入 records/<owner>/<handle>.md。
  const files = readdirSync(join(vault, "burrowl", "records", "order-demo")).filter((f) => f.endsWith(".md"));
  expect(files).toHaveLength(1);
  expect(readFileSync(join(vault, "burrowl", "records", "order-demo", files[0]), "utf8")).toContain("burrowl_ref: burrowl://");
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
