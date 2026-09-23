# 如何在本地推进一集访谈

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
6. 提交并查询 Qwen VoiceDesign：`uv run ai-interview submit-voice-design <voice_design_jobs/<job_id>>`、`uv run ai-interview voice-design-status EP####_slug --job-id <job_id>`。下载：`uv run ai-interview pull-voice-design <job_id> episodes/EP####_slug/voice_design_results/<job_id>`。
7. 听审候选后固化：`uv run ai-interview freeze-voice-reference episodes/EP####_slug/episode.yaml host episodes/EP####_slug/voice_design_results/<job_id>/<candidate>.json --selected-by <reviewer>`。此操作写入不可覆盖、带哈希的 48 kHz 单声道 Voice Reference。
8. 生成 IndexTTS-2.5 远程批处理包：`uv run ai-interview package-voice episodes/EP####_slug/episode.yaml`。AutoDL 上单独安装 ASR 环境：`bash scripts/remote_install_asr.sh`；它使用现有 Whisper tiny 权重，不会下载模型。
9. 提交、查询并拉回 voice job：`submit-voice`、`voice-status`、`pull-voice`。拉回会核对每条 WAV、metadata 与 SHA-256；ASR/CER 只作文字 QC。
10. 导演逐条试听并运行 `select-take` 记录选择，再运行 `build-audio-master` 生成 48 kHz Dialogue Master 和 `subtitles` 生成字幕。
11. Manifest 的 `studio.room_tone_reference` 指向真实 Studio room-tone WAV。运行 `build-edit-mix`，为对白镜头和 Reaction 镜头生成含连续 room tone 的 48 kHz 编辑音轨。
12. 使用官方 H3 结构编写最终 Prompt；Python 不改写它。打包：`package-h3 episodes/EP####_slug/episode.yaml S001`。GPU 在线时通过 `autodl-handoff.md` 提交和拉回。
13. 拉回时执行确定性 QC；也可运行 `qc-media <candidate.mp4>`。人工选片记录 LipSync A/B/C/D；完成音轨和选片后运行 `lock-edit`，再生成 hard-cut 粗剪：`roughcut <episode.yaml> <roughcut.mp4> --execute`。
14. 最终片运行 `qc-final <master.mp4> --duration <seconds> --fps 24`。片段需人工逐帧检查嘴型、字幕、听感、角色/场景连续性和平台 AI 标识。

预期结果：远程只收到自包含、带 SHA-256 的 Job；本地保留全部创作真源和结果选择记录。
