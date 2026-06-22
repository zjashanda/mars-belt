# listenai-token-tool 登录态与 token 获取流程

## 目的

`references/listenai-token-tool.zip` 用于“每台电脑首次扫码一次，后续尽量复用本机浏览器 profile 自动获取/刷新 token”。该工具优先于让用户反复在对话里提供 token，但不能保证永久免扫码；企业微信、LSCloud 或平台安全策略要求重新验证时仍需要人工扫码。

## 基本规则

- 工具 zip 可以随 skill git 同步；解压后的 `profiles/`、`tokens/`、截图、日志和本机 cookie/token 不允许同步。
- 每台 PC 第一次使用都必须在该 PC 本机扫码一次，生成本机持久 profile；不要复制其他电脑的 profile。
- 本 skill 推荐把工具解压到稳定本机目录：`artifacts/state/listenai-token-tool/`。`artifacts/` 不进 git，但会在当前电脑保留登录态，避免每次任务解压到一次性目录后丢失 profile。
- 第一次使用必须在本机生成持久 profile：`artifacts/state/listenai-token-tool/profiles/listenai/`。
- 成功后应生成：
  - `artifacts/state/listenai-token-tool/tokens/listenai-token.json`
  - `artifacts/state/listenai-token-tool/tokens/listenai-token.txt`
- 只有 token 通过 `/ai-voice-firmwares/api/backend/auth/b/getLoginUser` 校验后，才允许写回 skill 根目录 `TOOLS.md`。
- 报告、日志摘要和回复中不得打印 token 明文，只能打印 token 长度、脱敏值或校验状态。

## 浏览器选择与缺失处理

token-tool 依赖 Puppeteer，但首次扫码必须有可见浏览器窗口；不要用截图方式展示二维码。

执行前按顺序探测浏览器：

```bash
command -v google-chrome || command -v chromium || command -v chromium-browser || true
```

推荐优先级：

1. `/usr/bin/google-chrome`
2. `chromium`
3. `chromium-browser`
4. 其他 Chromium 系浏览器，需显式传 `--chrome <path>` 或设置 `CHROME_PATH=<path>`

如果本机没有 Google Chrome：

- **有 Chromium**：直接用 Chromium 路径执行，例如 `node token-tool.js init --chrome /usr/bin/chromium`。
- **没有任何浏览器**：优先安装系统 Chrome/Chromium，再执行初始化；Linux 可用发行版包管理器或公司标准软件源安装。
- **不能安装系统浏览器但网络可用**：可不设置 `PUPPETEER_SKIP_DOWNLOAD`，让 `npm install` 下载 Puppeteer 自带浏览器；该方式可能较慢，且仍需要本机图形依赖。
- **无桌面/无 DISPLAY**：不能完成首次扫码；需要 VNC/X11/本机桌面会话。已有有效 profile 后才可以 headless 刷新。

Linux 桌面会话存在但当前 shell 没有 `DISPLAY` 时，先探测：

```bash
ls -la /tmp/.X11-unix
ps -eo pid,user,cmd | grep -E 'gnome-shell|Xwayland|Xorg' | grep -v grep
```

若确认当前用户桌面是 Xwayland `:0`，可显式注入：

```bash
DISPLAY=:0 \
XAUTHORITY=/run/user/1000/.mutter-Xwaylandauth.* \
XDG_RUNTIME_DIR=/run/user/1000 \
WAYLAND_DISPLAY=wayland-0 \
DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
node token-tool.js init --chrome /usr/bin/google-chrome --timeout-ms 900000
```

## 标准初始化

在 skill 根目录执行，先把工具解压/同步到稳定本机目录：

```bash
mkdir -p artifacts/state/listenai-token-tool
python3 - <<'PY'
from pathlib import Path
import zipfile
out = Path('artifacts/state/listenai-token-tool')
with zipfile.ZipFile('references/listenai-token-tool.zip') as z:
    z.extractall(out)
PY
cd artifacts/state/listenai-token-tool
PUPPETEER_SKIP_DOWNLOAD=1 npm install
node token-tool.js init --chrome /usr/bin/google-chrome --timeout-ms 900000
```

如果当前 shell 没有 `DISPLAY`，但本机存在 GNOME/Wayland 桌面，可显式注入桌面环境：

```bash
DISPLAY=:0 \
XAUTHORITY=/run/user/1000/.mutter-Xwaylandauth.* \
XDG_RUNTIME_DIR=/run/user/1000 \
WAYLAND_DISPLAY=wayland-0 \
DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
node token-tool.js init --chrome /usr/bin/google-chrome --timeout-ms 900000
```

若桌面 Chrome 已打开扫码页，用户扫码后不要立刻关闭浏览器；必须等页面回跳到 MarsPlatform 首页/产品页，且工具日志输出已保存 token。

