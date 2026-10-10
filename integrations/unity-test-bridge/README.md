# 只读 Unity 测试快照桥

这是供专用测试构建接入的模块，未声明已安装到游戏、已完成真实业务适配或通过真机测试。`BridgeCompileSmoke` 只验证编译和合成快照序列化。

模块没有 socket、业务写接口、自动点击、金币或等级修改。项目适配器显式实现 `IGameTestStateProvider`，仅读取已经存在的状态，调用 `publisher.Install(provider)` 后使用 `PublishOnce()`，或明确指定采样帧间隔。未安装 provider 时不扫描业务对象、不调用业务 getter。

Runtime 文件在 `UNITY_EDITOR || (DEVELOPMENT_BUILD && AI_TEST_AGENT_BRIDGE)` 条件下编译。普通 release 不包含模块；Development 构建仍需额外设置 `AI_TEST_AGENT_BRIDGE`。发布构建是否确实排除必须在构建流程验证。

每条日志以 `AI_TEST_STATE_JSON:` 开头，包含 schema、session、单调序列、帧号、provider、运行来源、可选 UGUI 和目标状态。`source.available=true` 必须提供实际 build/resource/DLL/server 版本，不能使用本地源码默认配置冒充运行真值。目标状态必须来自项目适配器明确的目标，禁止推测地块与设备升级映射。

coins/cost 必须是精确十进制字符串，不能用 float、界面缩写或未知值填零。未知目标设置 `available=false`；level 的 `-1` 是不可用标记，coins/cost 为空。缺少业务观测时不提供伪造快照。

provider 返回的 cost 是被测业务的观测值，不能单独充当独立期望；升级判定仍须采用人工审核的配置/需求规则，并记录同期收益或该笔升级事务。

UGUI 捕获默认关闭；`Install(provider, everyFrames: 0, captureUi: true)` 可读当前活动 RectTransform 的层级路径和屏幕 bounds。坐标原点为屏幕左上角。bounds 不等于可点击性，不证明未被遮挡，也不允许跳过宿主前台/版本/坐标校验。

宿主 `parse_snapshot_line(line)` 无全局状态，允许 logcat 前缀，拒绝缺字段、错误 schema、重复 JSON 字段、截断、非有限/缩写金额及无效 bounds。`SnapshotStream(expected_build_id=..., expected_provider=...)` 负责跨行序列、帧和运行来源一致性；重复/倒序/版本变化均失败关闭。

Editor 编译入口为 `AiTestAgent.Bridge.Editor.BridgeCompileSmoke.Run`。成功标记 `BRIDGE_COMPILE_SMOKE_PASS` 仅代表合成验证，不能当成生产资源、服务器、真实升级或存档通过。
