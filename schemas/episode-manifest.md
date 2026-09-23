# Episode Manifest Contract

`episode.yaml` 使用 `ai-interview-episode-v1`。运行时契约由 `orchestrator/ai_interview/manifest.py` 执行；本页是快速参考。

必填顶层字段：`schema_version`、`pipeline_version`、`episode_id`、`format`、`fps`、`target_duration`、`retry_policy`、`characters`、`beats`、`shots`。

每个 Shot 必须声明：稳定 `S###` ID、镜头类型、Beat、编辑时长、生成时长、H3 模式、状态、最终 Prompt 路径、引用资产、声音意图、保留项与禁止项。

`edit_duration_sec` 是剪辑需要的时长；`generation_duration_sec` 是远端模型生成时长，必须覆盖前者且处于 4–15 秒。两者不得混用。
