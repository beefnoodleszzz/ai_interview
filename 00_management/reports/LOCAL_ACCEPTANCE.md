# 本地控制面验收

日期：2026-09-23（本轮实现状态）

## 结论

本地控制面关键闭环已实现：Ref2VA 多模态契约、VoiceDesign/IndexTTS 编排、ASR QC、48 kHz Audio Master、状态/预算恢复、粗剪与 Final QC Gate 均有实现。系统仍不能标记为完整生产就绪：IndexTTS 项目 Worker 尚无真实项目 job smoke；VoiceDesign 候选与视觉概念图尚待人类选择；黄金片段尚无批准参考、H3 成片和导演验收；LatentSync/SeedVR2 调度及不可再生资产异地备份仍未完成。

## 已实现的本地能力

- `ai-interview doctor`、manifest 校验及有预算记录的 H3/voice 状态同步。
- H3 package → 远端 contract → 固定 Ref2VA workflow 的多模态集成契约：图片/视频/音频计数、时长、SHA-256、master audio 优先规则均校验。
- Qwen VoiceDesign 包/提交/恢复/拉回和人工选择后冻结为不可变 48 kHz voice reference。
- IndexTTS 逐 beat/shot job 包装、远端 submit/status/pull、ASR 规范化/CER、候选哈希和人工选 take 入口。
- 48 kHz Dialogue Master、连续 room tone edit mix、SRT 字幕、无覆盖 hard-cut roughcut、候选选择与 edit lock。
- H3 尝试/预算原子写回、唯一 job、远端 `prompt_id`/GPU 时间状态同步与历史恢复；触顶暂停而非静默重试。
- 候选清理前校验本地导入、metadata、SHA-256、QC sidecar；Final approval 要求逐项人工通过并保存证据。

## 远端实测边界

- RTX 5090/ComfyUI/H3 doctor 曾通过；Worker/workflow 已通过带备份、无删除的部署脚本发布。
- Qwen3-TTS VoiceDesign r02（host）与 r01（guest）真实 GPU job 成功，各拉回两条 candidate WAV；host r01 初始化失败作业保留。候选未经人工试听，不是冻结参考。
- 独立 Whisper ASR venv 已安装并复用 LatentSync 的 Whisper tiny 权重，不下载模型。
- 尚未提交 H3 job；尚未用本项目 Worker 做真实 IndexTTS→ASR 全链路 job。
- AutoDL 数据盘上次检查约剩余 17 GiB；容量不足时不得并发堆积任务。

## 当前未完成 Gate

1. 项目所有者试听 Qwen 声音候选并选择、冻结正式 voice references。
2. 导演审核角色/演播室概念图；冻结 reference pack 并补齐 reaction 起止帧。
3. 用项目 Worker 完成 IndexTTS→Whisper→Audio Master 实际 smoke，再生成并人工审核 15–20 秒 H3 黄金片段。
4. 实现并实测 LatentSync/SeedVR2 调度入口；完善 Bible、workflow 与 benchmark 的异地备份和恢复证明。
5. OSS 对象清单/精确清理此前因 `UserDisable (403)` 无法确认；不影响模型运行目录，但备份清理状态仍未知。

以上未完成项属于人类选择、远端 GPU 生成、调度器实现或外部服务可用性；不能用模板结构校验或“模型已部署”替代。
