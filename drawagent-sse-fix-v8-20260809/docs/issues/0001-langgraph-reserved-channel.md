# LangGraph 保留字段名导致图无法编译

## 背景

在 v2 双节点 StateGraph 初版实现中，内部状态直接使用了 `checkpoint_id` 和 `checkpoint_path` 作为字段名。

## 初始方案

- 在 `WorkflowState` 中直接定义：
  - `checkpoint_id`
  - `checkpoint_path`
- 主控节点在总结完成后把 checkpoint 信息直接写回这两个状态字段

## 为什么初始方案失效

在真实 import 与 graph compile 阶段，LangGraph 抛出：

```text
ValueError: Channel name 'checkpoint_id' is reserved
```

也就是说，`checkpoint_id` 会与 LangGraph 内部保留 channel 名冲突，导致整个图无法编译。

## 最终解决办法

把内部状态字段统一改名为：

- `prompt_checkpoint_id`
- `prompt_checkpoint_path`

同时：

- 对外 FastAPI 接口仍然继续返回 `checkpoint_id`
- 内部状态与外部 API 契约分离

## 为什么最终方案有效

- 避开了 LangGraph 的保留字段
- 不影响前端与调用方对 `checkpoint_id` 的既有理解
- 内部状态命名更明确，能区分“Prompt checkpoint”与未来可能出现的其他 checkpoint

## 后续注意事项

- 新增 StateGraph 字段时，先确认是否与框架保留字段冲突
- 内部状态名和外部 API 返回字段不必强行保持完全一致
- 遇到框架保留字问题，优先采用“内部改名、外部兼容”的方式

