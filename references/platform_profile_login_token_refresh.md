# Profile 7 平台登录与 token 刷新说明

本说明用于 MarsPlatform token 失效后优先尝试自动刷新，减少用户反复手动提供 token。

## 结论

- `references/Profile 7` 是一个 Chrome 用户 profile 目录，里面保留了平台本地存储、cookie、历史会话和两个 Chrome Recorder 导出的脚本。
- 当前 profile 可打开平台页面，但本次实测其中保存的 `TOKEN` 已被冻结；直接复用 localStorage 里的 `TOKEN` 不可用。
- 清理冻结的 `TOKEN` 后，可以通过 UI 自动走到 `LSCloud 聆思开发者中心 -> 聆思员工登录 -> 企业微信 WeCom QR` 页面。
- 如果企业微信/LSCloud cookie 仍有效，后续可能自动跳回平台并生成新 `TOKEN`；如果跳到 WeCom QR，则仍需要人工扫码一次。扫码成功后脚本可以自动提取平台 `localStorage.TOKEN`，校验 `/auth/b/getLoginUser`，并写回 `TOOLS.md`。
- 因此：后续 token 失效时，先跑自动刷新脚本；只有脚本输出 `needQrScan=true` 或超时，才需要用户配合扫码/给新 token。

## 原始脚本解析

### `平台登录.js`

该文件是 Chrome Recorder 录制脚本，主要流程：

1. 打开 `https://integration-platform.listenai.com/ai-voice-firmwares/biz/index`。
2. 点击登录页中的 `LSCloud 聆思开发者中心`。
3. 在 LSCloud 页面点击 `聆思员工登录`，进入企业微信登录链路。
4. 录制中间还包含点击头像、`退出登录` 和确认退出，再重新点击 LSCloud 与员工登录。
5. 最后点击平台左侧菜单 `产品管理`。

限制：

- 没有指定 `userDataDir` 和 `--profile-directory=Profile 7`，直接运行不会自动使用 `references/Profile 7`。
- 没有读取 `localStorage.TOKEN`，也不会校验 token 或写回 `TOOLS.md`。
- 使用大量 recorder selector 和坐标点击，页面结构变化时稳定性不够。

### `重新登录.js`

该文件也是录制脚本，主要流程：

1. 打开 `.../biz/prod`。
2. 在登录失效/404 场景点击 `返回首页`。
3. 在弹窗中点击 `重新登录`。
4. 回到登录页后点击 `LSCloud 聆思开发者中心`。

限制：

- 同样未指定 `Profile 7`。
- 只走到 LSCloud 登录入口，没有完成员工登录后的 token 提取。
- 无 token 校验和持久化。

## 推荐脚本

使用已沉淀脚本：

```bash
NODE_PATH="$PWD/scripts/ui/node_modules" \
node scripts/ui/refresh_platform_token_from_profile.js \
  --profile-dir "references/Profile 7" \
  --timeout-ms 180000
```

行为：

1. 默认复制 `references/Profile 7` 到 `/tmp` 后启动 Chrome，避免污染原始 profile。
2. 使用 `--profile-directory=Profile 7` 打开平台。
3. 读取平台 `localStorage.TOKEN` 并调用 `/auth/b/getLoginUser` 校验。
4. 若 token 冻结/失效，清理平台本地 `TOKEN/MENU/DICT_TYPE_TREE_DATA/SNOWY_MENU_MODULE_ID` 后重新进入登录页。
5. 点击 `LSCloud 聆思开发者中心` 和 `聆思员工登录`。
6. 轮询平台回跳后的 `localStorage.TOKEN`；校验通过后写回 `TOOLS.md`。
7. 如果进入 WeCom QR 且超时未扫码，会保存 `wecom_qr.png` 并输出 `needQrScan=true`。

常用参数：

- `--headed`：打开可见浏览器，便于人工扫码。
- `--in-place`：直接使用原始 profile。仅在确认需要复用原 profile 活跃浏览器状态时使用；默认复制模式更安全。
- `--no-persist`：只验证并输出结果，不写 `TOOLS.md`。
- `--out-dir <dir>`：指定证据输出目录。

## token 失效后的处理顺序

1. 先运行脚本默认模式，不需要用户介入。
2. 若输出 `ok=true`，说明新 token 已写回 `TOOLS.md`，后续任务直接继续。
3. 若输出 `needQrScan=true`，打开 `qrPath` 或重新用 `--headed` 执行，让用户扫码。
4. 扫码完成后脚本会继续轮询并写回 token；不需要用户把 token 发到对话里。
5. 若扫码后仍失败，记录脚本输出目录和页面截图，再判断是账号权限、企业微信登录、平台跳转还是网络问题。

## 稳定性监控

如果需要验证“长时间不人工提供 token 时是否能自动恢复”，使用监控脚本按固定间隔执行 token 校验、Profile 刷新和 UI smoke：

```bash
NODE_PATH="$PWD/scripts/ui/node_modules" \
node scripts/ui/monitor_platform_token_refresh_until_9.js \
  --out-dir "artifacts/tasks/profile-token-refresh-stability-$(date +%Y%m%d-%H%M%S)" \
  --until "2026-06-18T09:00:00+08:00" \
  --interval-ms 10800000 \
  --refresh-timeout-ms 180000
```

判定口径：

- `tokenValidAfterRefresh=true` 且 `uiAccessOk=true`：后续 UI 配置自动化具备登录前置条件。
- `refreshOk=true`：Profile 链路成功获取并校验新 token，脚本可写回 `TOOLS.md`。
- `needQrScan=true`：无头链路已经到达企业微信 QR，但当前 Profile 缺少可自动回跳的企业微信/LSCloud 登录态，需要人工扫码一次。
- 多轮均为 `needQrScan=true` 时，不能认为已经摆脱用户 token 依赖，只能认为“自动登录路径可达，缺少有效扫码登录态”。

监控结束后生成脱敏 Markdown 汇总：

```bash
node scripts/ui/summarize_platform_token_refresh_monitor.js \
  --summary artifacts/tasks/<monitor-dir>/summary.json \
  --out artifacts/tasks/<monitor-dir>/final_report.md
```

## 安全规则

- 不在回复、日志摘要、报告中打印 token 明文。
- `references/Profile 7` 含浏览器账号状态和本地存储，必须保持在本机，不同步 git。
- `TOOLS.md` 仍然只作为本机敏感配置，不同步 git。
- 自动化证据目录位于 `artifacts/tasks/platform-token-refresh-*`，不进入 git。
