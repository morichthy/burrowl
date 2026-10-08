// 界面统一的中文标签。后端值保持英文，只在这里翻译。

export const REVIEW_BADGES: Record<string, { label: string; tone: string }> = {
  accepted: { label: "已确认", tone: "good" },
  "not-reviewed": { label: "未复核", tone: "muted" },
  disputed: { label: "有争议", tone: "warn" },
  retracted: { label: "已撤回", tone: "bad" },
  superseded: { label: "已替代", tone: "warn" },
  stale: { label: "已失效", tone: "bad" },
  "upstream-blocked": { label: "依据受阻", tone: "bad" },
  // 记录级的复核类别（检索结果）
  none: { label: "无结论", tone: "muted" },
  unreviewed: { label: "未复核", tone: "muted" },
  unsafe: { label: "有问题", tone: "bad" },
  mixed: { label: "部分确认", tone: "warn" },
};

export const REVIEW_FILTERS = [
  { value: "all", label: "全部" },
  { value: "reviewed_only", label: "只看已复核" },
  { value: "exclude_unsafe", label: "排除有问题的" },
  { value: "unreviewed_only", label: "只看未复核" },
] as const;

export const LEVELS: Record<string, string> = {
  L0: "L0 原始依据",
  L1: "L1 技术细节",
  L2: "L2 过程经过",
  L3: "L3 可复用认识",
  L4: "L4 概览",
};

export const KINDS: Record<string, string> = {
  source: "原始来源",
  detail: "技术单元",
  narrative: "过程叙述",
  experience: "经验",
  overview: "概览",
  document: "文稿",
  document_section: "文稿章节",
  question: "问题",
  goal: "目标",
  route: "路线",
  checkpoint: "检查点",
  association: "关联",
  review: "复核",
  wiki_page: "Wiki 页面",
  claim: "结论",
  run: "运行",
};

export const OWNER_TYPES: Record<string, string> = {
  research: "研究",
  project: "项目",
  "core-algorithm": "核心算法",
  knowledge: "知识",
  run: "运行",
  report: "报告",
  data: "数据",
  tool: "工具",
};

export const SESSION_STATUS: Record<string, string> = {
  active: "进行中",
  waiting_user: "等待用户",
  finished: "已完成",
  aborted: "已中止",
  archived: "已归档",
};

export const VERDICTS: Record<string, string> = {
  relevant: "相关",
  uncertain: "不确定",
  irrelevant: "不相关",
};

export const REQUEST_KINDS: Record<string, string> = {
  fulltext: "申请全文补偿",
  direction: "请用户定方向",
  clarify: "需要澄清",
};

export const STRATEGIES: Record<string, string> = {
  standard: "标准（筛选后完整阅读）",
  associative: "联想（不足时按方向补查）",
  quick: "快速（只判断片段）",
};

export const HUB_OPS: Record<string, string> = {
  create: "新增",
  update: "更新",
  delete: "删除",
  mark: "标记撤回",
  unchanged: "不变",
};

export const HUB_STATES: Record<string, string> = {
  pending: "排队中",
  processing: "解析中",
  completed: "已完成",
  failed: "失败",
  missing: "外部缺失",
  marked: "已标记撤回",
  drift: "外部被修改",
};

export const label = (table: Record<string, string>, value: string | null | undefined) =>
  value == null ? "—" : (table[value] ?? value);
