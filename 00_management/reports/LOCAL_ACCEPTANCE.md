# 本地控制面验收

日期：2026-09-23（本轮实现状态）

## 结论

本地控制面关键闭环已实现：Ref2VA 多模态契约、VoiceDesign/IndexTTS 编排、ASR QC、48 kHz Audio Master、状态/预算恢复、粗剪与 Final QC Gate 均有实现。LatentSync/SeedVR2 package、submit/status/pull、结果 SHA/QC 与验证后 cleanup dispatcher 已实现并有本地合同测试；远端 postprocess worker 已部署并通过无卡 doctor。系统仍未达到完整生产就绪：项目级 IndexTTS→ASR 尚无真实 smoke；视觉概念图与反应镜头关键帧待导演确认；黄金片段尚无 H3 成片和导演验收；不可再生资产异地备份恢复证明尚未完成。

## 已实现的本地能力

- `ai-interview doctor`、manifest 校验及有预算记录的 H3/voice 状态同步。
- H3 package → 远端 contract → 固定 Ref2VA workflow 的多模态集成契约：图片/视频/音频计数、时长、SHA-256、master audio 优先规则均校验。
- Qwen VoiceDesign 包/提交/恢复/拉回和人工选择后冻结为不可变 48 kHz voice reference。
- IndexTTS 逐 beat/shot job 包装、远端 submit/status/pull、ASR 规范化/CER、候选哈希和人工选 take 入口。
- 48 kHz Dialogue Master、连续 room tone edit mix、SRT 字幕、无覆盖 hard-cut roughcut、候选选择与 edit lock。
- H3 尝试/预算原子写回、唯一 job、远端 `prompt_id`/GPU 时间状态同步与历史恢复；触顶暂停而非静默重试。
- H3、LatentSync、SeedVR2 GPU 分钟预算原子预留并互相扣减；预算在远端累计用量回报后结算，终态释放未用额度。同一 AutoDL GPU 的 H3、TTS、VoiceDesign 与后处理通过全局 GPU 锁串行。
- H3 上传通过 worker 原子发布并校验重试包；Voice/VoiceDesign 的上传发布和 worker 入列共享队列锁，防止相同 job_id 并发重复提交。H3 时长在本地和远端统一校验 24fps `17k+5` 合法帧数与不超过 15 秒。
- 候选清理前校验本地导入、metadata、SHA-256、QC sidecar；Final approval 要求逐项人工通过并保存证据。
- LatentSync/SeedVR2 分别按镜头状态与 edit lock 打包；worker 校验固定参数、输入哈希、CUDA、预算和 SeedVR2 官方模型哈希；拉回做 SHA、ffprobe、解码与媒体 QC。结果未通过人工选择前不会推进镜头状态。

## 远端实测边界

- RTX 5090/ComfyUI/H3 doctor 曾通过；Worker/workflow 已通过带备份、无删除的部署脚本发布。
- Qwen3-TTS VoiceDesign r02（host）与 r01（guest）真实 GPU job 成功，各拉回两条 candidate WAV；用户已选择 01 并冻结为不可变 48 kHz mono references。host r01 初始化失败作业保留。
- 独立 Whisper ASR venv 已安装并复用 LatentSync 的 Whisper tiny 权重，不下载模型。
- LatentSync 与 SeedVR2 postprocess worker 已发布；两个无卡 doctor 的模型/依赖/CLI 检查通过，状态为 `CONFIGURED_NO_GPU`。
- 最新 H3/voice/VoiceDesign/postprocess worker 并发保护、预算/请求身份合同已部署到 AutoDL；串行无卡 doctor 与只读远端审计全部通过，详见远端最终复核。
- 尚未提交 H3 job；尚未用本项目 Worker 做真实 IndexTTS→ASR 全链路 job。
- AutoDL 2026-09-23 复核后数据盘剩余约 11.81 GiB；容量不足时不得并发堆积任务。

## 当前未完成 Gate

1. 用项目 Worker 完成 IndexTTS→Whisper→Audio Master 实际 smoke（GPU 成本门槛）；随后生成并人工审核 15–20 秒 H3 黄金片段。
2. 导演审核角色/演播室概念图；冻结 reference pack 并补齐 reaction 起止帧。
3. 待用户开卡后实测 LatentSync/SeedVR2 调度入口；完善 Bible、workflow 与 benchmark 的异地备份和恢复证明。
4. OSS 对象清单/精确清理此前因 `UserDisable (403)` 无法确认；不影响模型运行目录，但备份清理状态仍未知。

以上未完成项属于人类导演选择、远端 GPU 实测或外部服务可用性；不能用模板结构校验或“模型已部署”替代。

本轮最终回归为 `uv run python -m unittest discover -s tests -v`：44 项通过；
`python3 -m compileall -q orchestrator remote scripts`、Shell `bash -n`、
`git diff --check`、模板与 EP0001 manifest validate 通过；`ai-interview doctor`
中的 FFmpeg、ffprobe、SSH、SCP、pipeline config 均 PASS。

最终差异复核另外补齐 IndexTTS/VoiceDesign 的原子预算预留与并发扣减，并在后处理提交时
重新核验当前镜头状态、选片 SHA、edit lock 和输入资产 SHA；同一 postprocess job_id 的远端
冲突在本地预算预留前拒绝。H3 终态 job_id 不能在清理后复用；Voice/VoiceDesign 的人工复核
响应会结算已经消耗的 GPU 分钟。新增对应回归后全量 44 项通过。
