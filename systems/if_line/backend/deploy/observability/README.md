# if_line 本地观测栈

这个目录用于本地和测试环境启动观测服务。

## 服务分工

- Phoenix: 只看 Agent 对话 trace。
- OTel Collector: 全部 OTel trace / log 的统一接收入口，并负责分发。
- Tempo: 存普通后端 / worker trace。
- VictoriaLogs: 存普通后端 / worker log。
- Grafana: 查看 Tempo / VictoriaLogs，并通过 trace_id 做日志和 trace 关联。

应用侧不要绕过 Collector 直连 Phoenix 或 Tempo。后端只配置一个 OTel 出口，
Collector 根据 `service.name` 分发: `if-line-agent` 进 Phoenix，普通后端 /
worker trace 进 Tempo，日志进 VictoriaLogs。

## 启动

如果你已经手动启动过旧容器 `ifline-phoenix-dev`，先停掉，避免 6006 端口冲突:

```bash
docker rm -f ifline-phoenix-dev
```

启动整套观测服务:

```bash
docker compose -f backend/deploy/observability/docker-compose.yml up -d
```

首次启动前需要准备 `backend/deploy/observability/.env`:

```bash
PHOENIX_ENABLE_AUTH=true
PHOENIX_SECRET=<至少32位随机字符串>
PHOENIX_ADMIN_SECRET=<至少32位随机字符串>
PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD=<Phoenix 管理员初始密码>
PHOENIX_DISABLE_AGENT_ASSISTANT=true
PHOENIX_AGENTS_DISABLE_BASH=true
PHOENIX_AGENTS_DISABLE_WEB_ACCESS=true
PHOENIX_ENABLE_MCP_SERVER=false
PHOENIX_ENABLE_MCP_CODE_MODE=false
PHOENIX_ALLOWED_SANDBOX_PROVIDERS=NONE
PHOENIX_ALLOWED_PROVIDERS=NONE
PHOENIX_ENABLE_OAUTH2_AUTHORIZATION_SERVER=false
GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=<Grafana 管理员密码>
```

Phoenix 开启认证后，Collector 写入 Phoenix 会使用 `PHOENIX_ADMIN_SECRET`
作为 Bearer token。这个文件不要提交到仓库。
默认关闭 Phoenix 自带 assistant、server-side bash、联网访问、MCP、代码
执行 sandbox、模型 provider 和 OAuth2 授权服务。当前 Phoenix 只作为 Agent trace
看板使用。

访问地址:

- Phoenix: http://127.0.0.1:6006
- Grafana: http://127.0.0.1:3001
- Tempo HTTP: http://127.0.0.1:3200
- VictoriaLogs HTTP: http://127.0.0.1:9428
- OTel Collector HTTP: http://127.0.0.1:14318
- OTel Collector gRPC: 127.0.0.1:14317

Grafana 默认账号密码:

```text
见 .env 中的 GRAFANA_ADMIN_USER / GRAFANA_ADMIN_PASSWORD
```

VictoriaLogs 的 Grafana 数据源需要 `victoriametrics-logs-datasource` 插件。
compose 默认通过 `GF_INSTALL_PLUGINS` 安装该插件。如果运行环境不能访问
Grafana 插件下载地址，可以在 `.env` 中设置:

```text
GRAFANA_INSTALL_PLUGINS=
```

然后改用离线插件目录或自定义 Grafana 镜像。

日志和 trace 互跳依赖两个约定:

```text
trace -> logs: Tempo 使用 trace_id 精确查询 VictoriaLogs
logs -> trace: VictoriaLogs 从结构化字段 trace_id 跳转到 Tempo
```

VictoriaLogs 中的日志正文只展示 `_msg`。`severity_text`、`code.filepath`、
`code.function`、`code.lineno` 等 OTel 字段会作为结构化字段保留，展开单条
日志或切到 Table 视图可以查看。compose 里已通过 `logLevelRules` 把
`severity_text` 映射到 Grafana 的 error/warning/info/debug 等等级。

## 后端环境变量

统一 trace / log 出口:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:14318
OTEL_SERVICE_NAME=if-line-backend
AGENT_TRACE_SERVICE_NAME=if-line-agent
```

Collector 内部链路:

```text
后端 / worker / Agent -> Collector
Collector -> Phoenix（service.name=if-line-agent）
Collector -> Tempo（其他 trace）
Collector -> VictoriaLogs（log）
```

## 镜像源

默认使用毫秒镜像前缀:

```text
docker.1ms.run/
```

需要切换镜像源时:

```bash
OBS_IMAGE_PREFIX= docker compose -f backend/deploy/observability/docker-compose.yml up -d
```
