# ShadowCore Learning Demo（影核软件学习试验）

这是一个面向 Windows 第三方软件的 Shadow Profile（软件影子档案）实验项目。

目标不是让 Chrome、WPS 主动改造成影核应用，而是验证一条兼容路线：当软件没有可调用的 Action Core、CLI 或 API 时，系统能否通过少量示范，学习一个稳定的业务动作，并在新的窗口位置和界面状态下复现它。

## 与正式影核协议的边界

- [ActionParity / ShadowCore](../cli+gui兼容的ai时代的软件开放框架/) 是正式协议：软件自身应让 GUI、CLI、MCP、API 共用一个 Action Core。
- 本项目是外部兼容实验：为无法修改的第三方软件建立 Shadow Profile，并用 UI Automation、键盘语义、OCR 和视觉做兜底。
- 外部录制回放不能证明目标软件符合 ActionParity；它只是让没有机器入口的软件暂时可被 Agent 操作。

## 第一阶段试点

| 软件 | 第一个动作 | Action ID | 当前状态 |
| --- | --- | --- | --- |
| Chrome | 打开网址 | `browser.open_url` | 草案 Profile 已建立，待真实录制验证 |
| WPS 文字 | 新建、输入并保存文档 | `document.create_and_save` | 已发现本机 12.1.0.26895，待真实录制 |

学习按“一个软件、一个动作”推进，而不是一次学习整个软件。一个动作通常录制 2～3 遍：基准流程、窗口/初始状态变化、一个常见异常分支。若第一遍已经得到稳定的 UIA 或快捷键定位，第二、三遍只用于验证，不机械追求次数。

## 项目结构

```text
recorder/                 录制会话和原始事件封装
desktop_app/              「照做」Windows 漂浮录制与回放演示器
profile_builder/          从多次示范中提炼候选步骤（当前为设计骨架）
profiles/
  schema/                 Shadow Profile JSON Schema
  chrome/                 Chrome 动作档案
  wps/                    WPS 动作档案
executor/                 Profile 校验与安全执行计划
demos/chrome/             Chrome 试点步骤和验收条件
demos/wps/                WPS 试点步骤和验收条件
docs/                     架构、学习流程和格式说明
recordings/               本机录制数据；默认不进 Git
artifacts/                截图、OCR、报告等；默认不进 Git
tests/                    Profile 与会话契约测试
```

## 现在可以做什么

项目代码只依赖 Python 标准库。建议使用 Python 3.11 或更高版本。

```powershell
python -m unittest discover -s tests -v

python -m executor.cli validate profiles/chrome/open-url.shadow.json --json

python -m executor.cli plan profiles/chrome/open-url.shadow.json `
  browser.open_url --input url=https://example.com --json

python -m recorder.cli new-session `
  --app chrome --task browser.open_url --json

# 启动「照做」桌面演示器
pyw -3.11 -m desktop_app
```

命令行 `plan` 仍然只解析变量并输出执行计划，不会控制键盘鼠标。

「照做」桌面演示器已经可以捕获全局点击、快捷键和遮蔽后的文字输入段，生成候选
Shadow Profile，并在显式授权后执行窗口相对位置回放与窗口标题证据验证。它仍是功能
演示：当前步骤提炼是本地规则，不是多模态 AI；尚未接入 UIA 控件树、OCR 和版本回归。
完整说明见 [`docs/DESKTOP-DEMO.md`](docs/DESKTOP-DEMO.md)。

## 安全原则

1. 录制默认不保存输入正文；密码、验证码、令牌和隐私字段必须遮蔽。
2. 执行器先产生计划和风险说明，再进入真实执行模式。
3. 不使用裸坐标作为唯一定位；优先级为原生入口、UIA/无障碍、稳定快捷键、OCR/视觉、相对坐标。
4. 写文件、发送消息、付款、删除等动作必须在 Profile 中声明副作用与确认策略。
5. 每次执行都要保存可观察证据，不能以“没有报错”代替成功。

## 下一步

1. 用「照做」在本机录制 Chrome `browser.open_url` 两次，比较窗口移动前后的事件。
2. 接入 UIA 控件树，把相对位置点击升级为稳定控件定位。
3. 以本机 WPS 12.1.0.26895 录制 `document.create_and_save`，并确认实际启动进程没有落到残留旧版。
4. 加入失败分支：窗口未出现、目标控件缺失、保存路径已存在。
