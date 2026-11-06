# 第三方软件

## Android

Primitive FTPd 8.2，作者 wolpi 及贡献者。
项目：https://github.com/wolpi/prim-ftpd
版本：https://github.com/wolpi/prim-ftpd/releases/tag/prim-ftpd-8.2
许可证：Apache License 2.0，全文随附于 `android/LICENSE-PrimitiveFTPd.txt`。
上游原版为 Primitive FTPd 8.2；`发布/LC互传-安卓.apk` 是本项目改造后独立签名的版本。修改包括包名、应用标签、简洁主页面、原生传输列表、局域网及虚拟网 UDP 发现、公钥配对、接收目录配置、可选文件打开应用、共享存储路径限制、Ed25519 签名兼容修复、MediaStore 分类索引与手机端图片/视频缩略图。完整修改源码随附于 `android/upstream/wolpi-prim-ftpd-f0aa774`，并保留上游版权和许可。

## Windows 客户端依赖

- Paramiko — https://www.paramiko.org/ — LGPL-2.1-or-later。
- PySide6 / Qt for Python — https://doc.qt.io/qtforpython-6/ — LGPL-3.0 / GPL / 商业许可。本项目使用目录式动态链接发布，保留 `_internal/PySide6` 动态库以便替换。
- Pillow — https://python-pillow.github.io/ — MIT-CMU / HPND。
- cryptography — https://github.com/pyca/cryptography — Apache-2.0 / BSD。
- bcrypt — https://github.com/pyca/bcrypt — Apache-2.0。
- PyNaCl — https://github.com/pyca/pynacl — Apache-2.0。
- Python — https://www.python.org/ — PSF License。

打包时将安装环境中各依赖的许可证复制到发布目录的 `licenses` 子目录。Windows 客户端源码在本项目中随附；开发者可安装 PySide6 后修改和重新打包。

便携包固定使用 Qt / PySide6 6.8.3，源码可从以下官方版本取得：

- https://github.com/qt/qtbase/tree/v6.8.3
- https://code.qt.io/pyside/pyside-setup.git/tree/?h=v6.8.3
- https://github.com/qt/qtsvg/tree/v6.8.3
- https://github.com/qt/qtimageformats/tree/v6.8.3

本应用不限制为调试 Qt 修改而进行的逆向工程；可以替换发布目录的 Qt 动态库，也可以用随附客户端源码重新链接/打包。
