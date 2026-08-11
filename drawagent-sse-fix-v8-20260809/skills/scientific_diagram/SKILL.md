---
name: scientific_diagram
description: 科研绘图 skill。用于基于论文、摘要、图注、方法描述或用户粘贴正文生成科研方法图、框架图或 architecture diagram 的英文绘图 Prompt。
plan:
  stages:
    - stage: logic_extraction
      agent_name: logic_extraction
      depends_on: []
      input_refs:
        - document.full_text
        - user.input
      review_required: true
      review_phase: logic_review
    - stage: style_extraction
      agent_name: style_extraction
      depends_on: []
      input_refs:
        - user.primary_discipline
        - user.conference_name
        - user.preferences
        - user.input
      review_required: true
      review_phase: style_review
    - stage: visual_mapping
      agent_name: visual_mapping
      depends_on:
        - logic_extraction
        - style_extraction
      input_refs:
        - artifacts.stage_outputs.logic_extraction
        - artifacts.stage_outputs.style_extraction
        - user.input
      review_required: true
      review_phase: visual_review
    - stage: summarization
      agent_name: summarization
      depends_on:
        - visual_mapping
      input_refs:
        - artifacts.stage_outputs.logic_extraction
        - artifacts.stage_outputs.style_extraction
        - artifacts.stage_outputs.visual_mapping
        - user.input
      review_required: true
      review_phase: final_prompt_review
    - stage: image_generation
      agent_name: image_generation
      depends_on:
        - summarization
      input_refs:
        - artifacts.payload_final
      review_required: false
      trigger: user_confirm
---

# Scientific Diagram Skill

## Skill Intent

当用户上传论文、文档，或直接描述科研方法图、框架图、architecture diagram 需求时，主控可以选择本 skill。

本 skill 的目标不是复述论文，而是生成一段可用于绘图模型的英文科研绘图 Prompt。

## Entry Requirements

进入本 skill 前，需要两类信息：

1. 设计上下文
- `primary_discipline`
- `conference_name`
- `user_preferences`

2. 来源材料
- 已成功解析的论文/文档附件正文
- 或用户直接粘贴的摘要、方法部分、图注或其他足够具体的正文片段

如果只有学科、会议、偏好，而没有正文来源材料，则不能进入逻辑提取。

## Orchestration Rules

1. 逻辑提取读取论文全文或用户提供的正文材料，输出方法逻辑结构。
2. 风格提取读取学科、会议/期刊和用户偏好，输出风格约束。
3. 逻辑提取和风格提取互不依赖，可以并行执行。
4. 视觉映射依赖逻辑提取和风格提取。
5. 最后汇总为英文绘图 Prompt。
6. 每个 worker 阶段都要经过审查。
7. 审查失败时回灌给对应虚拟节点，只重做失败阶段。
8. 用户确认 Prompt 后，才进入图片生成。

## Review Rules

- 逻辑提取要检查是否忠于原文，是否缺失关键模块与依赖。
- 风格提取要检查是否只使用用户提供的学科、会议/期刊和偏好，不得越界补造技术路线。
- 视觉映射要检查是否忠于逻辑和风格结果。
- 汇总要检查英文 Prompt 是否忠于上游结果，且可直接用于生图。
