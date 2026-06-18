# 执行上下文与 token 消耗控制规则

## 背景

长线程、巨大的 `plan.md`、浏览器/烧录长日志和报告全文会快速消耗模型上下文与费用。Mars-belt 后续执行必须优先把证据落地到文件，只在对话里输出结论、关键路径和下一步。

## plan.md 管理

- `plan.md` 只保留当前任务状态、最近结论、待办和重要路径。
- 历史计划超过约 200KB 时必须归档到 `artifacts/plan_archive/plan_full_<timestamp>.md`，再生成精简版 `plan.md`。
- 归档后仍满足 AGENTS：每次启动读取当前 `plan.md`，但不再反复读取近 MB 级历史。
- `plan.md` 和归档文件属于本机运行状态，不同步 git。

## 命令输出控制

- 默认把长输出写入 `artifacts/tasks/<task>/`，回复中只给摘要和路径。
- 禁止无过滤打印：
  - Chrome/Chromium 完整进程参数
  - 大段串口日志
  - 大型 JSON/HTML/CSV
  - `lame`/编译/烧录完整流水日志
  - token、cookie、profile 存储内容
- 查看进程时只输出 PID、状态、关键脚本名；确需完整命令时写到文件。
- 搜索必须排除噪声目录：`artifacts/`、`scripts/artifacts/`、`node_modules/`、Chrome profile、缓存目录。

示例：

```bash
rg -n "keyword" SKILL.md references scripts \
  --glob '!artifacts/**' \
  --glob '!scripts/artifacts/**' \
  --glob '!**/node_modules/**' \
  --glob '!references/Profile*/**'
```

## 大文件读取

- 读取大文件时先 `wc -c` 或 `stat`，再按关键行 `sed -n`/`rg -n` 精确读取。
- 不要全文输出 `plan.md`、报告、运行日志和生成物。
- 对 HTML/报告只抽查标题、结论、关键表格字段和编码。

## 后台进程

- 长任务必须记录 PID、命令、输出目录。
- 任务取消或切换方向时，必须关闭本任务启动的后台进程和浏览器 profile 锁。
- 只杀匹配本任务目录/profile 的进程，禁止误杀用户自己的浏览器或其他任务。

## git 同步前检查

- 同步前构建可迁移副本，排除：`TOOLS.md`、`plan.md`、`artifacts/`、`profiles/`、`tokens/`、Chrome `Local State`、`Singleton*`、`DevToolsActivePort`、缓存和烧录临时 `app.bin`。
- 同步前校验 JSON/Markdown/zip 可读，避免乱码和损坏文件。
- 回复只报告 commit、push 状态和关键变更，不粘贴完整 diff。

## artifacts 知识沉淀

- `artifacts/` 只保存本机历史原始证据，不同步 git，也不作为其他 PC 使用 skill 的必要输入。
- 大任务完成后，必须把可复用经验抽象到 `references/`、稳定脚本或 `assets/`，不要只留在任务报告和串口日志里。
- 历史任务族与已沉淀文件的对应关系见 `references/artifacts_knowledge_index.md`。
- 遇到烧录、串口、语音注册、多唤醒、播报合成、SDK 或报告类已知现象时，先按 `references/known_issue_diagnosis_matrix.md` 执行最短闭环；只有需要追溯本机原始现场时才回看 `artifacts/`。
