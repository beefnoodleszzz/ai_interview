# AI 原生访谈 / 对谈视频生产系统

> **MiniMax H3 + ComfyUI + AutoDL + 本地/自托管语音 + LipSync + Codex 自动化**
> 版本：**v1.1（审查修订版）**
> 更新日期：**2026-09-22**
> 目标：稳定生产 **60–100 秒、逼真、节奏强、调侃味浓、人物一致、声音有戏、嘴型可靠** 的 AI 访谈 / 对谈短视频。

### v1.1 相对 v1.0 的修订说明

本版本在 v1.0 基础上做了一次全面审查后的修订，改动集中在四类：

1. **事实修正**：IndexTTS-2.5 的发布时间表述（原文写“2026-08”，官方资料显示更接近 2026 年初）。
2. **补充遗漏的官方约束**：Ref2VA 音频参考不能单独使用的规则、H3 商业许可的收入阈值。
3. **新增护栏机制**：GPU/成本预算上限、自动重试次数上限、任务断点续传设计、核心资产异地备份。
4. **新增可选工具评估项**：官方 `H3-Context-IR`（Prompt 自动增强）与 `H3-Regenerate-2K`（官方 2K 超分）。

不改变 v1.0 已冻结的核心技术选型（H3 / ComfyUI / AutoDL / IndexTTS-2.5 / Qwen3-TTS / LatentSync 1.6 / MuseTalk 1.5 / SeedVR2），只是让执行时更不容易踩坑。

---

## 0. 先说结论：这套系统到底怎么搭

最终不要做成“一个超级 ComfyUI 工作流把所有模型一起塞进去”。最稳定、最容易维护的架构是：

```text
台本 / 节目设计
    ↓
结构化 Episode Manifest（JSON/YAML）
    ↓
对白音频先锁死：Audio Master First
    ├─ 主力：IndexTTS-2.5
    ├─ 声音设计：Qwen3-TTS VoiceDesign → 固定 Voice Reference
    └─ 高级可选：真人表演 → Voice Conversion
    ↓
分镜 / Shot Planning
    ↓
角色、场景、表演 Reference
    ↓
MiniMax H3 / ComfyUI
    ├─ Ref2VA：人物一致、参考图/视频/音频、对白镜头
    ├─ FL2VA：高质量非对白 / 关键帧镜头
    └─ Fun ControlNet / Performance Reference：动作控制
    ↓
LipSync QC
    ├─ H3 原生嘴型通过 → 保留
    ├─ 仅嘴型轻微失败 → LatentSync 1.6 修复
    └─ 快速预览 / 批量低成本 → MuseTalk 1.5
    ↓
剪辑 / Reaction / Room Tone / SFX / Subtitle
    ↓
SeedVR2 最终超分 / 修复
    ↓
1080×1920 / 24 或 25fps Master
```

**整个系统最重要的原则：**

1. **台本是第一生产资料。**
2. **声音是表演的时间轴。**
3. **H3 是虚拟演员，不是编剧，也不是最终音频裁判。**
4. **LipSync 是救火层，不是默认第一层。**
5. **一集 60–100 秒不要生成成一条长视频，而是拆成 12–24 个 Shot。**
6. **80% 左右镜头只让一个人清晰说话。双人镜头主要承担空间关系和 Reaction。**
7. **ComfyUI 负责“镜头生成总控”；Codex 负责“流水线编排”。**
8. **先把 15–20 秒黄金测试片段做到几乎看不出 AI，再扩到 90 秒。**

---

# 1. 我们真正要做的是什么

不是传统意义上的 AI 数字人口播。

我们要做的是一个 **AI 原生访谈节目引擎**：

- 今天可以采访外星人；
- 明天可以采访未来人；
- 后天可以采访怪兽；
- 可以采访神话人物；
- 可以采访原创科幻人物；
- 可以做一本正经的荒诞访谈；
- 可以做“明明是假设，但人物自己信得非常真”的对谈。

观众应该感受到：

> “我知道这很离谱，但这俩怎么聊得像真的一样？”

而不是：

> “哦，这是 AI 数字人。”

因此质量排序建议固定为：

```text
台本 / 对话设计       40%
声音与表演节奏         25%
剪辑 / Reaction / 停顿  15%
角色与画面一致性       10%
嘴型与局部技术质量     10%
```

这些百分比不是模型评测分数，而是**生产优先级**。不要为了嘴型 100 分，把台本和表演做成 60 分。

---

# 2. 质量目标：什么叫“做成了”

一条合格成片至少达到以下标准。

## 2.1 台本

- 0–3 秒必须已经有冲突、异常信息或强问题。
- 10 秒内观众能明白“这个访谈为什么值得看”。
- 每 6–12 秒至少出现一个新刺激：
  - 新信息；
  - 反转；
  - 追问；
  - 误解；
  - 梗；
  - Reaction；
  - 世界观升级。
- 不允许两个角色像百科问答机器人。
- 不允许主持人只会“哦，是这样”“那你怎么看”。
- 结尾不总结，最好：
  - 留一刀；
  - 戛然而止；
  - 下一集钩子；
  - 角色说一句让观众想评论的话。

## 2.2 声音

- 不能有播音腔。
- 同一角色每集声线必须稳定。
- 情绪不是“大喜大悲”，而是**微表演**：忍笑、停顿、狐疑、嫌弃、疲惫、无奈。
- 重要停顿必须存在于最终 Audio Master 中，不能指望视频模型猜。
- 重点对白 ASR 回转必须与原台本一致。

## 2.3 视频

- 主持人几十期不能随机换脸。
- Studio 不能每个 Shot 都变装修。
- 光向、麦克风、桌子、座位关系固定。
- 说话人的视线大体朝对方或主持人方向。
- 不要求每一帧都超高清，但要求**像同一场真实采访**。

## 2.4 嘴型

- 大多数 Shot 直接使用 H3 的音频感知生成，不二次改嘴。
- 只有明确看得出错位的 Shot 才进入 LipSync 修复。
- Close-up / 大嘴型镜头优先严格检查。
- Reaction Shot 没对白时不要让角色莫名其妙动嘴。

---

# 3. 技术栈最终定稿

| 模块 | 推荐主方案 | 角色 |
|---|---|---|
| 编剧/台本 | ChatGPT | 对话、梗、节奏、角色关系 |
| 自动化 | Codex + Python | 生成 manifest、调用 API、批处理、质检 |
| 视频主模型 | **MiniMax H3** | 虚拟演员 / 画面与原生音视频生成 |
| 视频总控 | **ComfyUI** | H3、ControlNet、后期节点、API |
| 固定声音设计 | **Qwen3-TTS VoiceDesign** | 生成主持人/原创角色初始声音 |
| 主力对白 TTS | **IndexTTS-2.5** | 声音克隆、情绪、语速、发音控制 |
| 第二 TTS Benchmark | Fun-CosyVoice 3 / Qwen3-TTS Base | 遇到特定文本或口音时 A/B |
| Voice Conversion | 可选，非核心 | 高级表演保留 |
| LipSync 修复 | **LatentSync 1.6** | 最终质量修嘴 |
| LipSync 快速预览 | **MuseTalk 1.5** | 快速预览 / 低成本批量 |
| 视频超分 | **SeedVR2** | Final upscale / restoration |
| ASR 质检 | Whisper / FunASR / WhisperX | 验证 TTS/最终音频文本 |
| 剪辑 | FFmpeg + 可选 NLE | 自动粗剪、拼接、音频混合 |

---

# 4. 为什么主模型就是 MiniMax H3

截至 2026-09-22，对我们的要求而言，H3 的关键不是“排行榜第一”，而是下面几件事同时成立：

- 开放权重，可在自己租用的 GPU 上跑；
- ComfyUI 原生支持；
- 视频与声音联合建模；
- Ref2VA 可以吃图片、视频、音频参考；
- FL2VA 可以做 T2V / I2V / 首尾帧；
- 可以通过参考视频、Fun ControlNet、关键帧进一步控制表演；
- 可以被 Codex / API 自动化。

官方 MiniMax H3 Base 两个 checkpoint 是不同的模型权重：

### FL2VA

负责：

- T2VA：文本 → 音视频；
- I2VA：首帧 → 音视频；
- FL2VA：首帧 + 尾帧 → 音视频；
- L2VA：尾帧约束。

### Ref2VA

负责：

- 参考图；
- 参考视频；
- 参考音频；
- 多参考混合。

官方 Ref2VA 输入上限：

- 图片最多 9 张；
- 视频最多 3 段，每段 2–15 秒，总视频参考时长不超过 15 秒；
- 音频最多 3 段，每段 2–15 秒，总音频参考时长不超过 15 秒；
- 混合文件总数最多 12；
- **音频参考不能单独作为唯一参考素材**，必须至少搭配一张图片或一段视频一起提交。

