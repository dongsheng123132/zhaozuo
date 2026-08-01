# Shadow Profiles

Shadow Profile 是对“某个软件中的一个或多个业务动作如何被外部可靠执行”的机器可读描述。

- `schema/shadow-profile.schema.json`：当前实验格式；
- `chrome/open-url.shadow.json`：Chrome 打开网址；
- `wps/create-document.shadow.json`：WPS 新建、输入并保存文档。
- `social/action-contracts.shadow.json`：微信回复、公众号发布和抖音点赞的动作契约草案；

Profile 有三个生命周期状态：

- `draft`：根据任务分析写出的假设，尚未回放；
- `learned`：至少有真实示范支持，定位器仍可能变化；
- `validated`：已在明确的系统/应用版本范围内通过回归。

当前示例均为 `draft`，不能当成已跑通的自动化脚本。社交动作契约尤其只定义输入、
风险、最终确认点与成功证据；必须经过目标软件真实录制和回归后才能执行。
