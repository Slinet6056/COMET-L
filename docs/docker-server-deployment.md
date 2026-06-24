# 使用 Docker 在服务器上部署 COMET-L

这份文档面向想把 COMET-L Web 控制台部署到服务器上的使用者。你不需要在服务器上安装 Python、Node、Maven 或多个 JDK；Docker 镜像已经包含运行 Web 控制台和分析 Maven 项目所需的环境。

镜像名称：`slinet6056/comet-l:latest`

部署完成后，用户可以在浏览器中登录 Web 控制台，上传 Maven 项目 ZIP，选择目标 Java 版本，填写或上传运行配置，然后启动测试生成与变异分析任务。

## 适用场景

当前 Docker 部署适合小型团队、课程实验、内部服务器或半可信用户环境。COMET-L 会编译并运行用户上传的 Maven 项目，所以不要把当前版本直接开放给不可信的公网用户。

如果你需要了解更细的多用户限制、备份恢复和安全边界，见 [多用户 Web 控制台生产部署指南](production-multi-user-deployment.md)。日常部署按本文档操作即可。

## 服务器准备

服务器需要安装 Docker，并能访问 Docker Hub。建议准备：

- 2 核以上 CPU，4 GB 以上内存；并发任务越多，内存和 CPU 需要越高。
- 足够的磁盘空间，用于保存上传项目、运行日志和报告。
- 一个固定访问地址。下文用 `https://your-comet-domain.example` 作为占位符；部署时请替换成你自己的域名或服务器地址。
- 生产环境建议放在 HTTPS 反向代理后面，例如 Nginx、Caddy 或 Traefik。

先在服务器上创建一个部署目录：

```bash
mkdir -p /srv/comet-l
cd /srv/comet-l
mkdir -p data/{state,output,sandbox,logs}
```

这些目录会挂载进容器。以后升级镜像或重启容器时，用户、任务记录、输出报告和日志仍会保留。

## 准备服务器配置

在 `/srv/comet-l/config.yaml` 中写入服务器配置。下面是一份可以直接改的模板：

```yaml
deployment:
  max_budget: 10000
  max_run_timeout_seconds: 7200
  max_iterations: 100

  allowed_java_versions:
    - '8'
    - '11'
    - '17'
    - '21'
    - '25'

  # 如果只是用 http://服务器IP:8000 临时试用，可以先设为 false。
  # 如果通过 HTTPS 域名访问，生产环境请设置为 true。
  secure_auth_cookies: true
  allowed_origins:
    - 'https://your-comet-domain.example'

  global_max_running_tasks: 2
  per_user_max_running_tasks: 1
  global_max_pending_tasks: 50
  per_user_max_pending_tasks: 5

  # 普通用户使用 ZIP 上传项目即可。本地路径模式只建议管理员在内网使用。
  allow_local_path_mode: false
  local_path_allowlist: []

  upload_retention_hours: 24
  run_artifact_retention_days: 30

github:
  # 只有需要 GitHub 仓库导入、自动 push 或创建 PR 时才需要填写。
  oauth_client_id: ''
  oauth_client_secret: ''
  oauth_redirect_uri: 'https://your-comet-domain.example/api/github/auth/callback'
  oauth_scope: 'repo'
  encrypted_token_store_path: './state/github/auth/token.enc'
  encrypted_key_store_path: './state/github/auth/token.key'
  managed_clone_root: './sandbox/github-managed'
```

`https://your-comet-domain.example` 只是占位符，不能照抄。需要把上面的 `allowed_origins` 改成浏览器实际访问地址。临时 HTTP 试用时可以这样写：

```yaml
deployment:
  secure_auth_cookies: false
  allowed_origins:
    - 'http://你的服务器IP:8000'
```

正式部署到域名和 HTTPS 后，再改回 `secure_auth_cookies: true`，并把 `allowed_origins` 改成 HTTPS 地址。`allowed_origins` 必须和浏览器地址栏里的协议、域名、端口一致，否则登录后可能无法提交运行。

## 准备示例项目运行配置

如果想让首页的示例项目可用，可以再创建 `/srv/comet-l/example.config.yaml`。这份配置给单次运行使用，主要填写 LLM 和 embedding 服务：

