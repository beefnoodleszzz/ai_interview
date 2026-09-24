# EP0001 无卡阶段交接清单

日期：2026-09-23。以 `episode.yaml` 为状态真源；本页记录可复查的人工与环境门槛。后续逐步操作按[持续生产操作手册](../../docs/how-to/production-runbook.md)。`PENDING` 不能解读为验收通过。

## 已完成的本地工作

- [x] 五镜头台本和表演/剪辑稿：[`00_script/GOLDEN_CLIP_SCRIPT.md`](00_script/GOLDEN_CLIP_SCRIPT.md)，逐字台词与 Manifest 的 B01–B04 一致。
- [x] Host 01 / Guest 01 Voice Reference 已由项目所有者选择并冻结；本地 WAV 存在，SHA 与 Manifest 一致。
- [x] IndexTTS 项目任务 `voice_jobs/EP0001_golden_clip_voice_r02` 已在本地准备；四条台词和两条参考 WAV 的 SHA 均核对通过，尚未提交。
- [x] Host r01–r04、Guest r01、Studio r01 视觉候选保留在 `characters/` 与 `sets/` 的 `reference_candidates/`；Host r04 已由所有者确认并复制为 `02_refs/host_identity.png`。Host studio close-up r01 已批准并复制为 `02_refs/studio_host.png`；Guest studio close-up 与 Studio 宽景仍待审核。
- [x] S002 主持人无声反应 start_r04 / end_r04 已由项目所有者批准并复制为正式首尾帧 `02_refs/host_reaction_start.png` / `02_refs/host_reaction_end.png`；同为 941×1672，紧面部起始聆听与结束抬眉压笑差异清晰。相似度不足的旧版本已标记作废并保留。
- [x] 五个 H3 Prompt 草案位于 [`03_h3_prompts/drafts/`](03_h3_prompts/drafts/S001.txt)，保留逐字中文台词、固定 S1/S2 说话人 ID 和官方段落顺序；未作为最终不可变 Prompt 使用。
- [x] 本地 doctor、Manifest 结构校验、草案 Prompt 结构校验与项目测试完成，具体命令和结果记录于本轮交接。无卡远端审计只证明配置与盘面状态。
- [x] 后处理结果的本地验收入口已补齐：`select-postprocess` 复核导入结果、QC、任务输入与 edit lock；获批的 LatentSync 结果可进入待剪辑状态，获批的 SeedVR2 结果可进入 FFmpeg 粗剪。真实模型输出仍待有卡 smoke。
- [x] `scripts/local_asset_snapshot.py` 已把不可再生输入归档到本机项目外目录，写 SHA 清单，并在独立临时目录逐文件恢复核验；2026-09-23 新增 Host r04 后再次快照，归档清单包含 PNG 与 JSON 且恢复检查 PASS。该本机快照不算异地备份，异地状态仍为 `UNVERIFIED`。

## 无卡仍需人工完成的门槛

1. **导演视觉审核：** 按 [`VISUAL_REVIEW.md`](VISUAL_REVIEW.md) 完成剩余 Guest 演播室近景和 Studio 宽景审核。Host/Guest 身份图、Host 演播室近景和 S002 reaction 起止帧已批准；左右轴与跨镜头连续性仍需结合 Guest 近景和 Studio 宽景确认。不要把剩余待审资产当作已通过。
2. **Prompt 创作审核：** 对照正式视觉资产和台本审五个草案。真实 Audio Master 的时长与停顿确定后，再复制为 `03_h3_prompts/S###.txt` 并记录 SHA；最终文件不能由 Python 改写。
3. **Room tone 来源计划：** 固定演播室是虚构场景，优先在有卡后从获批 H3 原生环境音提取匹配的安静段，或使用有授权的匹配录音。选定 WAV 后审听无语音、音乐或突兀噪声，记录来源与哈希并填 `studio.room_tone_reference`。无卡阶段只确认方案，不以合成静音冒充成片房间声。
4. **异地备份：** 将 Bible、正式视觉参考、声音库、Manifest、最终 Prompt、workflow 和导演记录送至独立于本机/AutoDL 的目的地，做 SHA 清单和独立目录恢复核验。当前没有可验证的异地恢复证明；OSS 对象清单受 `UserDisable (403)` 阻断。

这些步骤包含人的选择与外部备份目的地；记录决定后才能把对应 `PENDING` 改为 `PASS`。声音参考已获选不等于视觉获批，也不等于最终台词表演通过。

## 有卡后严格按此顺序继续

1. 开卡和提交成本获得授权后，重查远端磁盘、队列和 `voice-doctor` 的 `READY`。提交现有 `EP0001_golden_clip_voice_r02`，查状态、拉回 IndexTTS 原始 WAV 与 Whisper ASR 元数据；失败创建新 revision。
2. 导演逐 Beat 选 Take，构建 48 kHz Audio Master；核对每镜头对白加停顿能放入计划剪辑时长。生成 SRT。room tone edit mix 等选定环境音来源后再做。
3. 完成视觉/Prompt 门槛后，逐镜头设为 `READY_FOR_H3`、执行 `package-h3`，校验 Prompt 字节与全部输入 SHA。远端 H3 doctor 为 `READY` 才提交。先做小样，再完成五镜头黄金片段。
4. 拉回并按确定性 QC → 代表帧 → 导演选片的顺序审核。必要时仅对 `NEEDS_LIPSYNC` 做 LatentSync；从获批原生环境音建立 room tone edit mix，镜头全部通过后锁定剪辑并做粗剪。SeedVR2 仅在 edit lock 后按需测试。
5. 导演签核身份、Studio、声音、嘴型、Reaction、room tone、硬切和手机竖屏观感；最终 QC 通过后才开放 70–100 秒完整集。

## 当前状态不得跨越的边界

- Manifest `validate` 的 PASS 只表示结构合法。当前 `01_audio/master/`、正式视觉 `02_refs/`、最终 `03_h3_prompts/S###.txt` 和 H3 视频结果尚不齐全，不能 `package-h3` 或声称黄金片段通过。
- EP0001 四个对白镜头目前在 Manifest 标成 `NEEDS_RETRY`，对应先前失败的 IndexTTS job；目前没有 H3 尝试记录。提交新的 voice revision 后按真实远端状态推进，不能把此状态误记为 H3 已失败。S002 仍是 `PLANNED`。
- 未启动 GPU、未提交本轮 voice_r02 或 H3 作业。Beads 的真实 GPU smoke、后半流程和黄金片段事项保持开放。
