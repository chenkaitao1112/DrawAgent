# drawAgent v2 重构 TODO

## 项目目标

在 `drawAgent-version2` 中从零搭建一个新的科研绘图 Agent 系统，采用“主控节点 + 虚拟节点”的双节点流转架构。

约束条件：

- 继续使用 `LangGraph`
- `StateGraph` 继续承担中间状态与工件存储职责
- 主控与虚拟节点之间通过 `messages` / 通知机制协作
- 模型输出优先通过原生 `@tool` 进行结构化约束
- 前端交互风格保持与现有版本一致，不在本阶段重设计 UI

首个落地 skill：

- `科研绘图`

该 skill 的标准流程：

1. 主控理解用户需求与附件
2. 逻辑提取
3. 风格提取
4. 可视化布局
5. 总结生成英文绘图 Prompt
6. 审查与回灌重试
7. 用户确认后调用固定出图工具

---

## 总体原则

- 不复用旧版“多真实节点 + 硬编码 revision_dispatch”的流程结构
- 新版真实图节点仅保留最少核心节点
- agent 扩展通过“任务协议 + skill 编排”完成，而不是继续加 graph node
- 所有中间结果都要可追踪、可审查、可回灌、可落盘
- 先保证链路正确，再逐步优化模型效果与提示词质量

---

## 阶段 A：项目骨架初始化

- [x] 初始化目录结构
- [x] 初始化 Python 项目配置
- [x] 初始化 `.env.example`
- [x] 初始化 `README.md`
- [x] 初始化 `docs/`、`skills/`、`prompts/`、`schemas/`、`runtime/` 目录
- [x] 初始化日志目录与运行时存储目录
- [x] 明确 v2 的配置文件分层

交付标准：

- 可以从空目录启动一个最小 FastAPI 服务
- 目录结构清晰，后续文件落点明确

---

## 阶段 B：核心领域模型设计

- [x] 设计 `GraphState` 顶层结构
- [x] 设计 `session` 子状态
- [x] 设计 `controller` 子状态
- [x] 设计 `messages` / mailbox 结构
- [x] 设计 `tasks` 结构
- [x] 设计 `artifacts` 结构
- [x] 设计 `review_history` 结构
- [x] 设计 `checkpoint` 结构
- [x] 设计 `image_generation` 结果结构

重点字段：

- `messages`
- `pending_tasks`
- `active_task`
- `completed_tasks`
- `payload_logic`
- `payload_style`
- `payload_mapper`
- `payload_final`
- `review_history`
- `retry_budget`
- `awaiting_user_confirmation`

交付标准：

- 有一份明确的状态 schema
- 每个虚拟节点输入输出都能映射到状态中

---

## 阶段 C：双节点 LangGraph 骨架

- [x] 实现 `controller_node`
- [x] 实现 `virtual_worker_node`
- [x] 构建最小可运行双节点状态图
- [x] 实现 `START -> controller -> worker -> controller -> END` 的基本闭环
- [x] 实现主控等待用户澄清分支
- [x] 实现主控等待用户确认分支
- [x] 实现失败终止分支

交付标准：

- 图上不再出现 logician/style/mapper/summarizer 等真实节点
- 所有任务流转都能通过双节点完成

---

## 阶段 D：任务协议与虚拟节点机制

- [x] 设计统一 `VirtualTask` 协议
- [x] 支持 `logic_extraction`
- [x] 支持 `style_extraction`
- [x] 支持 `visual_mapping`
- [x] 支持 `summarization`
- [x] 支持 `image_generation`
- [x] 实现任务派发器 `task_factory`
- [x] 实现 worker 对不同 `task_type` 的执行分发
- [x] 实现任务完成后的 message 回传

每个任务至少包含：

- `task_id`
- `task_type`
- `agent_name`
- `input_refs`
- `output_ref`
- `review_required`
- `review_phase`
- `retry_count`
- `max_retry`

交付标准：

- 主控只需要创建任务
- worker 只需要消费任务并写回结果

---

## 阶段 E：Skill 编排系统

