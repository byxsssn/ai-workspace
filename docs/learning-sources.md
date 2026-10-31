# 第一阶段：读取学习资料

当前可读取三个 FastAPI 官方章节，或取得明确标记为 demo 的虚构学习记录。配置独立的
Notion 内部连接后，可以读取绑定的 FastAPI 学习页面属性和正文。全部接口只读。

2026-10-30 验证：三篇官网真实读取成功；Notion 已验证模拟响应、字段映射与失败处理，
尚未使用真实 Notion 凭据联调。当前聊天的 Notion 插件授权不能直接供本应用使用。

## 先运行官网与演示数据检查

在项目根目录运行：

```powershell
uv sync --locked
uv run python scripts/check_learning_sources.py --source demo --all-sections
```

命令不调用模型、不连接应用数据库、不写 Notion；会访问 FastAPI 官网。
输出包含来源、笔记完整性、各章节正文字符数、是否有代码块、获取时间和内容 SHA-256。
demo 只指学习记录来自虚构样例，官方文档仍然是真实网络读取。
它不打印 Notion 密钥或私人笔记全文。

三个章节 ID：

| ID | 官方章节 |
| --- | --- |
| testing | https://fastapi.tiangolo.com/tutorial/testing/ |
| async-tests | https://fastapi.tiangolo.com/advanced/async-tests/ |
| testing-dependencies | https://fastapi.tiangolo.com/advanced/testing-dependencies/ |

也可以只检查一章：

```powershell
uv run python scripts/check_learning_sources.py --section async-tests
```

## 配置自己的 Notion 连接

