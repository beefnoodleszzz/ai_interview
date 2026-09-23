# H3 Prompt Artifacts

每个 Shot 一个最终 Prompt 文本。由 Codex 按官方 `h3-prompt-writing` 结构创作；Python只校验字段顺序、引用、台词原文与哈希，不改写文本。

对白 Shot 从 `templates/h3/ref2va_dialogue.txt` 开始，Reaction Shot 从 `templates/h3/fl2va_reaction.txt` 开始。生成前必须把占位符替换为本集的真实角色、机位、时长和逐字台词。
