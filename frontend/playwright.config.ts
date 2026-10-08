import { defineConfig } from "@playwright/test";

// 端到端测试：真实后端（样例数据 + 确定性假向量）+ 构建好的前端。先 npm run build。
// Windows 上使用系统自带的 Edge，不需要另外下载浏览器。
const PORT = 8799;
export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  timeout: 60_000,
  use: {
    baseURL: `http://127.0.0.1:${PORT}/e2e/`,
    browserName: "chromium",
    channel: process.platform === "win32" ? "msedge" : undefined,
    headless: true,
    viewport: { width: 1360, height: 900 },
  },
  webServer: {
    command: `uv run python frontend/e2e/server.py ${PORT}`,
    cwd: "..",
    url: `http://127.0.0.1:${PORT}/e2e/api/v1/ops`,
    timeout: 120_000,
    reuseExistingServer: false,
    env: { PYTHONUTF8: "1" },
  },
  reporter: [["list"]],
});
