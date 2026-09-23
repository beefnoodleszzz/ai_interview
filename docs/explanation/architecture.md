# 本地控制面与 AutoDL Worker 的分层原因

本项目把“创作决策”和“GPU 执行”分开。本地 Mac 保存不可再生、需要审计的生产资料；AutoDL 只保存可重新部署的模型和执行环境。这样实例关机、抢占或重建时，不会丢失台本、声音选择、Prompt、Manifest 和导演决策。

```text
Local Mac (source of truth)
  script / bible / audio master / prompts / manifest
  validation / package / review / edit / QC
                      │ SSH job/result packages
                      ▼
AutoDL (replaceable worker)
  ComfyUI + H3 → optional LatentSync → optional SeedVR2
```

远端不得改写 Prompt、选择候选或更新本地 Manifest。它只能校验输入、注入版本化 Workflow 的允许字段、生成候选并返回视频、原生环境音和运行元数据。

声音仍然决定表演时间轴，但模型推理不在本地进行：IndexTTS-2.5、Qwen3-TTS 与 ASR 都放在 AutoDL 的独立语音环境中。本地负责编写请求、拉回原始 WAV、记录 ASR 结果、人工选 Take，并生成最终 Audio Master。H3 生成长度和最终剪辑长度分离：远端生成满足模型对齐要求的片段，本地按 Manifest 精确裁切。

大显存后处理仍在 AutoDL，但只由本地 QC 结果触发：嘴型局部失败才进 LatentSync，剪辑锁定后才进 SeedVR2。这样不会为注定淘汰的镜头付费。
