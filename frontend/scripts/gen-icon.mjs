// 由图标源文件 frontend/public/favicon.svg 生成 Windows 图标 packaging/burrowl.ico。
// 用 Playwright 自带的 Chromium 把 SVG 栅格化成各尺寸 PNG，再按 ICO 格式打包（Vista 起支持 PNG 条目）。
// 用法：node frontend/scripts/gen-icon.mjs（只在改了图标后运行，产物提交进仓库）。
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const here = dirname(fileURLToPath(import.meta.url));
const svg = readFileSync(join(here, "../public/favicon.svg"), "utf-8");
const out = join(here, "../../packaging/burrowl.ico");
const SIZES = [16, 20, 24, 32, 40, 48, 64, 256]; // 任务管理器、开始菜单、资源管理器各视图用到的尺寸

// 与 e2e 一致：Windows 用系统自带的 Edge，不另下载浏览器
const browser = await chromium.launch({ channel: process.platform === "win32" ? "msedge" : undefined });
const page = await browser.newPage();
const images = [];
for (const size of SIZES) {
  await page.setViewportSize({ width: size, height: size });
  await page.setContent(
    `<style>html,body{margin:0;background:transparent}svg{display:block;width:${size}px;height:${size}px}</style>${svg}`,
  );
  images.push(await page.screenshot({ omitBackground: true, clip: { x: 0, y: 0, width: size, height: size } }));
}
await browser.close();

// ICONDIR（6 字节）+ 每个尺寸一条 ICONDIRENTRY（16 字节）+ PNG 数据；宽高字段 0 表示 256。
const header = Buffer.alloc(6 + 16 * images.length);
header.writeUInt16LE(0, 0);
header.writeUInt16LE(1, 2);
header.writeUInt16LE(images.length, 4);
let offset = header.length;
images.forEach((png, i) => {
  const entry = 6 + 16 * i;
  header.writeUInt8(SIZES[i] % 256, entry);
  header.writeUInt8(SIZES[i] % 256, entry + 1);
  header.writeUInt16LE(1, entry + 4); // 色彩平面
  header.writeUInt16LE(32, entry + 6); // 每像素位数
  header.writeUInt32LE(png.length, entry + 8);
  header.writeUInt32LE(offset, entry + 12);
  offset += png.length;
});
writeFileSync(out, Buffer.concat([header, ...images]));
console.log(`wrote ${out} (${SIZES.join(", ")} px)`);
