# WeChat Group Report for macOS

让当前 AI 智能体读取本人 Mac 微信已同步的指定群聊记录，读完后生成有消息来源的中文总结、离线 HTML、PNG 长图和 Markdown。

这是 **Skill + 本地读取/渲染工具**，不是独立模型服务。总结由当前 Codex 或具备本地工具权限的智能体完成，无需额外模型 API Key。单独运行导出脚本不会生成语义总结。

## 当前兼容范围

| 项目 | 已验证范围 |
| --- | --- |
| Mac | Apple Silicon，原生 arm64 |
| 系统与客户端 | macOS 27.0 / 微信 4.1.13，仅一台设备现场验证 |
| 运行环境 | Python 3.14.5、SQLCipher 4.19.0、Xcode Command Line Tools |
| 数据 | 微信 4 的本地 `xwechat_files`、联系人库及数字消息分片 |
| 日常读取 | 本机钥匙串复用，三个真实读取回合各成功；未启动第二个客户端 |

其他 macOS/微信版本尚未验证。首次初始化仅实现 Apple Silicon；Intel、Rosetta、Windows 不支持该取钥入口。微信升级、数据库重建或新分片可能使已保存密钥失效，届时停止，不自动重新登录。

**首次初始化会临时登录另一个隔离微信副本，可能顶掉原微信会话。** 本机已实际遇到该情况。初始化需要本人操作手机并恢复原版微信；之后日常读取只用已保存密钥，不再启动客户端。不要把首次初始化描述为无打扰操作。不关闭 SIP、不重签原版应用，也不随包提供微信客户端。

## 安装

下载并解压本仓库，或通过 Git 获取：

```sh
git clone https://github.com/xiaodanliu72/wechat-group-report-macos.git
cd wechat-group-report-macos
```

