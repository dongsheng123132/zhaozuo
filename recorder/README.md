# Recorder（录制器）

录制器负责保存“发生了什么”，不负责猜业务意图，也不直接生成可执行 Profile。

## 当前能力

`python -m recorder.cli new-session` 仍只负责建立一次隐私安全的录制会话和
`events.jsonl` 信封，因此该命令输出中的 `capture_active` 为 `false`。

真实 Windows 功能演示位于 `desktop_app/`：它采用轮询式全局输入采样，可捕获点击、
快捷键和遮蔽文字段，并生成候选 Profile。当前尚未接入 UIA 树或 OCR，也不会把这版
相对坐标录制标成 `learned` / `validated`。

## 计划采集的数据

- 单调时钟与 UTC 时间；
- 前台进程、窗口标题、窗口矩形和 DPI；
- 鼠标点击及其附近的 UIA 控件；
- 键盘按键类别；输入正文默认只保存长度和摘要，不保存原文；
- 关键步骤前后的截图引用；
- UI Automation 局部树与 OCR 候选；
- 人工标记的步骤开始、成功和失败。

原始事件只保存在 `recordings/`，该目录默认被 Git 忽略。