- [x] 定义 v2 skill 格式
- [x] 设计 skill 的“人可读层 + 机器可执行层”
- [x] 接入第一个 skill：`scientific_diagram`
- [x] 让主控可以根据用户意图选择 skill
- [x] 让主控可以从 skill 解析出任务序列
- [x] 让 skill 支持后续扩展更多任务类型

`scientific_diagram` 首版目标：

- 逻辑提取
- 风格提取
- 可视化布局
- 总结
- 用户确认后出图

交付标准：

- 主控不再写死“先跑哪个 agent”
- 流程顺序来自 skill，而不是 graph 结构

---

## 阶段 F：Prompt / Tool / Schema 约束体系

- [x] 设计 v2 prompt repository
- [x] 设计 prompt 注入上下文协议
- [x] 设计 tool 输出契约
- [x] 为 controller 定义工具集
- [x] 为 virtual worker 定义工具集
- [x] 定义各类 artifact 的 schema
- [x] 为逻辑提取输出定义 schema
- [x] 为风格提取输出定义 schema
- [x] 为布局输出定义 schema
- [x] 为总结输出定义 schema
- [x] 为审查输出定义 schema

Controller 预期工具：

- `select_skill`
- `request_clarification`
- `dispatch_virtual_task`
- `release_with_warning`
- `save_checkpoint`
- `trigger_image_generation`

Worker 预期工具：

- `submit_logic_artifact`
- `submit_style_artifact`
- `submit_layout_artifact`
- `submit_summary_artifact`
- `submit_image_artifact`
- `emit_message`

交付标准：

- 模型输出尽量不靠裸 JSON
- 结构化输出有 schema 与工具双重约束

---

## 阶段 G：附件摄取与文档上下文

- [x] 迁移并简化附件上传链路
- [x] 接入 `document-ingestion-routing` skill
- [x] 保留文档解析规则
- [x] 支持 PDF / MD / TXT / DOCX
- [x] 对不支持格式输出 warning
- [x] 统一产出 `document_context`
- [x] 统一产出 `document_context_summary`
- [x] 统一产出 `source_files`
- [x] 为主控和虚拟节点提供文档摘要引用

交付标准：

- 上传附件后主控能明确感知附件是否已成功进入状态
- 虚拟节点不需要重新打开文件进行格式判断

---

## 阶段 H：审查工具与回灌重试

- [x] 实现专用审查工具 `review_virtual_output`
- [x] 支持对逻辑提取结果的审查
- [x] 支持对风格提取结果的审查
- [x] 支持对布局结果的审查
- [x] 支持对总结结果的审查
- [x] 审查结果写入 `review_history`
- [x] 审查结果通过 `messages` 通知主控
- [x] 主控解析 `positive` / `negative` / `fatal` 审查信号
- [x] negative 审查时自动构建 revision task
- [x] revision task 注入：
  - 原始输入
  - 当前输出
  - 审查意见
  - 上游依赖工件
- [x] 超过最大重试次数后放行但标记 warning
- [x] 超过最大重试次数后也支持彻底失败返回

交付标准：

- 审查不通过时不会直接漏到前端
- 审查通过后才进入下一阶段
- 超限后有明确 warning 标记

---

## 阶段 I：主控决策与消息流

- [x] 设计主控的 message 消费策略
- [x] 支持“收到 positive check 后进入下一任务”
- [x] 支持“收到 negative check 后重建对应虚拟任务”
- [x] 支持“收到 fatal 错误后停止本轮流程”
- [x] 支持“等待用户确认出图”
- [x] 支持“用户继续修订 Prompt”
- [x] 支持“用户停止生成”
- [x] 支持“停止后恢复正常上传和再次发起流程”

交付标准：

- 主控成为真正唯一调度中心
- worker 不承担流程决策职责

---

## 阶段 J：出图工具链

- [x] 抽离统一图片生成接口
- [x] 支持固定出图模型配置
- [x] 支持从 checkpoint 读取最终 Prompt
- [x] 实现用户确认后再出图
- [x] 将图片结果写回状态
- [x] 将图片落盘到输出目录
- [x] 返回前端可展示地址
- [x] 为后续替换图片模型预留 provider 适配层

