# 只读 Unity 状态与流水桥

这是供专用测试构建接入的模块。schema1快照入口保持兼容，M3.1新增独立schema2状态/流水发布器；M3.2增加IdleOutpost只读适配与加载身份。当前加载身份及manifest为schema2。R3已修复Android IL2CPP身份读取和Main窗口映射，完成资源1019专包的两轮实际安装启动及只读闭环，等待用户验收后合并；业务升级、金币扣减和完整流水仍未评估。

模块没有 socket、业务写接口、自动点击、金币或等级修改。项目适配器按所用协议显式实现`IGameTestStateProvider`或`IGameTestObservationProvider`，仅读取已经存在的状态，调用`publisher.Install(provider)`后使用`PublishOnce()`，或明确指定采样帧间隔。未安装provider时不扫描业务对象、不调用业务getter。

Runtime 文件在 `UNITY_EDITOR || (DEVELOPMENT_BUILD && AI_TEST_AGENT_BRIDGE)` 条件下编译；Development 构建仍需额外设置 `AI_TEST_AGENT_BRIDGE`。已用五种编译符号组合检查二进制中的类型是否按guard出现，实际APK/AAB发布构建是否排除仍须在真实构建流程验证。

## M3.2：显式项目适配与加载身份

`Runtime/`保留游戏无关协议、`GameTestLoadedAssemblyIdentity`和无第三方依赖的`GameTestPeModuleIdentity`；`Adapters/IdleOutpost/`仅维护该项目的只读映射及Bootstrap。迁移游戏时替换适配层和实际加载钩子，审核getter副作用、货币范围、窗口/目标含义及构建方式，宿主协议不依赖特定游戏枚举或默认设备。

`IdleOutpostReadOnlyProvider`只读已初始化缓存与原始存档字典，不调用会插入默认值的业务getter。等级核对运行值与已有存档，成本读取已计算值；钱包保留源double的往返表示，未知不可填0。固定目标未配置时不猜首个设备。R3按照实际升级窗口配置ID4枚举已有`UIGroupEnum.Main=100`窗口，核对打开、活动和`m_Upgradeable`同一对象引用；不存在、关闭或目标不匹配时target整体不可用。账本保持不可用/不完整，不能用于真实升级扣款通过。

R2实机曾打开等级1、成本8.71T的真实升级窗口，但旧adapter误读PopUp=200而返回target不可用。R3修正项目映射并重新构建验证，使用明确目标47/215与资源1019；两轮实际采集source/wallet/target均可用，等级1、精确cost`8708971337959.25`与截图8.71T显示粒度相容。R2资源1018证据保持原样，没有改日志或强制available绕过。Main/PopUp分离的正反例继续验证窗口及目标门禁。

`IdleOutpostBridgeBootstrap.CaptureLoadedBytes(byte[])`必须从实际`Assembly.Load`边界收到同一byte[]。Editor调用`Identity.Capture(bytes, assembly)`，将PE MVID与RuntimeModule API交叉核对并标记`mvid_source=runtime_api`；Android Development调用`CaptureFromLoadedPe(bytes, assembly)`，只从加载字节的PE/CLI元数据读取MVID并标记`loaded_pe_metadata`，不读取RuntimeModule。原因是本次IL2CPP工具链的`GetGuidInternal`未提供可用模块GUID；不修改该工具链，也不将其私有实现复制到公共仓库。旧双参`CaptureLoadedAssembly(byte[], string)`仅保留Editor兼容，Android直接拒绝。

身份行`AI_TEST_LOADED_DLL_JSON:`的`schema=2`、manifest的`schema_version=2`均增加`mvid_source`；外层recording仍为`schema_version=1`，状态/流水observation仍为`schema=2`。来源标签必须匹配运行模式，已捕获身份不能切换来源。宿主从同次读取的实际DLL字节同时校验SHA256和PE MVID；旧schema1身份应使用对应旧提交复评，不能修改历史行补成新证据。

`Begin()`只在Editor或Android Development Player、实际TestServer及显式初始化入口下安装Provider；使用生成的`AiTestAgent.Generated.ProviderBuildStamp.Value`，不提供默认生产stamp。默认目标为`0/0`；`BeginWithTarget(terrainId, deviceId)`要求两个正ID，与默认入口共享宿主，不自动发现目标。每30帧成对发布已捕获identity及当前observation，保留原session/seq；异步失败、服务器变化和宿主销毁后的重试由回归覆盖。资源号按实际BM模式选择：Local仅取内置版本，Build取热更/内置最大值，Develop或无有效版本保持未知。

全部桥源码及调用钩子需使用相同编译guard。桥与适配器放入hotfix程序集，AOT加载钩子使用保留的反射入口，避免反向引用；加载钩子变化需要完整AOT/IL2CPP专用包，不能仅替换旧安装包中的DLL。测试身份stamp、实际DLL SHA/MVID和最终包hash由外部清单关联。

