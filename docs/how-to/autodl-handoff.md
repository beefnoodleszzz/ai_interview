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
4. 提交：`uv run ai-interview submit-h3 <remote_jobs/job_id>`。
5. 查询：`uv run ai-interview h3-status <episode_id> --shot-id <S###>`；同一 `job_id` 不重复提交。
6. 拉回：`uv run ai-interview pull-h3 <job.json> <remote_results/job_id>`。
7. 本地复核哈希、视频/音频流、时长、比例、解码和候选身份。
8. 只有本地导入成功且结果哈希匹配，才允许清理远端结果。

当前没有在本轮启动付费 GPU，也没有提交真实任务。实时 Smoke Test 保留在 Beads 远程任务中。
