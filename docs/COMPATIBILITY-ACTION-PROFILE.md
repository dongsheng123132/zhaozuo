# 兼容动作档案格式（速查）

> **规范性文档是 [ACTION-PROFILE-V1.md](ACTION-PROFILE-V1.md)（v1，已冻结）。**
> 本页是字段速查；两者冲突时以 v1 规范为准。v0.1 档案仍可读取和 dry-run，但不得提升到 `validated`。

Profile 根对象包含：

- `profile_version`：格式版本，当前为 `"1.0"`；
- `profile_id`：应用/平台级档案标识；
- `kind`：`external_ui_adapter`（外部兼容路径）或 `native_action_core`；前者不构成影核符合性声明；
- `status`：`draft`、`learned` 或 `validated`；
- `application`：进程、可执行文件和窗口发现线索；
- `actions`：以稳定 Action ID 为键的动作集合（ID 语法见 v1 规范 §1）；
- `compatibility` / `evidence`：`validated` 必填，声明验证范围与证据。

每个动作至少声明：

- `input_schema`：调用输入；
- `risk` 与 `confirmation`：副作用等级和确认策略；
- `steps`：有唯一 ID 的语义步骤；
- `success_evidence`：成功断言（`validated` 不得只靠窗口标题）；
- `learned_from`：支持此档案的录制会话 ID。

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

带 `effect` 的步骤是对外动作，必须在生效当下单独确认，并通过 `target_assertion`
断言目标身份 —— 人要确认的是「发给谁」，不只是「要不要发」。详见 v1 规范 §3 §4。
