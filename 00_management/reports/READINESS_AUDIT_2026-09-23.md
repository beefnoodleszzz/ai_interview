# 端到端生产就绪审查

日期：2026-09-23
需求基线：`../requirements/AI_Interview_Production_Pipeline_H3_ComfyUI_AutoDL_v1_1.md`
结论：**NOT READY — 控制面主能力已实现，黄金片段与完整后半流程尚未验收。**

## 已落实

- Ref2VA 本地 package、远端 contract 与固定 workflow 支持图片/视频/音频 references，数目/总时长/哈希一致校验；Audio Master 优先作为对话 reference。
- Worker 发布使用有 SHA-256 的临时包、远端暂存与原版本备份，不执行远端删除。
- VoiceDesign 和 IndexTTS 项目 submit/status/pull、ASR 文字 QC、48 kHz master、候选选择、字幕和 room-tone edit mix 编排入口已实现。
- Manifest 驱动 attempt/GPU 预算状态同步、失败保留、ComfyUI history 恢复和预算暂停已实现。
- roughcut 输出时长、无覆盖、edit lock、Final QC、人工审批、验证后远程 cleanup gate 已实现。
- Host/guest Character Bible、Studio Bible 已建立；Host r04 与 Guest r01 身份参考、Host 演播室近景 r01 和 S002 reaction start/end r04 获项目所有者批准；Guest 演播室近景和 Studio 宽景仍待审核。
- AutoDL 实测：RTX 5090/ComfyUI/H3 doctor 曾通过；Qwen VoiceDesign 两个角色各有 2 条候选；独立 Whisper ASR venv 已安装。

## 未达到放行条件

1. 用户已试听并选择 Host 01 / Guest 01，正式 48 kHz voice references 已冻结。Host r04 与 Guest r01 身份图、Host 演播室近景 r01、S002 reaction start/end r04 已由项目所有者批准；Guest 演播室近景和 Studio 宽景仍待审核，正式 Reference Pack 仍未齐全。
2. IndexTTS 项目 job 曾因辅助模型不完整停在 `loading_model`；现已补齐 BigVGAN 并将无进程的旧 job 标为 FAILED。项目级 IndexTTS→ASR→Audio Master smoke 仍待用户开卡。
3. 没有真实 H3 镜头和 15–20 秒导演验收通过的黄金片段；不得启动 70–100 秒正式集。
4. LatentSync/SeedVR2 dispatcher 和远端 worker 已实现、部署；无卡 doctor 的依赖、模型与 CLI 检查通过。真实项目任务仍需 GPU smoke。
5. Bible/workflow/benchmark 异地备份恢复未证明；此前 OSS API 返回 `UserDisable (403)`，对象清单/清理结果未知。

## 验收纪律

任何外部生成结果都必须先导入本地、验证哈希与媒体元数据，再记录人工选择。失败作业保留并使用新 revision；不通过自动重试耗尽预算。该报告不把模型安装、单模型 smoke、模板校验或候选图片生成记作端到端通过。

本地实现验收见 [`LOCAL_ACCEPTANCE.md`](LOCAL_ACCEPTANCE.md)，远端环境事实见 [`REMOTE_ACCEPTANCE.md`](REMOTE_ACCEPTANCE.md)。