> 最后一条容易被忽略：如果 Codex 自动拼装 prompt 时只传了 `<Audio 1>` 而没有配套的 `<Picture N>` 或 `<Video N>`，请求会被拒绝。建议在 orchestrator 的输入校验层直接加一条硬性检查。

> 来源：MiniMax 官方 H3 README；ComfyUI 官方 H3 教程。

---

# 5. AutoDL：硬件怎么租

## 5.1 推荐优先级

### 推荐开发档：RTX 5090 32GB

适合：

- H3 pruned INT8；
- Ref2VA；
- LatentSync 1.6；
- SeedVR2 分阶段；
- Qwen/IndexTTS；
- 高速迭代。

**重点：系统内存也非常重要。** H3 在 32GB VRAM 机器上依赖 offload 时，主机 RAM 过小会严重影响稳定性。社区实测建议至少 64GB RAM，96–128GB 更舒服。

### 最省心档：48GB GPU

例如 RTX 6000 Ada / A6000 等 48GB 档。

优势：

- offload 压力更低；
- Ref2VA 更容易长时间运行；
- 多个后处理阶段切换更轻松；
- 调试时不必过度关注每 GB VRAM。

### 能跑档：4090 / 3090 24GB

可以跑 H3 pruned INT8，但要接受：

- 更频繁 offload；
- host RAM / 磁盘速度非常关键；
- VAE decode、Ref2VA 和复杂参考会更容易顶内存；
- 不建议让 H3 + LatentSync + SeedVR2 同时驻留。

## 5.2 不建议

如果目标是正式长期做项目，不建议一开始为了省钱选 12–16GB 卡。

虽然社区已有极端量化和 GGUF 路线，但你现在的目标不是“证明能跑”，而是“快速导演和迭代”。租卡场景下，人力时间比省几块显存更贵。

---

# 6. 磁盘、RAM、缓存规划

H3 模型很大。以 Comfy-Org 当前推荐文件为例：

- Ref2VA pruned int8_convrot：约 21GB；
- FL2VA pruned int8_convrot：约 21GB；
- Qwen3-VL H3 text encoder NVFP4 AWQ：约 15GB；
- Video VAE：数 GB；
- Audio VAE：约亚 GB 级；
- 再加 TTS、LatentSync、SeedVR2、项目缓存。

### 建议持久盘至少

**200GB 可用：最低。**
**400–500GB：舒服。**

视频项目真正吃空间的不是模型，而是：

- 失败 Shot；
- 多 Seed A/B；
- 中间 WAV；
- Upscale 前后版本；
- PNG frame cache。

### AutoDL 路径原则

不同镜像的持久盘挂载路径可能不同，因此不要把整个系统写死成某个 `/root/...`。

第一步自己定义：

```bash
export AI_STUDIO_ROOT=/path/to/your/persistent-volume/ai_interview
mkdir -p "$AI_STUDIO_ROOT"
```

然后统一：

```bash
export HF_HOME="$AI_STUDIO_ROOT/cache/huggingface"
export TORCH_HOME="$AI_STUDIO_ROOT/cache/torch"
export XDG_CACHE_HOME="$AI_STUDIO_ROOT/cache/xdg"
mkdir -p "$HF_HOME" "$TORCH_HOME" "$XDG_CACHE_HOME"
```

这样换 AutoDL 实例时，只需要挂载持久盘，不要重新下载几十 GB。

## 6.3 持久盘不是备份，护城河资产要异地留一份

第 68 节说过，这个项目真正的护城河是 Script Bible / Character Bible / Voice Library / Studio Bible / Prompt Templates / Benchmark Dataset 这些沉淀资产，而不是 H3 模型本身。

但这些资产目前的方案是**只存在 AutoDL 一块持久盘上**。持久盘能防实例重建丢数据，防不了账号异常、盘损坏、误删这类小概率但后果很重的情况。

建议：

- 大体积、可重新下载的东西（模型权重、cache）：留在持久盘就够，本来就不该备份；
- 小体积、不可再生的东西（`characters/`、`sets/`、`workflows/`、`orchestrator/`、每集的 `episode.yaml` 和 `bible.md`）：定期（比如每天收工前）同步一份到持久盘之外的地方——自己的电脑、对象存储、或者一个私有 Git 仓库都行，体积通常只有几百 MB 到几 GB，成本很低；
- 一句话判断标准：**这份文件如果消失，是“重新跑一次生成”能解决，还是“凭记忆重写”才能解决？后者必须有异地副本。**

---

# 7. 项目目录：从第一天就规范

```text
ai_interview/
├── apps/
│   ├── ComfyUI/
│   ├── index-tts/
│   ├── qwen3-tts/
│   ├── latentsync/
│   ├── musetalk/
│   └── seedvr2/
│
├── models/
│   ├── h3/
│   ├── voices/
│   ├── lipsync/
│   └── upscale/
│
├── cache/
│
├── characters/
│   ├── host_v001/
│   │   ├── bible.md
│   │   ├── refs/
│   │   ├── voice/
│   │   └── expressions/
│   └── guests/
│
├── sets/
│   └── studio_v001/
│       ├── bible.md
│       ├── camera_A_host.png
│       ├── camera_B_guest.png
│       ├── camera_C_two_shot.png
│       └── lighting_reference.png
│
├── episodes/
│   └── EP0001_alien/
│       ├── 00_script/
│       │   ├── script.md
│       │   └── episode.yaml
│       ├── 01_audio/
│       │   ├── raw/
│       │   ├── master/
│       │   └── mix/
│       ├── 02_refs/
│       ├── 03_h3_prompts/
│       ├── 04_shots_raw/
│       ├── 05_shots_selected/
│       ├── 06_lipsync/
│       ├── 07_edit/
│       ├── 08_upscale/
│       └── 09_final/
│
├── workflows/
│   ├── h3_ref2va_dialogue_api.json
│   ├── h3_fl2va_reaction_api.json
│   ├── latentsync_api.json
│   └── seedvr2_api.json
│
└── orchestrator/
    ├── config.yaml
    ├── build_episode.py
    ├── render_audio.py
    ├── render_shots.py
    ├── qc.py
    └── assemble.py
```

关键原则：**每一个 Episode 可独立重建。**

不要到第 30 集时，自己都不知道“这个声音是哪版”“这个 Shot 用了哪个 Seed”。

---

# 8. 环境不要混装

这是整个系统稳定性的关键。

至少分四个环境：

```text
comfy-h3      → ComfyUI + H3
voice         → IndexTTS / Qwen3-TTS / CosyVoice
lipsync       → LatentSync / MuseTalk
orchestrator  → Python + requests + ffmpeg + ASR
```

SeedVR2 可以：

- 装进 ComfyUI；或者
- 独立环境运行。

如果 ComfyUI 插件依赖开始冲突，立即拆出去，不要硬救一个“大一统 Python 环境”。

---

# 9. ComfyUI + H3 基础安装

## 9.1 先检查机器

```bash
nvidia-smi
python --version
free -h
df -h
```

记录：

- GPU 型号；
- VRAM；
- Driver；
- CUDA / PyTorch；
- host RAM；
- 持久盘路径。

## 9.2 ComfyUI

```bash
cd "$AI_STUDIO_ROOT/apps"
git clone https://github.com/Comfy-Org/ComfyUI.git
cd ComfyUI

python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

> 如果 AutoDL 镜像已经有匹配 CUDA 的 PyTorch，不要盲目重新装一个不匹配版本。先 `python -c "import torch; print(torch.__version__, torch.version.cuda)"`。

截至本方案时间，ComfyUI 官方 H3 文档要求 **ComfyUI 0.30.0+**。

启动：

```bash
python main.py --listen 0.0.0.0 --port 8188
```

不要直接把 8188 暴露到公网无认证环境。优先：

- AutoDL 的端口代理；
- SSH Tunnel；
- VPN / ZeroTier / Tailscale；
- 或至少反向代理认证。

---

# 10. H3 模型文件：推荐组合

官方 Comfy-Org H3 模型仓库：

`https://huggingface.co/Comfy-Org/MiniMax-H3`

## 10.1 主力：pruned INT8 ConvRot

如果环境兼容，优先：

```text
minimax_h3_ref2va_pruned_int8_convrot.safetensors
minimax_h3_fl2va_pruned_int8_convrot.safetensors
qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors
minimax_h3_video_vae_fp16.safetensors
minimax_h3_audio_vae_fp32.safetensors
```

Comfy-Org 模型卡当前明确建议：在适配 PyTorch/cu130 的环境里，diffusion model 优先 `int8_convrot`；不兼容时再考虑 `fp8_scaled`。

## 10.2 下载示例

