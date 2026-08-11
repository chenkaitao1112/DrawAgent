# drawAgent v2

`drawAgent v2` 是一个参考deerflow2重新搭建的绘图 Agent ，核心架构为：

- `LangGraph` 双真实节点
  - `controller_node`
  - `virtual_worker_node`
- `StateGraph` 存储中间工件、任务、消息和审查记录
- 主控与虚拟节点通过 `messages` 协作
- 主控与虚拟节点都通过 `MCP`工具调用 约束结构化输出
- 审查通过真正的 `MCP` 调用完成

## 当前范围

三个 skill：

- `document_ingestion_routing`
- `scientific_diagram`
- `grant_diagram`

链路目标：

1. 用户上传文档并输入需求
2. 主控根据意图选择对应绘图 skill
3. 主控依次派发根据skill的编排逻辑(如科研绘图: 逻辑提取、风格提取、可视化布局、总结任务<相互依赖关系>)
4. 每一步结果进入状态并通过 MCP 审查
5. 审查不通过时自动重试至多三次，触发告警并记录错误原因；通过则审查调用工具写入State供其它节点共享
6. 生成最终英文 Prompt，等待用户确认
7. 用户确认后调用固定图片模型生成图片，可下载

## 启动

```powershell
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## LLM 接口模式

v2 支持通过 `.env` 切换大模型调用链路：

```env
LLM_API_MODE=chat_completions
```

可选值：

- `chat_completions`
- `responses`
- `gemini_generate_content`

对应端点分别为：

- `.../chat/completions`
- `.../responses`
- `.../v1beta/models/{model}:generateContent`

## 图片接口模式

v2 的图片生成也支持通过 `.env` 切换：

```env
IMAGE_API_MODE=openai_images
```

可选值：

- `openai_images`
- `gemini_generate_content`

对应端点分别为：

- `.../images/generations`
- `.../v1beta/models/{model}:generateContent`

## 注意

本项目内置了一个本地 MCP review server 入口，但运行时仍需要：

- 正确配置 `.env`
- 可用的 LLM Key
- 可用的图片生成 Key

如果不配置 MCP server 命令，系统会默认尝试用当前 Python 启动本仓库内置的 `mcp_servers.drawagent_mcp_server`。
