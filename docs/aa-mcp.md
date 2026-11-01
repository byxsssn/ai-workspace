# AA 模型查询接入现有 Agent

普通聊天现在可以使用 AA MCP 的 `query_model_rankings` 和 `compare_models`。
它们与 PyPI 查询工具由同一个 `AgentRunner` 使用；用户、BYOK、会话历史和消息保存
继续走原有流程。学习模式仍按自己的只读学习工具和来源规则执行。

本阶段增加的是聊天中的模型数据能力，没有新增模型排行榜网页，也没有部署远程 MCP。

## 数据怎样流动

```text
普通聊天 → AgentRunner → LangChain 工具 → AAClient
                                     → stdio MCP 子进程
                                     → AA 项目的快照与计算服务
工具结果 → Agent 解释 → 追加实际来源 → 保存到原会话
```

AA 项目位于独立的 `D:/Projects/aa-analysis`，保留数据读取、计算和下载逻辑。
AI Workspace 通过 MCP 调用它，不复制排序公式，也不直接读取其快照。
MCP 客户端使用官方 Python SDK；LangChain 工具是应用侧的参数、结果与来源控制层。

后端仅允许调用两个约定的工具。工具名称、运行的 Python 和快照位置由后端配置，
模型不能指定任意命令、路径或网址。普通聊天没有调用 AA 工具时，不启动 AA 进程。
调用使用独立子进程作用域，带超时与清理；不把用户模型密钥、Notion 凭据或数据库配置
传给 AA。MCP 只读取现有快照，联网刷新继续使用 AA 项目自己的维护命令。

## 本地配置

先在 AA 项目安装锁定依赖并验证服务：

```powershell
cd D:\Projects\aa-analysis
uv sync --locked
uv run python scripts/smoke_mcp.py
```

然后在 **AI Workspace 的根目录 `.env`** 中配置：

```dotenv
AI_WORKSPACE_AA_MCP_ENABLED=true
AI_WORKSPACE_AA_MCP_PYTHON_EXECUTABLE=D:/Projects/aa-analysis/.venv/Scripts/python.exe
AI_WORKSPACE_AA_MCP_SNAPSHOT_PATH=D:/Projects/aa-analysis/aa_models_export.json
AI_WORKSPACE_AA_MCP_TIMEOUT_SECONDS=15
```

这里需要 AA 项目环境中的 Python，因为那个环境安装了 `aa_analysis` 包。
服务会用该解释器执行固定命令 `-m aa_analysis.server --snapshot ...`，不会通过 shell。
解释器和快照均使用绝对路径。超时允许 1–30 秒；修改配置后重启 AI Workspace 后端。

未配置时 `ENABLED` 默认 `false`，普通聊天和 PyPI 工具可独立工作。
启用却连接失败时，AA 查询返回明确失败，不用模型记忆填造排名。
不要把 AA 密钥放进这些配置；只读工具不需要 `APIKEY`。

AA MCP 使用 stdio，没有需要打开的网页。手动运行 `aa-mcp` 时终端等待协议消息
属于正常状态；在 AI Workspace 中由客户端启动并关闭，不需要另外长期运行一个终端。

## 直接试用

在网站登录，进入 **AI 助手的普通会话**，例如询问：

> 查询 AA 快照里按能力与评测成本比排列的前 5 个配置，说明这个排名的局限。

或者：

> 查询前 3 个配置，再比较它们的能力指数和评测任务成本，解释取舍。

工具只反映已保存快照。模型配置 ID 对应具体评测设置，同一家族不同推理设置不合并。
一次回合首次成功查询会固定快照标识；比较时由后端绑定这个标识，
发现数据改变会拒绝混用。查询结果中的任意文本只作为数据，不作为新指令。

## 来源与口径

- 能力指标是 AA 的 `artificial_analysis_intelligence_index`。
- 成本指标是 `cost_per_task.total_cost`，代表对应评测任务成本；不是每百万 token
  的 API 单价，也不是用户业务的账单。
- `value` 排名使用自定义 **I/C**。排名只包含同时拥有正数、有限 I 与 C 的配置，
  不能称为全市场或所有任务下的最优模型。
- 来源信息包含快照标识、采集时间、评测版本和有效样本范围。旧快照缺少采集时间时
  明确显示未知，不能用报告生成时间替代。
- 同一回答可使用 PyPI 和 AA 两种来源。某个查询失败时使用保守的失败说明，
  成功来源独立保留；不会把一个来源的统计当成另一个来源的证据。

追加来源表明本轮实际读取了哪些数据，不代表程序自动核验了模型解释中的每一句话。

## 验证

在 AI Workspace 目录运行无需调用大模型的验证：

```powershell
uv run python scripts/smoke_aa_mcp.py
```

它会通过 AI Workspace 的 `AAClient` 真正启动 MCP 子进程，查询 3 条配置，
再使用同一快照标识比较它们，核对指标并输出简短元数据。

已有测试账号、保存的 OpenRouter 密钥和正在运行的网站时，可以额外验证一轮真实对话：

```powershell
uv run python scripts/smoke_aa_mcp.py --with-agent
```

此命令从所选环境文件读取 `AI_WORKSPACE_TEST_EMAIL`、`AI_WORKSPACE_TEST_PASSWORD`、
`AI_WORKSPACE_TEST_MODEL`，创建一个测试会话并产生模型费用；一次 Agent 回合可能包含
多次模型调用。它核对回答中的配置 ID、指标、来源标识以及数据库中的两条消息。
默认通过 Vite 的 `http://127.0.0.1:5173/api` 访问；直连后端可使用：

```powershell
uv run python scripts/smoke_aa_mcp.py --with-agent --base-url http://127.0.0.1:8000 --api-prefix ""
```

真实回答核对并不等于完整工具调用轨迹审计；具体工具调用、失败保护和取消清理
由协议与 Agent 测试覆盖。常规测试使用离线数据和临时目录，不要求 CI 机器拥有你的
AA 项目或私有快照。远程真实对话验证必须使用 HTTPS 和独立的 `--env-file`，
并保证本地对照快照与远程服务快照一致。

2026-11-01 本地验收：后端全量 661 项测试通过，Ruff 与格式检查通过。
真实 MCP 查询并比较 3 个配置；使用 `openai/gpt-6-luna` 的真实聊天接口联调通过，
回答中的配置 ID、原始指标、快照来源以及数据库中保存的两条消息均已核对。

## 部署边界

当前连接方式适用于同一台机器上运行的两个项目。Railway 等云端服务无法读取你电脑的
`D:/Projects`。线上应先把 AA 包和快照部署到后端可访问的运行环境，再配置该环境的
解释器和快照绝对路径；或者在后续实现带认证的远程 MCP 连接。
这次没有把本地路径写进生产配置，也没有开放远程 MCP 端口。

继续阅读：[统一 Agent 执行流程](agent-runtime.md)、[PyPI 榜单](pypi-rankings.md)。
AA 独立项目的说明位于本机 `D:/Projects/aa-analysis/README.md`。