如果 `npm install` 因 Puppeteer 下载 Chromium 卡住，而本机已有系统 Chrome/Chromium，应终止本次安装，改用：

```bash
PUPPETEER_SKIP_DOWNLOAD=1 npm install --no-audit --no-fund
```

再通过 `--chrome <path>` 指定系统浏览器。

## 免扫码获取验证

首次扫码成功后，必须验证后续免扫码路径：

```bash
node token-tool.js get --headless --chrome /usr/bin/google-chrome --timeout-ms 180000
```

判定：

- `PASS`：生成/更新 `tokens/listenai-token.txt`，token 校验通过，写回 `TOOLS.md` 后平台 API 可访问。
- `NEED_SCAN`：进入企业微信 QR 或 LSCloud 登录页，说明本机 profile 登录态不足，需要重新扫码。
- `FAIL_NO_TOKEN`：扫码后停留平台登录页，`localStorage.TOKEN` 为空，`tokens/` 无 token 文件，说明扫码没有完成平台回跳或账号未进入平台。
- `SCRIPT_LIMITATION`：页面可人工登录，但脚本反复点击 LSCloud、未跳转或无法识别新窗口，需要记录人工操作并修正脚本。

后续平台自动化使用 token 的默认链路：

```bash
cd artifacts/state/listenai-token-tool
node token-tool.js get --headless --chrome /usr/bin/google-chrome --timeout-ms 180000
```

成功后读取 `tokens/listenai-token.txt`，通过 `/getLoginUser` 校验，再写回 skill 根目录 `TOOLS.md`。如果进入二维码页，必须打开桌面浏览器让用户扫码，不要只截二维码图片。

## 当前已验证现象

- 2026-06-22：本机已按稳定目录 `artifacts/state/listenai-token-tool/` 完成桌面 Chrome 扫码；`get --headless --chrome /usr/bin/google-chrome` 复用 profile 获取 token 通过；写回 `TOOLS.md` 后 `/getLoginUser` 校验通过。
- 本机可通过 `DISPLAY=:0` 打开真实桌面 Chrome，不必只能用 headless 截图。
- 仅截取 QR 图片容易过期；优先打开桌面 Chrome 让用户直接扫码。
- 一轮扫码后若浏览器仍停在平台登录页，且 `localStorage` 没有 `TOKEN`，不能认为“扫码成功”。
- `get --headless` 反复点击 `LSCloud 聆思开发者中心` 但没有拿到 token 时，不要继续空转；应立即记录页面状态、cookie、网络请求并切换人工操作录制。
- 人工操作录制应记录点击、输入、URL、关键网络请求和 token 长度，但不得记录 token 明文。

## 手动操作录制口径

当用户要求“我手动操作，你记录”时：

1. 使用同一个持久 profile 打开桌面 Chrome。
2. 通过 DevTools 注入记录器，记录：点击、输入、change、submit、URL 跳转、ListenAI/WeCom 网络请求、`localStorage` key 列表、`TOKEN` 长度。
3. 日志写入 `artifacts/tasks/<task>/operation_log.jsonl`、`state_log.jsonl`、`network_log.jsonl`。
4. 记录完成后只汇总关键步骤，不在回复中粘贴长日志。
5. 如果不再录制，必须停止 recorder 和使用该 profile 的 Chrome，并清理 `DevToolsActivePort`、`Singleton*`、`LOCK`。

## 写回 TOOLS.md

校验通过后再执行写回。注意 `tokens/listenai-token.txt` 中的内容可能是 JSON 字符串字面量，写回前要做一次 normalize：

```bash
python3 - <<'PY'
from pathlib import Path
import json
src = Path('tokens/listenai-token.txt')
raw = src.read_text(encoding='utf-8').strip()
try:
    parsed = json.loads(raw)
    token = parsed.strip() if isinstance(parsed, str) else raw
except Exception:
    token = raw.strip().strip('"')
assert len(token) > 20
p = Path('/home/bszheng/.openclaw/skills/mars-belt/TOOLS.md')
lines = p.read_text(encoding='utf-8').splitlines() if p.exists() else []
seen = False
out = []
for line in lines:
    if line.startswith('LISTENAI_TOKEN='):
        out.append('LISTENAI_TOKEN=' + token)
        seen = True
    else:
        out.append(line)
if not seen:
    out.insert(0, 'LISTENAI_TOKEN=' + token)
p.write_text('\n'.join(out).rstrip() + '\n', encoding='utf-8')
PY
```

## 禁止事项

- 不要把 `profiles/listenai/`、`tokens/listenai-token.*`、扫码截图或 `TOOLS.md` 提交到 git。
- 不要用旧 token 冒充工具刷新成功。
- 不要在平台登录页停留时判定扫码成功；必须看到 token 文件或平台 API 校验通过。
- 不要让 headless 脚本长时间反复点击同一入口；超过 2-3 次无跳转就应停止并切人工录制。
