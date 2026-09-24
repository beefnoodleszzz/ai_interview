# Project Status

- 项目：AI 原生访谈 / 对谈视频生产系统
- 需求基线：v1.1（2026-09-22）
- 当前阶段：`EP0001_golden_clip` 的 15.8 秒 1080×1920 单层字幕候选片 v3 已完成；S003“本来？”的 0.48 秒画面取入误差已修正，48 kHz 音频未改，Final QC 通过。
- 当前 Gate：所有者已于 2026-09-25 确认 v3 黄金片段可以通过；确定性 Final QC 为 PASS。正式 `approve-final` 的逐项 Checklist、手机端证据和异地备份仍分别留证，70–100 秒完整集尚未启动。
- AutoDL：本轮新实例 `c40c4b807c-9daa1942` 已于 2026-09-25 在控制台确认“已关机”。
- 候选片、字幕、QC 与 SHA-256 见 [`REVIEW_STATUS.md`](../episodes/EP0001_golden_clip/09_final/REVIEW_STATUS.md)。

## 已冻结技术路线

| 模块 | 决策 |
| --- | --- |
| 视频核心 | MiniMax H3 |
| 视频总控 | ComfyUI |
| GPU 环境 | AutoDL，优先 32GB，48GB 更稳 |
| 声音策略 | Audio Master First |
| 主力 TTS | IndexTTS-2.5 |
| Voice Design | Qwen3-TTS |
| 嘴型修复 | LatentSync 1.6；MuseTalk 1.5 用于预览 |
| 超分 | SeedVR2 |
| 编排 | Codex + Python + ComfyUI API + FFmpeg |
| 成片 | 70–100 秒，短 Shot 拼接，1080×1920，24fps 优先 |

## 强制护栏

- `max_attempts_per_shot: 4`
- `max_gpu_minutes_per_shot: 15`
- `max_gpu_minutes_per_episode: 300`
- Ref2VA 音频参考不得单独提交，必须搭配图片或视频参考。
- 任务使用唯一 `job_id`；启动时恢复超时的 `*_RENDERING` 状态。
- 达到预算或重试上限时进入人工审核，不继续静默烧卡。

## 首个验收目标

15–20 秒黄金片段必须同时通过：主持人与嘉宾身份、Studio 连续性、声音人格、嘴型、Reaction timing、Room tone 和切镜节奏。

## 下一步

先对已交付的候选片完成逐项视听验收并记录导演结论；通过后才开放完整集。异地备份恢复证明及可选后处理 GPU Smoke 仍待独立验收。
