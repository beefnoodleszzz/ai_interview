# 如何向 AutoDL H3 Worker 交接

## 连接要求

远程 ComfyUI 只监听 `127.0.0.1:8188`。SSH 信息通过环境变量提供，不写入仓库：

```bash
export AI_INTERVIEW_H3_HOST=...
export AI_INTERVIEW_H3_USER=root
export AI_INTERVIEW_H3_SSH_PORT=...
```

远程运行根目录固定为 `/root/autodl-tmp/ai_interview`。Job 使用项目专属 `ai-interview-h3-remote-v1` 协议，因此可以复用已经部署的 H3 Worker。

## 交接顺序

1. 本地 `doctor` 与 `validate` 必须通过。
2. `uv run ai-interview package-h3 <episode.yaml> <S###>` 生成 `remote_jobs/<job_id>/job.json`、`prompt.txt`、`assets/` 和 SHA-256。
3. 远端 GPU 已明确授权后，运行 `uv run ai-interview remote-doctor`。
   无卡配置/清理审计可先运行只读 `uv run ai-interview remote-audit`；它不会下载或删除文件。
   语音与后处理环境可分别用 `voice-doctor`、`voice-design-doctor` 和
   `postprocess-doctor --mode {latentsync|seedvr2}` 验证；无卡时预期状态为
   `CONFIGURED_NO_GPU`，这只表示配置就绪，不代表 GPU 推理已验收。
4. 提交：`uv run ai-interview submit-h3 <remote_jobs/job_id>`。
5. 查询：`uv run ai-interview h3-status <episode_id> --shot-id <S###>`；同一 `job_id` 不重复提交。
6. 拉回：`uv run ai-interview pull-h3 <job.json> <remote_results/job_id>`。
7. 本地复核哈希、视频/音频流、时长、比例、解码和候选身份。
8. 只有本地导入成功且结果哈希匹配，才允许清理远端结果。

## LatentSync / SeedVR2 后处理

只对导演判定 `NEEDS_LIPSYNC` 的镜头打包 LatentSync；SeedVR2 只允许对已
`lock-edit` 的镜头打包。提交前远端状态会先按 `job_id` 查询，再要求 worker
报告 GPU `READY`；无卡时不会上传或提交新任务。

无卡配置验收先分别运行 `uv run ai-interview postprocess-doctor --mode latentsync`
和 `--mode seedvr2`。应报告 runtime、输入模型文件/大小（SeedVR2 还检查官方
SHA-256）均通过，GPU 为 `CONFIGURED_NO_GPU`；模型/依赖失败时不得开卡提交。

```bash
uv run ai-interview package-latentsync <episode.yaml> <S###> --selected-by <name>
uv run ai-interview package-seedvr2 <episode.yaml> <S###> --selected-by <name>
uv run ai-interview submit-postprocess <postprocess_jobs/{latentsync|seedvr2}/job_id>
uv run ai-interview postprocess-status <episode_id> [--job-id <job_id>]
uv run ai-interview pull-postprocess <job_id> <episodes/EPxxxx_name/postprocess_results/{mode}/job_id>
uv run ai-interview select-postprocess <episode.yaml> <shot_id> <episodes/EPxxxx_name/postprocess_results/{mode}/job_id> --selected-by <name> [--lip-grade A|B]
uv run ai-interview cleanup-postprocess <job_id> <verified_local_import>
```

远端 worker 使用独立 LatentSync 环境或 SeedVR2 CLI，共用 GPU 锁，校验输入
SHA-256、48 kHz edit audio、模型完整性与镜头/单集预算。拉回时验证结果身份、
SHA-256、ffprobe、完整解码与 deterministic QC；只有已拉回且哈希匹配的完成任务
可以清理，失败任务保留。以上入口已实现，worker 已部署并通过无卡配置 doctor；
真实运行 Smoke 仍需等待用户开卡，不能把代码/配置验收当作推理验收。

当前没有在本轮启动付费 GPU，也没有提交真实任务。实时 Smoke Test 保留在 Beads 远程任务中。