```yaml
llm:
  base_url: 'https://api.openai.com/v1'
  api_key: 'your-api-key-here'
  model: 'gpt-4'
  temperature: 0.3
  max_tokens: 131072
  supports_json_mode: true # 如果使用本地模型不支持 JSON 模式，请设置为 false
  timeout: 600 # API 请求超时时间（秒）
  reasoning_effort: null # 推理努力程度，可能的可选值: 'none', 'minimal', 'low', 'medium', 'high'，null 表示使用默认值
  reasoning_enabled: false # 是否启用推理，null 表示不配置，true/false 表示显式开关
  verbosity: null # 响应详细程度，可能的可选值: 'low', 'medium', 'high'，null 表示使用默认值

execution:
  timeout: 600 # 秒
  test_timeout: 60 # 测试执行超时时间（秒），默认30秒快速失败
  coverage_timeout: 600 # 覆盖率收集超时时间（秒），JaCoCo需要更多时间
  max_retries: 3
  maven_home: null # 留空使用系统默认

evolution:
  mutation_enabled: true
  max_iterations: 1000
  min_improvement_threshold: 0.01 # 绝对增量阈值，0.01 表示提升 1 个百分点
  budget_llm_calls: 10000
  stop_on_no_improvement_rounds: 3
  # 优秀水平阈值（达到后可提前停止，可根据项目调整）
  excellent_mutation_score: 0 # 变异分数阈值（默认 95%）
  excellent_line_coverage: 0.95 # 行覆盖率阈值（默认 90%）
  excellent_branch_coverage: 0.95 # 分支覆盖率阈值（默认 85%）
  min_method_lines: 3 # 目标方法的最小行数，小于此值的方法将被跳过

knowledge:
  enabled: true # 是否启用 RAG 知识库
  enable_dynamic_update: true
  pattern_confidence_threshold: 0.5
  contract_extraction_enabled: true
  # Embedding 配置（用于 RAG 检索）
  embedding:
    base_url: 'https://api.openai.com/v1'
    api_key: null # 留空则使用 llm.api_key
    model: 'text-embedding-3-small' # Embedding 模型名称
    batch_size: 100 # 批量 embedding 的大小
  # 检索配置
  retrieval:
    top_k: 5 # 每次检索返回的文档数
    score_threshold: 0.5 # 相似度阈值（0-1，越高越严格）

logging:
  level: 'INFO'
  file: 'comet.log'

preprocessing:
  enabled: true # 是否启用并行预处理
  exit_after_preprocessing: false # 预处理完成后导出测试并退出，不进入主循环
  max_workers: 4 # 最大并发数，null表示自动（cpu_count）
  timeout_per_method: 600 # 单个方法的超时时间（秒）

formatting:
  enabled: false # 是否启用代码格式化（使用 google-java-format）
  style: 'GOOGLE' # 格式化风格: GOOGLE 或 AOSP

agent:
  parallel:
    enabled: true # 是否启用并行 Agent 模式（批量并行处理多个目标）
    max_parallel_targets: 4 # 最大并行目标数（同时处理的方法数）
    max_eval_workers: 4 # 变异体评估并行度（每个目标内的评估并行度）
    timeout_per_target: 600 # 单个目标的超时时间（秒）
```

普通用户在 Web 控制台里也可以上传自己的运行配置。服务器配置和运行配置是两类文件：`config.yaml` 管服务器策略、登录和 GitHub OAuth；`example.config.yaml` 或用户上传的配置管某一次 COMET-L 运行使用哪个模型、预算和超时。

## 启动容器

先拉取镜像：

```bash
docker pull slinet6056/comet-l:latest
```

启动 Web 控制台：

```bash
docker run -d \
  --name comet-l \
  --restart unless-stopped \
  -p 8000:8000 \
  -v /srv/comet-l/data/state:/opt/comet-l/state \
  -v /srv/comet-l/data/output:/opt/comet-l/output \
  -v /srv/comet-l/data/sandbox:/opt/comet-l/sandbox \
  -v /srv/comet-l/data/logs:/opt/comet-l/logs \
  -v /srv/comet-l/config.yaml:/opt/comet-l/config.yaml:ro \
  -v /srv/comet-l/example.config.yaml:/opt/comet-l/example.config.yaml:ro \
  slinet6056/comet-l:latest
```

如果暂时不需要示例项目，可以删掉 `example.config.yaml` 那一行挂载。容器启动后访问：

```text
http://服务器IP:8000/
```

如果你配置了 HTTPS 反向代理，就访问自己的域名。

查看日志：

```bash
docker logs -f comet-l
```

停止服务：

```bash
docker stop comet-l
```

## 创建管理员账户

容器启动后，执行一次管理员创建命令：

```bash
docker exec -it comet-l \
  uv run python -m comet.web.admin create-admin \
  --username admin \
  --password "请换成一个强密码"
```

之后用这个账号登录 Web 控制台。管理员可以查看其他用户的运行记录，也可以在开启本地路径模式后使用服务器上的项目目录。普通用户默认使用 ZIP 上传项目。

常用用户管理命令：

```bash
# 创建普通用户
docker exec -it comet-l uv run python -m comet.web.admin create-user \
  --username alice \
  --password "user-password" \
  --role user

# 列出用户
docker exec -it comet-l uv run python -m comet.web.admin list-users

# 重置密码
docker exec -it comet-l uv run python -m comet.web.admin reset-password \
  --user-id 2 \
  --password "new-password"
```

## 让用户开始使用

用户登录后，通常按下面的方式创建运行：

1. 准备一个 Maven 项目 ZIP。项目根目录或解压后的单一目录中需要有 `pom.xml`。
2. 在 Web 控制台上传项目 ZIP。
3. 可选：上传缺陷报告 ZIP，支持 `.md`、`.txt`、`.diff` 和 `.patch`。
4. 选择目标 Java 版本。Docker 镜像内置 Java 8、11、17、21、25。
5. 填写或上传运行配置，至少需要可用的 `llm` 配置。
6. 启动运行，等待结果页生成报告和日志。

