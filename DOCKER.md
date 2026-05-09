# Docker · 一键起 agent + Go gateway

> **TL;DR**
> ```bash
> cp backend/.env.example backend/.env   # 填 ANTHROPIC_API_KEY
> docker compose up --build              # 等 ~3 分钟首次构建
> curl http://localhost:8080/api/ask -d '你好'
> ```

---

## 这套 compose 跑起来什么样

```
你/前端 ─→ :8080  gateway  (Go · 12 MB · distroless)   ─→ http://agent:8000  agent  (Python · LangGraph · 1.2 GB)
                  · /health                                                        · /chat (SSE)
                  · POST /api/ask     (blocking · text/plain)                      · /memory
                  · GET  /api/stream  (SSE 透传给浏览器)                            · /profile
                  · 你在这一层加 auth / rate-limit / audit                          · /usage / /traces / /models
```

两个容器同处一个 compose network · gateway 用 `http://agent:8000` 直连后端 · 不走宿主机回环。

---

## 文件清单

| 文件 | 干什么 |
|---|---|
| `backend/Dockerfile` | Python 3.11-slim · 多阶段 · 自动从 `pyproject.toml` 抽依赖 · 装 chromadb / sentence-transformers / langgraph |
| `backend/.dockerignore` | 排除 `.env` / `data/` / `__pycache__` |
| `clients/go-client/Dockerfile` | golang:1.22-alpine 编译 + distroless 运行 · 最终镜像 ~12 MB |
| `clients/go-client/.dockerignore` | 排除测试与 README |
| `docker-compose.yml` | 编排 agent + gateway + 健康检查 + 卷挂载 |

---

## 第一次启动

### 1. 准备 API key

```bash
cp backend/.env.example backend/.env
# 编辑 backend/.env · 至少填一个：
#   ANTHROPIC_API_KEY=sk-ant-...
# 或 OPENAI_API_KEY=sk-...
# 或 GOOGLE_API_KEY=...
```

### 2. 启动

```bash
docker compose up --build
```

第一次构建会下载 ~1.2 GB Python deps（chromadb + sentence-transformers + onnxruntime + langgraph 全套），耗时 ~3 分钟。后续构建走 cache 几秒就完。

成功的输出：

```
taotao-agent     | INFO:     Uvicorn running on http://0.0.0.0:8000
taotao-agent     | ✅ healthcheck passing
taotao-gateway   | 🚀 listening on :8080 · POST /api/ask · GET /api/stream?msg=...
```

### 3. 试一下

```bash
# 健康
curl http://localhost:8080/health
# {"ok":true,"upstream":"agent"}

# 阻塞调用 · 拿完整回复
curl -X POST http://localhost:8080/api/ask \
     -H 'X-Session-ID: my-session' \
     -d '用一句话介绍 LangGraph'

# 流式 · 浏览器打开都行
curl 'http://localhost:8080/api/stream?msg=hi&session=my-session'
```

---

## 常见操作

```bash
# 后台跑
docker compose up -d --build

# 看日志
docker compose logs -f agent
docker compose logs -f gateway

# 只重启 gateway（改了 Go 代码）
docker compose up -d --build gateway

# 停所有 · 保留数据
docker compose down

# 停所有 · 清空 chroma + 检查点（重置 memory）
docker compose down && rm -rf data/

# 看健康
docker compose ps
# NAME              STATUS
# taotao-agent      Up 2 minutes (healthy)
# taotao-gateway    Up 2 minutes (healthy)
```

---

## 调用本地 LLM (Ollama / LM Studio / vLLM)

agent 容器默认连不到宿主机。两步开通：

**1. 在 `docker-compose.yml` 的 `agent:` 下加：**

```yaml
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

**2. 在 `backend/.env` 写：**

```bash
AGENT_MODEL=ollama:qwen2.5:14b
AGENT_FAST_MODEL=ollama:qwen2.5:7b
OLLAMA_BASE_URL=http://host.docker.internal:11434
```

然后 `docker compose up --build agent`。

> Linux 用户 host-gateway 也可以；Mac/Windows 上的 Docker Desktop 会自动解析。

---

## 加 Ollama 进 compose（彻底容器化）

如果你想连 Ollama 也容器化（不依赖宿主机），加一个 service：

```yaml
  ollama:
    image: ollama/ollama:latest
    ports: ["11434:11434"]
    volumes:
      - ./data/ollama:/root/.ollama
    # GPU 透传 · 需要 nvidia-container-toolkit
    # deploy:
    #   resources:
    #     reservations:
    #       devices:
    #         - driver: nvidia
    #           count: all
    #           capabilities: [gpu]

  agent:
    # ... 已有内容 ...
    environment:
      OLLAMA_BASE_URL: http://ollama:11434
    depends_on:
      ollama:
        condition: service_started
