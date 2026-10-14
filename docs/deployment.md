# Railway 部署与恢复

## 部署结构

- 工作区：**byxsss's Projects**（`d143b5d6-2692-4b83-bff3-9e5c1aecfbd4`）。
- 项目：`ai-workspace`；环境：`production`。
- 代码来源：`byxsssn/ai-workspace` 的 `main` 分支。
- 应用与数据库区域：新加坡（`asia-southeast1-eqsg3a`）。
- `ai-workspace`：一个副本，资源上限为 1 vCPU、512 MB，关闭休眠，不挂载持久卷。
- `Postgres`：使用 Railway 的 `postgres` 模板和镜像
  `ghcr.io/railwayapp-templates/postgres-ssl:18`，一个副本，上限为 1 vCPU、512 MB。
  5 GB 持久卷挂载到 `/var/lib/postgresql/data`，模板中的 `PGDATA` 为
  `/var/lib/postgresql/data/pgdata`。
- 首次上线从空数据库开始，没有导入本地账号、消息或供应商密钥。
- `database-backup`：新加坡定时任务，上限为 1 vCPU、512 MB，无公网域名或持久卷，
  每次备份结束后退出。`backup-storage` 是新加坡私有存储桶，应用服务不持有桶凭据。

线上地址：[AI Workspace](https://ai-workspace-production-f76f.up.railway.app/)。

Dockerfile 使用 Node.js 24 构建前端，用 Python 3.14 和 uv 0.12.5 安装锁定的生产依赖，
并以非特权用户运行。本地配置、测试产物和凭据不会进入构建上下文。

生产 ASGI 工厂为 `ai_workspace.production:create_app`，提供首页 `/`、静态资源
`/assets/*`，并将后端挂载到 `/api`。未知路径返回 404，API 路径不会回退到网页。
`/readyz` 在 2 秒内检查 PostgreSQL，只返回安全状态；生产关闭调试、OpenAPI 和接口文档。
本地开发继续使用 `ai_workspace.main:app` 和 Vite 代理。

## 服务变量与密钥保管

在应用服务的 Variables 页面配置以下变量：

| 变量 | 值或用途 |
| --- | --- |
| `AI_WORKSPACE_ENVIRONMENT` | `production`。 |
| `AI_WORKSPACE_DEBUG` | `false`。 |
| `AI_WORKSPACE_DATABASE_URL` | `${{Postgres.DATABASE_URL}}`，通过私有网络引用数据库。 |
| `AI_WORKSPACE_JWT_SECRET_KEY` | 独立生成的随机值，至少 32 字符。 |
| `AI_WORKSPACE_CREDENTIAL_ENCRYPTION_KEY` | 单独用 `Fernet.generate_key()` 生成的加密主密钥。 |
| `AI_WORKSPACE_REGISTRATION_INVITE_CODE` | 独立生成的共享邀请码，至少 32 字符。 |
| `AI_WORKSPACE_JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | `30`。 |

`PORT` 由 Railway 提供。镜像默认前端目录为 `/app/frontend/dist`。
应用会将 `postgres://` 和 `postgresql://` 统一为 asyncpg 连接地址，保留凭据的正确编码；
Alembic 使用同一套配置和转换逻辑。

生产密钥应独立于开发环境生成，直接写入 Railway 变量，并保留限制访问权限的恢复副本。
本机恢复资料放在 Git 忽略的 `.local/` 下，长期副本请另存至密码管理器或受保护介质。
**即使数据库已经恢复，丢失 Fernet 主密钥仍会导致已保存的 OpenRouter 密钥无法解密。**
重新部署时保留原主密钥。今后如需轮换，必须先重新加密现有凭据，再停用旧主密钥。

用户在应用中填写自己的 OpenRouter 密钥，服务端没有共享的模型调用密钥。
不要将本地开发 `.env` 上传到 Railway，也不要将秘密写入源码或前端变量。

## 邀请码管理

注册接口接收 `{email, password, invite_code}`。生产环境中，缺少或错误的邀请码会在
密码哈希计算、创建用户之前返回 403。输入校验错误不回显提交的值。
邀请码不写入数据库、浏览器存储、日志或构建产物。
前端仅包含公开开关 `VITE_REGISTRATION_REQUIRES_INVITE=true`。

更换邀请码时，在应用变量中将 `AI_WORKSPACE_REGISTRATION_INVITE_CODE` 替换为新的
随机值，发布变量变更，并同步更新恢复副本。将新邀请码私下发给后续受邀用户即可。
已有账号、密码和登录状态不受影响。邀请码可重复使用，没有按用户设置额度；
首版适用于少量可信用户，目前不提供密码重置或邮箱验证流程。

## 发布与回退

1. 在 `codex/` 分支修改代码并创建 PR。PR 和推送到 `main` 都会触发 CI。
2. CI 检查 Python 代码与格式，在独立 PostgreSQL 18 上运行完整后端测试，
   并验证前端测试、构建、生产镜像和备份镜像。测试使用虚拟凭据，不访问生产库或真实模型。
3. 测试通过后合并相应提交。Railway 跟踪 `main`，开启 **Wait for CI（等待 CI）**。
   不要取消正在运行的 `main` 检查。连接服务前应确认 GitHub App 已获得该仓库及检查权限。
4. 发布前命令为 `alembic upgrade head`，超时 **300 秒**。
   迁移在构建完成后、流量切换前通过私有网络执行；不要在镜像构建期间执行迁移，
   也不要让每个 Web 工作进程分别执行迁移。
5. 使用 Dockerfile 的 CMD：一个 Uvicorn 工作进程，监听 `0.0.0.0:$PORT`。
   健康检查路径 `/readyz`，超时 **120 秒**；重启策略 **ON_FAILURE**，最多 **10 次**；
   应用下线等待时间为 **90 秒**，为进行中的聊天请求留出完成时间。
6. 先配置变量和服务设置，再连接代码来源。首次健康部署后生成 Railway HTTPS 域名，运行：

   ```text
   uv run python scripts/check_deployment.py https://<application-domain>
   ```

7. 核实当前提交、域名和运行日志。构建、迁移或就绪检查失败时，新版本不得替换上一成功版本。

部署设置通过 Railway 管理，并记录在此文档中。仓库没有引入旧的
`railway.json`／`railway.toml`；Railway 已将旧版 Config as Code 标记为弃用。
后续需要多环境声明式配置时，再评估其当前的 Infrastructure as Code 方案。

Railway 健康检查用于发布准入，不代表持续可用性监控。
小规模试用期间，可在控制台检查运行日志、重启事件、CPU、内存、数据库磁盘和费用。
日志中不要包含请求体、Authorization 头或明文凭据，也不要开启详细 SDK 日志。

应用故障时，在 Railway 的 Deployments 中选择上一成功版本回退，并核实该版本的变量和邀请码。
应用回退不会撤销数据库结构变更，**不要自动运行 `alembic downgrade`**。
采用兼容旧版应用的迁移；数据库数据恢复按下文单独处理。

## 费用控制

使用 **Hobby**：最低每月 5 美元，包含 5 美元资源额度。
在工作区 Usage 页将 **Compute Usage** 邮件提醒设置为 **15 美元**，硬上限设置为 **20 美元**。
这个上限作用于整个工作区，触顶后所有工作负载都会停止，备份任务也会受到影响。
模型费用、税费和其他服务费用另计。Railway Agent 用量单独管理，本应用无需 Railway Agent。

保存后应在页面上确认两个阈值。付款方式和平台要求的个人授权由账号所有者完成。

## 备份与恢复

2026-10-14，Railway 实际 API 拒绝在 Hobby 上创建原生手动／定时卷备份，要求升级 Pro。
按已确认的方案保留 Hobby，使用加密逻辑备份，**未启用原生卷快照**。

### 自动备份

`database-backup` 在 CI 通过后从 `main` 构建 `Dockerfile.backup`。
Railway 定时表达式为 `0 19 * * *`（UTC），对应北京时间次日 **03:00**。
任务使用 PostgreSQL 18 的 `pg_dump --format=custom --no-owner --no-acl` 导出并校验归档，
再用 AES-256-GCM 加密并上传到私有桶 `backup-storage`。

每日副本保留 **6 天**；每周一额外保留周副本，保留 **27 天**；首次运行同时建立两类副本。
如果长期停机导致副本过期，仍保留每类最后一份。清理仅匹配本任务命名空间和格式的文件，
并且只在本轮所需上传与元信息校验全部成功后执行。

任务限制内存使用，通过 `Postgres` 的变量引用获得 `PGHOST`、`PGPORT`、`PGDATABASE`、
`PGUSER` 和 `PGPASSWORD`。存储配置使用下表中的引用：

| 备份任务变量 | 引用 |
| --- | --- |
| `BACKUP_S3_ENDPOINT` | `${{backup-storage.ENDPOINT}}` |
| `BACKUP_S3_BUCKET` | `${{backup-storage.BUCKET}}` |
| `BACKUP_S3_REGION` | `${{backup-storage.REGION}}` |
| `BACKUP_S3_ACCESS_KEY_ID` | `${{backup-storage.ACCESS_KEY_ID}}` |
| `BACKUP_S3_SECRET_ACCESS_KEY` | `${{backup-storage.SECRET_ACCESS_KEY}}` |

新建 Railway 桶采用虚拟主机寻址方式。`BACKUP_ENCRYPTION_KEY` 为单独生成并经过
Base64 编码的 32 字节密钥。它与应用 Fernet 主密钥分别保管；
完整恢复用户凭据时，两者都需要。

检查最近一次执行日志是否包含 `status: backup_complete`，并确认桶内有 24 小时内的每日文件。
导出、上传或完整性检查失败时，任务以非零状态退出，记录 `backup_failed`，
不输出数据库凭据或 SDK 敏感信息。修复后手动运行任务，并核实结果。
工作区触及费用硬上限时，任务无法运行，因此每日恢复点是目标而非保证。
私有桶和短时任务会增加工作区资源用量，参见[存储桶价格](https://railway.com/pricing)。

### 立即备份

在已连接 Railway 项目的仓库目录运行：

```powershell
railway cron run --service database-backup --environment production --yes
railway cron list --service database-backup --environment production --limit 5
```

该命令将已部署任务镜像加入执行队列，记录可能需要几分钟才出现。
部署或重新部署定时服务仅更新镜像，不能据此判断备份已经执行。
进行有数据风险的结构变更前，确认执行已完成、日志包含 `backup_complete`，且桶中已有新文件。

同时保留一份可移植的 `pg_dump --format=custom --no-owner` 加密导出在 Railway 项目之外。
PostgreSQL 客户端主版本需与服务端一致。使用已授权 CLI 隧道，或在私有网络内执行导出；
不要为此开放数据库公网端口。凭据通过进程环境变量或限制访问的密码文件提供，
不要将其直接写入命令历史。

### 隔离恢复演练

将导出恢复到独立的空 PostgreSQL 18 数据库，检查精确行数、迁移版本、会话与消息关联，
并使用保存的 Fernet 主密钥解密专用测试凭据。记录恢复耗时和备份时间，演练不替换生产库。

通过私有桶的 Files 页面或已认证 S3 客户端下载加密文件，并对照备份日志中的 SHA-256。
在本机执行以下命令，将占位符替换为实际路径和隔离数据库信息：

```text
uv sync --locked --group backup
uv run --group backup python scripts/backup_database.py --decrypt <backup.dump.aesgcm> <verified.dump> --key-file <private-backup-key-file>
pg_restore --exit-on-error --no-owner --no-acl --host 127.0.0.1 --port <isolated-port> --username <restore-user> --dbname <new-empty-database> <verified.dump>
```

解密会拒绝被篡改的文件、错误密钥以及已存在的输出路径。
密钥通过文件提供，不要把密钥值直接放到命令行中。
解密后的归档包含用户数据，应保存在限制访问的目录；长期保留加密副本。

### 实际故障恢复

先保留当前数据，再恢复到新数据库，完成验证后切换应用的私有数据库引用。
保留原应用 Fernet 主密钥，验证成功前不要删除旧库。
Railway 项目或存储桶删除时，其备份也可能被删除，因此项目之外的加密副本不可省略。
本方案提供逻辑恢复，不提供时间点恢复或一键卷快照恢复。

## 上线验收

首次应用发布为 `ade1bb62ee0828339f8c263f36cc57fd3fe0747f`
（[PR #1](https://github.com/byxsssn/ai-workspace/pull/1)）。2026-10-14 完成以下验证：

- 生产 HTTP 检查通过，覆盖静态资源、就绪状态和 API 隔离。
- 两个账号通过邀请码拒绝、登录、会话隔离和凭据隔离验证。
  两轮真实 `openai/gpt-6-luna` 调用保存了四条消息，第二轮正确回忆第一轮标记；重新登录后历史仍在。
- 用户确认：大陆常用网络关闭代理／VPN 后，实际 HTTPS 域名可以打开。
  此结果对应当时的实际网络，不代表所有网络都能访问。
- 部署 `337adbdd-a620-48b2-99b9-ab28c654620e` 故意使用不存在的 Alembic 版本，
  在发布前失败，上一成功版本继续服务；随后恢复 `alembic upgrade head`。
- 生产导出经过认证解密后，在本机隔离端口 55432 的新 PostgreSQL 18 数据库中恢复。
  统一时区后逐行一致：2 个用户、3 个会话、4 条消息、2 条供应商凭据，迁移版本为 `47684641df93`。
  两条凭据都能用保管的 Fernet 主密钥解密，没有孤立消息。
  本机恢复与核验耗时 **3.15 秒**；另一份导出上传私有桶、重新下载、校验 SHA-256、
  认证解密并恢复后结果一致，恢复与核验耗时 **3.17 秒**。这两项为本次小数据量实测。
  加密导出和详细报告保存在受保护的本机恢复目录。
- 备份版本 `886defe53080c84b001250eedb97660bb62414ad` 的 CI 全部通过：
  244 项后端测试、9 项前端测试及构建、两个 Docker 镜像和独立加密备份恢复。
  部署后四条历史消息完全一致，两名用户仍可登录，密钥元信息仍为已配置，跨账号读取返回 404。
  旧邀请码返回 403，新邀请码通过校验。

首发收尾版本为 `2f13ebfa07cbab65209fc41faab33b2e7c50dacb`
（[PR #3](https://github.com/byxsssn/ai-workspace/pull/3)），
[CI 全部通过](https://github.com/byxsssn/ai-workspace/actions/runs/38557864527)。
2026-10-15 07:34（北京时间）再次通过生产 HTTP、已有账号、隔离和历史保留检查。
当日 03:02 的自动备份约 6 秒完成，日志确认 `backup_complete`，私有桶中已有新加密文件。
临时恢复数据库已清理，数据库隧道已关闭，临时 SSH 密钥已撤销。

后续发布时，按变更影响核对以下验收项目：

- 从受邀用户实际使用的大陆网络访问 HTTPS 域名。
- 使用邀请码注册并登录，通过页面填写 OpenRouter 密钥。
- 创建会话并发送两轮消息，刷新、退出重登后检查历史。
- 使用第二个账号验证会话和凭据隔离。
- 重新部署应用，验证历史和已保存凭据仍可使用。
- 核实备份计划、实际生成的文件，以及隔离恢复结果。

可选真实联调脚本会产生**两次计费模型调用**。独立配置文件中填写
`AI_WORKSPACE_TEST_EMAIL`、`AI_WORKSPACE_TEST_PASSWORD` 和 `AI_WORKSPACE_TEST_MODEL`。
优先通过页面配置密钥，脚本会复用已有凭据；仅在文件明确提供测试密钥且账号尚未配置时保存它。

```text
uv run python scripts/smoke_chat.py --base-url https://<application-domain> --env-file .local/production-smoke.env
```

远程验证拒绝使用默认开发 `.env`，也拒绝非 HTTPS、URL 内嵌凭据和重定向。
脚本保留名称明确的测试会话，只输出安全状态和会话 ID。

## 官方参考

- [套餐与价格](https://railway.com/pricing)
- [费用控制](https://docs.railway.com/pricing/cost-control)
- [GitHub 检查与自动部署](https://docs.railway.com/deployments/github-autodeploys)
- [发布前迁移命令](https://docs.railway.com/deployments/pre-deploy-command)
- [持久卷备份](https://docs.railway.com/volumes/backups)
- [定时任务与手动执行](https://docs.railway.com/cron-jobs)