```bash
pip install -U "huggingface_hub[cli]"

cd "$AI_STUDIO_ROOT/apps/ComfyUI"

hf download Comfy-Org/MiniMax-H3 \
  --include "diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors" \
  --include "diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors" \
  --include "text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors" \
  --include "vae/minimax_h3_video_vae_fp16.safetensors" \
  --include "vae/minimax_h3_audio_vae_fp32.safetensors" \
  --local-dir models
```

如果 Hugging Face 网络慢，使用你自己信任的镜像或 ModelScope 路径，但最终必须校验文件名和 hash。

## 10.3 第一阶段先别装一堆第三方 H3 魔改节点

先做到：

1. 官方 T2V 模板能跑；
2. 官方 I2V 能跑；
3. 官方 Ref2VA 能跑；
4. 5 秒 768p 级别可以稳定生成；
5. 连续跑 5 次不会炸环境。

然后再装：

- Fun ControlNet；
- Turbo / PDD；
- 缓存加速；
- 其他社区优化。

**先稳定，后加速。**

---

# 11. H3 的正确使用方式：它是“演员”

我们不让 H3 自由决定：

- 完整台词；
- 台词节奏；
- 声线；
- 所有停顿；
- 90 秒整段表演。

我们给 H3 的应该是一条非常明确的镜头任务：

> “这是角色，这是房间，这是这一句声音，这是动作参考；你把这 4.8 秒演出来。”

## 单个 Dialogue Shot 理想输入

```text
Picture 1 → 主持人身份参考
Picture 2 → 主持人当前服装/Studio 光照参考
Picture 3 → 当前机位构图参考
Video 1   → 可选的表演/动作参考
Audio 1   → 声音参考或这句对白参考
Prompt    → 这一镜头详细表演说明
```

嘉宾同理。

---

# 12. Script Bible：90 秒内容怎么写

## 12.1 推荐长度

第一阶段：

**70–100 秒。**

不要为了“长”堆废话。

## 12.2 基础结构

| 时间 | 作用 |
|---|---|
| 0–5s | Cold Open：最离谱/最危险的一句话 |
| 5–15s | 角色身份 + 世界观 |
| 15–30s | 第一层信息兑现 |
| 30–45s | 主持人攻击逻辑漏洞 |
| 45–60s | 第一次大反转 |
| 60–75s | 世界观升级 / 更大的瓜 |
| 75–90s | 最后一刀 / 戛然而止 |

这不是死模板。真正的规则是：**观众每十几秒都要重新获得一个“为什么继续看”的理由。**

## 12.3 对话写成“表演稿”，不是文章

错误：

```text
主持人：你为什么来到地球？
外星人：因为我们对人类文明很感兴趣。
主持人：你怎么看人工智能？
```

正确方向：

```text
主持人：（皱眉，半笑）
所以……你们观察地球两百多年，最后最想研究的是直播带货？

外星人：（停 0.6 秒，看主持人）
核聚变我们也研究。

主持人：
哦。

外星人：
但你们那个“家人们，上链接”……
（轻轻摇头）
我们文明目前解释不了。
```

## 12.4 必须在台本阶段写出来的东西

- `[pause=0.6]`
- `[small_laugh]`
- `[inhale]`
- `[interrupt]`
- `[looks_at_host]`
- `[deadpan]`
- `[dry]`
- `[lower_voice]`
- `[faster]`
- `[slow_down]`

后面 TTS、镜头、剪辑都从这些数据派生。

---

# 13. Episode Manifest：不要用 Word 文档管理生产

建议一集一个 YAML：

```yaml
episode_id: EP0001
format: 9:16
target_duration: 82.0
fps: 24

characters:
  host:
    character_id: host_v001
    voice_id: host_voice_v003
  guest:
    character_id: alien_a017
    voice_id: alien_voice_v002

beats:
  - id: B01
    purpose: cold_open
    start_target: 0.0
    speaker: guest
    text: "我们本来准备毁灭地球。"
    emotion: deadpan
    pause_after: 0.45

  - id: B02
    speaker: host
    text: "本来？"
    emotion: suspicious_amused
    pause_after: 0.25

shots:
  - id: S001
    camera: guest_closeup
    speaker: guest
    beat_ids: [B01]
    target_duration: 3.1
    h3_mode: ref2va
    lip_priority: high

  - id: S002
    camera: host_reaction
    speaker: none
    target_duration: 0.9
    h3_mode: fl2va
    lip_priority: none

  - id: S003
    camera: host_closeup
    speaker: host
    beat_ids: [B02]
    target_duration: 1.4
    h3_mode: ref2va
    lip_priority: medium
```

这个 YAML 后面可以直接让 Codex 生成：

- TTS job；
- H3 prompt；
- ComfyUI API JSON；
- filename；
- FFmpeg timeline。

---

# 14. Character Bible：主持人必须成为“资产”

## 14.1 主持人不要每次重做

至少准备：

- 正面头像；
- 左 3/4；
- 右 3/4；
- 左侧；
- 右侧；
- 坐姿中近景；
- 手放桌面的状态；
- 微笑；
- 忍笑；
- 严肃；
- 惊讶；
- 怀疑；
- 同一个 Studio 光照。

## 14.2 Bible 必须写文字

例如：

```text
Host v001
- 男性，约 30 岁
- 不是职业新闻主播
- 聪明、欠一点，但不油腻
- 追问快
- 不轻易大笑，更多是憋笑、低头、嘴角上扬
- 遇到特别荒诞的信息会先停 0.4–0.8 秒再接
- 左手偶尔碰桌面，避免夸张手势
- 说话时身体轻微前倾
```

H3 reference 和 Script 都从这个 Bible 继承。

---

# 15. Studio Bible：固定空间比画质更重要

建立四个固定机位：

### Camera A — Host CU

- 主持人中近景；
- 50mm 左右视觉感；
- 轻浅景深；
- 嘉宾方向留 look room。

### Camera B — Guest CU

- 嘉宾中近景；
- 镜头高度与 Host 一致；
- 背景元素稳定。

### Camera C — Two Shot

- 两人同时出现；
- 用于建立空间关系；
- 不承担长对白。

### Camera D — Reaction / Tight Close-up

- 专门服务梗；
- 0.5–2 秒；
- 让停顿看起来像真实节目。

### 固定的东西

- 桌子；
- 麦克风；
- 灯位；
- 背景灯；
- 主持人座位；
- 嘉宾座位；
- 空间方向。

越固定，H3 每集越容易保持一致。

---

# 16. 声音系统：Audio Master First

这是整套生产线第二重要的部分。

## 原则

**最终台词音频必须在视频生成之前基本确定。**

因为真正的表演节奏来自：

- 句子长度；
- 重音；
- 停顿；
- 呼吸；
- 笑；
- 抢话；
- 半句话被打断。

如果音频在最后才换，嘴型、Reaction、剪辑都会跟着崩。

---

# 17. Qwen3-TTS：用来“发明声音”

官方仓库：

`https://github.com/QwenLM/Qwen3-TTS`

当前官方模型支持：

- VoiceDesign；
- Voice Clone；
- Custom Voice / instruct；
- 10 个主要语言。

## 17.1 安装建议：独立环境

```bash
conda create -n qwen3-tts python=3.12 -y
conda activate qwen3-tts
pip install -U qwen-tts
```

可选：

```bash
pip install -U flash-attn --no-build-isolation
```

FlashAttention 必须和实际 GPU / PyTorch / CUDA 匹配，装不上不要硬装。

## 17.2 VoiceDesign 工作流

先写声音描述：

```text
30岁左右男性，普通话自然，不是播音腔。
中低音，但不要故意压低。
讲话聪明、快速、有一点干式幽默。
多数时候情绪克制，梗出现时像在忍笑。
停顿自然，句尾不要总是上扬。
```

生成一批 8–20 个候选。

不要马上拿它长期生产。

### 正确流程

```text
VoiceDesign
  ↓
选中最像节目主持人的版本
  ↓
录/生成一段 10–30 秒干净 reference
  ↓
进入 Voice Clone 模型
  ↓
固定为 host_voice_v001
```

**为什么？** VoiceDesign 本身是创造声音，固定 Seed 虽然有帮助，但长期一致性最好还是转成 clone reference。

---

# 18. IndexTTS-2.5：主力对白引擎

官方仓库：

`https://github.com/index-tts/index-tts`

IndexTTS-2.5 的技术报告与模型权重在 2026 年年初已公开（早于本方案最初记录的“2026-08”），当前支持：

- 中文；
- 英文；
- 日文；
- 西班牙语；
- 阿拉伯语；
- 音色克隆；
- 独立情绪参考；
- 8 维情绪向量；
- `emo_text`；
- `duration_factor` 0.5–2.0；
- 中文拼音 / 英文 CMU / 日文假名发音控制。

