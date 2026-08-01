# AGENTS.md

## 项目目标

本仓库只负责第三方 Windows 软件的操作学习与 Shadow Profile 兼容实验。正式 ActionParity / ShadowCore 规范仍以相邻的 `cli+gui兼容的ai时代的软件开放框架` 仓库为准。

## 开发约束

- 与用户使用中文沟通，代码标识和协议字段使用英文。
- 一个 Profile 动作对应一个稳定 Action ID；不要把切标签、移动窗口等纯界面行为冒充业务动作。
- 先收集证据再泛化步骤。不能仅凭一次坐标录制就宣称动作已经学会。
- 定位优先级：原生入口 > UIA/无障碍 > 稳定快捷键 > OCR/视觉 > 相对坐标。裸屏幕坐标只能做最后兜底。
- 录制文本默认遮蔽。不得把密码、验证码、Cookie、令牌、联系人内容或文档正文提交进仓库。
- 执行器默认 dry-run。真实回放必须显式启用，并在 Profile 中遵守风险与确认策略。
- stdout 只输出结果，stderr 输出诊断；`--json` 顶层必须含 `ok`，失败必须含 `error`；退出码为 0/1/2。
- 业务成功要有证据（窗口、控件状态、文件存在、内容摘要等），不能只检查点击是否完成。

## 提交前验证

```powershell
python -m unittest discover -s tests -v
python -m executor.cli validate profiles/chrome/open-url.shadow.json --json
python -m executor.cli validate profiles/wps/create-document.shadow.json --json
```
