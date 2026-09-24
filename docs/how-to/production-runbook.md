# AI 访谈持续生产操作手册

适用范围：从 15–20 秒黄金片段到后续每集 70–100 秒成片。操作者在本地 Mac 管理创作、状态和交付；AutoDL 只执行 GPU 推理。以 `episodes/<episode_id>/episode.yaml` 为每集唯一生产状态；本手册是操作顺序，技术约束以 [`AGENTS.md`](../../AGENTS.md) 和[需求基线](../../00_management/requirements/AI_Interview_Production_Pipeline_H3_ComfyUI_AutoDL_v1_1.md) 为准。

本手册里的 `EP_DIR` 表示实际集目录，例如 `episodes/EP0001_golden_clip`。所有命令从仓库根目录运行。命令中的 `<job_id>`、`<candidate.json>`、`<name>` 和路径占位符须换成实际值。每次执行前看 `bd ready` 和 Manifest，不用 Beads 代替 Manifest。

## 0. 判定当前运行模式

| 模式 | 可以做 | 不得据此宣称 |
| --- | --- | --- |
| 本地、AutoDL 无卡 | 台本、Bible、参考候选、镜头设计、Prompt 草案、Manifest、VoiceDesign/IndexTTS 本地任务包、静态校验、只读远端盘点、备份恢复演练 | TTS、ASR、H3、LipSync、超分或成片已经实测 |
| AutoDL 有卡且任务提交已获授权 | 分组件 doctor 达到 `READY` 后，按预算提交推理、拉回、质检和导演选择 | 单模型 smoke 等于整集通过 |

`CONFIGURED_NO_GPU` 只证明配置检查通过。即使有卡，提交前仍检查剩余磁盘、队列、预算、参考授权和任务 revision。不得让 ComfyUI 对公网监听，服务只绑定远端 `127.0.0.1:8188`。本地不安装或调用生成模型，包括现有本地 TTS。

## 1. 每集开始：台本与 Manifest（本地、无卡）

1. 对照 Character/Studio Bible 写短台本：3 秒内钩子、人物动机、主持人真正追问、结尾补刀。明确每个 Beat 的逐字台词、情绪、停顿和全局稳定的 `(Sx)` 说话人 ID。任何台词变更先改 Manifest，再改相关 Prompt 草案；已提交的 Job 不覆盖，使用新 revision。
2. 黄金片段用 `episodes/EP0001_golden_clip`；以后从 `episodes/_template` 复制为新的 `EP####_slug`，避免复制旧集的 `voice_jobs`、运行状态和已选结果。按 9:16、24 fps 分 4–15 秒生成镜头，分别填写 `edit_duration_sec` 与合法 `generation_duration_sec`。
3. 确认角色声音来源有授权；Character/Studio Bible、固定机位、视线轴、衣着、麦克风和房间材质一致。候选图只供审核，导演批准后才复制为版本化正式参考，并记录来源与 SHA-256。不要把待审图伪装成已批准 Reference Pack。
4. 运行：

   ```bash
   bd ready
   uv run ai-interview doctor
   uv run ai-interview validate episodes/EP####_slug/episode.yaml
   uv run ai-interview budget episodes/EP####_slug/episode.yaml
   ```

5. 核对 Manifest 引用文件存在、角色 voice reference 哈希正确、每个镜头机位和剪辑时长与台本一致。`validate` 只验证结构；引用资产在 `package-h3` 时才逐项检查。记录缺失文件、待审资产与下一步，不把结构 PASS 写成开拍就绪。

**阶段产物：** 台本、Bible/视觉参考候选、Episode Manifest、Shot 表、缺项清单。**放行条件：** 台词与镜头计划可审、Manifest 结构通过；正式视觉 Reference Pack 由导演批准。

## 2. 声音设计与主力配音

### 2.1 声音设计（已有冻结声音可跳过）

1. 在 Manifest 中填写 `voice_design.description`、50 字以上 `sample_text` 和语言。本地打包：

   ```bash
   uv run ai-interview package-voice-design episodes/EP####_slug/episode.yaml host
   uv run ai-interview package-voice-design episodes/EP####_slug/episode.yaml guest
   ```

2. 无卡时只检查 `voice-design-doctor` 的配置状态并保存任务包。有卡、获授权且 doctor 为 `READY` 后，对每个 `voice_design_jobs/<job_id>` 依次运行 `submit-voice-design`、`voice-design-status`、`pull-voice-design`。相同 `job_id` 先查询，不盲目重复提交。
3. 导演听审原始 WAV 的身份、情绪、清晰度和自然停顿。选择候选后运行 `freeze-voice-reference <episode.yaml> <character_id> <candidate.json> --selected-by <name>`；核对冻结 WAV 的 48 kHz、单声道、SHA-256 和版本号。未被选中的 WAV 与失败记录保留。

### 2.2 IndexTTS、ASR 与 Audio Master