对我们这种“每一句都需要微调”的访谈特别合适。

## 18.1 安装

```bash
cd "$AI_STUDIO_ROOT/apps"
git clone https://github.com/index-tts/index-tts.git
cd index-tts
pip install -U uv
uv sync --all-extras
```

下载：

```bash
uv tool install "huggingface-hub"
hf download IndexTeam/IndexTTS-2.5 --local-dir=checkpoints
```

## 18.2 主持人语音生成策略

同一个 `spk_audio_prompt` 固定。

然后逐句控制：

```text
普通追问      → emotion: calm + curious
忍笑          → happy 0.15 + calm 0.65
怀疑          → surprise 0.10 + calm 0.50 + disgust 0.10
补刀          → calm / lower energy / dry
真震惊        → surprise 0.35，不要 1.0
```

**AI 访谈最怕情绪过度。**

现实中的人并不会每一句都戏剧爆炸。

---

# 19. 声音生产的两种模式

## Mode A：Fast / 批量生产

```text
Script
→ IndexTTS-2.5 / Qwen3-TTS
→ ASR QC
→ WAV Master
```

适合 80–90% 的台词。

## Mode B：Premium / 关键戏

只有在以下情况才使用人工表演：

- 抢话；
- 大笑；
- 极细的迟疑；
- “不是……等一下”；
- 复杂节奏梗；
- 情绪转折。

你自己先演 timing，然后再用 Voice Conversion 改成目标声线。

### 注意

Seed-VC 曾经非常适合这个用途，但原仓库已在 2025-11 归档。它仍可作为**固定版本实验工具**，但不应该成为整个长期系统的硬依赖。

如果你需要 VC，优先做一个 A/B：

- CosyVoice 旧版 voice conversion；
- Seed-VC pinned commit；
- 后续更新的开放 VC 模型。

**核心生产线必须在没有 VC 的情况下也能完整运行。**

---

# 20. 音频文件规范

每一条对白独立保存：

```text
S001_guest_take01.wav
S001_guest_take02.wav
S003_host_take01.wav
```

选择后：

```text
01_audio/master/S001_guest.wav
```

建议：

- 不要带 BGM；
- 不要带 room tone；
- 语音尽量干净；
- 最终剪辑阶段再加环境。

每句保留 metadata：

```json
{
  "text": "本来不是。",
  "speaker": "guest",
  "voice_id": "alien_voice_v002",
  "emotion": "deadpan",
  "duration": 1.42,
  "asr_text": "本来不是",
  "approved": true
}
```

---

# 21. ASR 质检：必须自动做

任何 TTS 都可能出现：

- 漏字；
- 多字；
- 名字读错；
- 数字读错；
- 英文混读异常。

流程：

```text
TTS 输出 WAV
   ↓
ASR
   ↓
原文 normalize
   ↓
ASR normalize
   ↓
字符串 / CER 比较
   ↓
不合格自动重生成或进入人工检查
```

中文建议对：

- 标点；
- 阿拉伯数字；
- 英文字母大小写；
- “儿化/嗯/啊”等语气词

做合理 normalize。

不要要求 ASR 文本字面 100% 一致，目标是确保**语义和实际发音没有跑掉**。

---

# 22. 音频节奏：停顿写进 WAV

例如台词：

> “你们观察地球……两百多年了？”

不要只给 TTS：

```text
你们观察地球两百多年了？
```

然后希望 H3 自己制造戏剧停顿。

更好的做法：

- TTS 本身出自然 pause；或
- 把两段语音拼起来；
- 中间插 300–700ms 静音。

比如：

```text
你们观察地球
[450ms]
两百多年了？
```

**剪辑节奏的权威源是 Audio Master。**

---

# 23. H3 Prompt：必须用官方结构，不要一句自然语言乱糊

MiniMax 官方现在直接提供 H3 Prompt Writing Skill。

仓库：

`https://github.com/MiniMax-AI/MiniMax-H3`

官方要求：

### Base 模式

- `integrated_multimodal_description`
- `overall_soundscape`
- `non_diegetic_music`

### Ref2VA

按顺序写：

1. `subject_definitions`
2. `summary`
3. `retention_analysis`
4. `detailed_description`
5. `overall_soundscape`
6. `non_diegetic_music`

官方 Prompt Skill 还要求：

- 结构字段写英文；
- 对白保留原语言；
- 图片、视频、音频 reference 使用明确标签；
- Shot 2 以后明确时间点。

---

# 24. 一个 Ref2VA Dialogue Shot 模板

下面不是要求一字不改，而是我们自己的**生产模板**：

```text
subject_definitions:
<Subject 1> is the fixed host identity from <Picture 1>. Preserve his facial identity, hairstyle, age, outfit, and overall appearance.
<Subject 2> is the fixed podcast studio environment from <Picture 2>. Preserve the table, microphone, warm practical background lights, seating direction, and lighting logic.
<Audio 1> is the exact dialogue performance for the host. Preserve the timing, pacing, pauses, emotional delivery, and spoken content.

summary:
A realistic podcast interview close-up. The host reacts to an absurd statement from the off-camera guest and asks one short skeptical follow-up. The result should feel observational and naturally performed, not like a presenter speaking to camera.

retention_analysis:
Preserve <Subject 1> identity closely. Preserve the studio geometry and microphone position from <Subject 2>. Use <Audio 1> as the performance timing reference. Do not invent additional spoken words before or after the supplied dialogue.

detailed_description:
A realistic live-action podcast close-up, medium close-up, approximately 50mm lens feeling, shallow but natural depth of field. <Subject 1> sits behind the same microphone in <Subject 2>, body angled slightly toward the off-camera guest. He first listens silently for roughly half a second. His eyebrows lift subtly, then the corner of his mouth tightens as if suppressing a laugh. He leans forward a few centimeters and speaks the exact dialogue from <Audio 1>. Keep gestures minimal. His eyes remain directed toward the guest, never toward the viewer. Lip and jaw motion should follow the supplied speech naturally. After the final word, he pauses and keeps looking at the guest for a brief reaction beat. No extra dialogue.

overall_soundscape:
Quiet podcast studio room tone only. Very faint air-conditioning ambience. No crowd, no loud environmental effects.

non_diegetic_music:
None.
```

如果 H3 仍然擅自生成额外语音，不要无限增加“DO NOT”。进入后面提到的**外部 Audio Master + 后期替换 / LipSync 修复策略**。

---

# 25. Performance Reference：这是“真人感”的秘密武器

如果只用文字告诉模型：

> 主持人先怀疑，再忍笑，再前倾。

模型每次自己理解。

更稳定的方法：

1. 手机 / webcam 拍一段真人表演；
2. 不需要长得像角色；
3. 只需要演：
   - 头部动作；
   - 身体前倾；
   - Reaction；
   - 手势；
   - Timing；
4. 把这段作为 Video Reference / Control 输入。

最终：

```text
人的表演节奏
+
AI 角色身份
+
最终声音
=
H3 虚拟演员
```

如果 Fun ControlNet Pose 对你的版本更稳定，也可以先：

```text
performance video
→ pose/depth/control video
→ H3 Fun ControlNet
```

两条路线都要 A/B，不要先假设谁绝对更好。

---

# 26. Shot 长度：不要贪

虽然 H3 可以生成最长约 15 秒级别的片段，我们的生产默认建议：

### 对白 Close-up

**2–7 秒**。

### 无对白 Reaction

**0.6–2.5 秒**。

### Two Shot

**1.5–4 秒**。

### Establishing

**2–5 秒**。

### 为什么

- LipSync 更容易稳；
- 身份漂移更少；
- 手更少出错；
- 失败只重跑一个镜头；
- 剪辑更有节奏；
- 90 秒节目不依赖一次性奇迹。

---

# 27. 我们的镜头语法

一条 80 秒片子可以是：

```text
S001 Guest CU — 3.2s
S002 Host Reaction — 0.8s
S003 Host CU — 2.1s
S004 Guest CU — 5.4s
S005 Host Reaction — 1.0s
S006 Two Shot — 2.6s
S007 Guest CU — 6.2s
S008 Host CU — 4.1s
...
```

推荐比例不是铁律，但第一阶段可用：

- 65–80%：单人说话 Close-up；
- 10–20%：Reaction；
- 5–15%：Two Shot / Wide。

**特别不要让双人镜头承担长篇轮流说话。**

这是 AI 多人嘴型最容易出错的场景。

---

# 28. H3 生成分两阶段

## Pass 1：Preview / Casting

目标：快速判断：

- 脸是否对；
- 动作是否对；
- 眼神是否对；
- 表情是否好；
- Seed 值不值得继续。

使用：

- 较低分辨率；
- Turbo / 少步数（如果已经验证不伤关键质量）；
- 每 Shot 一次出多个 Seed。

