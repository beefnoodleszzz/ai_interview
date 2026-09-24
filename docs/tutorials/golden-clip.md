# 制作第一条黄金测试片段

实际操作从 [EP0001 无卡交接清单](../../episodes/EP0001_golden_clip/EP0001_NO_GPU_HANDOFF.md) 和[持续生产操作手册](../how-to/production-runbook.md)开始。本页是镜头节奏概要。

目标是先交付 15–20 秒、五镜头的可验收样片，而不是直接生产 90 秒整集。

1. 从 `episodes/_template` 复制一个 Episode。
2. 保持五个 Shot：嘉宾冷开场、主持人无声 Reaction、主持人追问、嘉宾反转、主持人补刀。
3. 先在 AutoDL 生成两位角色的候选 WAV，拉回本地、人工选 Take，再锁定 48 kHz Audio Master 的停顿与时长。
4. 为对白 Shot 使用 Ref2VA，为无声 Reaction 优先使用 FL2VA。
5. 每镜头先出低成本候选；人工确认身份、视线、表演和构图后才出 Final。
6. H3 原生嘴型通过就保留；仅局部嘴型失败才进入 LatentSync。
7. 本地 hard cut 粗剪，加入连续 room tone，不加花哨转场。
8. 同时通过身份、Studio、声音人格、嘴型、Reaction timing、Room tone 和切镜节奏后，才开放完整集生产。
