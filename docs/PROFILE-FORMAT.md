# Shadow Profile 格式（v0.1）

Profile 根对象包含：

- `profile_version`：实验格式版本；
- `profile_id`：应用/平台级档案标识；
- `kind`：固定为 `external_ui_adapter`，明确它不是应用内部 Action Core；
- `status`：`draft`、`learned` 或 `validated`；
- `application`：进程、可执行文件和窗口发现线索；
- `actions`：以稳定 Action ID 为键的动作集合。

每个动作至少声明：

- `input_schema`：调用输入；
- `risk` 与 `confirmation`：副作用等级和确认策略；
- `steps`：有唯一 ID 的语义步骤；
- `success_evidence`：成功断言；
- `learned_from`：支持此 Profile 的录制会话 ID。

## 步骤示例

```json
{
  "id": "focus_address_bar",
  "kind": "keyboard.shortcut",
  "args": { "keys": ["CTRL", "L"] },
  "fallback": [
    {
      "kind": "uia.focus",
      "locator": {
        "control_type": "Edit",
        "name_any": ["地址和搜索栏", "Address and search bar"]
      }
    }
  ]
}
```

`fallback` 是显式声明的降级，不允许执行器临时猜一个坐标继续点击。

动作输入用 `${name}` 引用。校验器会拒绝未在 `input_schema.properties` 中声明的占位符。