## Pass 2：Final Shot

只对选中的 Seed / prompt：

- 回到正式参数；
- 目标 native 768p 级；
- 完整 Ref；
- 生成最终 Raw Shot。

不要拿高成本设置去“找感觉”。

---

# 29. FL2VA 和 Ref2VA 怎么分工

## Ref2VA：优先用于

- 固定主持人；
- 固定嘉宾；
- 需要声音 / 动作 reference；
- Dialogue Shot；
- 身份一致比 raw quality 更重要。

## FL2VA：优先用于

- Reaction；
- 静默 close-up；
- 片头；
- 怪兽进场；
- Studio 建立镜头；
- 道具动作；
- 你已经有精确首帧 / 尾帧。

工程上不要教条：

> 同一镜头用 Ref2VA 一直失败，就拿 FL2VA 做一版，看是不是控制反而太多。

---

# 30. 嘴型：三层防线

## Layer 1 — H3 原生嘴型

第一选择永远是：

> 视频生成时就让模型知道角色正在说什么、怎么说。

因为这样嘴唇、下巴、面部肌肉、头部动作是共同生成的。

如果已经好，**不要二次处理。**

## Layer 2 — Audio Master 替换 / 轻修

即使 H3 自己生成了声音，最终节目仍以我们锁定的 Audio Master 为准。

做完视频后：

```text
H3 output audio
→ 不作为最终对白权威源

Master WAV
→ 最终剪辑对白
```

如果嘴型仍然与 Master 基本一致，直接换轨即可。

## Layer 3 — LatentSync 1.6

如果画面、表演、身份都很好，只是嘴型不够准：

**不要重跑整个 H3 Shot。**

进入 LatentSync。

---

# 31. LatentSync 1.6

官方仓库：

`https://github.com/bytedance/LatentSync`

### 当前重要事实

- 1.6 使用 512×512 训练来改善嘴部模糊；
- 官方 README 给出的推理最低 VRAM：
  - 1.5：约 8GB；
  - **1.6：约 18GB**。

所以之前常见的“LatentSync 只要 7–8GB”说法不能用于规划 1.6。

## 建议

- 24GB / 32GB 卡可以跑；
- H3 模型必须先卸载 / 释放显存；
- 独立进程 / 独立环境最好；
- 只修失败 Shot；
- 不要整集每个镜头都跑。

### 修复顺序

```text
H3 Raw Shot
  ↓
换成 Master Audio
  ↓
LatentSync 1.6
  ↓
人工看嘴 / 牙齿 / 下巴 / identity
  ↓
合格 → selected shot
```

---

# 32. MuseTalk 1.5

官方仓库：

`https://github.com/TMElyralab/MuseTalk`（仓库可能以 jjt997/musetalk 入口显示）

特点：

- 实时/准实时；
- 官方介绍可 30fps+；
- 256×256 face region；
- 支持中英日等多语言声音驱动；
- 1.5 比早期版提升了 identity 和 sync。

我们的定位：

### 适合

- Preview；
- 低成本批量；
- 测试 Audio Master；
- 非极近景。

### 不默认作为 Final Master

因为我们的节目会有大量真实人物式 Close-up，最终品质优先 LatentSync 1.6 / H3 原生。

---

# 33. LipSync QC 怎么判

不要只看“嘴有没有动”。

检查：

1. 爆破音 P/B/M 是否大致闭口；
2. 长元音嘴型是否保持；
3. 句尾停止后嘴是否还在乱动；
4. 牙齿是否突然变化；
5. 嘴周围皮肤有没有一块“贴片感”；
6. 下巴有没有不自然抽动；
7. 头转动时嘴部是否漂；
8. 字幕 / 音频 / 嘴型是否感觉同节奏。

### 质检等级

```text
A — 原生 H3 好，直接用
B — 有轻微问题，但正常速度观看几乎感知不到
C — 需要 LatentSync
D — 表演或脸本身坏了，直接重生 H3
```

不要用 LipSync 去救 D 级素材。

---

# 34. Reaction Shot：比 LipSync 更重要

真实节目最有价值的镜头之一是**没有对白的反应**。

例如：

```text
嘉宾：
“我们本来准备毁灭地球。”

CUT TO HOST
0.75 秒
主持人眉毛抬起，没说话。

Host：
“本来？”
```

这 0.75 秒会让整条片子的“真人感”提升巨大。

每个大梗后问：

> 观众需不需要看到另一个人的反应？

如果需要，单独生成 Reaction Shot。

---

# 35. 环境声音：不要把所有音频做得像真空

最终 Mix 至少有：

- Studio room tone；
- 极弱空调底噪；
- 麦克风空间感；
- 必要时椅子/桌面微音；
- 特殊嘉宾可以有极低的环境提示音。

但原则：

**宁少，不要“短视频音效包”堆满。**

我们做的是“像真的访谈”，不是综艺罐头音效。

---

# 36. 声音混合层次

推荐最终：

```text
Dialogue Master       - 主体
Room Tone             - 极低
Foley / physical SFX  - 很克制
Music                  - 只有需要时
```

重点：

- Dialogue 永远最清楚；
- 同一个角色不同 Shot loudness 不要跳；
- 两人声线频谱不要完全重叠；
- 不要过度降噪成“玻璃声音”。

最终交付建议保留一个无 BGM Dialogue Stem，方便以后重剪。

---

# 37. SeedVR2：最后再做

官方 ComfyUI 项目：

`https://github.com/comfyorg/comfyui_seedvr2`

ComfyUI 官方视频超分文档把 SeedVR2 放在高质量/一致性路线中。

### 顺序一定是

```text
H3
→ LipSync 修复
→ 剪辑确认
→ 必要的 artifact fix
→ SeedVR2
→ Final encode
```

**不要先超分再修嘴。**

否则：

- 更慢；
- LipSync 重新生成脸区域后还要二次 upscale；
- 更容易出现局部画质不一致。

## SeedVR2 资源

官方 ComfyUI repo 提醒：

- 3B 版本也可能需要 18GB+ VRAM；
- batch_size 至少 5 才会利用 temporal consistency；
- 更大 batch 更吃显存。

所以同样采用**阶段式运行**。

---

# 38. 最终输出规格建议

抖音 / TikTok 主版：

```text
1080 × 1920
H.264 / H.265（按上传兼容性选择）
24fps 或 25fps
AAC
48kHz final audio
```

H3 自己的原生生成是 24fps 路线，整个项目如果没有明确理由，**不要来回在 24 / 25 / 30fps 之间转换**。

字幕：

- 直接生成 SRT / ASS；
- 不建议把每个字都做夸张 KTV 弹跳；
- 重点词可以少量强调。

---

# 39. ComfyUI 最终需要的五个工作流

## Workflow A：H3 Ref2VA Dialogue

输入：

- character refs；
- set ref；
- performance video optional；
- audio reference；
- generated prompt；
- seed；
- duration；
- resolution。

输出：

- Raw MP4；
- prompt metadata；
- seed；
- execution time。

## Workflow B：H3 FL2VA Reaction / B-roll

输入：

- first frame；
- optional last frame；
- prompt。

输出：Reaction / establishing。

## Workflow C：Fun Control / Performance Transfer

输入：

- control video；
- reference character；
- prompt。

只在需要强动作控制时用。

## Workflow D：LipSync Repair

建议独立服务，不一定硬塞 ComfyUI。

输入：

- selected video；
- Master WAV。

输出：

- synced video。

## Workflow E：SeedVR2

输入：final selected shot / final edit。

输出：1080p master。

## 可选：官方 H3 附加 API 节点（Benchmark 阶段评估，不进第一版核心链路）

MiniMax 官方 ComfyUI 节点包里还带了两个没有进入上面五个核心工作流、但值得在 Benchmark 阶段单独测一测的节点：

- **`H3ContextIR`**：调用官方 Context-IR API 做 prompt 增强/结构化，可能能替代一部分第 23–24 节里手写 prompt 模板的工作；
- **`H3Regenerate2K`**：官方 2K 分辨率重生成/超分 API，是 SeedVR2 之外的另一条最终超分路径。

两者都依赖调用官方 API（需要 `IR_KEY` 等环境变量配置的 API Key），不是纯本地推理，所以要评估的是：**延迟、费用、以及是否比现有手写 prompt / SeedVR2 方案有明显收益**。如果没有明显收益，不需要引入这条额外的外部依赖——这符合第 60 节"第一版不要做的事情"的精神。

---

# 40. 为什么 LipSync / TTS 不一定要做成 ComfyUI Node

ComfyUI 擅长：

- 图像；
- 视频；
- H3 生成图；
- 可视化参数。

但生产系统不应该被“有没有社区 Node”绑架。

例如 IndexTTS 独立服务：