宿主命令为`inspect-provider-m3 INPUT --output PATH.json`。Editor程序集探测只能获得`compiled_assembly_identity`；device记录需实际日志文件及M2已装证据，正常空命令输出仍须校验空文件SHA。离线结果保持`scope=provider_runtime_identity, execution=device_free, live_provider=false, business_status=NOT_EVALUATED`。本轮Local专包不生成远端资源更新日志，M2使用`expected_resource_version=null`只判启动就绪；R3包内1019与两轮实际M3 source1019已交叉核验，没有复用1018批准清单。

两轮每次核验170份文件，run/attempt/PID/session不同且配置相同；同一录制的两次标准CLI复评input SHA相同、assessment ID不同、结论一致。账本保持不可用/不完整，钱包存在同期收益，不能据两轮余额变化推定扣款。实际操作、截图、完整证据留本地，脱敏结果见[实机验证记录](../../docs/verification/2026-10-16-m32-android-device-loop.md)。

完整字段、结构图、接入步骤及验收清单见[M3.2文档](../../docs/m3/runtime-provider.md)，专用包步骤见[项目构建recipe](Editor/BuildRecipes/README.md)。recipe依赖IdleOutpost构建API，并有独立opt-in编译守卫；通用核心及其他游戏不依赖该recipe。此前提交`20bd0ff`的schema1身份/Editor探测见[历史验证记录](../../docs/verification/2026-10-16-m32-runtime-provider.md)，其中结果不表示后续schema2专包已通过。

## M3.1 schema2：明确状态与连续流水

`IGameTestObservationProvider` 提供稳定的 `ProviderId` 和 `ReadObservation()`。在Unity主线程创建 `GameTestObservationPublisher`，显式 `Install(provider)` 后调用 `PublishOnce()`；没有Provider或在后台线程调用均失败，不发现、扫描或推测业务对象。每次重新Install会开始新session。

```csharp
var publisher = host.AddComponent<GameTestObservationPublisher>();
publisher.Install(readOnlyProjectProvider);
publisher.PublishOnce();
```

`readOnlyProjectProvider`必须由项目单独实现和审核，仅返回已经存在的业务状态；未取得完整流水时明确标为不可用或不完整。此示意代码不会自动创建正式适配器。publisher拥有schema/session/seq/frame/provider传输字段，不修改Provider返回值，不进行金额换算，不将available、complete或ready自动改为true。

schema2日志标记为 `AI_TEST_OBSERVATION_JSON:`，根字段精确为 `schema=2, session_id, seq, frame, provider, source, target, wallet, ledger`。DTO包括 `GameTestObservation`、`ObservationTargetSnapshot`、`WalletSnapshot`、`LedgerSnapshot` 和 `LedgerEventSnapshot`；source复用 `RuntimeSource`。

发布器通过固定schema的`GameTestObservationJson.Serialize`输出完整嵌套section/events，避免动态加载程序集时Unity JsonUtility静默省略字段。序列化验收独立解析原始JSON，检查字段/事件齐全及转义/64位整数边界；不能用FromJson默认初始化的空DTO代替原始字段存在性验证。此变化不增加外部库或业务操作。

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

记录关联还要求设备时间/PID、前后session/seq、目标/窗口/币种、采集时间括号及evidence_root中的实际不同PNG绑定，并用连续事务流水解释同期收益。使用和可移植正负例见[M3.1说明](../../docs/m3/README.md)。上述M3.1离线核验不执行设备操作，也不创建M0已执行run；其PASS仅表示记录一致性，始终`business_status=NOT_EVALUATED`、`execution=device_free`。M3.2的实际设备动作另留执行证据，不能扩大离线命令的成功范围。

## 既有schema1快照入口

每条日志以 `AI_TEST_STATE_JSON:` 开头，包含 schema、session、单调序列、帧号、provider、运行来源、可选 UGUI 和目标状态。`source.available=true` 必须提供实际 build/resource/DLL/server 版本，不能使用本地源码默认配置冒充运行真值。目标状态必须来自项目适配器明确的目标，禁止推测地块与设备升级映射。

coins/cost 必须是精确十进制字符串，不能用 float、界面缩写或未知值填零。未知目标设置 `available=false`；level 的 `-1` 是不可用标记，coins/cost 为空。缺少业务观测时不提供伪造快照。

provider 返回的 cost 是被测业务的观测值，不能单独充当独立期望；升级判定仍须采用人工审核的配置/需求规则，并记录同期收益或该笔升级事务。

UGUI 捕获默认关闭；`Install(provider, everyFrames: 0, captureUi: true)` 可读当前活动 RectTransform 的层级路径和屏幕 bounds。坐标原点为屏幕左上角。bounds 不等于可点击性，不证明未被遮挡，也不允许跳过宿主前台/版本/坐标校验。

宿主 `parse_snapshot_line(line)` 无全局状态，允许 logcat 前缀，拒绝缺字段、错误 schema、重复 JSON 字段、截断、非有限/缩写金额及无效 bounds。`SnapshotStream(expected_build_id=..., expected_provider=...)` 负责跨行序列、帧和运行来源一致性；重复/倒序/版本变化均失败关闭。

Editor 编译入口为 `AiTestAgent.Bridge.Editor.BridgeCompileSmoke.Run`。成功标记 `BRIDGE_COMPILE_SMOKE_PASS` 仅代表合成验证，不能当成生产资源、服务器、真实升级或存档通过。
