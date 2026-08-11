---
name: document_ingestion_routing
description: 文档摄取与入口运行态识别 skill。用于判断用户本轮是在上传材料、补充目标、修订 Prompt、确认生图、停止流程，还是需要继续澄清。它不绑定任何具体业务绘图 skill。
plan:
  stages: []
---

# Document Ingestion Routing Skill

这个 skill 是入口层的“交通岗”，不是内容生成 skill，也不是某个业务流程的默认前置节点。

它只负责帮助主控理解当前对话轮次处于什么入口状态：

- 用户是否只是上传了文件，还没有说明要做什么。
- 上传文件是否已经被解析成可用正文。
- 用户是否正在补充上一轮澄清信息。
- 用户是否想修订已经生成的 Prompt。
- 用户是否想确认出图。
- 用户是否想停止当前流程。
- 当前信息是否不足，需要继续向用户追问。

它不应该假设所有文档都会进入科研绘图，也不应该把基金、论文、技术路线、海报、汇报图等目标写死成固定分支。

## Routing Intents

主控可以使用以下通用意图：

- `skill_request`: 用户已经表达了一个可交给具体 skill 的业务目标。
- `document_only_upload`: 用户只上传了文档，还没有说明目标任务。
- `document_parse_failed`: 附件已上传，但没有解析出可用正文。
- `prompt_revision_request`: 用户想继续修订已有 Prompt。
- `image_confirmation_request`: 用户想基于已有 checkpoint 确认出图。
- `stop_request`: 用户想停止当前流程。
- `clarification_needed`: 当前信息不足，需要继续澄清。

## Routing Rules

1. 如果用户只上传文档，没有说明目标，识别为 `document_only_upload`，询问用户想把材料变成哪类图或 Prompt。
2. 如果附件解析失败，识别为 `document_parse_failed`，要求用户重新上传可解析文件，或直接粘贴正文片段。
3. 如果用户表达“修订、修改、调整、继续优化 Prompt”，识别为 `prompt_revision_request`，提示走已有 checkpoint 的修订入口。
4. 如果用户表达“开始生图、确认出图、生成图片”，识别为 `image_confirmation_request`，提示需要基于已生成 Prompt 的 checkpoint 确认。
5. 如果用户表达“停止、取消、终止”，识别为 `stop_request`，交给停止流程处理。
6. 如果用户已经说明业务目标，主控应根据 `available_skills` 的自然语言描述选择具体 skill，并把意图记录为 `skill_request`。
7. 如果多个 skill 都可能匹配，或用户目标仍不清楚，识别为 `clarification_needed`，只问最必要的下一步问题。

## Output Contract

这个 skill 不创建 worker task。它只影响主控状态：

- `selected_skill`
- `detected_intent`
- `routing_reason`
- `target_skill`
- `final_response`

## Important Constraints

- 不要在这个 skill 中枚举具体业务 skill 的关键词表。
- 不要把 `scientific_diagram` 或其他任何 skill 当作默认目标。
- 不要要求用户理解内部字段名、阶段名或 payload 名。
- 信息可以渐进式收集；不要强制用户在单轮提供全部信息。
