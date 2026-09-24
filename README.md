# AI Interview Production

AI 原生访谈 / 对谈视频生产系统的本地管理根目录。

本目录是本地制片控制面，不存放或运行生成模型。所有模型推理在 AutoDL 完成。

## 生产原则

- 台本是第一生产资料，Audio Master 是表演时间轴。
- MiniMax H3 是短镜头演员；ComfyUI 是镜头生成总控；Codex/Python 是流水线编排层。
- 每集拆成 12–24 个 Shot，每个 Episode 必须可独立重建。
- LipSync 只修复局部失败，不用于挽救身份、构图或表演失败的镜头。
- 第一里程碑是 15–20 秒黄金测试片段，通过后再扩展到 70–100 秒完整集。
- 每个 Shot 限制重试次数和 GPU 时间；状态、版本、产出与失败原因必须写回 manifest。
- 模型和缓存可重新下载；Bible、Voice Library、Workflow、Orchestrator、Manifest 与 Benchmark 必须异地备份。

## 目录职责

- `00_management/`：原始需求、决策、计划、检查表、Benchmark 和报告。
- `characters/`：主持人和嘉宾的 Character Bible、参考图、声音与表情资产。
- `sets/`：Studio Bible、固定机位和灯光参考。
- `episodes/`：按集隔离的生产文件；`_template/` 是目录模板。
- `remote/workflows/`：版本化的 ComfyUI API Format 工作流。
- `orchestrator/`：manifest、任务状态机、重试/预算护栏、断点续传和 FFmpeg 编排代码。
- `logs/`：ComfyUI、TTS 与 orchestrator 运行日志。
- `docs/`：教程、操作指南、执行矩阵与架构解释。
- `config/`：无凭据的本地控制面和远程 Worker 配置。
- `remote/`：部署到 AutoDL 的项目专属 H3 Worker 与固定工作流。
- `scripts/`：可重复执行的 AutoDL 基础部署、环境安装和模型清单脚本。

## 当前 Gate

本地控制面关键路径和 AutoDL Worker/ASR 环境已搭建。Host 01 / Guest 01 声音候选已选定并冻结；Character/Studio 与 reaction 起止帧候选已备好，仍需导演审核和正式冻结。无卡代码/配置验收完成后，等所有者开卡再做项目级 IndexTTS/ASR、15–20 秒 H3 黄金片段与后处理实测。黄金片段通过前不得生产 70–100 秒完整集。每次 GPU 提交前核对原子预算预留和远端剩余磁盘空间。

后续每集按 [持续生产操作手册](docs/how-to/production-runbook.md) 执行；EP0001 从[无卡交接清单](episodes/EP0001_golden_clip/EP0001_NO_GPU_HANDOFF.md)继续。

完整需求基线见：
`00_management/requirements/AI_Interview_Production_Pipeline_H3_ComfyUI_AutoDL_v1_1.md`
