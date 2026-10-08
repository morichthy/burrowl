import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// 页面挂在 /<token>/ 下，所有资源与接口都用相对路径。
export default defineConfig({
  base: "./",
  plugins: [react()],
  build: { outDir: "dist", emptyOutDir: true, chunkSizeWarningLimit: 1500 },
  test: { environment: "jsdom", setupFiles: ["src/test-setup.ts"], css: false, include: ["src/**/*.test.{ts,tsx}"] },
});
