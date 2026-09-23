# Project Status

- 项目：AI 原生访谈 / 对谈视频生产系统
- 需求基线：v1.1（2026-09-22）
- 当前阶段：本地控制面主链路已实现；AutoDL H3/Voice/ASR 环境已部署；黄金片段资产处于人工审核前
- 当前 Gate：冻结经人工审核的声线/视觉参考，完成真实 IndexTTS→ASR job，再生产并审核 15–20 秒黄金片段
- 当前 Episode：`EP0001_golden_clip` 模板骨架已定义，视觉/声音候选仍未批准

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

不要启动 70–100 秒完整集。先让项目所有者试听并冻结声音候选、让导演审核
`characters/` 与 `sets/` 下的视觉候选，再完成项目级 IndexTTS/ASR 实测和真实
15–20 秒 H3 黄金片段。LatentSync/SeedVR2 调度、异地备份恢复与 OSS 对象核对
仍需单独完成。详见
[`READINESS_AUDIT_2026-09-23.md`](reports/READINESS_AUDIT_2026-09-23.md)。