```

首次启动后拉模型：

```bash
docker compose exec ollama ollama pull qwen2.5:14b
docker compose exec ollama ollama pull qwen2.5:7b
```

---

## 持久化的数据

`./data/` 挂载到 agent 容器的 `/app/data/`：

```
data/
├── chroma/                ← Chroma 向量库 (long-term memory · skills · reflections)
├── checkpoints.sqlite     ← LangGraph checkpointer · 会话状态
├── workspace/             ← agent 自己创建的文件 (file_ops 工具产物)
├── traces.jsonl           ← 所有 trace 事件归档 (给 GET /traces 读)
└── hf-cache/              ← HuggingFace embedding 模型缓存
```

删 `data/` 等于完全重置 memory · session · 工作区文件。

---

## 部署到生产

### k8s

```yaml
# agent-deployment.yaml · 简化版
apiVersion: apps/v1
kind: Deployment
metadata: { name: agent }
spec:
  replicas: 1                               # ⚠ chroma 单点 · 多副本要换 PG/Qdrant
  selector: { matchLabels: { app: agent } }
  template:
    metadata: { labels: { app: agent } }
    spec:
      containers:
        - name: agent
          image: taotao-agent-backend:dev
          ports: [{ containerPort: 8000 }]
          envFrom: [{ secretRef: { name: agent-secrets } }]
          volumeMounts: [{ name: data, mountPath: /app/data }]
          livenessProbe:
            httpGet: { path: /health, port: 8000 }
            initialDelaySeconds: 30
          readinessProbe:
            httpGet: { path: /health, port: 8000 }
      volumes:
        - name: data
          persistentVolumeClaim: { claimName: agent-data }
---
apiVersion: apps/v1
kind: Deployment
metadata: { name: gateway }
spec:
  replicas: 3                               # gateway 无状态 · 随便多副本
  selector: { matchLabels: { app: gateway } }
  template:
    metadata: { labels: { app: gateway } }
    spec:
      containers:
        - name: gateway
          image: taotao-agent-gateway:dev
          ports: [{ containerPort: 8080 }]
          env:
            - name: AGENT_URL
              value: http://agent:8000
          livenessProbe:
            exec: { command: [/gateway, -mode=healthcheck, -base=http://agent:8000] }
```

### 推到 registry

```bash
# 编译 + 标签 + 推
docker compose build
docker tag taotao-agent-backend:dev    your-registry/taotao-agent:0.1.0
docker tag taotao-agent-gateway:dev    your-registry/taotao-gateway:0.1.0
docker push your-registry/taotao-agent:0.1.0
docker push your-registry/taotao-gateway:0.1.0
```

### multi-arch（Mac → Linux 服务器）

```bash
# 启用 buildx · 一次构建出 amd64 + arm64
docker buildx create --use
docker buildx build --platform linux/amd64,linux/arm64 \
    -t your-registry/taotao-agent:0.1.0 --push ./backend
docker buildx build --platform linux/amd64,linux/arm64 \
    -t your-registry/taotao-gateway:0.1.0 --push ./clients/go-client
```

---

## Debug 速查

| 现象 | 诊断 | 修 |
|---|---|---|
| `agent` 一直 `unhealthy` · `restarting` | `docker compose logs agent` 看日志，多半是 API key 没填 | 编辑 `backend/.env` 补 key · `docker compose restart agent` |
| `gateway` 起来了但 `/health` 503 | upstream agent 没 ready | 等 30-40s start_period · 看 `docker compose ps` 直到 agent healthy |
| 改了 Go 代码不生效 | 没重 build | `docker compose up -d --build gateway`（`--build` 关键） |
| `pip install` 失败 · 卡在 onnxruntime | 用了 alpine 之类的 musl 镜像 | Dockerfile 用的是 `python:3.11-slim` (glibc) 应该 OK · 报 issue 给我 |
| 容器跑起来但调用超时 | 网络被防火墙挡 · 或 LLM provider 限速 | 进容器 `docker compose exec agent curl https://api.anthropic.com` 验证出网 |
| Mac 容器跑很慢 | Docker Desktop 默认资源给得少 | Settings → Resources → CPU/Memory 各拉到一半物理资源 |

---

## 安全注意

> ⚠ 本 repo 默认配置是 **dev 友好** · 直接拿来上生产之前先做这几件事：

1. **Auth** —— gateway 现在没鉴权 · 加 JWT 中间件或挡在 nginx/traefik 后面
2. **Rate limit** —— LLM 是钱 · 给 gateway 加 token-bucket 限流
3. **CORS** —— `app.py` 当前 `allow_origins=["*"]` · 收紧到你的前端域名
4. **Secrets** —— `.env` 别提交 git · 用 docker secret / k8s secret / SOPS
5. **资源上限** —— compose 加 `mem_limit` / `cpus` · 一个 OOM 进程别拖死宿主机
6. **日志脱敏** —— Trace 事件可能含用户输入 · 进数据库前过 PII redactor

---

## 想砍点什么？

- **不要 Go gateway**：删 `gateway:` block · 直接对着 agent:8000 调
- **不要持久化**：删 `volumes:` 块 · 每次重启 memory 清零
- **不要健康检查**：删 `healthcheck:` 块 · 启动更快但故障检测变慢
- **不要 chroma**：把 `backend/agent/memory/long_term.py` 换成内存 dict · `pyproject.toml` 删 chromadb · 镜像缩到 ~400 MB
