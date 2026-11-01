# 学习地图与 FastAPI 续学

学习地图是 AI Workspace 的第一个功能页面。它展示自己的技术记录，Agent 则负责选定
场景中的讲解和答疑。地图与助手共用数据读取能力，继续使用原有登录、凭据、会话和消息表。
普通和学习对话通过同一个 `AgentRunner` 执行，学习地图仍是技术工作台的一个模块。

## 体验流程

1. 按 README 配置本地环境，执行 `uv run alembic upgrade head`，启动后端和前端。
2. 登录后打开“学习地图”。默认读取 Notion；连接未配置或读取失败时明确显示错误，
   不自动替换为虚构进度。可手动选择演示数据体验界面。
3. 按类型、状态筛选卡片，查看进度、下一步、实践情况和来源。
4. 点击记录查看笔记；列表只读取摘要属性，详情才读取正文。
5. 从 FastAPI 的“继续学习”进入助手。优先续接同一来源的 FastAPI 会话；没有时新建。
   页面不会自动发送问题或产生模型调用。确认问题、模型和密钥后点击发送。
6. 重新加载会话时，学习主题和数据来源来自数据库，不依赖浏览器猜测。

地图可以展示多门技术。当前文档教学只支持 FastAPI 的 Testing、Async Tests、
Testing Dependencies 三章；其他卡片提供记录详情及原文链接，不冒充已经接通对应教学工具。
学习状态和掌握程度沿用原记录，不推算完成百分比。

## Notion 配置

先按 [资料读取配置](learning-sources.md#配置自己的-notion-连接) 设置内部连接、应用所有者和
FastAPI 记录页面 ID。连接需要读取“技术学习进度”整个数据库的权限。

地图额外使用一个配置：

```dotenv
AI_WORKSPACE_LEARNING_NOTION_LEARNING_DATA_SOURCE_ID=<学习库的数据源 UUID>
```

数据库可以包含数据源；此处使用 `data_source_id`，不能用数据库页面 ID 代替。
当前 API 的 FastAPI 记录响应中 `parent.data_source_id` 可用于确认所在数据源。
这里只把 ID 写入本地 `.env` 或部署环境；不把私人配置写入源码。

只配置原来的三项时，单记录学习接口仍可使用；地图会提示数据源未配置。
真实记录只对配置的应用用户开放，其他账号不会通过这个内部连接读取私人学习库。

## API 与数据边界

所有接口要求 Bearer JWT；Vite 代理与生产入口需加 `/api` 前缀。

| 接口 | 用途 |
| --- | --- |
| `GET /learning/records?source=notion` | 学习记录摘要列表，包含来源、读取时间和是否完整 |
| `GET /learning/records/{id}?source=notion` | 核验记录属于配置的数据源后读取正文 |
| `POST /conversations` | 创建普通或带固定学习上下文的会话 |
| `POST /conversations/{id}/chat` | 同一个发送入口，根据服务端保存的上下文执行 |

创建学习会话：

```json
{
  "title": "FastAPI 学习",
  "learning_topic": "fastapi",
  "learning_source": "notion"
}
```

普通会话省略两个学习字段。主题和来源成对保存，不提供修改入口；演示和真实记录分开续接。
旧的 `/learning/fastapi/conversations/{id}/chat` 仅保留 HTTP 兼容入口，
复用同一个 `ChatService`、`AgentRunner` 和会话保存流程。
兼容入口首次成功时可绑定学习上下文；不允许将已绑定的来源换成另一种来源。

Notion 适配器将外部属性转换为项目自己的记录格式。分页查询有时间、条数与响应大小上限；
`has_more` 和 warnings 表示结果是否完整。没有拿到全部结果时，界面不能声称全库只有这些记录。
记录 ID、页面归属和返回对象均需校验，不能借详情接口读取连接可访问的任意其他页面。

普通与学习聊天共用“鉴权与历史快照 → 结束读取事务 → 生成 → 行锁核对 → 保存”流程。
生成期间历史或学习上下文改变时拒绝提交旧答案。生成阶段共用 LangChain `create_agent`
与 OpenRouter 模型工厂；普通模式可查询公共 PyPI 榜单，学习模式由 `LearningPolicy` 提供资料工具、
提示词及来源校验。模式分别设置预算，普通聊天不套用学习模式的 2,000 输出 Token 限制。
普通聊天仍可指定推理强度，学习模式仍只使用默认推理设置。详见
[统一 Agent 运行链路](agent-runtime.md)。

正文中的来源随消息保存。本轮的结构化来源卡片、工具执行摘要和用量仍只在响应中返回，
未新增完整运行轨迹存储。地图当前按需读取，尚未加入后台同步或 Notion 写回。
公共 PyPI 榜单有独立页面与数据服务；地图尚未把下载指标关联到每条个人学习记录。

## 验证与学习顺序

历史基线（共享 Agent 运行链路重构前）：2026-11-01 后端 545 项测试、前端 25 项测试通过，
TypeScript/Vite 构建通过。以下读取与联调结果也属于该次验证，不代表本轮重构后的验证结果。
真实 Notion 地图读取 26 条记录且无截断；FastAPI 详情完整。统一聊天接口完成一次真实
Notion 学习调用，两种读取工具成功、来源通过核验、两条消息和学习上下文持久化成功。
浏览器验证了未授权账号隔离、显式演示切换、筛选、详情与续学入口；不自动发送模型请求。

同日完成运行链路统一后，后端 **542 项测试通过**，普通两轮和真实 Notion 学习一轮
联调通过；地图继续读取 26 条真实记录。详见 [统一链路验证](agent-runtime.md#验证)。

只读检查，无模型费用，不打印私人笔记正文：

```powershell
uv run python scripts/check_learning_sources.py --source notion --map
```

真实 Agent 联调会调用模型、读取 Notion，并保留本地测试会话；真实回答只输出字符数：

```powershell
uv run python scripts/smoke_learning_agent.py --source notion --turns 1
```

建议先沿着“页面请求列表 → FastAPI 路由 → LearningService → Notion 字段转换”的数据流阅读。
再看“创建带来源的会话 → ChatPanel 发送 → ChatService / ConversationTurnService →
AgentRunner 加载学习策略 → 保存历史”。
先理解模块之间传什么数据，再按需研究分页、取消和事务冲突的细节。

参考：[Notion 查询数据源](https://developers.notion.com/reference/query-a-data-source)、
[内部连接授权](https://developers.notion.com/guides/get-started/internal-connections)。
