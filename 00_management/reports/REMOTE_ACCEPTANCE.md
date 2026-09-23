# AutoDL 远端验收

日期：2026-09-23

## 结论

AutoDL 实例上的 H3、Qwen3-TTS、IndexTTS、LatentSync 和 SeedVR2 主环境与
主权重已完成部署。模型均直接写入
`/root/autodl-tmp/ai_interview/models`，没有把同实例首次下载经 OSS 中转；运行目录
通过应用软链接或应用配置使用这些路径。真实 GPU Smoke 进一步发现 IndexTTS
还需要约 5.2 GiB 首次运行辅助模型；现已下载到运行目录。此报告仅证明模型运行
环境，不代表项目端到端编排已完成。

## 已验证环境

| 组件 | Python 环境 | 验证结果 |
| --- | --- | --- |
| IndexTTS-2.5 | `apps/index-tts/.venv` | GPU 推理 PASS；生成 4.946 秒 22.05 kHz WAV；辅助模型已落盘 |
| Qwen3-TTS | `envs/qwen3-tts` | VoiceDesign GPU 推理 PASS；生成 4.32 秒 24 kHz WAV |
| LatentSync | `envs/latentsync` | 离线依赖导入成功；该次导入检查未挂载 GPU，尚未做真实推理 Smoke |
| H3 / ComfyUI | `/root/miniconda3` | 离线依赖检查后，已在 RTX 5090 实例启动并通过 ComfyUI/H3 doctor；尚未完成真实 Ref2VA 作业 |

系统 SoX 已补齐；`flash-attn` 仍为可选加速，不影响当前 SDPA 推理。RTX 5090 和
ComfyUI API 已启动并通过 H3 doctor。

## 运行模型路径

| 能力 | 运行路径 | 关键文件大小 |
| --- | --- | --- |
| IndexTTS-2.5 | `models/voice/IndexTTS-2.5` | `gpt.pth` 3,259,599,833；`codec.pth` 607,290,935；`s2mel.pth` 414,908,601 |
| Qwen3-TTS VoiceDesign | `models/voice/Qwen3-TTS-12Hz-1.7B-VoiceDesign` | 主权重 3,833,402,552；语音 tokenizer 682,293,092 |
| Qwen3-TTS Base | `models/voice/Qwen3-TTS-12Hz-1.7B-Base` | 主权重 3,857,413,744；语音 tokenizer 682,293,092 |
| LatentSync 1.6 | `models/lipsync/LatentSync-1.6` | UNet 5,072,222,488；Whisper tiny 75,572,083 |
| SeedVR2 | `models/upscale/SEEDVR2` | 主权重 3,391,544,696；VAE 501,324,814 |
| MiniMax H3 | `models/h3` | 五个既有权重均存在并生成 SHA-256 inventory |

ComfyUI 的 H3 模型和 SeedVR2 模型通过软链接指向上述运行树；IndexTTS 的
`apps/index-tts/checkpoints` 也已软链接到 `models/voice/IndexTTS-2.5`。

## 磁盘与清理

- 持久盘 `/dev/md0`：首次 GPU Smoke 前总使用约 98 GiB、可用约 23 GiB
  （当时 inventory：23,719,505,920 bytes）。
- IndexTTS 首次 GPU 初始化后新增约 5.2 GiB 辅助模型，当前可用空间约 17 GiB；
  后续生成必须保留严格磁盘护栏。
- 已清除可再生 `cache/pip`、`cache/xdg/uv`、`cache/wheelhouse`。
- 已删除已完成直连部署后不再需要的 `staging/model-source/LatentSync-1.6.tar`（5,147,801,600 bytes）。
- `staging/model-source`、任务 inbox/running/work/complete/failed 均为空；保留小型 Hugging Face 缓存和 LatentSync 约束/需求清单。

## OSS 限制

已有日志证明 `IndexTTS-2.5.tar` 恢复归档下载并校验成功（5,491,210,240 bytes）。
最终尝试列出 OSS 对象及删除已识别的 Qwen 临时对象时，OSS 服务返回
`UserDisable (403)`；因此本地/远端模型部署已完成，但“OSS 仅保留 IndexTTS 归档”
尚无法对外部对象存储做最终确认。凭据恢复后只需重新执行对象清单和精确删除，
不需要重新下载任何模型。
