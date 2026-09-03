# 四模型会诊 · Four AI Consult

同一个问题，看清不同 AI 的观点，再读有出处的比较。

Windows 桌面应用，从 DeepSeek、Kimi、豆包、通义千问、腾讯元宝和智谱清言中选择四家，独立提问、保留回答并生成比较报告。项目与这些服务商无隶属关系。

> 当前为小范围测试版，不是稳定版。网站改版、账号状态、生成耗时会影响发送和采集；本地测试通过不等于真实网站和其他电脑已通过验收。

## 下载与使用

在 [Releases](https://github.com/Fusu0425/four-ai-consult/releases) 优先下载 `FourAIConsult-版本号-onefile.exe`：它不需要解压软件，双击即可启动；因为每次都要临时展开运行文件，启动会比便携 ZIP 慢一些。熟悉压缩包的用户也可下载 `FourAIConsult-版本号-portable.zip`，完整解压并保留 `_internal` 文件夹后运行。两种版本都不需要安装 Python，但都须使用自己的账号登录模型网站。GitHub 自动生成的 Source code.zip 不是可运行程序。

1. 首次启动完成引导，确认四家网站均已登录并能独立聊天。
2. 输入适合比较的问题，例如两套学习计划的取舍；开始会诊。
3. 在“完整回答”连续阅读各家原文，再看“会诊结论”和分析附页。
4. 有回答未采集时先核对官网是否完整，再尝试补采。未确认完整的材料不应当作已完成结论。

免费网页综合无需 API Key，但受各网站的规则、额度和可用性限制，不承诺永久免费或无限使用。可选 API 加强版需要自己的 Key，可能产生费用。多个模型意见一致不代表事实正确。

## 能做什么

- 六选四、统一提问，四宫格可调整或单格放大。
- 保留各家采集原文、历史记录与 Markdown 导出；长报告分篇展示。
- 比较观点、共识、分歧和下一步，支持免费网页综合及可选 API 综合。
- 单站失败不阻塞其他回答；迟到回答可补采，报告更新会重新核对来源。
- 使用引导、适配诊断和私人历史备份。诊断不等于完整聊天备份。

## 数据与安全

默认数据目录为 `%LOCALAPPDATA%\FourAIConsult`，实际路径见“使用与帮助”。聊天历史保存在本机，模型网站仍会接收你发送的内容；生成综合报告时，会把选定材料发送给所选综合模型。不要公开私人备份、浏览器登录资料、密钥或未经检查的诊断。

详见 [隐私说明](PRIVACY.md)、[安全说明](SECURITY.md) 和 [试用验收计划](docs/PUBLIC_TESTING.md)。反馈入口：[Issues](https://github.com/Fusu0425/four-ai-consult/issues)。

## 开发与构建

建议使用官方 Python 3.12 x64。源码仓库是清理后的当前桌面应用快照，不包含私人开发记录、聊天诊断和宣传录屏，也不部署云端后端。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe main.py
```

普通用户不需要上述步骤。开发测试与打包见 [构建说明](docs/OPEN_SOURCE_BUILD.md)，贡献前阅读 [贡献指南](CONTRIBUTING.md)。

## 开源许可

本项目原创代码按 [MIT](LICENSE) 许可发布。Python、Qt、Chromium 等第三方组件保留各自许可，不因应用使用 MIT 而改变。详见 [第三方声明](THIRD_PARTY_NOTICES.md)。

