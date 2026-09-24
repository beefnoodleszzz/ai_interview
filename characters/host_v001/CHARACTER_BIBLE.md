# 主持人 Character Bible — host_v001

状态：视觉 r01 已被项目所有者否决；r02 人脸方向基本认可但发型不符；r03 中分黑长直候选保留；r04 已由项目所有者确认并复制为 EP0001 主持人身份参考。Host 演播室近景 r01 与 S002 reaction start/end r04 已于 2026-09-23 获批并登记为正式参考。Host 01 声音已由项目所有者选择并冻结。Guest 演播室近景与 Studio 宽景仍待审核，完整视觉 Reference Pack 尚未通过。

## 角色基线

- 原创当代中文访谈主持人，成年女性表达，约 30 岁；观察力强、语气克制，带轻微干燥的怀疑感。
- 说话自然、有停顿，不使用播音腔；通过具体追问和短反应形成节奏，不替嘉宾总结笑点。
- 视觉方向：按项目所有者提供的脸部与发型参考制作 AI 生成的虚构主持人形象；r04 测试深色中分长波浪、深炭灰西装、暖象牙色圆领内搭。所有画面需标明是 AI 生成的虚构节目形象，不能冒充参考人物真实出镜或代言。
- 镜头视线通常落在画面左侧的嘉宾方向；反应以抬眉、短暂停顿、压住的笑为主。
- 避免大幅挥手、夸张惊讶、对镜头主持、宽笑和无台词时嘴部动作。

## 声音

- 生产引擎：AutoDL 独立环境中的 IndexTTS-2.5；初始声音设计由 Qwen3-TTS VoiceDesign 生成。
- 固定声音候选要求：成年女性表达、温暖中低音区、轻微质感、近讲、克制干幽默、自然呼吸与对话停顿。
- 候选任务：host VoiceDesign r02 已在远端完成并拉回 2 条 WAV；项目所有者已选择 candidate 01，冻结为 `episodes/EP0001_golden_clip/02_refs/voices/host_voice_v001_r02.wav`（48 kHz 单声道，SHA-256 记录于 Episode Manifest）。
- r01 远程初始化失败记录保留。正式对白仍须由 IndexTTS 生成、逐句听审；冻结声音不等于对白表演已验收。

## 当前视觉候选

- `reference_candidates/host_visual_concept_r01.png` — 旧原创概念，项目所有者认为外貌不适合，保留作历史记录。
- `reference_candidates/host_visual_concept_r02.png` — 哈妮克孜形象方向初稿；项目所有者认为脸部接近，侧分蓬松发型不符合要求。
- `reference_candidates/host_visual_concept_r03.png` — 中分黑长直发型修订；保留为历史候选，Host r04 已获批准并作为正式身份参考。
- `reference_candidates/host_visual_concept_r04.png` — 按项目所有者提供的脸部与发型参考生成；采用中分深色自然长波浪，沿用 r03 的服装与演播室氛围；项目所有者已确认，正式身份参考为 EP0001 `02_refs/host_identity.png`，SHA 见同名 JSON。
