import { describe, expect, it } from "vitest";
import { linkify } from "./ui";

describe("正文链接（R21 D-11）", () => {
  const targets = { "@定义": "#/memory/KNW-A/MEM-1", "!误差更小": "#/memory/KNW-A/MEM-2" };

  it("已解析的 [[…]] 换成可点击的链接，竖线后是显示文字", () => {
    expect(linkify("见 [[@定义]] 与 [[!误差更小|结论]]。", targets)).toBe(
      "见 [定义](#/memory/KNW-A/MEM-1) 与 [结论](#/memory/KNW-A/MEM-2)。",
    );
  });

  it("没解析的链接与代码里的 [[…]] 原样保留", () => {
    const text = "未知 [[@其他]]，代码 `[[@定义]]`\n\n```\n[[@定义]]\n```\n后文 [[@定义]]";
    expect(linkify(text, targets)).toBe(
      "未知 [[@其他]]，代码 `[[@定义]]`\n\n```\n[[@定义]]\n```\n后文 [定义](#/memory/KNW-A/MEM-1)",
    );
  });
});
