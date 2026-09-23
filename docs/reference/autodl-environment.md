# AutoDL 环境与下载策略

## 固定路径

- 持久根目录：`/root/autodl-tmp/ai_interview`
- 应用源码：`$AI_STUDIO_ROOT/apps`
- 模型：`$AI_STUDIO_ROOT/models`
- 环境：`$AI_STUDIO_ROOT/envs`
- Hugging Face 缓存：`$AI_STUDIO_ROOT/cache/huggingface`
- 安装日志：`$AI_STUDIO_ROOT/logs/install.log`
- 环境与模型清单：`$AI_STUDIO_ROOT/reports`

首次部署时，模型必须直接落入各自的运行目录（`models/voice`、
`models/lipsync`、`models/upscale`），供 AutoDL 服务直接读取。OSS 不是同一
实例的首次下载中转：`源站 → OSS → 同一实例`会增加一次完整上传与下载，只用于
已归档模型的恢复或明确要求的异地备份。模型字节不会经过本机或本机 TUN。

MiniMax H3 的五个既有权重不重复下载。它们从旧目录迁移到
`models/h3`，ComfyUI 通过软链接复用：FL2VA、Ref2VA、Qwen3-VL 文本
编码器、Video VAE 和 Audio VAE。

## AutoDL 学术资源加速

AutoDL 官方加速只用于 GitHub、GitHub 静态资源和 Hugging Face：

```bash
source /etc/network_turbo
```

用完立即关闭，避免影响普通 pip/系统源：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY
```

项目脚本将 GitHub/Hugging Face 下载和普通 pip 安装分段处理。AutoDL
镜像缺少刚发布的 ComfyUI wheel 时，wheel 固定存放在
`cache/wheelhouse`，由远端离线安装。

## 公开数据审计

2026-09-22 通过 AutoDL 控制台与公开数据 API 核对全部 39 项。远端
`/root/autodl-pub` 是只读挂载；公开数据可直接从页面给出的路径读取，
无需复制到数据盘。

当前公开数据没有 MiniMax H3、IndexTTS-2.5、Qwen3-TTS、LatentSync、
SeedVR2、MuseTalk 或 ComfyUI 权重。与项目接近的 Aishell、Vimeo-90k、
DIV2K 等是训练/评测数据集，不是运行必需模型，因此不纳入生产依赖。

## 容量护栏

需求基线建议至少 200GB 可用空间；当前数据盘总容量 120GB。因此当前配置仅允许
顺序部署：不在本地保留模型归档、完成环境安装后清理可再生包缓存、生成任务另行
预留空间。模型部署脚本在每一批前检查剩余空间，空间不足会退出，不会把数据盘
写满。生成前还要为失败 Shot、多 Seed、中间 WAV 和帧缓存预留空间。
