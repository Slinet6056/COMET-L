# COMET-L 多用户 Web 控制台生产部署指南

本文档面向需要在小型团队或半可信环境中部署 COMET-L 多用户 Web 控制台的运维人员。本指南说明单进程部署架构、安全配置、用户管理、数据备份与恢复等操作边界。

## 重要声明

**本版本为全新部署专用，不支持旧数据迁移。** 如果你之前使用过单用户版本的 COMET-L，旧版本的运行记录、配置文件和数据库无法直接迁移、导入或兼容读取到多用户版本。请作为全新实例部署。验证关键词：fresh deployment, migration unsupported。

**Maven 代码执行警告：** 用户上传的 Maven 项目会在服务器上执行 `mvn` 构建、测试和项目代码。Maven 插件、测试用例、构建脚本和依赖解析都可能产生代码执行、网络访问或资源消耗。本 MVP 版本适用于小型内部团队或半可信用户环境，不适合直接面向不可信的公共互联网用户。如需面向公网部署，必须额外增加容器隔离、cgroup 限制、网络隔离、只读挂载、进程和内存限制等更强的沙箱机制。

## 单进程架构限制

COMET-L Web 控制台采用单进程 SQLite 架构，这是当前版本的重要设计约束：

- **必须使用单 worker 模式启动：** `uvicorn --workers 1`
- **不支持多 worker、多进程或多节点部署**
- **不支持 Redis、Celery 或 Kubernetes 水平扩展**
- 调度器是单进程内的 FIFO 队列，依赖 SQLite 进行任务排队

启动命令示例：

```bash
uv run python -m uvicorn comet.web.app:app --host 0.0.0.0 --port 8000 --workers 1
```

或使用 just 命令（开发环境）：

```bash
just web-serve 8000
```

## 部署前提

- 只部署全新实例，不把旧单用户 `state/`、`output/`、`sandbox/` 或旧 Web 数据库复制进多用户实例。
- 只运行一个 FastAPI 进程，并固定 `workers 1`。反向代理可以多实例，但后端应用实例只能有一个。
- 将服务放在 HTTPS 后面，生产配置中启用 `deployment.secure_auth_cookies: true`。
- 只给内部或半可信用户开放账号。不要把当前 MVP 当作面向陌生用户的公共 SaaS。
- 为 `state/`、`output/`、`sandbox/` 和 `logs/` 配置持久化存储、备份和访问权限。

## 数据存储路径

Web 控制台使用以下用户隔离的数据存储路径。路径中的 `{user_id}` 是数据库用户 ID：

| 用途 | 默认路径 | 说明 |
|------|----------|------|
| SQLite 数据库 | `state/web/comet-web.sqlite3` | 用户、会话、运行记录、上传元数据 |
| 用户状态目录 | `state/users/{user_id}/` | 用户专属的运行时状态 |
| 用户输出目录 | `output/users/{user_id}/` | 运行产物和报告 |
| 用户沙箱目录 | `sandbox/users/{user_id}/` | 上传文件和项目克隆 |
| 用户日志目录 | `logs/users/{user_id}/` | 运行日志 |

环境变量 `COMET_WEB_DB_PATH` 可以覆盖 SQLite 数据库路径（主要用于测试和 CLI 场景）。

生产环境建议把这些目录放在同一台机器的本地磁盘或可靠块存储上。不要把 SQLite WAL 数据库放在不支持 SQLite 共享内存语义的网络文件系统上。

## 认证与会话模型

### Cookie 配置

系统使用名为 `comet_session` 的 HTTP-only Cookie 进行会话管理：

- **HttpOnly:** 是，JavaScript 无法读取
- **SameSite:** Lax，提供基本的 CSRF 防护
- **Secure:** 由配置 `deployment.secure_auth_cookies` 控制
  - 生产环境必须设置为 `true`（要求 HTTPS）
  - 本地 HTTP 开发环境可设为 `false`
- **Path:** /
- **有效期:** 7 天

### 生产环境 Cookie 配置

在 `config.yaml` 中配置：

```yaml
deployment:
  secure_auth_cookies: true
  allowed_origins:
    - "https://comet.example.internal"
```

`allowed_origins` 只写真实访问域名。不要使用通配符。服务会对携带 Cookie 的变更请求做 Origin 或 Referer 检查，配置错误会导致登录后无法提交运行或管理用户。

### 会话安全