1. 已冻结 voice reference 后，本地执行 `uv run ai-interview package-voice <episode.yaml>`。检查生成的 `voice_jobs/<job_id>/job.json`、逐 Beat 文本、角色 SHA 和预算。无卡时到此停止；不得用本地模型或未审核的声音替代。
2. 有卡并获授权后，`voice-doctor` 必须为 `READY`。按 `submit-voice <voice_jobs/<job_id>>` → `voice-status <episode_id> --job-id <job_id>` → `pull-voice <job_id> <episode_dir>/voice_results/<job_id>` 执行。确认原始 WAV、元数据、ASR 文本与哈希齐全；CER 只检查文字，不评判表演。
3. 导演逐 Beat 听审并执行 `select-take <episode.yaml> <B##> <candidate.json> --selected-by <name>`。全部通过后执行 `build-audio-master <episode.yaml>`，得到保留原始文件的 48 kHz Dialogue Master。测量每个 Shot Master 的真实时长；若超出剪辑/生成时长，调整台本、停顿、镜头计划和 Prompt，再开启新 revision。
4. 执行 `subtitles <episode.yaml> --output <episode_dir>/07_edit/<episode_id>.srt`，逐字核对字幕时间与错字。room tone 可从后续获批 H3 镜头的原生环境音提取并审听，也可使用已有且获授权的匹配录音；选定后填 `studio.room_tone_reference`，执行 `build-edit-mix <episode.yaml>`。对白与无声 Reaction 都必须有连续 room tone；无合格来源时不以合成静音冒充。

**阶段产物：** 原始 Take、ASR 元数据、人工选 Take 记录、48 kHz Master、room tone edit mix 和 SRT。**放行条件：** 声音人格、台词、停顿、时长与听感由导演通过，不能只看 ASR/CER。

## 3. H3 镜头（有卡之前可完成草案与视觉审核）

1. 根据获批 Reference Pack、真实 Audio Master 和实际时长，用 `h3-prompt-writing` 结构定稿每镜头 Prompt。Ref2VA 的六段顺序为 `subject_definitions`、`summary`、`retention_analysis`、`detailed_description`、`overall_soundscape`、`non_diegetic_music`；FL2VA 的三段顺序为 `integrated_multimodal_description`、`overall_soundscape`、`non_diegetic_music`。正文英文，中文台词逐字放入 `<d>[Chinese] ...</d>`；说话人 ID 跨镜头固定。草案可迭代，复制到 Manifest 所指最终路径后按不可变输入管理。
2. 将正式图像、关键帧和 Master 放到 Manifest 声明的路径。Ref2VA 至少有一张图片或一段视频，不能只有音频；音视频数目、合计时长和格式须符合契约。对白优先用选定 Audio Master，不用未批准 Take。
3. 每镜头状态到 `READY_FOR_H3` 后，本地执行 `package-h3 <episode.yaml> <S###>`。核对 `prompt.txt` 与最终文件字节和 SHA 一致、输入哈希齐全、`job_id` 为当前 revision。无卡时只保留本地包。
4. 有卡并获授权后，`remote-doctor` 必须为 `READY`，再运行 `submit-h3 <remote_jobs/<job_id>>` → `h3-status <episode_id> --shot-id <S###>` → `pull-h3 <remote_jobs/<job_id>/job.json> <episode_dir>/remote_results/<job_id>`。断线或超时先查询远端状态和 ComfyUI history；同一 `job_id` 不覆盖失败或不同内容。
5. 导入先核验 SHA、ffprobe、完整解码、黑帧/冻结/静音报告，再做 contact sheet、代表帧审查和导演看片。`qc-media <candidate.mp4>` 的事件需结合镜头语义判断。用 `select-video <episode.yaml> <S###> <candidate.json> --selected-by <name> --lip-grade A|B|C|D` 留下人工判断。身份、表演或构图失败应新 revision 重生；局部嘴型 C 才考虑 LatentSync。

**阶段产物：** 原始 H3 候选、元数据、确定性质检、人工选片记录。**放行条件：** 每镜头达到 `READY_FOR_EDIT`，没有未解释的身份/场景/嘴型问题。单镜头最多 4 次、15 GPU 分钟，整集最多 300 GPU 分钟；触顶进人工审核。

## 4. 修复、剪辑与交付

