# 开发与构建

公开仓库从当前桌面应用的清理快照开始，本地原开发历史另行保留。开发者建议使用官方 Python 3.12 x64，普通用户下载完整便携包即可。

```powershell
py -3.12 -m venv .venv-packaging
.\.venv-packaging\Scripts\python.exe -m pip install -r requirements-build.txt
```

设置 `FOUR_AI_DATA_DIR` 为专用测试目录的绝对路径，避免测试接触个人数据。完整 Qt 测试在测试进程中使用以下设置，不应全局禁用日常浏览器沙箱：

```powershell
$env:RUN_QT_WEBENGINE_TESTS='1'
$env:QT_QPA_PLATFORM='offscreen'
$env:QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu --no-sandbox'
$env:PYTHONIOENCODING='utf-8'
.\.venv-packaging\Scripts\python.exe -m ruff check main.py launcher.pyw four_ai_consult tools tests packaging
.\.venv-packaging\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp .test-temp-local
```

不设置 `RUN_QT_WEBENGINE_TESTS` 时部分测试会跳过。测试后打开新的 PowerShell，再运行 `packaging\build.ps1`。构建会清理项目内对应生成目录，不要在 dist 中保存个人文件。

发布前阅读发行检查表并核对第三方组件许可。依赖使用版本范围，当前构建不是逐字节可复现构建；发行时应保存确切依赖版本及对应源码获取说明。