- 服务器仅存储会话令牌的 SHA-256 哈希值，不存储原始令牌
- 禁用用户后，其所有会话立即失效
- 密码使用 Argon2id 哈希存储

## 初始管理员创建

首次部署时，必须先创建一个管理员账户：

```bash
uv run python -m comet.web.admin create-admin \
  --username admin \
  --password "your-secure-password"
```

注意：
- 用户名会被规范化为小写
- 密码必须安全存储，系统不存储明文
- 如果用户名已存在，命令会返回错误

## 用户管理

### 命令行管理工具

管理员可以使用 CLI 工具管理用户：

```bash
# 创建普通用户
uv run python -m comet.web.admin create-user \
  --username alice \
  --password "user-password" \
  --role user

# 列出所有用户
uv run python -m comet.web.admin list-users

# 禁用用户
uv run python -m comet.web.admin disable-user --user-id 2

# 重置密码
uv run python -m comet.web.admin reset-password \
  --user-id 2 \
  --password "new-password"

# 提升为管理员
uv run python -m comet.web.admin promote-user --user-id 2

# 降级为普通用户
uv run python -m comet.web.admin demote-user --user-id 2
```

### 管理员 API

管理员可以通过 Web API 管理用户（需要管理员身份认证）：

- `GET /api/admin/users` - 列出所有用户
- `POST /api/admin/users` - 创建新用户
- `POST /api/admin/users/{user_id}/disable` - 禁用用户
- `POST /api/admin/users/{user_id}/reset-password` - 重置密码
- `POST /api/admin/users/{user_id}/role` - 修改角色

普通用户访问这些接口会得到 403。管理员接口只返回安全用户字段，不返回密码哈希、会话 token 或 token hash。

### 最后活跃管理员保护

系统保护最后一个活跃管理员：
- 不能禁用最后一个活跃管理员
- 不能将最后一个活跃管理员降级为普通用户
- 操作会被拒绝并返回 `last_admin_protected` 错误

## 部署配置选项

以下配置项位于 `config.yaml` 的 `deployment` 部分，控制服务器端策略：

```yaml
deployment:
  # 单次运行限制
  max_budget: 500                    # 最大 LLM 调用次数
  max_run_timeout_seconds: 7200      # 最大运行超时（秒）
  max_iterations: 10                 # 最大迭代次数

  # Java 版本控制
  allowed_java_versions:             # 允许的 Java 版本
    - "8"
    - "11"
    - "17"
    - "21"
    - "25"

  # Cookie 安全
  secure_auth_cookies: true          # 生产环境必须启用
  allowed_origins: []                # 允许的额外 Origin

  # 队列限制
  global_max_running_tasks: 2        # 全局最大并行运行数
  per_user_max_running_tasks: 1      # 每用户最大并行运行数
  global_max_pending_tasks: 50       # 全局最大排队数
  per_user_max_pending_tasks: 5      # 每用户最大排队数

  # 本地路径模式（仅管理员）
  allow_local_path_mode: false       # 是否允许本地路径模式
  local_path_allowlist: []           # 允许的本地路径根目录

  # 数据保留
  upload_retention_hours: 24         # 未使用上传保留时间
  run_artifact_retention_days: 30    # 运行产物保留天数
```

上传限制由服务端固定检查执行。当前项目 ZIP 最大 25 MB，解压后总大小最大 200 MB，单文件最大 50 MB，最多 5000 个条目，并拒绝路径穿越、重复规范化路径、符号链接、特殊文件和异常压缩率。超过限制会返回“上传文件过大”等稳定错误。缺陷报告 ZIP 只接受 `.md`、`.txt`、`.diff` 和 `.patch` 文件。

队列限制是单进程 SQLite FIFO 调度器的保护阈值。默认全局同时运行 2 个任务，每用户同时运行 1 个任务；默认全局最多排队 50 个任务，每用户最多排队 5 个任务。超过限制时 API 返回 429 和 `queue_limit_exceeded`。

保留策略由清理逻辑读取：未使用上传默认保留 24 小时，已结束运行产物默认保留 30 天。运维侧应安排定时清理，并在清理前确认备份策略。

### 公开部署配置 API

`GET /api/deployment/public-config` 返回前端安全的部署配置（不包含敏感信息）：

- Cookie 安全标志
- 允许的 Origin 列表
- 本地路径模式开关（不包含具体路径）
- 队列限制
- 预算和超时限制
- 保留策略