进入项目根目录后，先安装 Homebrew（如尚未安装，请使用 [Homebrew 官方说明](https://brew.sh/)），再准备依赖：

```sh
xcode-select --install  # 已安装 Command Line Tools 时跳过
brew install python@3.14 sqlcipher
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

依赖安装完成后，在“系统设置 → 隐私与安全性 → 完全磁盘访问权限”中允许实际运行工具的宿主，例如 Codex 或 Terminal。仅在系统要求时重启宿主。

```sh
.venv/bin/python -m wechat_local doctor
.venv/bin/python -m unittest discover -s tests -v
python3 scripts/install_skill.py
```

安装脚本将 Skill 放入 `$CODEX_HOME/skills/wechat-group-report`（未配置时为 `~/.codex/skills/wechat-group-report`），写入本机项目指针。它不会复制微信数据或钥匙串密钥，不覆盖已有 Skill。请保留项目目录及 `.venv`；移动后重新安装或为入口传 `--project`。新会话未发现技能时，让智能体直接读取安装后的 `SKILL.md`。

应用自动识别 `/Applications` 或用户 `Applications` 下的 `微信.app` / `WeChat.app`，并核对 bundle ID。有多个副本或自定义安装位置时，为 `doctor` 和初始化加 `--app '/实际路径/WeChat.app'`。

## 第一次初始化本人账号

先确认微信已同步所需记录，保存工作并正常退出原版微信。理解上述会话切换影响后，显式运行：

```sh
.venv/bin/python -m wechat_local.isolated_bootstrap \
  --group '完整群名' \
  --allow-login-network --accept-session-disruption --save-keychain \
  --out "outputs/init-$(date +%Y%m%d-%H%M%S)" --timeout 240
```

在临时微信中使用本人手机确认登录；看到“登录/进入微信”按钮时，需本人完成。成功输出 `keychain_saved_and_verified: true`；结束后重新打开原版微信并确认正常。已有钥匙串项时程序拒绝重复初始化。取钥失败不是零消息成功，不会生成伪造报告。

若存在多个账号，按报错列出的目录明确指定 `--db-root`；不要混读账号。完整群名有多个匹配时根据返回的稳定群 ID 消歧，再加 `--group-id`。本工具不能替你证明电脑账户归属，仅用于本人或明确获授权的数据。

## 日常使用

对当前智能体说：

> 使用 $wechat-group-report，读取“完整群名”2026 年 9 月 27 日全天的本机已同步记录，读完全部消息后总结，给我 PNG、HTML 和 Markdown。

也支持“昨天全天”“最近 24/48/72 小时”以及明确起止时间。默认最近 24 小时。全天按北京时间 `[当日 00:00, 次日 00:00)`；若是今天，只统计到本次运行开始，并注明尚未覆盖全天。

确定性命令可直接执行：

```sh
python3 scripts/run.py export --group '完整群名' --day '2026-09-27'
python3 scripts/run.py export --group '完整群名' --hours 48
python3 scripts/run.py export --group '完整群名' \
  --start '2026-09-27T09:00:00+08:00' --end '2026-09-27T18:00:00+08:00'
```

每次独立生成目录。`export` 返回 `output` 和 `review/manifest.json`；智能体按 `part_count` 顺序读完所有批次，再依照 `references/report-schema.md` 写 `report.json`：

```sh
python3 scripts/run.py read '/本次输出目录' --part 1
# 继续读到最后一批；完成总结后：
python3 scripts/run.py render '/本次输出目录'
```

`--dry-run` 仅检查时间范围，不触碰账号数据。当前智能体承担语义总结与证据核对，脚本不会调用远程模型。使用云端智能体时，供它阅读的消息文本会进入其会话上下文，因此不是“聊天内容始终不离开电脑”的离线 AI 方案；请根据自己的数据要求选择宿主。

## 交付文件

v0.2.2 保留大师风格杂志版的字体与布局，优先输出完整单张长图；默认同时生成杂志版与标准版，优先分享杂志版：

- `report-master.html`：米白纸感、红色强调的杂志版离线网页，完整统计时间，重要结论附可展开的消息来源。
- `report-master.png`：同风格中文长图，默认高度上限 16000 像素，保持文字大小和完整内容；超长时按版块边界输出 `report-master-02.png` 等编号分图，按 `master-validation.json` 清单交付全部。

以下标准文件继续保留：

- `messages.json`：群及时间范围、计数、结构化消息、稳定消息 ID。
- `messages.txt`：完整可读消息及已解析卡片字段。
- `report.json`：有真实消息 ID 引用的结构化总结。
- `summary.md`：中文总结及来源编号。
- `index.html`：无需服务、字体 CDN 或远程脚本的离线网页，来源默认折叠。
- `report.png`：中文长图；过长时另有 `report-02.png` 等，按 `validation.json` 清单交付全部。

区分建议、决定、收到、同意和执行完成。待办的负责人及期限没有明确依据时写“未明确”。未解析的图片、视频、语音和表情只标类型；仅使用消息内已有语音转写并注明来源。群内自述不等于外部核实事实。

可选的 `master.json` 仅调整标题、副标题和按真实消息 ID 选择的时间线；统计、结论和待办始终来自校验后的原始导出与 `report.json`。字段见 [杂志版配置](references/master-schema.md)。旧版上传模板中的自定义统计、正文等覆盖字段会明确拒绝，需迁移到规范的 `report.json`，避免展示与来源不一致。

```sh
# 只重绘杂志版，不重新读取微信
python3 scripts/render_master.py '/已有输出目录'
# 只生成原标准版
python3 scripts/run.py render '/已有输出目录' --standard-only
```

升级已有 Skill 时，先把旧 Skill 移到技能目录之外备份，再从更新后的完整项目运行 `python3 scripts/install_skill.py`。不要删除项目虚拟环境或账号钥匙串项；此次版式升级不需要重新初始化微信。

## 数据处理与限制

原始数据库只读；完整必要加密 DB/WAL 复制前后核对哈希，变化时重试，仍不稳定则拒绝读取。SQLCipher 原生处理分页、认证和已提交 WAL，不手工重放。只查询目标群、时间窗口及必要发送人映射，逐页读完并核对 SQL 计数；相同 ID 内容冲突则停止。

密钥经匿名管道传递，用户显式选择的复用模式仅存入本机项目专用 login Keychain 项，`synchronizable=false`。无明文数据库导出；输出报告本身包含明文聊天摘录，应妥善保管。不会自动发布或发送。Python 不能保证秘密内存物理清零；普通异常/中断清理临时目录，SIGKILL 或断电后需人工检查残留。

“本地已同步记录”不代表完整群历史。发送人目前采用通讯录备注/昵称，尚未解析群专属昵称。读到 0 条不能证明群内无人发言。

## 虚构示例与排错

无需登录微信即可运行（只使用合成数据）：

```sh
.venv/bin/python -m tests.make_demo
open outputs/fixture-20260928/index.html
```

示例目录存在时程序拒绝覆盖；请保留原结果，将示例脚本中的输出目录改为新的测试目录再运行，不覆盖真实输出。

- `Operation not permitted`：核对实际宿主的完全磁盘访问权限，不必给临时副本加权限。
- `--app` 匹配失败：明确指定真实微信应用；工具不会自动选择多个副本。
- 密钥缺失或 HMAC/完整性失败：停止；先核对本人账号、客户端版本和本地数据状态，不猜参数、不自动取钥。
- 取钥超时：本机离线启动未能取钥；首次初始化需要明确允许正常登录联网并接受会话切换，不能用于日常重试。
- 数据库持续变化：稍后重试；不跳过一致性检查。
- zstd 模块缺失：使用项目 Python 3.14 虚拟环境。
- SQLCipher 未找到：安装 Homebrew `sqlcipher`；特殊路径可通过 `SQLCIPHER_LIBRARY` 指定库文件。
- 钥匙串访问被拒：由本人在 macOS 完成授权，不在聊天中输入系统密码或导出密钥。

仅删除本项目当前账号的钥匙串项：`.venv/bin/python -m wechat_local keychain forget`。删除后不能继续复用，需本人另行决定是否重新初始化。

提交问题时仅提供脱敏错误、版本与复现步骤，不上传真实聊天、账号目录、数据库、密钥或登录配置。验证范围见 [VALIDATION.md](docs/VALIDATION.md)，来源与修改见 [NOTICE.md](NOTICE.md)。

## 许可证

本项目以 [MIT](LICENSE) 开源，复用文件保留原始 [MIT 许可](wechat_local/vendor/LICENSE) 与来源说明。MIT 允许使用、修改与再分发，但需保留版权及许可声明；许可文本见 [Open Source Initiative](https://opensource.org/license/mit)。不包含微信、macOS、SQLCipher 或字体二进制；外部依赖按各自许可证分发。非腾讯官方项目。
