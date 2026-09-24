# 嘉宾 Character Bible — guest_v001

状态：Guest r01 身份肖像和演播室近景 r01 均已由项目所有者确认，分别冻结为 EP0001 `02_refs/guest_identity.png`、`02_refs/studio_guest.png`。Guest 01 声音已由项目所有者选择并冻结。

## 角色基线

- 原创虚构外星访谈嘉宾，成年男性表达；视觉上有异星特征，表演上冷静、一本正经。
- 视觉候选：低饱和橄榄灰皮肤、略长但可读的脸部比例、清晰且接近人类结构的嘴唇；深色头戴式监听耳机、无标志深石板色上衣。
- 面部和嘴部保持清楚，便于普通话嘴型生成；目光落在画面右侧主持人方向。
- 主要笑点来自荒谬内容和极度克制的表达。避免獠牙、发光眼、巨大嘴部、恐怖妆效、夸张手势及模仿现有影视/游戏外星人。

## 声音

- 生产引擎：AutoDL 独立环境中的 IndexTTS-2.5；初始声音设计由 Qwen3-TTS VoiceDesign 生成。
- 固定声音候选要求：成年男性表达、平稳中低音区、轻微颗粒感、精准辅音、冷面表达、不做怪物音效。
- 候选任务：guest VoiceDesign r01 已在远端完成并拉回 2 条 WAV；项目所有者已选择 candidate 01，冻结为 `episodes/EP0001_golden_clip/02_refs/voices/guest_voice_v001_r01.wav`（48 kHz 单声道，SHA-256 记录于 Episode Manifest）。正式对白仍须由 IndexTTS 生成并逐句听审。

## 当前视觉候选

- `reference_candidates/guest_visual_concept_r01.png` — 原创写实摄影概念；GPT-Image2 风格库 `Realistic Photography`（`realistic-photography`，case 377）提示策略；2026-09-23 项目所有者确认外星人形象可用，EP0001 身份参考 SHA 记录于 `02_refs/guest_identity.json`。
- 演播室近景 r01 已于 2026-09-24 批准；来源、正式文件 SHA-256 见 EP0001 `02_refs/studio_guest.json`。视频回片仍须检查身份和嘴型连续性。