```text
POST /tts
{
  "text": "本来？",
  "voice_id": "host_voice_v003",
  "emotion": "suspicious"
}
```

返回：

```text
/audio/EP0001/S003_host.wav
```

Codex 再把文件喂给 ComfyUI。

这样比安装一个维护不明的 TTS custom node 稳定得多。

---

# 41. ComfyUI API：Codex 怎么控制

工作流最终要导出 **API Format** JSON。

Orchestrator 做：

```text
1. 加载 workflow JSON
2. 替换 input image / audio / prompt / seed / duration
3. POST 到 ComfyUI /prompt
4. 获取 prompt_id
5. 轮询 /history
6. 下载/定位 output
7. 写回 episode manifest
```

伪代码：

```python
workflow = load_json("h3_ref2va_dialogue_api.json")
workflow = patch_inputs(
    workflow,
    prompt=shot.h3_prompt,
    audio=shot.audio_path,
    refs=shot.references,
    seed=shot.seed,
    duration=shot.target_duration,
)

prompt_id = comfy.submit(workflow)
result = comfy.wait(prompt_id)
shot.raw_video = result.video
save_manifest()
```

**不要让 Codex 每次重新“发明 workflow”。**

Workflow 是版本化模板；Codex 只修改允许变动的输入。

---

# 42. 版本化：一定要记录

Manifest 记录：

```yaml
pipeline_version: 1.0.0
comfyui_commit: abc123
h3_ref_model: minimax_h3_ref2va_pruned_int8_convrot
h3_fl_model: minimax_h3_fl2va_pruned_int8_convrot
index_tts_version: 2.5
latentsync_version: 1.6
seedvr2_commit: xyz789
```

否则未来模型更新以后：

> “为什么上个月人物很稳，这个月突然不稳？”

你根本无法追查。

---

# 43. 自动化状态机

每个 Shot 有状态：

```text
PLANNED
AUDIO_RENDERING
AUDIO_QC
READY_FOR_H3
H3_RENDERING
H3_QC
NEEDS_RETRY
NEEDS_LIPSYNC
READY_FOR_EDIT
UPSCALED
DONE
```

不要靠“文件夹里有没有文件”猜状态。

## 43.1 实例中断后要能接着跑，而不是从头来

AutoDL 是按时长/竞价租用的，实例随时可能因为网络问题、被抢占（竞价实例）或手动关机而中断。如果这时正好有 Shot 停在 `H3_RENDERING` 或 `AUDIO_RENDERING`，需要明确恢复规则，否则要么重复花钱重跑，要么状态永远卡死：

- **每个 Shot 的状态和产出路径都写回持久盘上的 manifest**，不要只存在内存 / 临时变量里；
- Orchestrator 启动时第一步永远是「扫描 manifest，找出所有停在 `*_RENDERING` 状态超过一定时间的 Shot」，判定为中断任务；
- 中断任务的处理原则：**已提交给 ComfyUI 的 `prompt_id` 如果还能查到 `/history` 结果，直接取回；查不到就按一次失败重试处理**（计入 44.1 的重试上限，不要平白多算一次尝试）；
- 幂等性关键：同一个 Shot 的任务提交要带唯一 `job_id`，重复提交时先检查该 `job_id` 是否已有产出，避免同一个 Shot 被意外重复渲染。

---

# 44. H3 自动 Retry 策略

不要简单：失败 → 同参数重新跑 10 次。

失败分类：

### Identity Fail

处理：

- 加强 reference；
- 减少冲突 reference；
- 调整 ref image crop；
- 检查角色与 studio 是否被错误标签。

### Performance Fail

处理：

- 加 performance reference；
- 把动作写更小；
- 拆短 Shot。

### Lip Fail

处理：

- 如果其他都好 → LatentSync；
- 不重生 H3。

### Weird Hands

处理：

- 切更近；
- 手不进镜；
- reaction 改头肩镜头。

### Extra Speech

处理：

- 最终换 Master Audio；
- Prompt 约束；
- 拆短；
- 必要时 LipSync 修复。

## 44.1 重试必须有硬上限，否则会安静地烧钱

上面的分类处理容易让人忽略一件事：**没有上限的自动重试，是全自动流水线里最容易失控的成本黑洞。**

建议在 orchestrator 里对每个 Shot 强制加两道护栏：

```yaml
retry_policy:
  max_attempts_per_shot: 4        # 超过直接转 NEEDS_MANUAL_REVIEW
  max_gpu_minutes_per_shot: 15    # 单 Shot 累计生成时间上限
  max_gpu_minutes_per_episode: 300  # 单集总预算，触顶自动暂停并通知
```

- 达到 `max_attempts_per_shot` 但仍未通过 QC → 状态改为 `NEEDS_MANUAL_REVIEW`，不再自动重试；
- 达到 `max_gpu_minutes_per_episode` → orchestrator 暂停当前集的所有排队任务，等待人工确认是否继续；
- 每次重试都要记录触发原因（Identity/Performance/Lip/Weird Hands/Extra Speech），方便复盘“这一集为什么比预期贵”。

**目标不是完全禁止重试，而是让失控在能被发现的地方停下来，而不是在月底账单里被发现。**

---

# 45. 角色一致性怎么提高

优先级：

1. **Reference 本身一致。**
2. Studio lighting 一致。
3. 每个机位固定 composition reference。
4. 同一角色不要同时喂太多互相矛盾角度。
5. Reference crop 干净。
6. 角色服装不要每集大变。
7. Prompt 里不要每次重新发明脸部细节。

### 一个常见错误

给主持人 9 张图：

- 4 个不同发型；
- 3 个不同光线；
- 两套衣服。

以为“参考越多越稳”。

实际上模型得到的是冲突信息。

**Reference 要少而一致。**

---

# 46. 嘉宾设计策略

嘉宾可以疯狂，但保持一个原则：

> **视觉夸张，表演克制。**

比如：

- 巨大怪兽坐 podcast 椅；
- 外星人戴耳机；
- 未来机器人拿咖啡。

画面本身已经荒谬，台词和演技越一本正经越好笑。

不要：

- 怪兽还一直夸张挥手；
- 大张嘴；
- 每句大笑；
- 镜头乱飞。

这不仅降低喜剧高级感，也提高 AI 生成失败率。

---

# 47. 第一条黄金测试片段

在任何 90 秒完整片之前，只做这个：

## 目标：15–20 秒

### S001 — Guest Close-up — 3.5 秒

嘉宾：

> “我们本来准备毁灭地球。”

要求：

- deadpan；
- 嘴型准确；
- 看向主持人；
- 说完后不乱动嘴。

### S002 — Host Reaction — 0.8 秒

- 眉毛抬；
- 看嘉宾；
- 不讲话。

### S003 — Host CU — 1.5 秒

> “本来？”

停一下。

### S004 — Guest CU — 5–6 秒

> “后来领导开始刷你们短视频了。”

### S005 — Host Reaction + follow-up — 4–5 秒

> “所以……是短视频救了地球？”

## 验收

必须同时通过：

- Host identity；
- Guest identity；
- Studio continuity；
- 两个声音人格；
- LipSync；
- Reaction timing；
- Room tone；
- 切镜节奏。

如果这 20 秒不行，不要开始做 90 秒。

---

# 48. 第一阶段搭建顺序

## Phase 0 — 机器

- [ ] 选好 AutoDL GPU
- [ ] 确认 RAM
- [ ] 确认持久盘
- [ ] 设 HF cache
- [ ] 装 FFmpeg

## Phase 1 — H3 Baseline

- [ ] ComfyUI 0.30+
- [ ] 官方 H3 T2V 跑通
- [ ] I2V 跑通
- [ ] Ref2VA 跑通
- [ ] 连续 5 次生成不崩

### Gate

**没通过，不装其他高级节点。**

## Phase 2 — Voice

- [ ] Qwen VoiceDesign 跑通
- [ ] 生成主持人候选声音
- [ ] 固化 host reference
- [ ] IndexTTS-2.5 跑通
- [ ] 10 条情绪对白测试
- [ ] ASR QC 跑通

## Phase 3 — Character / Studio

- [ ] 主持人 Reference Pack
- [ ] Studio Reference Pack
- [ ] Camera A/B/C/D

## Phase 4 — Dialogue Shot

- [ ] 外部 Audio Master
- [ ] H3 Ref2VA
- [ ] 3–7 秒稳定说话
- [ ] 嘴型 A/B

## Phase 5 — LipSync

- [ ] LatentSync 1.6 独立环境
- [ ] 修 3 个失败 Shot
- [ ] 与原生 H3 比较

## Phase 6 — Edit

- [ ] 自动 timeline
- [ ] Reaction insert
- [ ] Subtitle
- [ ] Room tone

## Phase 7 — Upscale

