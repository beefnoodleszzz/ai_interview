# EP0001 视觉候选导演审核单

状态：`PARTIAL_REVIEW`。项目所有者已确认 Guest r01 身份图、Host r04 身份图、Host 演播室近景 r01，以及 S002 reaction start/end r04；正式参考与候选 SHA 均已记录。Host r01 已否决；r02 发型不符；r03 保留。Guest 演播室近景和 Studio 宽景仍待审核。三张获批图用途不同：Host 对白近景含桌面/麦克风；S002 起始帧为紧面部聆听特写；结束帧为同机位抬眉、闭嘴压笑。不能据此标记黄金片段通过。

| 用途 | 候选 | 审核重点 |
| --- | --- | --- |
| 主持人身份 | [Host r04](../../characters/host_v001/reference_candidates/host_visual_concept_r04.png) / [正式身份图](02_refs/host_identity.png) | 2026-09-23 项目所有者已确认；SHA 见 `02_refs/host_identity.json` |
| 嘉宾身份 | [Guest r01](02_refs/guest_identity.png) | 2026-09-23 项目所有者已确认身份肖像；仍须审视频嘴型和演播室近景连续性 |
| 固定演播室 | [Studio r01](../../sets/studio_v001/reference_candidates/studio_wide_concept_r01.png) | 嘉宾左、主持人右；桌、两支悬臂麦克风、灯和背景轴线 |
| 主持人演播室近景 | [正式参考](02_refs/studio_host.png) / [候选原件](02_refs/studio_candidates/host_closeup_r01.png) | 项目所有者 2026-09-23 批准；正式文件 SHA 见 `02_refs/studio_host.json` |
| 嘉宾演播室近景 | [Guest studio close-up r01](02_refs/studio_candidates/guest_closeup_r01.png) | 与嘉宾身份图同一面孔；麦克风不挡嘴、看向右侧主持人；与 Studio 宽景匹配 |
| S002 起始帧 | [正式参考](02_refs/host_reaction_start.png) / [候选原件](02_refs/reaction_candidates/host_reaction_start_r04.png) | 项目所有者 2026-09-23 批准；紧面部聆听特写、闭嘴、看向画面左侧；941×1672；SHA 见正式 JSON |
| S002 结束帧 | [正式参考](02_refs/host_reaction_end.png) / [候选原件](02_refs/reaction_candidates/host_reaction_end_r04.png) | 项目所有者 2026-09-23 批准；同为 941×1672，单侧抬眉与闭嘴压笑清晰可辨；SHA 见正式 JSON |

## 待签核项目

- [x] Guest r01 身份肖像由项目所有者确认；正式文件 `02_refs/guest_identity.png` 的 SHA 见同名 JSON。
- [x] Host r04 身份由项目所有者确认；正式文件 `02_refs/host_identity.png` 的 SHA 见同名 JSON。后续表演和场景连续性仍须审核。
- [x] Host 演播室近景 r01 由项目所有者批准；正式文件 `02_refs/studio_host.png`，SHA 见同名 JSON。
- [ ] Studio 左右轴、近景背景与麦克风的位置能在五个镜头中保持一致。
- [ ] 嘉宾演播室近景与嘉宾身份图、Studio 宽景的脸部和空间连续性通过。
- [x] S002 Reaction start_r04 / end_r04 由项目所有者批准；同一身份与紧面部机位，均闭嘴且同为 941×1672；正式文件和 SHA 已记录。
- [ ] 记录其余待审资产：Studio 宽景和 Guest 演播室近景；全套仍缺这两项，不能放行 Reference Pack。

审核决定：**部分通过**。已批准的资产均保留候选原件并以无覆盖方式复制到正式路径；Guest 演播室近景及 Studio 宽景待审。

后续生成图片按批次制作对照拼图，由 Agent 先做整批视觉审核与筛选，再一次性提交需要项目所有者决定的整批结果。

## 2026-09-24 剩余两项整批预审

对照图：[Studio + Guest batch r01](02_refs/studio_candidates/studio_guest_batch_review_r01.png)（2400×2360，SHA-256 `2892f43da66c3c7894da9df71714be275fa6071d7f2315df67be0bd3ab534a96`）。原始候选文件保留，未复制到正式参考路径。

- Studio 宽景 r01：1672×941，SHA-256 `d41fbfba56c1d2b10119784013bad5e8ea67bfbd4b5bae24ded54dd08f28d185`。空场包含左右两座、两支朝各自座位的悬臂麦克风、胡桃木桌、炭灰吸音板与暖木条。画面左座可分配 Guest，右座可分配 Host；它是空间参考，不是 EP0001 的生成镜头。
- Guest 近景 r01：941×1672，SHA-256 `d6eb1afb6cf6058d82691ccfade5043d2782f47e92c1d02eaab26ff19182e3df`。脸部轮廓、橄榄灰肤色、耳机与深石板色服装接近已批准 Guest 身份图；双唇清楚且未被麦克风遮挡；视线朝画面右侧，麦克风由右侧入画，与 Host 已批准近景的反向视线/入画方向配对。
- 整批连续性：两张近景均有炭灰吸音板、暖木条、暖灯、深胡桃木桌与黑色麦克风；两人服装保持各自身份图。宽景左右轴和近景眼线没有明显冲突。宽景为空场、近景背景取景较紧，灯与木条的精确空间位置仍需项目所有者按对照图判断；后续 H3 回片还须检验实际布景连续性。

预审结论：两张 r01 已于 2026-09-24 获项目所有者整批批准。候选原件保留，并以无覆盖方式复制为 `02_refs/studio_wide.png`、`02_refs/studio_guest.png`；来源和 SHA-256 见同名 JSON。静图 Reference Pack 已齐备；H3 成片中的身份与空间连续性仍须逐镜头验收。