1. 仅对 `NEEDS_LIPSYNC` 的已选镜头执行 `package-latentsync <episode.yaml> <S###> --selected-by <name>`。有卡且 `postprocess-doctor --mode latentsync` 为 `READY` 后，使用 `submit-postprocess`、`postprocess-status`、`pull-postprocess`。导入目录必须为 `<episode_root>/postprocess_results/latentsync/<job_id>`；导演审核通过后执行 `select-postprocess <episode.yaml> <S###> <result_dir> --selected-by <name> --lip-grade A|B`，通过本地结果、哈希、QC 与原始输入核验后才进入 `READY_FOR_EDIT`。身份或表演错误不得用嘴型工具掩盖。
2. 从获批原生环境音或授权录音中选定 room tone，制成适配的连续 48 kHz WAV，审听并记录来源与 SHA；执行 `build-edit-mix <episode.yaml>`。所有镜头的选片、Master 和 edit mix 齐全且状态为 `READY_FOR_EDIT` 后，运行 `lock-edit <episode.yaml> --selected-by <name>`。只在锁定后执行 `roughcut <episode.yaml> <episode_dir>/07_edit/<name>.mp4 --execute`。播放器检查硬切节奏、Room tone 连续性、字幕和手机竖屏观看效果。CLI 当前不会替操作者强制 roughcut 的 edit lock，因此此项必须人工核对。
3. 需要评估超分时，在 edit lock 后对目标镜头 `package-seedvr2 <episode.yaml> <S###> --selected-by <name>`；有卡、`postprocess-doctor --mode seedvr2` 为 `READY` 后提交、查询、拉回和质检。导入目录必须为 `<episode_root>/postprocess_results/seedvr2/<job_id>`；导演认可后执行 `select-postprocess <episode.yaml> <S###> <result_dir> --selected-by <name>`。通过原片与 edit lock 哈希核验后记录 `upscaled_video`，后续 `roughcut` 采用该文件并再次验证锁定资产与超分哈希。最终输出仍需完整 QC 和导演验收。
4. 最终文件放入 `<episode_dir>/09_final/`，执行 `qc-final <master.mp4> --duration <实际目标秒数> --fps 24`。导演按 `review.py` 的 Final Checklist 逐项写 PASS 与证据说明，再运行 `approve-final <episode.yaml> <master.mp4> --duration <秒数> --fps 24 --checklist <checklist.json> --selected-by <name>`。最后核对成片 SHA、手机全屏、字幕、声音、平台 AI 标识和权利。
5. 远端结果清理只能在本地导入、结果 SHA 与 QC sidecar 验证后，用对应 `cleanup-h3` 或 `cleanup-postprocess` 精确执行。失败 Job 保留；不要清空整个远端队列。

**阶段产物：** edit lock、粗剪、可选修复/超分、最终成片、Final QC 与人工签核记录。**放行条件：** 程序检查和导演检查全部完成。

## 5. 黄金片段与常态化生产门槛

EP0001 的 15–20 秒黄金片段须同时通过身份、Studio、声音人格、嘴型、Reaction timing、room tone 和切镜节奏；形成导演验收记录后才允许 70–100 秒正式集进入 H3 生产。当前代码没有对后续集自动读取黄金片段验收记录，操作者必须在 `bd` 与制作报告中检查此门槛。

后续每集重复第 1–4 节；沿用获批且版本冻结的角色/Studio/声音 Reference Pack，新嘉宾或新机位单独审核。每集保留 Manifest、台本、Prompt、Job revision、原始结果、导演选片记录、预算、最终 QC 和实际成本。月度复盘重点看表演、镜头接受率、失败原因和成本，变更模板先做小样验证，不直接改已锁定的生产输入。

## 6. 中断恢复、备份与交接

- **SSH/Worker 超时：** 先运行相应 `*-status` 和只读 `remote-audit`；确认 `job_id`、远端位置与哈希，再决定恢复。不要直接重复提交或删除 partial job。
- **失败结果：** 保存原 Job、日志、候选与原因，更新 Beads 和 Manifest；更改输入或重试时创建新 revision，先看剩余预算。
- **磁盘不足：** 暂停新任务，盘点远端空间和已有结果；只清理经过本地导入及哈希证明的完成任务。模型与失败历史不按空间压力盲删。
- **不可再生资产：** 定期将 Bible、正式视觉/声音参考、Manifest、最终 Prompt、版本化 workflow、脚本、Benchmark 与导演记录备份到独立于本机和 AutoDL 数据盘的位置。用 `python3 scripts/local_asset_snapshot.py --destination <备份目的地>` 生成归档、SHA 清单并做独立临时目录恢复演练。脚本输出 `restore_check: PASS` 只证明该归档可在本机恢复；还须核验异地目的地确实收到相同 SHA。仅本机复制或远端持久盘不算异地备份。OSS 不可用时记录阻塞并选定新的独立目的地，不声称已完成。
- **交接记录：** 每次停工写明当前 Episode/Shot 状态、最近 Job ID、已验证文件及 SHA、失败或待审项、GPU 已用/预留、远端队列/磁盘、下一个具体动作。Beads 只在验收标准实证通过后关闭。

## 7. EP0001 已验收经验与后续入口

EP0001 黄金片段 v3 已于 2026-09-25 获所有者确认通过；原始任务、失败历史和早期交接文件保留为历史，不再作为当前待办。后续集先读项目 skill [`ai-interview-production`](../../.agents/skills/ai-interview-production/SKILL.md)，再从本手册第 1 节开始。尤其在锁定剪辑前核对 H3 候选内嵌音频与选定 WAV 的波形偏移，在交付前实际播放字幕版，避免重复 EP0001 的 S003 口型和双层字幕问题。正式 Final Checklist、异地备份和可选后处理仍按各自证据单独记录。