- [ ] SeedVR2
- [ ] 1080×1920
- [ ] 发布 Master

---

# 49. 参数调优不要“全局找神参数”

建立一个 Benchmark Episode。

固定：

- 同一角色；
- 同一 5 秒台词；
- 同一 Studio；
- 同一 Audio；
- 同一 prompt；
- 同一 resolution。

一次只改一个变量：

```text
A: reference images 数量
B: ref_image_size
C: steps
D: seed
E: performance reference
F: quantization
G: lipsync post process
```

用表格记录：

| Test | Identity | Lip | Acting | Stability | Time | VRAM | Notes |
|---|---:|---:|---:|---:|---:|---:|---|
| A01 | 9 | 7 | 8 | 9 | 120s | 28G | good |

否则你会进入最典型的 ComfyUI 陷阱：

> 节点越来越多，但不知道哪一个真正让结果变好。

---

# 50. 生产指标：每条成片记录什么

### Content

- Hook 类型；
- 首个反转时间；
- 梗数量；
- 总时长。

### Production

- TTS 失败次数；
- H3 总生成 Shot 数；
- 被选中 Shot 数；
- H3 平均尝试次数；
- LatentSync 使用比例；
- 总 GPU 分钟；
- **本集预估成本（按 AutoDL 实际计费换算）**，与第 44.1 节的 `max_gpu_minutes_per_episode` 预算对照，超支/低于预算都要记录一下原因。

### Platform

- 3s retention；
- 5s retention；
- average watch time；
- completion；
- rewatch；
- comment / share。

未来我们要发现：

> 哪种脚本结构不仅好笑，而且生成成本最低。

---

# 51. 自动选片：先做半自动

第一版不要让 AI 自动决定所有东西。

可以自动做：

- face detection；
- blur detection；
- ASR；
- audio duration；
- 黑帧；
- corrupt MP4；
- 简单 identity embedding similarity。

但是：

> “这个眼神够不够损？”

这种暂时让人选。

长期可以训练自己的 preference dataset。

---

# 52. FFmpeg 自动剪辑

Manifest 里每个 Shot 有：

```yaml
in: 0
out: 4.82
video: S004_final.mp4
audio: S004_master.wav
transition: hard_cut
```

Orchestrator 自动：

1. trim；
2. 替换声音；
3. 拼 timeline；
4. 加 room tone；
5. burn subtitle 或输出独立 subtitle；
6. encode preview。

第一版所有镜头都用 **hard cut**。

别刚开始就做花哨转场。

真实访谈节目 95% 的力量来自：

> 正确的切镜时机。

---

# 53. 安全的“真实感”来自哪里

不要追求每一个镜头像广告片。

加入：

- 轻微呼吸；
- 细微头动；
- 不完美的停顿；
- 轻微 room tone；
- 不完全对称的坐姿；
- 偶尔低头；
- Reaction；
- 说完后保持表情 0.4 秒。

不要人为加入：

- 夸张 fake camera shake；
- 强烈胶片颗粒；
- 故意失焦十次；
- 过度压缩假装手机拍摄。

**真实感来自行为，不来自滤镜。**

---

# 54. 对动画 / 怪兽 / 非人角色的嘴型

非人类嘴型不必追求“人类音素 100%”。

越接近：

- 人类嘴；
- 人类牙齿；
- 人类下巴；

LatentSync 越容易发挥。

如果是：

- 长喙；
- 巨型怪兽口器；
- 完全机械面罩；

优先让 **H3 原生生成**。

因为通用人脸 LipSync 模型往往没有对应训练分布。

因此嘉宾的视觉设计阶段要提前问：

> 这个角色以后要说 40 秒台词，它的嘴是否容易被模型稳定驱动？

“好看”不是唯一指标。

---

# 55. H3 Fun ControlNet 什么时候用

当下面问题出现：

- 手势每次都不对；
- 身体前倾 timing 必须准确；
- 想复用真人 performance；
- 角色要有明确坐姿动作；

再启用。

ComfyUI 官方 H3 生态已经支持 Fun ControlNet Union 方向，官方模型仓库也提供对应 patch 文件。

第一阶段最值得测试：

- Pose；
- Depth。

不要同时堆 Canny + HED + Pose + Depth。

---

# 56. H3 Multi-frame Reference 什么时候用

如果你需要：

- Shot 开头必须是某个 pose；
- 2 秒以后切到某个姿态；
- 最后一帧必须落到一个特定表情；

ComfyUI 原生的 `MiniMaxH3AddGuide` 可以在时间轴上的 frame index 锚定：

- 图片；
- clip；
- audio；
- AV clip。

这对 Reaction / 动作控制非常有价值。

但：

> 能用简单 Ref2VA 解决，就不要先上多 guide。

---

# 57. 什么情况下要重生，而不修

直接重生：

- 人脸身份错；
- 眼睛严重异常；
- 头和身体断裂；
- 手挡脸；
- 镜头构图错；
- 人物看错方向；
- 表演完全没情绪。

可以后修：

- 嘴型小错；
- 轻微清晰度；
- 色彩轻偏；
- 音轨问题；
- 小范围 artifact。

原则：

**Post 只修“局部失败”，不救“镜头失败”。**

---

# 58. AutoDL 运行习惯

## 58.1 tmux

所有长服务都跑 tmux：

```bash
tmux new -s comfy
tmux new -s voice
tmux new -s orchestrator
```

## 58.2 日志

```text
logs/
  comfy.log
  tts.log
  orchestrator.log
```

## 58.3 每次启动打印版本

```bash
nvidia-smi
python -c "import torch; print(torch.__version__, torch.version.cuda)"
git -C ComfyUI rev-parse HEAD
```

保存进 `run_info.txt`。

## 58.4 不要没备份就 git pull

生产环境升级：

```text
production branch
↓
复制 / snapshot
↓
测试新版本
↓
Benchmark Episode
↓
通过才切生产
```

---

# 59. 推荐的开发策略：稳定分支 + 实验分支

```text
ComfyUI-prod
ComfyUI-lab
```

Prod：

- 不乱更新；
- 不装随机 node；
- 专门出片。

Lab：

- 新 H3 节点；
- 新 Turbo；
- 新 lipsync；
- 新 VSR。

验证后才 merge 思路到 Prod。

---

# 60. 第一版不要做的事情

- 不要一开始训练 H3 LoRA。
- 不要追 2K 原生。
- 不要做连续 30 秒双人口播。
- 不要同时试 6 个 TTS。
- 不要把所有第三方 ComfyUI 节点都装上。
- 不要追求全自动发布。
- 不要做自动剪辑到“无人审核”。
- 不要为了 batch 数量牺牲台本。

第一阶段唯一目标：

> **20 秒黄金片段像真的。**

---

# 61. 第二阶段才做的优化

当完整 Episode 已经能稳定出：

1. ComfyUI API 批处理；
2. Seed 自动并行；
3. TTS 服务常驻；
4. H3 workflow cache；
5. 自动 ASR；
6. 自动选错误帧；
7. 自动 lipsync 路由；
8. SeedVR2 batch；
9. 自动上传草稿；
10. 数据回传到脚本系统。

---

# 62. 版权 / 角色 / 声音：必须提前考虑

你提到的题材可能包含：

- 动漫人物；
- 奥特曼；
- 影视角色；
- 名人；
- 知名声线。

技术上能做，不代表商业使用天然安全。

## 最安全的长期 IP 路线

优先：

- 原创外星人；
- 原创未来人；
- 原创怪兽；
- 公共领域神话 / 古典人物的原创视觉解释；
- “某种类型的角色”，而不是复制具体受保护影视形象。

对仍受保护的现代动漫 / 特摄角色：

- 商标；
- 角色造型版权；
- 影视素材；
- 演员脸；
- 配乐；
- 原演员声音

都可能有独立权利问题。

如果用于商业账号，要按具体司法辖区、平台规则和使用方式评估。

声音同理：**不要未经许可克隆现实个人的声音用于冒充或商业表达。**

---

# 63. MiniMax H3 许可证：部署前读当前版本

MiniMax H3 权重不是传统 Apache/MIT 模型许可证，而是 **MiniMax H3 Community License Agreement**。

截至 2026-09：

- 官方仓库明确要求遵守 Community License；
- ComfyUI 官方文档提示：自托管 H3 做商业用途，需要关注 MiniMax 商业许可；
- **Comfy 已于 2026-08-30 起正式成为 MiniMax H3 商业许可的官方唯一分销商**，购买入口在 Comfy 官网（Professional / Enterprise 两档，Enterprise 含未蒸馏权重和新版本优先访问）；
- Community License 本身有地域限制，并且**当使用 H3 的商业产品或服务年收入超过 2000 万美元时，需要额外单独的书面授权**——这个阈值早期阶段用不上，但作为长期项目（护城河思路）应该提前知道天花板在哪；
- 通过 Comfy 购买的商业许可同时覆盖 MiniMax 的音频/音乐模型，不只是视频。