注意：此 API 故意不返回 `local_path_allowlist` 的具体路径，以防泄露服务器目录结构。

## 用户权限模型

### 普通用户权限

- 只能使用上传模式创建运行（上传 ZIP 文件）
- 创建运行时使用 `projectUploadId`，缺陷报告使用可选 `bugReportsUploadId`
- 只能查看自己的运行记录和结果
- 无法访问其他用户的数据
- 无法使用本地路径模式

### 管理员权限

- 可以使用本地路径模式（如果启用且配置了 allowlist）
- 可以查看和管理所有用户的运行记录
- 可以创建、禁用、重置密码、提升/降级用户
- 可以使用 GitHub 仓库导入（如果配置了 OAuth）

### 本地路径模式

本地路径模式允许管理员直接使用服务器上的 Maven 项目路径，但受以下限制：

1. 必须设置 `deployment.allow_local_path_mode: true`
2. 必须配置 `deployment.local_path_allowlist`（非空数组）
3. 项目路径和缺陷报告路径必须解析到 allowlist 中的某个根目录下

示例配置：

```yaml
deployment:
  allow_local_path_mode: true
  local_path_allowlist:
    - "/srv/comet-l/projects"
    - "/srv/comet-l/bug-reports"
```

公开部署配置接口不会返回 `local_path_allowlist` 的具体路径，只返回是否已配置和数量，避免泄露服务器目录结构。

## 数据备份与恢复

### 备份内容

完整备份应包含以下文件和目录：

1. **SQLite 数据库：** `state/web/comet-web.sqlite3`
2. **用户状态目录：** `state/users/`
3. **用户输出目录：** `output/users/`
4. **用户沙箱目录：** `sandbox/users/`
5. **用户日志目录：** `logs/users/`

### 备份命令示例

```bash
# 创建备份目录
mkdir -p /backup/comet-l/$(date +%Y%m%d)

# 备份数据库和目录
cp state/web/comet-web.sqlite3 /backup/comet-l/$(date +%Y%m%d)/
tar czf /backup/comet-l/$(date +%Y%m%d)/users.tar.gz state/users/ output/users/ sandbox/users/ logs/users/
```

### 恢复注意事项

- **仅支持相同版本恢复：** 备份和恢复的 COMET-L 版本必须一致
- **不支持跨版本迁移：** 不同版本之间的数据库结构可能不兼容
- **不支持旧单用户版本迁移：** 无法将单用户版本的数据迁移到多用户版本
- **不支持导入旧运行记录：** 不要把旧 `state/runs/` 或旧 `output/runs/` 手工复制为多用户数据
- 恢复前停止服务，恢复后重新启动

### 恢复命令示例

```bash
# 停止服务
pkill -f "uvicorn comet.web.app"

# 恢复数据库和目录
cp /backup/comet-l/20260101/comet-web.sqlite3 state/web/
tar xzf /backup/comet-l/20260101/users.tar.gz

# 重新启动服务
uv run python -m uvicorn comet.web.app:app --host 0.0.0.0 --port 8000 --workers 1
```

## Docker 部署

### 多 JDK 镜像

COMET-L 提供内置多 JDK 的 Docker 镜像：

```bash
# 构建镜像
just docker-build

# 运行容器（生产模式）
mkdir -p .docker-data/{state,output,sandbox,logs}

docker run --rm -it \
  --name comet-l \
  -p 8000:8000 \
  -v "$PWD/.docker-data/state:/opt/comet-l/state" \
  -v "$PWD/.docker-data/output:/opt/comet-l/output" \
  -v "$PWD/.docker-data/sandbox:/opt/comet-l/sandbox" \
  -v "$PWD/.docker-data/logs:/opt/comet-l/logs" \
  -e COMET_WEB_DB_PATH=/opt/comet-l/state/web/comet-web.sqlite3 \
  comet-l:multi-jdk \
  uv run python -m uvicorn comet.web.app:app --host 0.0.0.0 --port 8000 --workers 1
```

### GitHub OAuth 配置（可选）

如需使用 GitHub 仓库导入和 PR 功能，配置以下环境变量：

