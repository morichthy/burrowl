// 工作台端到端：阅读会话双向交互、一键复核后检索过滤生效、知识中枢预览与发布。
import { expect, type APIRequestContext, test } from "@playwright/test";
import { existsSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
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

/** 通过真实 Cytoscape 实例计算连线中点，再以浏览器鼠标点击画布。 */
async function clickGraphLink(page: import("@playwright/test").Page, source: string, target: string) {
  await page.locator(".graph").scrollIntoViewIfNeeded();
  const readPoint = () => page.locator(".graph").evaluate((element, endpoints) => {
    type Point = { x: number; y: number };
    type Edge = { empty(): boolean; midpoint(): Point };
    type Core = { getElementById(id: string): Edge; pan(): Point; zoom(): number };
    const container = element as HTMLElement & { _cyreg?: { cy?: Core } };
    const cy = container._cyreg?.cy;
    if (!cy) return null;
    const id = [endpoints.source, endpoints.target].sort().join("|");
    const edge = cy.getElementById(id);
    if (edge.empty()) return null;
    const midpoint = edge.midpoint();
    const pan = cy.pan();
    const zoom = cy.zoom();
    const rect = container.getBoundingClientRect();
    return { x: rect.left + pan.x + midpoint.x * zoom, y: rect.top + pan.y + midpoint.y * zoom };
  }, { source, target });
  // Cytoscape 初次布局和筛选变化会移动节点；等连线中点在相邻两次采样中稳定后再点击。
  let previous = "";
  await expect.poll(async () => {
    const point = await readPoint();
    if (!point) return false;
    const current = `${point.x.toFixed(1)},${point.y.toFixed(1)}`;
    const stable = current === previous;
    previous = current;
    return stable;
  }, { timeout: 10_000, intervals: [100, 200, 300] }).toBeTruthy();
  const point = await readPoint();
  if (!point) throw new Error(`找不到关系图连线：${source} → ${target}`);
  await page.mouse.click(point.x, point.y);
}

test.describe.configure({ mode: "serial" });

test("设置保存与影响预览清晰，关闭请求日志隐藏历史，兼容开关可保存", async ({ page, request }) => {
  const original = await op(request, "config.get", {});
  await op(request, "config.update", { expected_revision: original.revision, patch: { diagnostics: { request_log: "summary" } } });
  await op(request, "memory.owners", {});
  await page.goto("#/settings");
  await expect(page.getByText("memory.owners", { exact: true })).toBeVisible();
  await page.getByRole("radio", { name: "关闭", exact: true }).check();
  await page.getByRole("checkbox", { name: "开启兼容返回" }).check();
  await page.getByRole("button", { name: "检查影响", exact: true }).click();
  await expect(page.getByText("检查通过，尚未保存配置。", { exact: true })).toBeVisible();
  const preview = await op(request, "config.get", {});
  expect(preview.config.diagnostics.request_log).toBe("summary");
  expect(preview.config.mcp.error_envelope).toBe(false);
  await page.getByRole("button", { name: "保存设置", exact: true }).click();
  await expect(page.getByText("配置已保存。", { exact: true })).toBeVisible();
  await expect(page.getByText(/请求日志已关闭：停止记录/)).toBeVisible();
  await expect(page.getByText("memory.owners", { exact: true })).toHaveCount(0);
  const saved = await op(request, "config.get", {});
  expect(saved.config.mcp.error_envelope).toBe(true);
  expect((await op(request, "system.request_log", {})).items).toEqual([]);
  // 测试只改变临时库；恢复基线，避免影响同文件的其他场景。
  await op(request, "config.update", { expected_revision: saved.revision, patch: original.config });
});

test("关系图筛选：分类不重复，节点可见性、空选与保存重载一致", async ({ page, request }) => {
  await page.goto("#/relations");
  const types = page.getByRole("group", { name: "节点类型" });
  const details = page.getByRole("group", { name: "记录内部细节" });
  const kinds = page.getByRole("group", { name: "记录分类" });
  const categories = page.getByRole("group", { name: "关系类别" });
  await expect(types.getByRole("checkbox")).toHaveCount(5);
  await expect(types.getByRole("checkbox", { name: "对象" })).toBeChecked();
  await expect(types.getByRole("checkbox", { name: "记录" })).toBeChecked();
  await expect(types.getByRole("checkbox", { name: "概念" })).toBeChecked();
  await expect(types.getByRole("checkbox", { name: "结论（独立判断）" })).toBeChecked();  // R23 D-12
  await expect(types.getByRole("checkbox", { name: "来源" })).not.toBeChecked();
  await expect(details.getByRole("checkbox", { name: "展开 L1 记录内的块" })).not.toBeChecked();
  await expect(categories.getByRole("checkbox", { name: "结论要素" })).toBeChecked();
  await expect(categories.getByRole("checkbox", { name: "提及" })).not.toBeChecked();
  // 七个分类外加显式的"全部"；它替代了原先的"不选＝全部"文字提示
  await expect(kinds.getByRole("checkbox")).toHaveCount(8);
  // 筛选区为"组名＋选项"两列：分类只筛选记录的说明放在组名的悬停提示中；未选任何分类时"全部"处于选中态
  await expect(page.locator(".filter-label", { hasText: "记录分类" })).toHaveAttribute("title", /只筛选记录节点/);
  await expect(kinds.getByRole("checkbox", { name: "全部" })).toBeChecked();
  await page.screenshot({ path: "test-results/relations-filters.png", fullPage: true });

  // 浏览器请求走真实服务；分类只限制记录，默认显示的结论仍保留导航上下文。
  const [classified] = await Promise.all([
    page.waitForResponse((response) => response.url().endsWith("/relations.graph") && response.request().postDataJSON().kinds?.includes("experience")),
    kinds.getByRole("checkbox", { name: "经验（L3）" }).check(),
  ]);
  const classifiedNodes = (await classified.json()).nodes as { type: string; kind: string }[];
  expect(classifiedNodes.some((node) => node.type === "claim")).toBeTruthy();
  expect(classifiedNodes.filter((node) => node.type === "record").every((node) => node.kind === "experience")).toBeTruthy();
  expect(classified.request().postDataJSON()).not.toHaveProperty("levels");

  // 选块后再全部取消，最后一个请求必须保留 []，不能变成代表全选的 null。
  await details.getByRole("checkbox", { name: "展开 L1 记录内的块" }).check();
  for (const name of ["对象", "记录", "概念"]) {
    await types.getByRole("checkbox", { name, exact: true }).uncheck();
  }
  await details.getByRole("checkbox", { name: "展开 L1 记录内的块" }).uncheck();
  const [empty] = await Promise.all([
    page.waitForResponse((response) => response.url().endsWith("/relations.graph") && response.request().postDataJSON().node_types?.length === 0),
    types.getByRole("checkbox", { name: "结论（独立判断）", exact: true }).uncheck(),
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
    page.waitForResponse((response) => response.url().endsWith("/relations.graph") && response.request().postDataJSON().node_types?.length === 4),
    page.getByRole("button", { name: "重置", exact: true }).click(),
  ]);
  expect(reset.request().postDataJSON()).toMatchObject({ node_types: ["owner", "record", "claim", "concept"], kinds: null });
  await expect(types.getByRole("checkbox", { checked: true })).toHaveCount(4);
  expect((await reset.json()).nodes.some((node: { type: string }) => node.type === "block")).toBeFalsy();
});

test("关系图连线：点击关联与结论依据显示声明记录和完整声明", async ({ page, request }) => {
  type Node = { id: string; type: string; owner_id: string; ref?: { target_kind: string; target_id: string; revision?: number; sha256?: string; locator?: string; relation?: string } };
  type Edge = { source: string; target: string; category: string; type: string; declared_by?: string; field?: string };
  const graph = await op(request, "relations.graph", { node_types: ["owner", "record", "block", "claim", "concept", "source"], limit: 2000 });
  const nodes = graph.nodes as Node[];
  const edges = graph.edges as Edge[];
  const visibleNodeIds = new Set(nodes.filter((node) => ["owner", "record", "claim", "concept"].includes(node.type)).map((node) => node.id));
  let association = edges.find((edge) => edge.field === "association" && visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target));
  if (!association) {
    const records = nodes.filter((node) => node.type === "record" && node.ref?.target_kind === "record");
    const existingPairs = new Set(edges.filter((edge) => edge.field === "association")
      .map((edge) => [edge.source, edge.target].sort().join("|")));
    const pair = records.flatMap((from, i) => records.slice(i + 1).map((to) => [from, to] as const))
      .find(([from, to]) => !existingPairs.has([from.id, to.id].sort().join("|")));
    if (!pair) throw new Error("关系图种子需要两条尚未建关联、可引用的记录");
    const [from, to] = pair;
    await op(request, "relations.decide", {
      owner_id: from.owner_id,
      payload: { from: from.ref, to: to.ref, explanation: "端到端测试：关系图连线声明详情", certainty: "supported" },
      reason: "验证关系图关联边点击后的声明详情",
    });
    const updated = await op(request, "relations.graph", { node_types: ["owner", "record", "block", "claim", "concept", "source"], limit: 2000 });
    association = (updated.edges as Edge[]).find((edge) => edge.field === "association");
    expect(association, "保存的关联应进入真实关系图投影").toBeTruthy();
  }
  // R23 D-11：结论按角色连向参与者，没有对象间的快捷边
  const claimPart = edges.find((edge) => edge.category === "claim_part" && edge.field === "claims.relation.participants"
    && edge.declared_by?.startsWith("CLM-") && edge.source === edge.declared_by);
  expect(claimPart, "关系图种子需要一条结论要素边").toBeTruthy();
  expect(edges.some((edge) => (edge as { derived?: boolean }).derived)).toBeFalsy();

  await page.goto("#/relations");
  const types = page.getByRole("group", { name: "节点类型" });
  await page.waitForResponse((response) => response.url().endsWith("/relations.view"));
  const [defaults] = await Promise.all([
    page.waitForResponse((response) => response.url().endsWith("/relations.graph") && response.request().postDataJSON().node_types?.length === 4),
    page.getByRole("button", { name: "重置", exact: true }).click(),
  ]);
  expect(defaults.ok()).toBeTruthy();
  await expect(types.getByRole("checkbox", { name: "结论（独立判断）" })).toBeChecked();
  await expect(types.getByRole("checkbox", { name: "来源" })).not.toBeChecked();
  const defaultGraph = await defaults.json();
  expect((defaultGraph.nodes as Node[]).some((node) => node.id === claimPart!.declared_by)).toBeTruthy();
  expect((defaultGraph.edges as Edge[]).some((edge) => edge.category === "claim_part"
    && edge.declared_by === claimPart!.declared_by)).toBeTruthy();
  await expect(page.locator(".graph canvas").first()).toBeVisible();
  const openAndCheck = async (edge: Edge, screenshot: string, sectionTitle: string) => {
    const from = nodes.find((node) => node.id === edge.source);
    const to = nodes.find((node) => node.id === edge.target);
    expect(from, "连线起点必须来自服务端关系图投影").toBeTruthy();
    expect(to, "连线终点必须来自服务端关系图投影").toBeTruthy();
    await clickGraphLink(page, edge.source, edge.target);
    const details = page.getByRole("heading", { name: "连线详情" });
    await expect(details).toBeVisible();
    const item = page.locator(".edge-detail-entry", { hasText: edge.declared_by ?? "" }).first();
    await expect(item).toContainText(edge.declared_by ?? "");
    await expect(item).toContainText("声明位置");
    await expect(item).toContainText("原记录：");
    const section = item.getByText(sectionTitle, { exact: true });
    await expect(section).toBeVisible();
    const width = await item.evaluate((element) => ({ scroll: element.scrollWidth, client: element.clientWidth }));
    expect(width.scroll).toBeLessThanOrEqual(width.client + 2);
    const [declarationBox, sectionBox] = await Promise.all([
      item.locator("p.hint").first().boundingBox(), section.boundingBox(),
    ]);
    expect(declarationBox).toBeTruthy();
    expect(sectionBox).toBeTruthy();
    expect(sectionBox!.y).toBeGreaterThanOrEqual(declarationBox!.y + declarationBox!.height - 1);
    await page.screenshot({ path: screenshot, fullPage: true });
    return item;
  };

  const associationItem = await openAndCheck(association!, "test-results/relations-association-detail.png", "关系说明");
  await expect(associationItem).toContainText("关系说明");
  const claimItem = await openAndCheck(claimPart!, "test-results/relations-claim-detail.png", "结论原文");
  await expect(claimItem.getByText("结论原文")).toBeVisible();
  await expect(claimItem).toContainText("排序后求和误差更小");
  await expect(claimItem).toContainText("自动生成，以陈述为准");
  await expect(claimItem).toContainText("结论依据");
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

test("工作台笔记（R15）：交给 AI → AI 方案只预演 → 用户执行后才写入", async ({ page, request }) => {
  await page.goto("#/relations");
  await page.getByPlaceholder("标题", { exact: true }).fill("顺序实验与补偿求和是否同一类问题");
  await page.getByRole("button", { name: /^添加/ }).click();
  const draft = page.locator(".claims li", { hasText: "顺序实验与补偿求和是否同一类问题" });
  await draft.getByRole("button", { name: "交给 AI" }).click();
  await expect(page.getByText("AI 处理中（1）")).toBeVisible();

  // 模拟 AI：读 queued 笔记，用两条记录的固定引用提出建关联的方案
  const [note] = await op(request, "relations.notes", { status: ["queued"] });
  const graph = await op(request, "relations.graph", { node_types: ["record"], limit: 50 });
  const [a, b] = graph.nodes.filter((n: { ref?: unknown }) => n.ref);
  const before = (await op(request, "relations.associations", {})).length ?? 0;
  const reply = await op(request, "relations.note_reply", {
    note_id: note.id, outcome: "proposal", message: "两者都在讨论误差来源，建议建一条导航关联",
    steps: [{ op: "relations.decide", owner_id: a.owner_id, payload: { from: a.ref, to: b.ref, explanation: "都在讨论误差来源" } }],
  });
  expect(reply.status).toBe("proposed");
  expect(((await op(request, "relations.associations", {})).length ?? 0)).toBe(before);  // 只预演，没有写入

  await page.reload();
  const card = page.locator(".note-card", { hasText: "顺序实验与补偿求和是否同一类问题" });
  await expect(card.getByRole("list", { name: "写入预览" })).toContainText("都在讨论误差来源");
  await card.getByRole("button", { name: "执行" }).click();
  await expect(page.getByText(/已处理（\d+）/)).toBeVisible();
  const [done] = (await op(request, "relations.notes", {})).filter((n: { id: string }) => n.id === note.id);
  expect(done.status).toBe("handled");
  expect(done.result.writes[0].record_ids.length).toBe(1);
});

test("维护计划（R16）：勾选候选生成计划 → 交给 AI → AI 写处置 → 用户执行", async ({ page, request }) => {
  const { data } = state();
  await op(request, "monitor.run", {});                                         // 建立（或刷新）基线
  writeFileSync(join(data, "research", "order-demo", "data", "result.txt"), "升序误差 0.0（已更正）\n", "utf8");
  await page.goto("#/maintenance");
  await page.getByRole("button", { name: "扫描并记录" }).click();
  const box = page.getByRole("checkbox", { name: /^选择 / }).and(page.locator(":enabled")).first();
  await expect(box).toBeVisible({ timeout: 20_000 });
  await box.check();
  await page.getByRole("button", { name: "为所选生成维护计划" }).click();
  await page.getByRole("button", { name: "交给 AI" }).click();
  await expect(page.getByText(/AI 会在下次对话中写好处置/)).toBeVisible();

  // 模拟 AI：读 queued 计划并逐条写处置（保留）
  const [queued] = await op(request, "maintenance.plans", { status: ["queued"] });
  const plan = await op(request, "maintenance.status", { plan_id: queued.plan_id });
  expect(plan.items.length).toBeGreaterThan(0);                                 // 结论候选也落到记录上，计划不为空
  await op(request, "maintenance.review", {
    plan_id: plan.plan_id, message: "更正不影响结论",
    decisions: plan.items.map((i: { target: { target_id: string } }) => ({ target_id: i.target.target_id, action: "retain", reason: "更正只改了说明文字" })),
  });

  await expect(page.getByRole("button", { name: "执行" })).toBeVisible({ timeout: 12_000 });   // 页面自动刷新到"待你确认"
  await expect(page.getByText("理由：更正只改了说明文字").first()).toBeVisible();
  await page.getByRole("button", { name: "执行" }).click();
  await expect(page.locator(".plan-detail").getByText("已执行")).toBeVisible();
});

test("删除与恢复（R22）：记录删除后可恢复；对象移入回收站后在维护页恢复", async ({ page }) => {
  await page.goto("#/memory/RES-VENV-DEMO");
  const row = page.getByRole("row", { name: /虚拟环境验证概览/ });
  await expect(row).toBeVisible();
  await row.getByRole("checkbox").check();
  await page.getByRole("button", { name: "删除所选（1）" }).click();
  await page.getByLabel("删除理由").fill("端到端测试");
  await page.getByRole("button", { name: "删除 1 条记录" }).click();
  await expect(row).toHaveCount(0);
  await page.getByText("已删除的记录（1）").click();
  await page.getByRole("button", { name: "恢复" }).click();
  // 恢复完成后"已删除的记录"消失、记录回到列表；之后的对象预检才基于恢复后的 HEAD
  await expect(page.getByText(/已删除的记录/)).toHaveCount(0);
  await expect(page.getByRole("row", { name: /虚拟环境验证概览/ })).toBeVisible();

  await page.getByRole("button", { name: "删除对象" }).click();
  await page.getByLabel("删除理由").fill("端到端测试");
  await page.getByRole("button", { name: "移入回收站" }).click();
  await expect(page).toHaveURL(/#\/memory$/);
  await expect(page.getByText("RES-VENV-DEMO")).toHaveCount(0);
  await page.goto("#/maintenance");
  const trash = page.locator("section.panel").filter({ has: page.getByRole("heading", { name: "已删除的对象（1）" }) });
  await trash.getByRole("button", { name: "恢复" }).click();
  await expect(page.getByRole("heading", { name: "已删除的对象（0）" })).toBeVisible();
  await page.goto("#/memory");
  await expect(page.getByText("RES-VENV-DEMO")).toBeVisible();
});
