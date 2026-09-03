# 第三方组件声明

本项目的 MIT 许可只适用于项目原创部分，不替代第三方组件的许可。各模型名称和商标属于对应权利人；本项目不是官方客户端。

当前开发/打包环境使用 Python 3.12.10、PySide6 / Shiboken6 / Qt 6.11.2、keyring 25.7.0；PyInstaller 6.22.2 为构建工具。构建环境中其他依赖不代表全部被程序使用。

- Python：PSF 及其随附第三方许可，见 https://www.python.org/downloads/release/python-31210/ 。
- PySide6 / Shiboken6：上游元数据声明 LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only。对应源码：https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.2-src/ 。
- Qt 及 Qt WebEngine：开放源码组件保留相应 LGPL/GPL 和第三方许可。Qt WebEngine 内含 Chromium，不能仅附上应用 MIT 许可。说明：https://doc.qt.io/qtforpython-6/overviews/qtwebengine-licensing.html 。
- keyring 与间接 Python 依赖：以安装发行物所附许可为准，来源 https://pypi.org/project/keyring/25.7.0/ 。
- PyInstaller：GPL 与打包应用分发例外，见 https://pyinstaller.org/en/stable/license.html 。

## 二进制发行要求

发行包的 `licenses` 目录保存对应构建环境的版本清单、Python 发行物声明及 Qt 6.11.2 官方第三方声明快照（包括 Chromium）。Qt 声明是上游全集，包含本应用并未使用的部分模块，不表示应用启用了所有组件。`sources.json` 记录原始来源和下载内容哈希。自动收集不能代替人工核对。

源码可从以下上游位置免费取得，与此次未修改的动态库版本对应：

- [Qt 6.11.2 全部模块源码目录](https://download.qt.io/official_releases/qt/6.11/6.11.2/submodules/)，包括 qtbase、qtdeclarative、qtshadertools、qtsvg、qtwebchannel、qtwebengine。
- [Qt Base 6.11.2 源码](https://download.qt.io/official_releases/qt/6.11/6.11.2/submodules/qtbase-everywhere-src-6.11.2.tar.xz)。
- [Qt WebEngine 6.11.2 源码（含 Chromium）](https://download.qt.io/official_releases/qt/6.11/6.11.2/submodules/qtwebengine-everywhere-src-6.11.2.tar.xz)。
- [PySide / Shiboken 6.11.2 源码](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.2-src/pyside-setup-everywhere-src-6.11.2.tar.xz)。

Qt 构建说明：https://doc.qt.io/qt-6/build-sources.html 。PySide 构建说明：https://doc.qt.io/qtforpython-6/building_from_source/windows.html 。若上述对应源码无法取得，请通过仓库 Issue 报告失效链接。

采用 LGPL 的动态库必须允许接收者依许可修改、替换和为调试修改而进行逆向工程；本项目不对这些权利另加限制。应用完整源码及构建步骤公开，但这不代替第三方库本身的对应源码要求。不要把本页当成已完成所有二进制分发义务的证明。
