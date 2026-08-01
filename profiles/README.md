# 兼容动作档案

兼容动作档案描述“如何从外部可靠执行第三方软件中的一个或多个业务动作”。它是照做的兼容资产，不是影核（ActionParity）符合性声明。

- `schema/compatibility-action-profile.schema.json`：当前实验格式；
- `chrome/open-url.action-profile.json`：Chrome 打开网址；
- `wps/create-document.action-profile.json`：WPS 新建、输入并保存文档；
- `social/action-contracts.action-profile.json`：微信回复、公众号发布和抖音点赞的动作契约草案。

档案有三个生命周期状态：

- `draft`：根据任务分析写出的假设，尚未回放；
- `learned`：至少有真实示范支持，定位器仍可能变化；
- `validated`：已在明确的系统和应用版本范围内通过回归。

当前示例均为 `draft`，不能当成已跑通的自动化脚本。社交动作契约尤其只定义输入、风险、最终确认点与成功证据；必须经过目标软件真实录制和回归后才能执行。

格式说明见 [`docs/COMPATIBILITY-ACTION-PROFILE.md`](../docs/COMPATIBILITY-ACTION-PROFILE.md)。