```bash
-e COMET_GITHUB_OAUTH_CLIENT_ID='替换为你的 GitHub OAuth Client ID' \
-e COMET_GITHUB_OAUTH_CLIENT_SECRET='替换为你的 GitHub OAuth Client Secret' \
-e COMET_GITHUB_OAUTH_REDIRECT_URI='https://comet.example.internal/api/github/auth/callback' \
-e COMET_GITHUB_OAUTH_SCOPE='repo' \
-e COMET_GITHUB_ENCRYPTED_TOKEN_STORE_PATH='./state/github/auth/token.enc' \
-e COMET_GITHUB_ENCRYPTED_KEY_STORE_PATH='./state/github/auth/token.key' \
-e COMET_GITHUB_MANAGED_CLONE_ROOT='./sandbox/github-managed'
```

## 安全建议

### 网络安全

1. **使用 HTTPS：** 生产环境必须启用 HTTPS，并设置 `secure_auth_cookies: true`
2. **反向代理：** 建议使用 Nginx 或 Traefik 作为反向代理，处理 SSL 终止和静态资源缓存
3. **防火墙：** 限制对 8000 端口的直接访问，仅允许反向代理访问

### 主机安全

1. **文件权限：** 确保 `state/`、`output/`、`sandbox/`、`logs/` 目录的权限正确，防止未授权访问
2. **定期备份：** 设置定时任务备份 SQLite 数据库和用户目录
3. **日志审计：** 定期检查运行日志和审计日志

### 上传安全

1. **ZIP 文件验证：** 系统会对上传的 ZIP 文件进行安全检查，包括路径遍历防护、文件大小限制、压缩炸弹检测
2. **Maven 验证：** 项目上传阶段会验证包含单一 `pom.xml` 的 Maven 根目录；创建运行后会执行 Maven 构建、测试和相关项目代码
3. **沙箱隔离：** 每个用户的运行在自己的沙箱目录中进行，但这不是强安全沙箱，不能抵御恶意 Maven 项目

### 代码执行隔离

**当前限制：**
- 用户上传的代码会在服务器上编译和执行
- 没有容器级隔离或 cgroup 资源限制
- 没有网络隔离

**缓解措施：**
- 仅向可信用户开放访问
- 在隔离的虚拟机或容器中部署
- 监控资源使用情况
- 考虑使用 Docker 的 `--security-opt` 选项限制容器能力

## 运维检查清单

部署前请确认以下事项：

- [ ] 已创建初始管理员账户
- [ ] 已配置 `deployment.secure_auth_cookies: true`（生产环境）
- [ ] 已配置 `deployment.allowed_origins`（如需要）
- [ ] 已设置 HTTPS 反向代理
- [ ] 已配置防火墙规则
- [ ] 已设置数据备份计划
- [ ] 已确认使用 `--workers 1` 启动
- [ ] 已了解代码执行风险并告知用户
- [ ] 已配置适当的 `local_path_allowlist`（如启用本地路径模式）

## 故障排查

### 数据库锁定

SQLite 在并发写入时可能出现锁定。系统已配置：
- WAL 模式（Write-Ahead Logging）
- 5 秒 busy timeout

如仍遇到锁定问题，检查是否有其他进程访问数据库文件。

### 端口冲突

如果 8000 端口被占用，可以更换端口：

```bash
uv run python -m uvicorn comet.web.app:app --host 0.0.0.0 --port 8080 --workers 1
```

### 权限错误

确保运行用户对以下目录有读写权限：
- `state/`
- `output/`
- `sandbox/`
- `logs/`

## 不支持的功能

以下功能在当前版本中**明确不支持**：

- 多 worker、多进程或多节点部署
- Redis、Celery 或消息队列集成
- Kubernetes 原生部署（需自行配置单 Pod 单副本）
- 旧单用户版本数据迁移
- 跨版本数据库迁移
- 公共 SaaS 级别的多租户隔离（需要更强的沙箱机制）

## 自动验证关键词

以下关键词用于文档 guardrail 检查，保留英文原文：fresh deployment, workers 1, comet_session, state/web/comet-web.sqlite3, allow_local_path_mode, migration unsupported, Maven code execution warning.

## 版本兼容性

- 数据库结构版本与代码版本绑定
- 不支持降级恢复
- 升级时需要按照版本发布说明进行操作（如有）

## 获取帮助

遇到问题请通过以下方式获取帮助：

1. 查看项目 README.md 了解基本用法
2. 检查日志文件 `logs/` 目录
3. 提交 Issue 到项目仓库

---

**文档版本：** 与 COMET-L 多用户 MVP 版本对应
**最后更新：** 2026-05-05
