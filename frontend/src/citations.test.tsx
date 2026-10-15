import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CitationList, CitationScope, CitedMarkdown, citationEntries, type CitationFragment, type StoredLink } from "./citations";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const source = { owner_id: "RES-X", record_id: "R-TEXT", revision: 3 };
const recordLink = (text: string, revision = 2, locator = "record"): StoredLink => ({ text, status: "resolved",
  ref: { target_kind: "record", target_id: "R-PAPER", revision, sha256: "abc", locator } });
const targets = { "R-PAPER": { kind: "record" as const, owner_id: "RES-X", record_id: "R-PAPER", title: "一份名称很长的原始论文记录" } };
const fragment = (text: string, links: StoredLink[], extra: Partial<CitationFragment> = {}): CitationFragment =>
  ({ source, texts: [text], links, targets, ...extra });

describe("引用编号只派生在阅读视图", () => {
  it("跨段落按出现顺序编号，目标别名共用编号，不同修订和块分开", () => {
    const fragments = [
      fragment("第一段 [[@论文|长显示名]]", [recordLink("@论文")]),
      fragment("再次 [[@别名]]，旧版 [[@旧版]]，块 [[@论文#method]]", [recordLink("@别名"), recordLink("@旧版", 1), recordLink("@论文#method", 2, "block:method")]),
    ];
    expect(citationEntries(fragments).map((e) => [e.number, e.text])).toEqual([[1, "@论文"], [2, "@旧版"], [3, "@论文#method"]]);
    render(<CitationScope fragments={fragments}>
      {fragments.map((f, i) => <CitedMarkdown key={i} text={f.texts[0]} source={f.source} links={f.links} />)}<CitationList />
    </CitationScope>);
    expect(screen.getAllByRole("link", { name: "引用 1：一份名称很长的原始论文记录" })).toHaveLength(2);
    expect(within(screen.getByRole("region", { name: "引用对照" })).getAllByRole("listitem")).toHaveLength(3);
    expect(screen.getAllByRole("tooltip")[0]).toHaveTextContent("固定修订 2");
    expect(screen.getAllByRole("tooltip")[3]).toHaveTextContent("位置：块 method");  // R24：定位显示为可读写法
    // 编号不写回文本，别名和原有定位仍完整保存在规范输入里。
    expect(fragments[0].texts[0]).toBe("第一段 [[@论文|长显示名]]");
  });

  it("原文 [1]、数学、代码、概念与未解析链接都不冒充出处", () => {
    const text = "原文 [1] 与 [文献](https://example.org)，公式 $x_{[1]}$，`[[@论文]]`。\n\n```md\n[[@论文]]\n```\n\n概念 [[相干性]]，缺口 [[@未知]]，出处 [[@论文]]";
    const links = [recordLink("@论文"), { text: "@未知", status: "unresolved", ref: null },
      { text: "相干性", status: "resolved", ref: { target_id: "C-1", target_kind: "record" } }];
    const f = fragment(text, links, { targets: { ...targets,
      "C-1": { kind: "record", owner_id: "KNW-X", record_id: "C-1", title: "相干性" } } });
    expect(citationEntries([f])).toHaveLength(1);
    const { container } = render(<CitationScope fragments={[f]}><CitedMarkdown text={text} source={source} links={links} /><CitationList /></CitationScope>);
    // 概念在正文中是普通链接，在底部"提及的概念"中另列一次（R24 D-11），都不编号。
    screen.getAllByRole("link", { name: "相干性" }).forEach((link) => expect(link).toHaveAttribute("href", "#/memory/KNW-X/C-1"));
    expect(within(screen.getByRole("region", { name: "引用对照" })).getByText("提及的概念")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "文献" })).toHaveAttribute("href", "https://example.org");
    expect(container.querySelectorAll("code")).toHaveLength(2);
    expect(container.querySelector(".katex")).toBeInTheDocument();
    expect(container).toHaveTextContent("原文 [1]");
    expect(container).toHaveTextContent("缺口 [[@未知]]");
    expect(screen.getAllByRole("link", { name: /^引用 / })).toHaveLength(1);
  });

  it("引用别的记录的图：![[@…#图]] 显示图并计入编号，[[#图]] 由归属记录显示（R24）", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
      const args = JSON.parse(String(init.body ?? "{}"));
      return new Response(JSON.stringify(url.endsWith("memory.figure") ? { data_url: "data:image/png;base64,AA==",
        caption: `成绩对比 ${args.label}`, name: "Figure 1" } : targets), { status: 200 });
    }));
    const figureLink = recordLink("@论文#fig-1", 2, "figure:fig-1");
    const text = "见 [[@论文#fig-1|Figure 1]]。\n\n![[@论文#fig-1]]\n\n本记录的图 ![[#fig-2]]";
    const f = fragment(text, [figureLink]);
    const own = (key: number | string) => <span>本记录图 {String(key)}</span>;
    const { container } = render(<CitationScope fragments={[f]}><CitedMarkdown text={text} source={source} links={f.links} figure={own} />
      <CitationList /></CitationScope>);
    expect(await screen.findByRole("img", { name: "成绩对比 fig-1" })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: /^引用 1/ })).toHaveLength(2);  // 文中引用与图注后的编号是同一条
    expect(container).toHaveTextContent("Figure 1：成绩对比 fig-1");
    expect(container).toHaveTextContent("本记录图 fig-2");
    expect(container.querySelector("p figure")).toBeNull();
    expect(within(screen.getByRole("region", { name: "引用对照" })).getAllByRole("listitem")).toHaveLength(1);
  });

  it("结论引用与不同内容指纹分开编号；未显示的引用不占号", () => {
    const links = [recordLink("@正文"), { ...recordLink("@不同指纹"), ref: { ...recordLink("@正文").ref!, sha256: "def" } },
      { ...recordLink("!结论"), ref: { target_kind: "claim", target_id: "CLM-1", sha256: "xyz", locator: "claim" } }, recordLink("@未显示")];
    expect(citationEntries([fragment("[[@正文]] [[@不同指纹]] [[!结论]]", links)]).map((e) => e.text))
      .toEqual(["@正文", "@不同指纹", "!结论"]);
  });

  it("名称查询按来源固定修订去重，失败时仍保留编号和原引用名", async () => {
    const fetch = vi.fn(async (_url: string, _init: RequestInit) => new Response(JSON.stringify({ code: "UNAVAILABLE", message: "不可用" }), { status: 503 }));
    vi.stubGlobal("fetch", fetch);
    const f = fragment("[[@论文]]", [recordLink("@论文")], { targets: undefined });
    render(<CitationScope fragments={[f, { ...f, texts: ["再次 [[@论文]]"] }]}>
      <CitedMarkdown text={f.texts[0]} source={source} links={f.links} /><CitationList />
    </CitationScope>);
    expect(screen.getByRole("button", { name: "引用 1：论文" })).toHaveTextContent("〔引1〕");
    expect(await screen.findByText(/部分引用名称未能加载/)).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(JSON.parse(String(fetch.mock.calls[0][1]?.body))).toEqual(source);
  });

  it("异步名称加载不改变号码，也不重建原位置的图片", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(targets), { status: 200 })));
    const f = fragment("[[@论文]]\n\n![原图](figure:0)", [recordLink("@论文")], { targets: undefined });
    function Image() { return <figure><img alt="原图" src="/test.png" /></figure>; }
    const figure = () => <Image />;
    const { container } = render(<CitationScope fragments={[f]}><CitedMarkdown text={f.texts[0]} source={source} links={f.links} figure={figure} /><CitationList /></CitationScope>);
    const image = screen.getByRole("img", { name: "原图" });
    await waitFor(() => expect(screen.getByRole("link", { name: "引用 1：一份名称很长的原始论文记录" })).toBeInTheDocument());
    expect(screen.getByRole("img", { name: "原图" })).toBe(image);
    expect(container.querySelector("p figure")).toBeNull();
  });
});
