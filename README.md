# LC Transfer / LC 互传

Windows 与 Android 文件互传客户端。电脑端用 PySide6 / Paramiko，Android 端基于 [Primitive FTPd 8.2](https://github.com/wolpi/prim-ftpd/releases/tag/prim-ftpd-8.2) 改造。默认优先局域网发现；跨网络可使用用户自己的 VPN / 虚拟网络地址。

## 功能

- 双向文件、文件夹传输；同名另存，保留修改时间。
- 手机媒体库分类、最近 30 天、按日期分组、搜索和 Ctrl/Shift 多选。分类每批 60 项滚动加载。
- 图片和视频只下载手机生成的小缩略图；双击图片在客户端内加载原图。
- 电脑拖入文件自动上传；单文件原格式下载，手动多选/文件夹下载为 ZIP。
- Android 系统分享“发送到电脑”，支持 APK、单文件和多文件；电脑接收至 Downloads 或自定义目录。
- 两端传输进度/历史；电脑端确认后删除选中的手机文件；阻塞中的连接和传输可取消。
- 首次双方核对配对码，之后固定手机 SSH 身份并使用已批准的电脑公钥。

## 默认连接与配置

公开版不带维护者的手机地址、网段、设备名或密钥。`host` 与 `fallback_host` 默认为空，不探测固定远端。手机默认监听 `0.0.0.0`，SFTP 为 TCP `52022`，局域网发现为 UDP `52023`；这些是公开版协议约定，不是任何人的私有服务。

1. 手机安装自己构建的 APK，打开后授权共享文件访问、通知与必要的后台运行权限。
2. 两端在同一局域网运行互传，首次核对双方六位码并确认身份。
3. 跨网络时，在电脑“连接设置”填写你自己的“备用手机地址”和备用 SFTP 端口，再点“保存设置并自动发现”。默认 LAN 优先；只有配置了备用地址才进行备用探测。两端的虚拟网络由用户自行配置。
4. 电脑默认接收到 Downloads；手机默认接收到 `/storage/emulated/0/Download/互传/`，可配置。

电脑配置、主机指纹、电脑私钥和缓存位于 `%LOCALAPPDATA%/LCTransfer`；不要提交这些运行数据。手机配对身份与主机密钥在应用私有存储；共享存储的 `.lc-transfer` 用于索引、状态和分享暂存，不能提交。

## 从源码运行 Windows 客户端

需要 Python 3.10+；当前主要验证平台为 Windows、Python 3.13、Qt 6.8.3。

```powershell
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt pyinstaller
./.venv/Scripts/python.exe desktop.py
./.venv/Scripts/python.exe -m unittest discover -s tests -v
./build.ps1
```

`build.ps1 -Python <python.exe>` 可选择解释器。便携版目录为 `发布/LC互传/`，必须保留 `_internal`，不能只复制 EXE；构建后生成根目录快捷方式。公开仓库不提供个人环境构建的旧 EXE/APK。

## 构建 Android

需要 JDK 21、Android SDK（android-36 与 build-tools 36.0.0）。使用 `JAVA_HOME` 和 `ANDROID_SDK_ROOT` / `ANDROID_HOME`，不依赖维护者的安装路径。

```powershell
# 可选：下载安装到项目 android/sdk；需要 requests。
python -m pip install requests
python android/prepare_sdk.py
# 按 SDK 提示接受许可证，并安装：
# sdkmanager "platforms;android-36" "build-tools;36.0.0"
./android/build.ps1 -Python ./.venv/Scripts/python.exe
```

Gradle Wrapper 固定版本和校验和。首次构建需要访问 Google/Maven/Gradle 官方仓库。Android 源码位于 `android/upstream/wolpi-prim-ftpd-f0aa774`，保留上游版权声明。

签名脚本在被忽略的 `android/signing/` 创建本地开发密钥；每个构建者应保管自己的密钥，仓库不含维护者密钥或口令。不同签名不能直接覆盖现有安装；不要为了分享源码公开签名私钥。JVM 互通测试需要先构建 Android，并将 `JAVA_HOME` 指向 JDK 21。

## 使用边界

- 这是个人工具的公开源码，尚未经独立安全审计。Android 和 SSH 依赖继承自上游，发布到不受信网络之前应自行审查和更新依赖；建议仅在可信 LAN / VPN 中使用。
- 日期按文件修改时间；HEIC/AVIF 预览暂不支持；GIF 预览只显示首帧。
- 删除是永久删除，只有普通文件可删除；确认框默认取消。无回收站或断点续传。
- 取消会主动关闭当前任务连接，本地未完成下载会清理；中断上传可能在手机留下隐藏 `.part` 文件，已完成文件保留。
- Android 分享先复制临时副本，准备时保持页面打开，电脑离线时排队；完成后清理暂存。来源应用须提供可读文件 URI，纯文本/链接不作为文件传输。
- 两端默认保留最近 100 个传输任务。日志、路径和分享暂存位于用户设备，不应附在公开问题或仓库中。

## 测试与维护

公开版准备时已在 Windows 完成 52 项测试（含 Android JVM/SFTP 互通），并完成 Windows EXE 启动检查与 Android APK 编译/签名验证。公开通用默认值未进行本轮真实手机端到端实测；构建产物和原始测试日志不放入源码仓库。

`python tools/check_public_source.py` 检查待发布文件中常见私钥、令牌、个人目录和禁止提交文件；它不是对所有未知敏感内容的绝对保证。GitHub Actions 运行此检查和 Windows Python 回归测试。没有 Android 编译产物时，JVM 互通测试会明确跳过。

## 开源许可

本项目新增代码按 [Apache-2.0](LICENSE) 公开；上游及第三方代码仍遵循各自的版权和许可证。见 [NOTICE](NOTICE)、[第三方说明](THIRD_PARTY_NOTICES.md) 和 [Android 改造说明](android/MODIFICATIONS.md)。