因此：

**当项目开始变现前，不要只看二手帖子；重新打开当时最新的 License 原文和 Comfy commercial license FAQ。**

这部分不是技术问题，不能靠“模型是 open weights”自动推导“所有商业用途都无限制”。

---

# 64. 平台 AI 标识

抖音 / TikTok / YouTube 等平台对合成媒体都有自己的标识和误导性内容规则，而且持续变化。

工程上建议：

- 永久保留项目 manifest；
- 保存生成日志；
- 可在 metadata / 文件名中记录 AI 来源；
- 发布前按平台当时规则选择 AI 内容标识。

尤其当内容使用“像新闻采访一样真实”的视觉语言时，**不要把虚构采访包装成真实事实证据。** 喜剧 / 虚构定位可以很真实，但不要制造现实欺骗。

---

# 65. 最终 Production Checklist

## Script

- [ ] 前 3 秒有钩子
- [ ] 10 秒内完成世界观建立
- [ ] 每 6–12 秒有新刺激
- [ ] 嘉宾人格稳定
- [ ] 主持人真的追问
- [ ] 没有百科式长回答
- [ ] 结尾有最后一刀

## Audio

- [ ] Voice ID 正确
- [ ] ASR 通过
- [ ] 停顿自然
- [ ] 重点词正确
- [ ] 无 BGM 污染
- [ ] loudness 基本一致

## H3

- [ ] Character identity
- [ ] Studio continuity
- [ ] Look direction
- [ ] Microphone continuity
- [ ] No extra person
- [ ] No weird hands near face
- [ ] No unexpected speech

## Lip

- [ ] 起嘴时间
- [ ] 收嘴时间
- [ ] 长元音
- [ ] 爆破音
- [ ] 牙齿
- [ ] 下巴
- [ ] 句尾无 ghost mouth

## Edit

- [ ] Reaction 有留白
- [ ] Hard cut timing
- [ ] 没有每句都切得一样
- [ ] Room tone 连续
- [ ] 字幕正确

## Final

- [ ] SeedVR2 / upscale
- [ ] 1080×1920
- [ ] 正确音频
- [ ] 无黑帧
- [ ] 手机全屏检查
- [ ] 平台 AI 标识 / 权利检查

---

# 66. 我建议你真正执行的“第一周计划”

不是按日期死排，而是按 Gate。下面每个 Gate 附了一个**粗略耗时预估**（单人、环境已装好的前提下）——第一次跑流水线几乎必然比预想慢，这个预估是留出容错空间用的，不是承诺。

### Gate A：H3 能稳定跑

成功定义：连续 5 个 5 秒 Shot。
预计耗时：0.5–1 天（不含环境搭建和模型下载时间）。

### Gate B：主持人固定

成功定义：5 个不同台词 Shot 看起来是同一个人。
预计耗时：0.5–1 天。

### Gate C：声音固定

成功定义：20 句不同情绪还是同一个人。
预计耗时：0.5–1 天。

### Gate D：嘴型

成功定义：5 句不同长度，4 句以上无需明显救火；失败镜头 LatentSync 可救。
预计耗时：0.5–1 天。

### Gate E：20 秒黄金片段

成功定义：发给不知道技术路线的人看，他第一反馈是“内容/角色”，而不是“嘴怎么怪怪的”。
预计耗时：1–2 天（这一步经常要返工多次，是全流程里最值得多花时间的地方）。

### Gate F：75–90 秒完整集

这时候才真正做 Episode 001。
预计耗时：2–4 天。

**合计：第一集大概率需要 1–2 周，而不是"一个周末"。** 如果实际进度明显慢于这个预估，大概率不是哪里做错了，而是流水线本来就需要这么久——不要因此临时改架构，先看是不是卡在某个 Gate 的 QC 标准上。

---

# 67. 核心决策总结：不要再来回摇摆

当前 v1 先冻结这些决定：

### 冻结

- 视频核心：**MiniMax H3**
- 总控：**ComfyUI**
- 服务器：**AutoDL GPU**
- 声音架构：**Audio Master First**
- 主声音：**IndexTTS-2.5**
- Voice Design：**Qwen3-TTS**
- 嘴型救火：**LatentSync 1.6**
- 快速嘴型：**MuseTalk 1.5**
- Upscale：**SeedVR2**
- 编排：**Codex + Python + ComfyUI API + FFmpeg**
- 片长：**60 秒以上，优先 70–100 秒**
- 镜头：**短 Shot 拼成整集**

### 需要 Benchmark，不提前迷信答案

- Ref2VA vs FL2VA 在静默 Reaction 的质量；
- H3 原生 Audio vs 外部 Audio + LatentSync；
- Performance Video Ref vs Fun ControlNet Pose；
- IndexTTS-2.5 vs Qwen3-TTS / CosyVoice 在特定角色上的表现；
- H3 Turbo/加速方案对 close-up 嘴型、脸、微表情的影响；
- SeedVR2 3B/7B 的实际收益。

---

# 68. 真正的“护城河”最后会是什么

不是 H3。

所有人都能下载 H3。

真正逐渐积累的是：

```text
Script Bible
+ Character Bible
+ Voice Library
+ Studio / Camera Bible
+ Prompt Templates
+ Benchmark Dataset
+ Successful Seeds / Settings
+ Reaction Grammar
+ QC Rules
+ Codex Orchestrator
+ 观众数据
```

到那个时候，即使明年 H4 / 新模型出来：

> 你换的是“演员渲染引擎”。

节目系统本身不用推倒重来。

这就是为什么从第一天就要把模型放在**生产系统里的正确位置**。

---

# 69. 官方 / 高可信资料索引

以下资料是搭建时应优先看的原始来源。

## MiniMax H3

- MiniMax H3 官方仓库
  https://github.com/MiniMax-AI/MiniMax-H3

- MiniMax H3 官方 README / 模型规格
  https://github.com/MiniMax-AI/MiniMax-H3/blob/main/README.md

- MiniMax 官方 H3 Prompt Writing Skill
  https://github.com/MiniMax-AI/MiniMax-H3/blob/main/.claude/skills/h3-prompt-writing/SKILL.md

- 官方 Ref2VA prompt format
  https://github.com/MiniMax-AI/MiniMax-H3/blob/main/.agents/skills/h3-prompt-writing/references/ref-en.txt

- 官方 Base prompt format
  https://github.com/MiniMax-AI/MiniMax-H3/blob/main/.agents/skills/h3-prompt-writing/references/base-en.txt

## ComfyUI / H3

- ComfyUI 官方 H3 教程
  https://docs.comfy.org/tutorials/video/minimax/minimax-h3

- Comfy-Org H3 模型文件
  https://huggingface.co/Comfy-Org/MiniMax-H3

- ComfyUI 官方 workflow templates
  https://github.com/Comfy-Org/workflow_templates

- Multi-frame guide / H3 Add Guide
  https://github.com/Comfy-Org/embedded-docs/blob/main/comfyui_embedded_docs/docs/MiniMaxH3AddGuide/en.md

## Qwen3-TTS

- 官方仓库
  https://github.com/QwenLM/Qwen3-TTS

## IndexTTS

- 官方仓库
  https://github.com/index-tts/index-tts

## CosyVoice

- 官方仓库（当前归 QwenAudio）
  https://github.com/QwenAudio/CosyVoice

## LatentSync

- 字节官方仓库
  https://github.com/bytedance/LatentSync

## MuseTalk

- 官方项目
  https://github.com/TMElyralab/MuseTalk

## SeedVR2

- ComfyUI 官方集成
  https://github.com/comfyorg/comfyui_seedvr2

- ComfyUI 视频超分指南
  https://docs.comfy.org/tutorials/utility/video-upscale

## MiniMax 商业许可

- Comfy H3 commercial licensing FAQ
  https://support.comfy.org/articles/6065098425-minimax-commercial-licensing-who-needs-a-license-and-how-to-get-one

---

# 70. 最后一条原则

这个项目最容易走错的方向是：

> “怎样把所有 AI 模型接在一起？”

真正正确的问题是：

> **“怎样导演一场让人愿意看完的表演？”**

模型只是部门：

- ChatGPT：编剧室；
- Qwen / IndexTTS：配音演员；
- H3：演员 + 摄影现场；
- LatentSync：口型修复师；
- SeedVR2：后期修复；
- ComfyUI：摄影棚控制台；
- Codex：制片主任 + 自动化工程师；
- 你：总导演。

当这一套跑起来以后，采访谁只是内容层面的变量。

**先把“人为什么像真的在聊天”解决掉，题材就是无限的。**
