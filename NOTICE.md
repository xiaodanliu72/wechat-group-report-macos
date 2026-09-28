# 来源与第三方声明

## 实际复用代码

来源：[acmerfight 的 macOS 微信读取工具](https://gist.github.com/acmerfight/0a01249ef72970d07a2603dbb629e80f/c98dbb10cf23c30e83d5d744905101b0880887a0)。固定 revision：`c98dbb10cf23c30e83d5d744905101b0880887a0`。

原始许可：MIT，`Copyright (c) 2026 Contributors`，完整文本保留在 `wechat_local/vendor/LICENSE`。发布准备时已通过 GitHub Gist API 按该 revision 重新获取许可并与本地文本核对一致。

- `wechat_local/vendor/sqlcipher_probe.py` 改编自上游同名文件：只保留内存接收密钥的 DB 包装，去掉读取密钥文件的 CLI；生产连接只读，仅虚构测试允许写入。
- `wechat_local/vendor/capture_ephemeral.py` 改编自上游 `capture_keys.py`：限制到所选数据库盐，候选须经页 HMAC 验证；通过匿名管道传递，不写密钥文件；仅管理本次启动的自有进程。
- 数据表适配参考上游 `wechat_db.py` 中的联系人、消息分片、发送人映射字段；本项目另行实现窗口筛选、完整分页与计数核对。本机表结构匹配已验证，不能推定未来版本兼容。

## 研究参考，未复制实现

- [Tina2088/wechat-group-report](https://github.com/Tina2088/wechat-group-report/tree/e2df0db8ef266f695a25359aab740ece4ab9dacc)，提交 `e2df0db8ef266f695a25359aab740ece4ab9dacc`，Apache-2.0。参考其报告二次校验思路；未复制 Windows 取钥、内存扫描或 WAL 重放代码。
- [LC044/WeChatMsg](https://github.com/LC044/WeChatMsg/tree/5d6d094d8a77c9837d0b7479f79dc6a6c8c677b2)，所查提交缺少可采用的读取源码，未纳入。
- [sjzar/chatlog](https://github.com/sjzar/chatlog/tree/7dad93d7b55a1f801bad3be16620083d2f998599)，所查提交为项目移除说明，未纳入。
- [TANGandXUE/wcdb-key-tool](https://github.com/TANGandXUE/wcdb-key-tool/tree/79f1b5b92e12c66aa281b4a60a3c478b5f547dfa)，MIT，研究 LLDB 路径，未纳入代码。
- [3351666087/wechat-key-macos](https://github.com/3351666087/wechat-key-macos/tree/6594ece61c37bf60aa8fdbb75a5ec2b9cebc8cbb)，Apache-2.0，研究 Frida/重签方案，未纳入代码。

## 外部运行依赖

Pillow 11.3.0、defusedxml 0.7.1 由 pip 安装；SQLCipher、Python 3.14 由用户通过 Homebrew 等渠道安装。Swift、LLDB、codesign、sandbox-exec 来自用户的 Apple 开发工具及系统。中文渲染引用用户系统已安装字体。此发布包不包含这些依赖、字体或微信客户端的二进制，也不授予其商标或软件权利。
