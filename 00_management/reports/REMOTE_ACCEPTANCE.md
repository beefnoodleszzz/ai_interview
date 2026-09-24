# AutoDL 远端验收

日期：2026-09-23

## 部署基线（历史快照）

AutoDL 实例上的 H3、Qwen3-TTS、IndexTTS、LatentSync 和 SeedVR2 主环境与
主权重曾完成部署。模型均直接写入
`/root/autodl-tmp/ai_interview/models`，没有把同实例首次下载经 OSS 中转；运行目录
通过应用软链接或应用配置使用这些路径。真实 GPU Smoke 进一步发现 IndexTTS
还需要约 5.2 GiB 首次运行辅助模型；当时记录其已下载到运行目录。此报告仅证明模型运行
环境，不代表项目端到端编排已完成。

## 当前复核状态（2026-09-23）

首次 SSH 检查曾关闭/超时；随后只读盘点成功，发现 BigVGAN 权重缺失且有
126,312,448 字节 `.tmp` 残片。依照 IndexTTS 上游必需文件清单，在无卡状态下把残片
复制到隔离 staging 并续传；文件大小 449,228,171 字节、SHA-256
`e95ba25972d3de0628d99cd156e9315a9c018899bf739988959ebe3544080ced`（校验值见
[NVIDIA Hugging Face 文件清单](https://huggingface.co/nvidia/bigvgan_v2_22khz_80band_256x/blob/main/bigvgan_generator.pt)；权重准备逻辑见
[IndexTTS 上游代码](https://github.com/index-tts/index-tts/blob/ee40fa7d6c6b8a2c7f06105f9f1e65775b74868c/indextts/utils/model_download.py)）。
校验后原子安装至 `models/voice/IndexTTS-2.5/hf_cache/bigvgan/bigvgan_generator.pt`，
再删除已验证成功后的旧残片。

最终只读盘点：数据盘可用 12,683,173,888 bytes（约 11.81 GiB）；H3、voice、
voice_design 的 inbox/running/work 队列为空，完成/失败历史 job 保留；没有活动
模型/worker/下载进程、`.part/.partial/.tmp` 文件或断链。pip/uv/wheelhouse 为空，
Hugging Face 缓存约 1.6 MiB，staging 为空。host VoiceDesign r01 的失败 job 保留；
对应空 work 目录已清理。

IndexTTS `EP0001_golden_clip_voice_r01` 的 PID 已确认不存在，状态从陈旧
`RUNNING/loading_model` 修正为 `FAILED/orphaned_process`；job、state、输入的 host/guest
voice WAV 和空 partial work 均保留，GPU 时间为 0.0 分钟。项目级真实配音尚未完成。
`remote-doctor` 通过 SSH 并报告 `CONFIGURED_NO_GPU`；GPU、ComfyUI API 和 API 节点
检查未通过，符合无卡状态。本轮没有做语音或 H3 推理。没有其他模型下载或缓存清理
需求。OSS `UserDisable (403)` 仍未解决，因此 OSS 对象清单和归档清理状态未知。

## 已验证环境

| 组件 | Python 环境 | 验证结果 |
| --- | --- | --- |
| IndexTTS-2.5 | `apps/index-tts/.venv` | 主权重与辅助权重齐备；单模型 GPU smoke 曾通过；本次项目 job 停在加载阶段并留档 FAILED |
| Qwen3-TTS | `envs/qwen3-tts` | VoiceDesign GPU smoke 曾通过；host r02、guest r01 完成并拉回；host r01 失败 job 留档 |
| Whisper ASR | `envs/asr` | 独立 venv 安装 PASS；复用 LatentSync tiny checkpoint；尚未做项目级转写 job |
| LatentSync | `envs/latentsync` | 离线依赖导入成功；该次导入检查未挂载 GPU，尚未做真实推理 Smoke |
| H3 / ComfyUI | `/root/miniconda3` | 离线依赖检查后，已在 RTX 5090 实例启动并通过 ComfyUI/H3 doctor；尚未完成真实 Ref2VA 作业 |

系统 SoX 已补齐；`flash-attn` 仍为可选加速，不影响当前 SDPA 推理。RTX 5090 和
ComfyUI API 已启动并通过 H3 doctor。

## 运行模型路径

| 能力 | 运行路径 | 关键文件大小 |
| --- | --- | --- |
| IndexTTS-2.5 | `models/voice/IndexTTS-2.5` | `gpt.pth` 3,259,599,833；`codec.pth` 607,290,935；`s2mel.pth` 414,908,601 |
| IndexTTS auxiliary | `models/voice/IndexTTS-2.5/hf_cache` | BigVGAN generator 449,228,171 bytes; SHA-256 verified |
| Qwen3-TTS VoiceDesign | `models/voice/Qwen3-TTS-12Hz-1.7B-VoiceDesign` | 主权重 3,833,402,552；语音 tokenizer 682,293,092 |
| Qwen3-TTS Base | `models/voice/Qwen3-TTS-12Hz-1.7B-Base` | 主权重 3,857,413,744；语音 tokenizer 682,293,092 |
| LatentSync 1.6 | `models/lipsync/LatentSync-1.6` | UNet 5,072,222,488；Whisper tiny 75,572,083 |
| SeedVR2 | `models/upscale/SEEDVR2` | 主权重 3,391,544,696；VAE 501,324,814 |
| MiniMax H3 | `models/h3` | 五个既有权重均存在并生成 SHA-256 inventory |

ComfyUI 的 H3 模型和 SeedVR2 模型通过软链接指向上述运行树；IndexTTS 的
`apps/index-tts/checkpoints` 也已软链接到 `models/voice/IndexTTS-2.5`。IndexTTS
BigVGAN 辅助权重现已通过大小和 SHA-256 校验并落入 hf_cache。

## 追加无卡复核（2026-09-23）

最新只读审计仍显示无需下载模型：H3、IndexTTS、Qwen3-TTS Base/VoiceDesign、
LatentSync 1.6、SeedVR2 所需文件均在；LatentSync UNet/Whisper 文件大小通过，
SeedVR2 主权重和 VAE 官方 SHA-256 通过，两个 postprocess doctor 的 CLI 参数检查通过。
H3 `remote-doctor` 的 SSH、配置和静态模型/workflow 检查通过，并报告
`CONFIGURED_NO_GPU`；GPU/ComfyUI API 项失败是无卡状态下预期结果。未执行推理或提交任务。

审计时数据盘可用 12,682,887,168 bytes（约 11.81 GiB）。H3、voice、voice_design、
LatentSync、SeedVR2 的 inbox/running/work 队列均为空；既有 voice_design complete 和
failed 记录，以及 IndexTTS failed 记录均保留。没有活动生成/下载进程、模型/staging/cache
部分下载文件或断链。
额外检查发现没有残留 worker 上传归档；4 份版本回滚备份共约 112 KiB，staging 为 0。

系统级 `/root/.cache/pip`、`/root/.cache/uv`、`/root/miniconda3/pkgs` 分别约
0.94 GiB、0.16 GiB、1.06 GiB（合计约 2.15 GiB）。它们是可再生依赖缓存，不是残留
下载；当前保留以便恢复环境。项目 staging 与项目级 pip/uv/wheelhouse 为空；远端暂时
没有必须执行的额外下载或安全清理项。

项目 Worker/workflow 由本地 `scripts/deploy_remote_worker.py` 以 SHA-256 包部署，
替换前版本备份在 `worker/backups/`。本轮 `remote-doctor` SSH/configuration PASS，
worker 报告 `CONFIGURED_NO_GPU`；ComfyUI 仍绑定配置 `127.0.0.1:8188`。另行探测确认 LatentSync
`scripts/inference.py`、隔离环境、SeedVR2 节点存在。磁盘 `/dev/md0` 当前仅
约 11.81 GiB 可用（91% 使用）；此刻为 `CONFIGURED_NO_GPU`，推理前须由用户开卡并
重查空间、服务和预算。

## 磁盘与清理

- 持久盘 `/dev/md0`：首次 GPU Smoke 前总使用约 98 GiB、可用约 23 GiB
  （当时 inventory：23,719,505,920 bytes）。
- IndexTTS 首次 GPU 初始化后新增约 5.2 GiB 辅助模型；本轮另外完成 BigVGAN
  449,228,171 字节权重续传及完整性校验。
- 已清除项目级可再生 `cache/pip`、`cache/xdg/uv`、`cache/wheelhouse`；当前发现的系统级 pip/uv/Conda 缓存见“追加无卡复核”。
- 已删除已完成直连部署后不再需要的 `staging/model-source/LatentSync-1.6.tar`（5,147,801,600 bytes）。
- `staging` 为空；voice / voice_design 的 inbox/running/work 均已清空；历史
  complete/failed 任务和小型 Hugging Face 缓存保留。

## OSS 限制

已有日志证明 `IndexTTS-2.5.tar` 恢复归档下载并校验成功（5,491,210,240 bytes）。
最终尝试列出 OSS 对象及删除已识别的 Qwen 临时对象时，OSS 服务返回
`UserDisable (403)`；因此本地/远端模型部署已完成，但“OSS 仅保留 IndexTTS 归档”
尚无法对外部对象存储做最终确认。凭据恢复后只需重新执行对象清单和精确删除，
不需要重新下载任何模型。

## 最终复核（2026-09-23 05:31 UTC）

再次部署本地 worker 修订后，串行复核 `remote-doctor` PASS，H3 worker 为
`CONFIGURED_NO_GPU`；只读 `remote-audit` PASS。H3、TTS、VoiceDesign、LatentSync、SeedVR2
任务队列均为空（失败/完成历史保留），没有活动进程、部分下载或断链；磁盘剩余
12,682,727,424 bytes（约 11.81 GiB）。系统 pip/uv/Conda 缓存约 2.15 GiB，项目 Hugging Face
缓存约 1.60 MiB，staging 为空。

最新 14 文件 SHA-256 部署备份在 `worker/backups/deploy-20260923T052402Z`。部署后 LatentSync
与 SeedVR2 doctor 均为 `CONFIGURED_NO_GPU`，路径、依赖、CLI、文件大小检查通过；SeedVR2
模型与 VAE 官方 SHA-256 通过。H3 原子发布、Voice/VoiceDesign 和 postprocess 发布/submit 的
命令入口均已部署；无 GPU 任务被提交。无需额外下载模型，也没有确认可安全删除的其他文件；
保留系统依赖缓存、失败历史和部署回滚备份。OSS 对象清单仍受 `UserDisable (403)` 限制，
不能声称已审计 OSS。

## 本轮追加复核（2026-09-23）

只读 `remote-audit` 再次成功：数据盘空闲 `12,683,067,392` bytes（约 11.81 GiB）；
H3、IndexTTS、VoiceDesign、LatentSync、SeedVR2 所需权重均在，未发现部分下载、断链或
活动任务/下载进程。完成/失败 VoiceDesign 记录和 IndexTTS 失败记录仍在，未改动。结论是
当前没有需要补下的模型。

系统 pip、uv、Conda 包缓存分别为 `1,007,140,864`、`170,897,408`、`1,134,825,472`
bytes，合计约 2.15 GiB；它们可重建，若清除可回收此空间，但也会失去快速恢复依赖的缓存。
Qwen3-TTS Base 模型目录约 `4,544,229,700` bytes（4.23 GiB）；当前 VoiceDesign 与 IndexTTS
worker 没有引用它，但部署和 OSS staging 脚本仍包含 Base，所以记录为待收窄部署范围的候选，
不把它判定为孤儿文件。此次没有删除远端内容。

随后一条自定义细项 SSH 探测曾在 banner exchange 阶段超时；再连接成功后，`remote-audit`、
H3 `remote-doctor`、LatentSync 与 SeedVR2 `postprocess-doctor` 均通过。两个 postprocess
doctor 都报告 `CONFIGURED_NO_GPU`，模型文件/CLI 检查通过；H3 的模型、workflow、protocol、native
audio 检查通过，GPU/API/node 检查因无卡未通过。Conda `clean --all --dry-run`（只预览）报告
可清 tarball 155.8 MB、package 176.0 MB 和 1 个 index cache（工具未报告其大小）；没有实际执行
清理。Qwen3-TTS Base 目录约 4.3 GB；保留，因为远端规则仍声明支持 base cloning，当前生产 Worker
没有引用它。此次没有模型下载或远端删除。OSS API 仍返回 `UserDisable (403)`，OSS 对象空间不在本次
已验证范围内。

## 最终部署后验收（2026-09-23）

部署脚本完成 SHA-256 校验并发布 14 个 worker/workflow 文件，旧版本备份于
`worker/backups/deploy-20260923T062251Z`。串行无卡 doctor 结果：H3 worker、IndexTTS、
Qwen3-TTS VoiceDesign、LatentSync、SeedVR2 均报告 `CONFIGURED_NO_GPU`；各自模型、运行环境、
协议/workflow、CLI 与 SeedVR2 官方模型/VAE SHA 检查通过。H3 的 GPU、ComfyUI API 和节点检查
因实例当前无卡/ComfyUI 未启动而不通过，符合当前配置状态。全程没有提交推理任务。

部署后只读 `remote-audit` PASS：磁盘剩余 `12,682,649,600` bytes（约 11.81 GiB）；
没有 inbox/running/work 任务、活动生成或下载进程、部分下载文件、断链。既有 IndexTTS
失败记录及 VoiceDesign 完成/失败历史保留。模型权重齐备，无需额外下载。Conda dry-run 仅
显示最多约 332 MB 可再生包/索引缓存候选；未清理。系统 pip/uv 缓存和 Qwen3-TTS Base 也保留，
避免改变可恢复环境或已声明的 VoiceDesign/base cloning 配置。OSS 仍因 `UserDisable (403)` 无法盘点。

## 最终代码部署与无卡复核（2026-09-23 06:56 UTC）

预算/请求身份修订已部署。首轮 SCP 超时留下的唯一临时包
`worker/.upload-20260923T064707Z.tar.gz`（27,278 bytes）已确认是本次失败上传文件并移除；
部署脚本改为 SSH stdin 传输后通过 SHA-256 校验发布 14 个 worker/workflow 文件，旧版备份在
`worker/backups/deploy-20260923T065655Z`。没有覆盖 job 历史或模型。

部署后串行检查全部通过：H3 `remote-doctor`、IndexTTS/ASR `voice-doctor`、Qwen3-TTS
`voice-design-doctor`、LatentSync 与 SeedVR2 `postprocess-doctor` 均成功。各组件状态均为
`CONFIGURED_NO_GPU`；所需模型/运行环境/CLI/import 检查通过，SeedVR2 模型和 VAE 官方 SHA-256
校验通过。H3 GPU、ComfyUI API 与节点项因无卡而不可用，符合当前状态。

最终只读 `remote-audit` PASS：磁盘剩余 `12,682,571,776` bytes（约 11.81 GiB）；所有 H3、
voice、VoiceDesign、LatentSync、SeedVR2 队列均无 inbox/running/work 项，无活动生成/下载进程、
部分模型下载、断链或部署临时包。既有 IndexTTS 失败记录与 VoiceDesign 成功/失败记录仍保留。
模型完整，无需补下；可再生系统缓存约 2.15 GiB 和 Qwen3-TTS Base 仍保留。OSS 对象清单继续受
`UserDisable (403)` 限制。没有启动 GPU、提交推理或删除任何任务/生成结果。