运行结束后，结果页会提供报告、日志和产物下载入口。管理员可以看到所有用户的运行；普通用户只能看到自己的运行。

## GitHub 功能

如果需要从 GitHub 导入仓库、自动 push 或创建 PR，需要先创建 GitHub OAuth App，并把回调地址设置为：

```text
https://your-comet-domain.example/api/github/auth/callback
```

这里的地址必须换成浏览器访问 COMET-L 的实际地址。GitHub OAuth App 的回调地址、`deployment.allowed_origins` 和下面配置里的 `oauth_redirect_uri` 要保持一致。

然后在 `/srv/comet-l/config.yaml` 中填写：

```yaml
github:
  oauth_client_id: '你的 GitHub OAuth Client ID'
  oauth_client_secret: '你的 GitHub OAuth Client Secret'
  oauth_redirect_uri: 'https://your-comet-domain.example/api/github/auth/callback'
  oauth_scope: 'repo'
  encrypted_token_store_path: './state/github/auth/token.enc'
  encrypted_key_store_path: './state/github/auth/token.key'
  managed_clone_root: './sandbox/github-managed'
```

修改后重启容器：

```bash
docker restart comet-l
```

不使用 GitHub 功能时，可以留空这些字段，ZIP 上传仍然可用。

## 管理员本地路径模式

默认情况下，用户不需要把 Maven 项目目录挂载到容器里，上传 ZIP 就可以运行。

如果管理员确实要直接分析服务器上的项目目录，可以这样做：

1. 把项目目录挂载进容器。
2. 在 `config.yaml` 开启 `allow_local_path_mode`。
3. 配置允许访问的路径白名单。

示例：

```yaml
deployment:
  allow_local_path_mode: true
  local_path_allowlist:
    - '/workspace/projects'
```

启动容器时增加挂载：

```bash
docker run -d \
  --name comet-l \
  --restart unless-stopped \
  -p 8000:8000 \
  -v /srv/comet-l/data/state:/opt/comet-l/state \
  -v /srv/comet-l/data/output:/opt/comet-l/output \
  -v /srv/comet-l/data/sandbox:/opt/comet-l/sandbox \
  -v /srv/comet-l/data/logs:/opt/comet-l/logs \
  -v /srv/comet-l/config.yaml:/opt/comet-l/config.yaml:ro \
  -v /srv/projects:/workspace/projects \
  slinet6056/comet-l:latest
```

管理员在 Web 页面选择本地路径时，填写 `/workspace/projects/你的项目`。普通用户仍建议使用 ZIP 上传。

## 升级镜像

升级前先确认没有正在运行的任务。然后执行：

```bash
docker pull slinet6056/comet-l:latest
docker stop comet-l
docker rm comet-l
```

再使用前面的 `docker run` 命令重新启动。只要继续挂载同一组 `/srv/comet-l/data/*` 目录，已有用户、运行记录和产物会保留。

## 备份

至少备份以下内容：

```text
/srv/comet-l/config.yaml
/srv/comet-l/example.config.yaml
/srv/comet-l/data/state
/srv/comet-l/data/output
/srv/comet-l/data/sandbox
/srv/comet-l/data/logs
```

最小备份命令示例：

```bash
mkdir -p /backup/comet-l
tar czf /backup/comet-l/comet-l-$(date +%Y%m%d).tar.gz \
  /srv/comet-l/config.yaml \
  /srv/comet-l/example.config.yaml \
  /srv/comet-l/data
```

恢复时先停止容器，再把这些文件放回原路径后启动容器。

## 常见问题

### 登录后提交运行失败

优先检查 `deployment.allowed_origins`。它必须包含浏览器实际访问的地址，包括协议和端口。HTTPS 部署还需要 `secure_auth_cookies: true`。

### 上传项目后提示不是 Maven 项目

ZIP 里需要有一个清晰的 Maven 项目根目录，并包含 `pom.xml`。不要把多个项目根目录平铺在同一个 ZIP 顶层。

### 容器没有权限写入目录

确认 `/srv/comet-l/data` 对容器运行用户可写。简单处理方式：

```bash
sudo chown -R 1000:1000 /srv/comet-l/data
```

### 任务一直排队

检查是否已有任务占满并发。默认全局同时运行 2 个任务，每个用户同时运行 1 个任务。可以在 `config.yaml` 中调整 `global_max_running_tasks` 和 `per_user_max_running_tasks`，然后重启容器。

### 8000 端口被占用

把宿主机端口换掉即可，例如：

```bash
docker run -d \
  --name comet-l \
  --restart unless-stopped \
  -p 8080:8000 \
  -v /srv/comet-l/data/state:/opt/comet-l/state \
  -v /srv/comet-l/data/output:/opt/comet-l/output \
  -v /srv/comet-l/data/sandbox:/opt/comet-l/sandbox \
  -v /srv/comet-l/data/logs:/opt/comet-l/logs \
  -v /srv/comet-l/config.yaml:/opt/comet-l/config.yaml:ro \
  slinet6056/comet-l:latest
```

之后访问 `http://服务器IP:8080/`。
