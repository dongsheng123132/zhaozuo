# 贡献指南

感谢你帮助“照做”把第三方 Windows 软件变得更容易被 Agent 安全操作。

## 适合贡献的内容

- 更稳定的 UI Automation、快捷键或原生入口定位器；
- 常见失败分支和恢复策略；
- 在不同 Windows、DPI、语言和软件版本上的回归结果；
- 不包含隐私数据的兼容动作档案；
- dry-run、安全确认、成功证据和可访问性改进；
- 文档、测试和安装体验改进。

## 隐私红线

不要提交真实密码、验证码、Cookie、令牌、联系人、聊天内容、文档正文、账号信息或未经授权的截图。`recordings/` 与 `artifacts/` 默认被 Git 忽略；不要绕过这一设置提交真实录制。

如需提供回归样本，请使用虚构账号、虚构文字和专用测试文件，并确认截图中没有任务栏通知、浏览器标签、个人路径等旁路信息。

## 档案状态

- `draft`：假设或单次示范生成，尚未稳定回放；
- `learned`：至少有真实示范和回放证据，但适用范围有限；
- `validated`：在明确的系统、应用版本和变化条件下完成回归。

一次坐标录制不得标记为 `learned` 或 `validated`。提交状态升级时，请在 PR 中说明测试环境、变化条件、定位器降级和成功证据。

## 开发与测试

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python -m pip install -e .
.\.venv\Scripts\python -m unittest discover -s tests -v

.\.venv\Scripts\python -m executor.cli validate `
  profiles/chrome/open-url.action-profile.json --json
.\.venv\Scripts\python -m executor.cli validate `
  profiles/wps/create-document.action-profile.json --json
```

stdout 只应包含结果，stderr 用于诊断；`--json` 顶层必须包含 `ok`。业务成功必须有可观察证据，不能只检查点击是否执行。

## 提交 Pull Request

1. 一个 PR 尽量只解决一个清晰问题；
2. 说明改了什么、为什么改、影响哪些动作；
3. 列出测试和真实回放环境；
4. 明确是否产生外部副作用；
5. 确认没有提交录制隐私或本机路径。

提交贡献即表示你同意按本仓库的 Apache License 2.0 授权该贡献。
