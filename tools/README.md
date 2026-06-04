`tools/` 用于托管 `mars-belt` 的外部依赖。

当前已接入：

- `audio/`
  - `sources.json`：配置 `listenai-play / listenai-laid-installer` 的 Git 仓库地址与本地 checkout 目录。
  - `repos/`：本地缓存的音频相关依赖仓库。
  - 首次运行缺失时自动 `git clone`，用户显式要求更新时才 `git pull --ff-only`。
- `mail/`
  - `send_email.py`：仓内托管的邮件发送脚本，避免再依赖 skill 外部绝对路径。
  - 邮件账号配置默认从环境变量或当前 skill 根目录 `TOOLS.md` 读取：
    - `MAIL_FROM_ADDR`
    - `MAIL_PASSWORD`
    - `MAIL_SMTP_SERVER`
    - `MAIL_SMTP_PORT`

后续新增外部依赖时，统一放到 `tools/` 下，并按功能拆到简短的二级目录中。

可选环境变量：

- `MARS_BELT_TOOLS_ROOT`
  - 覆盖默认 tools 根目录。
- `MARS_BELT_LISTENAI_PLAY_REPO_URL`
  - 覆盖 `listenai-play` 仓库地址。
- `MARS_BELT_LISTENAI_LAID_INSTALLER_REPO_URL`
  - 覆盖 `listenai-laid-installer` 仓库地址。
