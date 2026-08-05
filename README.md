# 照做（Zhaozuo）

> 你做一遍，它照做；不是照着点，而是对结果负责。

[English](README.en.md) · [影核（ActionParity）协议](https://github.com/dongsheng123132/action-parity) · [贡献指南](CONTRIBUTING.md) · [安全策略](SECURITY.md)

照做是一款面向 Windows 第三方软件的开源“示范即自动化”工具。它捕获经过隐私遮蔽的人工操作，将操作提炼成可审阅的**兼容动作档案**（Compatibility Action Profile），再按照风险、确认和成功证据策略进行回放。

当前版本是 **v0.1.0 技术预览版**，适合研究、演示和沙箱测试，不应直接用于无人监督的生产自动化。

## 它解决什么问题

理想情况下，软件应按照[影核（ActionParity，亦称 ShadowCore protocol）](https://github.com/dongsheng123132/action-parity)，让 GUI、CLI、MCP、API 和测试共同调用一个无界面的 Action Core。但现实中大量 Windows 软件只有图形界面，无法由使用者改造。

照做提供外部兼容路径：

```text
新软件：Agent ──► Action Core ──► 业务结果
旧软件：Agent ──► 照做 ──► 兼容动作档案 ──► 第三方 GUI
```

外部录制回放**不表示目标软件符合影核协议**。Chrome、WPS 或微信能被照做操作，只代表存在一条受约束的兼容路径。目标软件以后提供正式 CLI、API 或 Action Core 时，档案应优先切换到原生入口。

## 格式：兼容动作档案 v1（已冻结）

档案格式的规范性文档是 **[docs/ACTION-PROFILE-V1.md](docs/ACTION-PROFILE-V1.md)**，Schema 在 [`profiles/schema/action-profile-v1.schema.json`](profiles/schema/action-profile-v1.schema.json)。v1 于 2026-08 冻结，此后只做向后兼容的增补。

格式的重心不在"怎么点"，在三件今天的 GUI Agent 普遍做不到的事：

| 关切 | v1 的回答 |
|---|---|
| 这是哪一个业务动作？ | 稳定唯一的 **Action ID**，中文目标同样不撞车 |
| 这一步会不会造成不可撤销的后果？ | **effect** 声明 + 生效当下确认 + **目标身份断言** |
| 它成功了吗？ | **success_evidence**，且 `validated` 不得只靠窗口标题 |

校验器会**执行**这些规则，而不只是记录它们：拿不出证据的 `validated` 声明会被拒收（缺兼容范围、无通过回归、无提升人、成功证据过弱、对外动作缺目标断言、只靠绝对坐标定位）。

## 当前能力

- 用低级钩子逐事件捕获全局输入：点击、双击、拖拽、滚轮、快捷键与输入焦点；
- 队列满时被丢弃的输入会计入会话摘要并在界面告警，缺步的录制不会看起来像完整录制；
- 采集点击处或输入焦点的 Windows UI Automation 控件身份；
- 文本默认只保存占位符和长度，不保存原文；
- 支持分段补录和应用重启后恢复最近动作；
- 生成带稳定 Action ID、输入契约、风险和成功证据的候选档案；
- 按“UIA → 窗口相对位置 → 绝对坐标”逐级回放并报告降级；
- 回放等的是可观察状态（窗口/控件是否就绪），不是录制时的秒表；无锚点的步骤单独计为降级；
- 发送、发布、点赞等对外动作在最终一步再次确认，并在执行前断言目标窗口身份；
- 成功证据引擎覆盖窗口、UIA 值与开关态、文件、剪贴板、进程；验不了的证据种类判为未通过，不默默放行；
- 默认 dry-run，真实执行必须显式授权，执行中可按 Esc 中止。

尚未完成：完整 UIA 树、多模态步骤理解、OCR、档案库、聊天软件会话流证据、代码签名。进程声明 per-monitor DPI 感知，坐标是物理像素；档案记录录制时的缩放，回放时不一致会被报出来（`python scripts/dpi-check.py` 可在任意机器上自证这台机器的折算比例）。当前步骤提炼使用本地规则，不应宣传为已经具备通用 AI 学习能力。

## 快速开始

要求：Windows 10/11、Python 3.11 或更高版本。

```powershell
git clone https://github.com/dongsheng123132/zhaozuo.git
cd zhaozuo
py -3.11 -m venv .venv
.\.venv\Scripts\python -m pip install -e .

# 启动桌面演示器
.\.venv\Scripts\zhaozuo.exe
```

开发时也可以直接运行：

```powershell
pyw -3.11 -m desktop_app
```

## 下载 Windows 版本

GitHub [Releases](https://github.com/dongsheng123132/zhaozuo/releases) 提供两种 Windows x64 产物：

- `Zhaozuo-Setup-*.exe`：免管理员权限的按用户安装包，数据保存在 `%LOCALAPPDATA%\Zhaozuo`；
- `Zhaozuo-portable-*.zip`：解压即用，数据保存在解压目录的 `data\` 中。

当前技术预览版尚未购买代码签名证书，因此 Windows SmartScreen 可能显示“未知发布者”。请从本仓库 Release 下载，并使用同一 Release 中的 `SHA256SUMS.txt` 核对文件。不要从第三方网盘或转载站下载。

校验和检查兼容动作档案：

```powershell
python -m executor.cli validate profiles/chrome/open-url.action-profile.json --json

python -m executor.cli plan profiles/chrome/open-url.action-profile.json `
  browser.open_url --input url=https://example.com --json
```

`plan` 只解析变量并输出执行计划，不控制键盘鼠标。

对着真机跑一遍成功证据（同样不执行任何步骤），以及把一次真实回放记入回归、提升档案状态：

```powershell
python -m executor.cli evidence <profile> <action> --json

python -m executor.cli record-run <profile> --report <replay-report.json> `
  --app-version 16.0.17 --dpi-scale 1.5 --varied --json

python -m executor.cli promote <profile> --to validated --by <提升人> --json
```

`record-run` 拒绝 dry-run 报告，`promote` 在证据不足时拒绝并且不写文件 ——
`validated` 是一条可审计的链，不是一个能手改的字段。

## 学习流程

学习单位不是“整个软件”，而是一个稳定的业务动作：

1. 命名业务目标并分配 Action ID；
2. 录制最短成功路径；
3. 改变窗口、输入和初始状态再次示范；
4. 提炼变量、定位器、风险和成功证据；
5. 先 dry-run 并人工审阅；
6. 在沙箱中真实回放；
7. 明确版本范围并完成回归后，才能把档案从 `draft` 提升为 `validated`。

一次坐标录制只能生成 `draft`，不能宣称动作已经学会。完整流程见 [docs/LEARNING-WORKFLOW.md](docs/LEARNING-WORKFLOW.md)。

## 示例状态

| 软件 | 动作 | Action ID | 状态 |
| --- | --- | --- | --- |
| Chrome | 打开网址 | `browser.open_url` | `draft` |
| WPS 文字 | 新建、输入并保存文档 | `document.create_and_save` | `draft` |
| 微信等社交软件 | 回复、发布、点赞的安全契约 | 多个 | `draft`，未绑定稳定定位器 |

示例只用于说明档案结构，不等于支持对应软件的所有版本，也不表示本项目与这些软件厂商存在隶属或背书关系。

## 项目结构

```text
desktop_app/              “照做”Windows 漂浮录制与回放界面
recorder/                 隐私安全的录制会话封装
profile_builder/          多次示范到候选动作的提炼设计
profiles/schema/          兼容动作档案 JSON Schema
profiles/                 示例动作档案
executor/                 档案校验、dry-run 与安全执行计划
demos/                    试点步骤和验收条件
docs/                     架构、工作流和格式说明
recordings/               本机录制数据，默认不进入 Git
artifacts/                截图和报告，默认不进入 Git
tests/                    契约与执行安全测试
```

## 安全边界

1. 不要把密码、验证码、Cookie、令牌、联系人内容或文档正文提交到仓库；
2. 截图默认关闭，录制文本默认遮蔽；
3. 真实回放必须显式授权；
4. 对外通信、发布、付款、删除等动作必须声明副作用并在生效前确认；
5. 成功必须由窗口、控件、文件或内容摘要等证据证明；
6. 请只在你拥有或获准操作的电脑、账号和软件上使用。

发现安全问题请阅读 [SECURITY.md](SECURITY.md)，不要在公开 Issue 中发布可直接利用的细节。

## 参与贡献

欢迎提交新的定位器、失败分支、回归样本和第三方软件档案。贡献前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)，尤其注意隐私数据和 `draft` / `learned` / `validated` 的证据要求。

## 许可证

Apache License 2.0，见 [LICENSE](LICENSE)。

“照做 / Zhaozuo”是本项目名称。第三方产品和商标归各自权利人所有。
