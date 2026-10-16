# 只读 Unity 状态与流水桥

这是供专用测试构建接入的模块，未声明已安装到游戏、已完成真实业务适配或通过真机测试。schema1快照入口保持兼容，M3.1新增独立schema2状态/流水发布器；二者均未接入真实游戏Provider。`BridgeCompileSmoke` 和 `ObservationCompileSmoke` 只验证独立合成宿主中的编译、行为和序列化。

模块没有 socket、业务写接口、自动点击、金币或等级修改。项目适配器显式实现 `IGameTestStateProvider`，仅读取已经存在的状态，调用 `publisher.Install(provider)` 后使用 `PublishOnce()`，或明确指定采样帧间隔。未安装 provider 时不扫描业务对象、不调用业务 getter。

Runtime 文件在 `UNITY_EDITOR || (DEVELOPMENT_BUILD && AI_TEST_AGENT_BRIDGE)` 条件下编译；Development 构建仍需额外设置 `AI_TEST_AGENT_BRIDGE`。已用五种编译符号组合检查二进制中的类型是否按guard出现，实际APK/AAB发布构建是否排除仍须在真实构建流程验证。

## M3.1 schema2：明确状态与连续流水

`IGameTestObservationProvider` 提供稳定的 `ProviderId` 和 `ReadObservation()`。在Unity主线程创建 `GameTestObservationPublisher`，显式 `Install(provider)` 后调用 `PublishOnce()`；没有Provider或在后台线程调用均失败，不发现、扫描或推测业务对象。每次重新Install会开始新session。

```csharp
var publisher = host.AddComponent<GameTestObservationPublisher>();
publisher.Install(readOnlyProjectProvider);
publisher.PublishOnce();
```

`readOnlyProjectProvider` 必须由项目单独实现和审核，仅返回已经存在的真实业务状态与完整流水；此示意代码不会自动创建正式适配器。publisher拥有schema/session/seq/frame/provider传输字段，不修改Provider返回值，不进行金额换算，不将available、complete或ready自动改为true。

schema2日志标记为 `AI_TEST_OBSERVATION_JSON:`，根字段精确为 `schema=2, session_id, seq, frame, provider, source, target, wallet, ledger`。DTO包括 `GameTestObservation`、`ObservationTargetSnapshot`、`WalletSnapshot`、`LedgerSnapshot` 和 `LedgerEventSnapshot`；source复用 `RuntimeSource`。

| 区段 | Provider责任 |
| --- | --- |
| source | 提供实际build/resource/DLL/server；未知时available=false且四项为空 |
| target | 明确target/window、非负level、精确cost和显式ready；未知时标识/cost为空、level=-1、ready=false |
| wallet | 明确currency与非负精确balance；未知时available=false且币种/金额为空 |
| ledger | 明确available/complete和连续first_seq..last_seq窗口；未知时false/false、1/0及空events |
| event | 提供seq、唯一transaction_id、operation_id、kind、target_id、currency_id、delta、balance_after；upgrade必须有operation/target，external允许这两项为空 |

金额使用最多1024字符的十进制字符串，delta可有符号，其余业务金额非负；禁止float、指数、缩写、非有限值及用零代替未知。普通文本上限512字符、观测日志4MiB、单窗口4096个事件；普通整数为有符号64位，frame为非负有符号32位。发布器负责序列化，宿主负责严格字段、可用性、边界和跨行校验，Provider仍须保证实际读取含义正确。

Python入口 `parse_observation_line(line)` 保留精确金额文本；`ObservationStream(expected_source=..., expected_provider=...)` 固定单session与完整运行来源，拒绝序列/frame回退、流水窗口回退、重叠事件改变及事务重用，失败不推进状态。Provider提供的cost只是观测，独立expected_cost必须来自审核规则，来源引用字段本身不能认证其真实或独立。

`AiTestAgent.Bridge.Editor.ObservationCompileSmoke.Run` 在独立UnityHost验证合成可用/不可用/不完整数据、精确金额、主线程与未安装Provider失败分支，成功标记为 `OBSERVATION_COMPILE_SMOKE_PASS`。本轮已观察到缺少发布器时RED、实现后GREEN，发布日志通过Python解析；这是合成往返验证，不是真实升级测试。

记录关联还要求设备时间/PID、前后session/seq、目标/窗口/币种、采集时间括号及evidence_root中的实际不同PNG绑定，并用连续事务流水解释同期收益。使用和可移植正负例见[M3.1说明](../../docs/m3/README.md)。离线PASS仅表示记录一致性，始终 `business_status=NOT_EVALUATED`、`execution=device_free`；本环没有设备操作，也不创建M0已执行run。

## 既有schema1快照入口

每条日志以 `AI_TEST_STATE_JSON:` 开头，包含 schema、session、单调序列、帧号、provider、运行来源、可选 UGUI 和目标状态。`source.available=true` 必须提供实际 build/resource/DLL/server 版本，不能使用本地源码默认配置冒充运行真值。目标状态必须来自项目适配器明确的目标，禁止推测地块与设备升级映射。

coins/cost 必须是精确十进制字符串，不能用 float、界面缩写或未知值填零。未知目标设置 `available=false`；level 的 `-1` 是不可用标记，coins/cost 为空。缺少业务观测时不提供伪造快照。

provider 返回的 cost 是被测业务的观测值，不能单独充当独立期望；升级判定仍须采用人工审核的配置/需求规则，并记录同期收益或该笔升级事务。

UGUI 捕获默认关闭；`Install(provider, everyFrames: 0, captureUi: true)` 可读当前活动 RectTransform 的层级路径和屏幕 bounds。坐标原点为屏幕左上角。bounds 不等于可点击性，不证明未被遮挡，也不允许跳过宿主前台/版本/坐标校验。

宿主 `parse_snapshot_line(line)` 无全局状态，允许 logcat 前缀，拒绝缺字段、错误 schema、重复 JSON 字段、截断、非有限/缩写金额及无效 bounds。`SnapshotStream(expected_build_id=..., expected_provider=...)` 负责跨行序列、帧和运行来源一致性；重复/倒序/版本变化均失败关闭。

Editor 编译入口为 `AiTestAgent.Bridge.Editor.BridgeCompileSmoke.Run`。成功标记 `BRIDGE_COMPILE_SMOKE_PASS` 仅代表合成验证，不能当成生产资源、服务器、真实升级或存档通过。
