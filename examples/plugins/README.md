# 扩展模型示例

新模型应继承 `timepilot.plugins.ModelPlugin`，至少实现 `capabilities`、`fit`、`predict`，并确保 `fit` 在其输出目录中保存 `model.json` 或重写 `load`。对依赖过去历史窗口的模型，应另外实现 `predict_from_history`。

目前 `PluginRegistry` 采用显式注册。新插件经测试后添加到注册列表。后续将通过 `entry_points` 实现第三方自动发现，并增加插件隔离与权限检查。

禁止插件在 `validate`、`capabilities` 或模块导入期间进行网络连接、读取企业原始数据或写入正式生产系统。