1. 按 [Notion 官方授权步骤](https://developers.notion.com/guides/get-started/authorization)
   创建属于自己工作区的内部连接。当前只需要读取内容权限。
2. 在 Notion 的“技术学习进度”数据库／目标学习页面中添加这个连接，使其能读取 FastAPI
   对应的那条记录。接口不会因为拿到页面链接就自动获得访问权限。
3. 从 FastAPI 记录的页面 URL 取得页面 ID（32 位十六进制或带连字符的 UUID）。这里用
   学习记录页面 ID，不用数据库 ID，也不把整个 URL 填入配置。
4. 登录自己的 AI Workspace，通过 GET /users/me 取得应用用户 id。
   这是连接所有者，不是 Notion 用户 ID。其他应用账号无法使用这份连接。
5. 将下面三项加入本地根目录 .env。占位说明要替换为真实值；三项一起配置，未使用时全部省略。

```dotenv
AI_WORKSPACE_LEARNING_NOTION_API_TOKEN=<内部连接密钥>
AI_WORKSPACE_LEARNING_NOTION_OWNER_USER_ID=<AI Workspace 用户 UUID>
AI_WORKSPACE_LEARNING_NOTION_FASTAPI_PAGE_ID=<FastAPI 学习页面 UUID>
```

密钥不要发到聊天、写进源码或放进前端 VITE_* 环境变量。.env 已在版本控制忽略范围。
已有服务需要重启才能重新加载连接配置。

然后在本机运行：

```powershell
uv run python scripts/check_learning_sources.py --source notion --section async-tests
```

成功时 source 为 notion，能查看笔记字符数与完整性。命令由本机操作者执行；Web 接口另行
验证 JWT 身份和连接归属。未配置时返回 notion_not_configured，不会自动退回演示数据。

若页面字段经过改名，统一调整 learning/notion.py 中的 NOTION_FIELD_MAP。首版预期字段为：

| Notion 字段 | 属性类型 | 应用字段 |
| --- | --- | --- |
| 技术 / 主题 | title | topic_name |
| 学习状态 | select | learning_status |
| 当前进度 | rich_text | current_progress |
| 下一步 | rich_text | next_step |
| 掌握程度 | select | proficiency |
| 项目实践 | checkbox | project_practice |
| 官方文档 | url | official_docs_url |
| 成果链接 | url | evidence_url |

缺失的可选字段返回 null 和明确 warning；结构／类型不匹配返回 source_schema_mismatch。
读到的掌握程度是已有记录，本阶段不推断、不修改它。source_updated_at 表示 Notion 页面编辑时间，
fetched_at 表示本次读取时间，两者都不代表能力验证日期。

## 后端接口

所有接口使用现有 Bearer JWT 登录；本地开发可在 /docs 登录授权后调用。
生产入口和现有前端代理需要添加 /api 前缀。

| GET 路径 | 结果 |
| --- | --- |
| /learning/fastapi/sections | 三个受控章节 ID、标题与官方 URL |
| /learning/fastapi/sections/async-tests | 官方正文 Markdown、来源、获取时间、内容哈希 |
| /learning/fastapi/state?source=demo | 虚构学习记录，不访问 Notion |
| /learning/fastapi/state?source=notion | 当前登录用户有权使用的 Notion 学习记录 |

state 默认 source=demo，并在响应中明确标记来源。真实数据必须显式选择 source=notion。
接口不接受调用者提供 Notion 页面 ID、连接所有者或任意网页 URL。

当前没有新增前端学习页面，因此在已有聊天框中输入学习问题还不会触发这些读取器。
这些接口与读取器已经被第二阶段的 [学习 Agent 后端](learning-agent.md) 复用；学习页面尚未接入。

## 数据流与技术导读

```text
GET 请求 → JWT 鉴权 → 结束鉴权读取事务 → LearningService
    ├─ 官方章节 ID → HTTP 请求 → HTML 正文 → Markdown + 来源
    ├─ source=demo → 固定虚构记录
    └─ source=notion → 校验连接所有者 → 页面属性 + 正文 → LearningState
```

建议按这个顺序看代码：

1. src/ai_workspace/learning/models.py：Pydantic 定义统一输出。
   字段类型帮助发现外部数据不符合约定的问题；null 表示未知，不凭空补值。
2. src/ai_workspace/learning/documents.py：先用固定目录确定 URL，再用 BeautifulSoup 选择文章，
   去掉导航和按钮，用 markdownify 保留标题及代码。哈希对应提取后的 Markdown，不证明版本或真实性。
3. src/ai_workspace/learning/notion.py：用两个只读 API 获取属性和 Markdown，集中转换 Notion 字段。
   逐请求传 Authorization，避免把 Notion 密钥带到官网请求。正文截断或存在未知块时 notes_complete=false。
4. src/ai_workspace/learning/http.py：httpx2.AsyncClient 配合 await 等待网络，设置时间与大小限制。
   只允许预定读取地址，不跟随重定向，不向接口透传上游错误正文。
5. src/ai_workspace/learning/service.py：业务边界，决定使用哪种来源，并验证连接属于谁。
6. src/ai_workspace/api/routes/learning.py：FastAPI Depends 组装鉴权、服务和请求资源。
   仅读取身份的数据库事务先结束，外部网络等待期间不持有这次事务。

目前每次请求按需读取，没有后台同步或持久化文档缓存；返回版本为空表示尚未可靠识别，
不能把“今天获取”当作“与项目安装版本一致”。本阶段也没有引入 LangChain 或 MCP，
后续的 Agent 会调用这里已经验证过的读取能力。

## 错误与验证

错误响应包含固定 code 和 message，便于页面给出可理解的提示：

- notion_not_configured：连接尚未配置（503）。
- notion_not_available：登录账号不是该连接所有者（404）。
- source_unauthorized：Notion 凭据失效；这是上游错误，不表示应用 JWT 失效（502）。
- source_forbidden / source_not_found：核对内容权限和页面是否共享给连接（502）。
- source_rate_limited：上游限流（503）；有效秒数通过 Retry-After 返回，不自动重复请求。
- source_timeout：读取超时（504）。
- source_schema_mismatch / invalid_source_response：字段、正文或完整性信息无法可靠解析（502）。
- source_redirect / source_too_large：地址需要重新核验或内容超过读取限额（502）。

使用 MockTransport 将网络响应替换成可控样例，验证成功、权限失败、截断、重定向、限流和凭据隔离。
这能稳定测试错误分支，但不能代替真实 Notion 授权联调。

```powershell
uv run pytest -q tests/test_learning_documents.py tests/test_learning_notion.py tests/test_learning_http.py tests/test_learning_service.py tests/test_learning_api.py
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
```

首次完成真实授权后，用只读检查命令确认 source=notion，再查看应用接口返回的进度、正文和 warnings。
这一阶段不修改学习状态，不需要模型 API 费用。
