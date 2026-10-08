# 部署 WeKnora（Docker Desktop，本机使用）

已测试版本：**v0.8.2**。burrowl 的 WeKnora 连接器按这个版本的接口编写（`packages/burrowl-connector-weknora/.../api_v08.py`）。

## 1. 获取源码并固定版本

```bash
git clone --depth 1 --branch v0.8.2 https://github.com/Tencent/WeKnora.git D:/共享桌面/A_work/WeKnora
```

## 2. 配置

在 WeKnora 目录中：

1. 把 `.env.example` 复制为 `.env`，并修改以下几项：
   - `WEKNORA_VERSION=v0.8.2`：固定镜像版本，不用 latest。
   - `FRONTEND_PORT=8090`：80 端口常被其他服务占用。
   - `APP_PORT=8080`。
   - `DB_PASSWORD`、`REDIS_PASSWORD`、`JWT_SECRET`、`SYSTEM_SIGNING_KEY`：填随机长字符串。
   - `SYSTEM_AES_KEY`：用 `openssl rand -hex 16` 生成 32 个字符。**丢失后已加密的数据无法恢复，请妥善保存 `.env`。**
   - `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`：留空。
2. 把本目录的 `docker-compose.override.yml` 复制过去。它让 WeKnora 只监听 127.0.0.1，局域网无法访问。

## 3. 启动

```bash
docker compose up -d app frontend docreader postgres redis
```

- 首次启动会下载约 3.5 GB 镜像。
- 健康检查：`curl http://127.0.0.1:8080/health`。
- 网页界面：<http://127.0.0.1:8090>。

## 4. 在 WeKnora 网页中完成初始化（需要你本人操作）

1. **注册管理员账号**，登录后会自动创建个人空间（租户）。
2. **配置模型**。WeKnora 解析文档、检索和自动 Wiki 都要用模型，在"设置 → 模型"中添加：
   - **嵌入模型（必需）**：用于向量检索。
   - **对话模型（LLM）**：用于问答、摘要和自动 Wiki。
   - 本机已有 oneapi（3001 端口）等 OpenAI 兼容网关时，接口地址可以填 `http://host.docker.internal:3001/v1`，密钥填网关里为 WeKnora 单独创建的令牌。
   - 也可以直接用云厂商（如 DeepSeek、硅基流动、通义）的 API Key。
   - 模型密钥只填在 WeKnora 网页中，不要发给 AI。
3. **创建 API Key**：在"设置 → API Key"中创建，供 burrowl 连接使用。
4. **记下自动 Wiki 用的模型 ID**（可选）：对话模型的 ID，填到 burrowl 连接器的 `wiki.synthesis_model_id`。

## 5. 在 burrowl 中连接

1. 写入 API Key（交互输入，不回显，保存在 Windows 凭据管理器）：

   ```bash
   burrowl secret set keyring:burrowl/weknora
   ```

2. 在工作台的"设置 → 知识中枢连接器"中添加连接器：type 选 `weknora`，`base_url` 填 `http://127.0.0.1:8080`，`secret_ref` 填 `keyring:burrowl/weknora`，知识库名称默认是 `burrowl`。
3. 在"知识中枢"页面依次：测试连接 → 预览发布 → 确认发布 → 查看解析状态。

## 6. 实测验收（可选）

```bash
WEKNORA_URL=http://127.0.0.1:8080 WEKNORA_API_KEY=<你的 Key> uv run pytest -m live_hub
```

测试会在 `burrowl-live-test` 知识库中写入一条条目，结束后删除。

## 维护

- **停止**：`docker compose stop`。
- **升级**：先阅读 WeKnora 的 CHANGELOG，连接器只保证兼容已测试的版本。
- **数据**：保存在 Docker 卷中（`weknora_postgres-data`、`weknora_data-files`）。WeKnora 里的内容都能从 burrowl 重新发布，权威数据始终在 burrowl 本地。
