# 执行与模型矩阵

状态基线：2026-09-23。`PASS`/`READY` 只表示对应检查已执行，不代表完整生产放行。

| 能力 | 位置 | 模型/工具 | 当前状态 | 输出 |
| --- | --- | --- | --- | --- |
| 台本、Manifest、角色/Studio Bible | 本地 | Python/YAML/Markdown | 控制面已实现；首套视觉 Bible 与图像为待审核候选 | 版本化创作资产 |
| H3 多模态任务 | 本地 + AutoDL | ComfyUI / MiniMax H3 | package-contract-workflow 集成实现；远端 worker 已部署；真实 H3 job 未提交 | MP4 + metadata |
| 声音设计 | AutoDL 独立 voice 环境 | Qwen3-TTS VoiceDesign | 两个角色各生成 2 个 GPU 候选；人类选择/冻结未完成 | candidate WAV + metadata |
| 主力配音 | AutoDL 独立 voice 环境 | IndexTTS-2.5 | 模型 GPU smoke 已通过；项目 Worker/ASR 编排已实现，项目全链路真实 job 未验证 | raw WAV + ASR metadata |
| 台词 ASR QC | AutoDL 独立 ASR venv | OpenAI Whisper | venv 已安装并复用现有 tiny 权重；生产候选识别未实测 | transcript/CER metadata |
| Audio Master / room tone / 字幕 | 本地 | FFmpeg/ffprobe | 48 kHz Dialogue Master、连续 room tone edit mix、SRT 与粗剪入口已实现 | WAV/SRT/MP4 |
| 状态、预算与恢复 | 本地 + AutoDL | YAML state / ComfyUI history | 原子写回、差量 GPU 时间、attempt 限制、恢复/暂停已实现；需真实 H3 job 压测 | manifest 状态与预算 |
| LipSync 修复 | AutoDL | LatentSync 1.6 | 环境/权重已部署；项目 job dispatcher 未实现；只在人工判定 NEEDS_LIPSYNC 后使用 | repaired MP4 |
| 最终超分 | AutoDL | SeedVR2 | 环境/权重已部署；项目 job dispatcher 未实现；仅 edit lock 后使用 | upscale MP4 |
| 最终 QC/审批/清理 | 本地 + AutoDL | FFmpeg、哈希、人工 checklist | Final QC、逐项导演批准与安全 cleanup 入口已实现；无 final master 可验 | QC/approval records |

## 当前禁止放入 Git

- H3、LatentSync、SeedVR2、TTS、ASR 权重和环境；生成 WAV/MP4、临时缓存；
- AutoDL SSH 凭据、cookies、`.env.local` 及任何秘密。

## AutoDL 事实边界

远端固定根目录为 `/root/autodl-tmp/ai_interview`。部署脚本只发布项目 Worker 与 workflow，并在替换时备份旧版本，不删除既有远端文件。远端 doctor 曾确认 RTX 5090、ComfyUI 绑定 `127.0.0.1` 和 H3 节点可用。当前远端数据盘剩余空间有限（上次读数约 17 GiB）；每次实际提交前重新运行 doctor 和磁盘检查。

本项目默认不在本机运行任何生成模型。已有其他项目的本地模型不是本项目依赖，也不得静默作为 fallback。
