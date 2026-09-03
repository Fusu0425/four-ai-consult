# 第三方组件声明

本项目的 MIT 许可只适用于项目原创部分，不替代第三方组件的许可。各模型名称和商标属于对应权利人；本项目不是官方客户端。

当前开发/打包环境使用 Python 3.12.10、PySide6 / Shiboken6 / Qt 6.11.2、keyring 25.7.0；PyInstaller 6.22.2 为构建工具。构建环境中其他依赖不代表全部被程序使用。

- Python：PSF 及其随附第三方许可，见 https://www.python.org/downloads/release/python-31210/ 。
- PySide6 / Shiboken6：上游元数据声明 LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only。对应源码：https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.2-src/ 。
- Qt 及 Qt WebEngine：开放源码组件保留相应 LGPL/GPL 和第三方许可。Qt WebEngine 内含 Chromium，不能仅附上应用 MIT 许可。说明：https://doc.qt.io/qtforpython-6/overviews/qtwebengine-licensing.html 。
- keyring 与间接 Python 依赖：以安装发行物所附许可为准，来源 https://pypi.org/project/keyring/25.7.0/ 。
- PyInstaller：GPL 与打包应用分发例外，见 https://pyinstaller.org/en/stable/license.html 。

## 二进制发行要求

运行 `tools.collect_licenses` 收集对应构建环境的版本清单、Python 发行物声明和实际 WebEngine 内嵌 Chromium credits。此自动收集不能代替人工核对：本机部分 Qt wheel 仅附商业条款文本，必须补齐实际采用的开源许可、版权声明及对应版本源码获取材料，才能公开发行新的二进制包。

采用 LGPL 的动态库必须允许接收者依许可修改、替换和为调试修改而进行逆向工程；本项目不对这些权利另加限制。应用完整源码及构建步骤公开，但这不代替第三方库本身的对应源码要求。不要把本页当成已完成所有二进制分发义务的证明。
