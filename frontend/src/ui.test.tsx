import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { linkify, Markdown } from "./ui";

afterEach(cleanup);

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

it("原位置图片与就近出处并存，figure 不嵌进 p", () => {
  const text = linkify("![原图注](figure:0) [原图](figure:0) [\u005b@原件|出处\u005d]", { "@原件": "#/memory/A/B" });
  const { container } = render(<Markdown text={text} figure={() => <figure><img src="/test.png" alt="原图" /><figcaption>原图注</figcaption></figure>} />);
  expect(screen.getByRole("img", { name: "原图" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "出处" })).toHaveAttribute("href", "#/memory/A/B");
  expect(container.querySelector("p figure")).toBeNull();
});

it("没有引用占位符的 Markdown 链接仍显示文字，包括空地址与对象原型同名地址", () => {
  render(<Markdown text="[空地址]() [普通地址](constructor)" anchors={{}} />);
  expect(screen.getByText("空地址")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "普通地址" })).toHaveAttribute("href", "constructor");
});