交付标准：

- 点击“开始生图”后能从 checkpoint 恢复并出图
- 不依赖前端重复传 prompt 正文

---

## 阶段 K：FastAPI 接口层

- [x] 初始化 v2 FastAPI 服务
- [x] 实现首页与静态资源服务
- [x] 实现上传接口
- [x] 实现会话启动接口
- [x] 实现确认出图接口
- [x] 实现停止当前流程接口
- [x] 实现工作流摘要接口
- [x] 实现 agents / skills 描述接口
- [x] 保持与现有前端兼容的返回结构

交付标准：

- 前端样式不改也能接 v2 后端
- 至少具备端到端最小联调能力

---

## 阶段 L：可观测性与调试

- [x] 设计统一日志格式
- [x] 输出主控节点关键决策日志
- [x] 输出虚拟任务派发日志
- [x] 输出审查结果日志
- [x] 输出 revision 构造日志
- [x] 输出 checkpoint 保存日志
- [x] 输出图片生成日志
- [x] 支持查看当前 graph state 摘要

交付标准：

- 出问题时可以从控制台快速定位是在：
  - 意图识别
  - 任务派发
  - tool 调用
  - 审查失败
  - 出图失败

---

## 阶段 M：问题记录文档

- [x] 建立 `docs/issues/` 目录
- [x] 建立统一问题记录模板
- [x] 记录本次 v2 架构设计过程中的关键问题
- [x] 每个问题说明：
  - 背景
  - 初始方案
  - 为什么失效
  - 最终方案
  - 为什么有效
  - 后续注意事项

交付标准：

- 不是只修 bug，而是留下可复用经验

---

## 阶段 N：测试与验收

- [ ] 准备测试论文样本
- [x] 覆盖纯文本输入
- [ ] 覆盖 PDF 输入
- [ ] 覆盖用户仅上传文件不输入文字
- [x] 覆盖用户继续修订 Prompt
- [x] 覆盖审查通过链路
- [ ] 覆盖审查失败重试链路
- [ ] 覆盖最大重试后放行 warning 链路
- [x] 覆盖确认出图链路
- [x] 覆盖停止流程链路
- [x] 覆盖停止后重新上传与重新发起链路

验收标准：

- 能稳定产出一个英文绘图 Prompt
- 能看到中间工件与审查信息
- 用户确认后才能出图
- 失败与 warning 状态前端可感知

---

## 当前建议实施顺序

### 第 1 批

- [ ] 阶段 A：项目骨架初始化
- [ ] 阶段 B：核心领域模型设计
- [ ] 阶段 C：双节点 LangGraph 骨架


### 第 2 批

- [ ] 阶段 D：任务协议与虚拟节点机制
- [ ] 阶段 E：Skill 编排系统
- [ ] 阶段 F：Prompt / Tool / Schema 约束体系

### 第 3 批

- [ ] 阶段 G：附件摄取与文档上下文
- [ ] 阶段 H：审查工具与回灌重试
- [ ] 阶段 I：主控决策与消息流

### 第 4 批

- [ ] 阶段 J：出图工具链
- [ ] 阶段 K：FastAPI 接口层
- [ ] 阶段 L：可观测性与调试

### 第 5 批

- [ ] 阶段 M：问题记录文档
- [ ] 阶段 N：测试与验收

---

## 首个里程碑定义

当满足以下条件时，认为 v2 第一里程碑完成：

- 双节点图可以运行
- 主控可以识别 `scientific_diagram` skill
- 主控可以依次派发：
  - 逻辑提取
  - 风格提取
  - 可视化布局
  - 总结
- 每一步输出都写入 state
- 审查结果能通过 message 回给主控
- 审查失败时能自动重试至少 1 轮
- 前端能拿到最终 Prompt

---

## 备注

后续开发以本 TODO 为主线推进。

每完成一个阶段，及时：

- 更新勾选状态
- 落地对应文档
- 记录关键问题
- 保证最小可运行链路始终可验证
