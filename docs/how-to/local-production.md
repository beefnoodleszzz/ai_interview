# 如何在本地推进一集访谈

完整的无卡准备、有卡执行、验收与恢复顺序见[持续生产操作手册](production-runbook.md)。本页保留 CLI 快速步骤。

## 前提

- 在项目根目录执行命令。
- 已运行 `uv sync`。
- AutoDL 的 voice/H3 Worker 连接信息由环境变量提供；本地没有 TTS 模型依赖。

## 步骤

1. 查看任务：`bd ready`。
2. 复制 `episodes/_template` 为新的 `EP####_slug`，填写 `episode.yaml`。
3. 运行本地预检：`uv run ai-interview doctor`。
4. 验证 Manifest：`uv run ai-interview validate episodes/EP####_slug/episode.yaml`。
5. 为原创角色定义 `characters.<id>.voice_design` 的 `description`、50 字以上 `sample_text` 与 `language`。建立 Qwen 包：`uv run ai-interview package-voice-design episodes/EP####_slug/episode.yaml host`。
6. 开卡前分别运行 `uv run ai-interview voice-design-doctor` 与 `uv run ai-interview voice-doctor`，应报告配置就绪的 `CONFIGURED_NO_GPU`。提交 Qwen VoiceDesign：`uv run ai-interview submit-voice-design <voice_design_jobs/<job_id>>`、`uv run ai-interview voice-design-status EP####_slug --job-id <job_id>`。提交时原子预留 voice-design episode 预算；下载：`uv run ai-interview pull-voice-design <job_id> episodes/EP####_slug/voice_design_results/<job_id>`。
7. 听审候选后固化：`uv run ai-interview freeze-voice-reference episodes/EP####_slug/episode.yaml host episodes/EP####_slug/voice_design_results/<job_id>/<candidate>.json --selected-by <reviewer>`。此操作写入不可覆盖、带哈希的 48 kHz 单声道 Voice Reference。
8. 生成 IndexTTS-2.5 远程批处理包：`uv run ai-interview package-voice episodes/EP####_slug/episode.yaml`。AutoDL 上单独安装 ASR 环境：`bash scripts/remote_install_asr.sh`；它使用现有 Whisper tiny 权重，不会下载模型。
9. 提交、查询并拉回 voice job：`submit-voice`、`voice-status`、`pull-voice`。提交前会在 Episode Manifest 锁内扣除 H3、后处理、其他 voice job 的活动预留，并按 shot/episode 上限原子预留。拉回会核对每条 WAV、metadata 与 SHA-256；ASR/CER 只作文字 QC。
10. 导演逐条试听并运行 `select-take` 记录选择，再运行 `build-audio-master` 生成 48 kHz Dialogue Master 和 `subtitles` 生成字幕。
11. 如已有获批且与演播室匹配的 room-tone WAV，可填 `studio.room_tone_reference` 并提前运行 `build-edit-mix`。如需从 H3 原生环境音提取，则待镜头拉回并审听后再做；无卡时不以合成静音替代。
12. 使用官方 H3 结构编写最终 Prompt；Python 不改写它。打包：`package-h3 episodes/EP####_slug/episode.yaml S001`。GPU 在线时通过 `autodl-handoff.md` 提交和拉回。
13. 拉回时执行确定性 QC；也可运行 `qc-media <candidate.mp4>`。人工选片记录 LipSync A/B/C/D；确认 room tone、完成 `build-edit-mix` 和选片后运行 `lock-edit`，再生成 hard-cut 粗剪：`roughcut <episode.yaml> <roughcut.mp4> --execute`。
14. 最终片运行 `qc-final <master.mp4> --duration <seconds> --fps 24`。片段需人工逐帧检查嘴型、字幕、听感、角色/场景连续性和平台 AI 标识。

预期结果：远程只收到自包含、带 SHA-256 的 Job；本地保留全部创作真源和结果选择记录。
