# M3.2 当前本地基线专用包与实机闭环

用户已授权基于当前本地游戏基线制作含新桥的Android专用测试包并完成实机闭环。沿用M3.2分支，验收后合并；游戏源码、SDK工程、包体和设备原始证据保持私有。

当前检查点：R3已完成Main窗口映射修复、资源1019专包构建、两轮真实安装启动及只读状态采集，source/wallet/明确目标均可用；同参实际复跑与同输入CLI复评均通过，等待用户验收后合并。账本仍不可用/不完整，业务升级及金币扣款未评估。首版曾发现SDK Billing失配与目标1/1不匹配当前地图；R2对齐依赖、目标47/215、资源1018后，实机又发现升级窗口实际属于Main=100而旧adapter误扫PopUp=200，R3据此修正。各轮包与证据分别保留，此前提交`20bd0ff`的442项及schema1历史记录不变。

## 1. 冻结输入

- 保留当前HEAD、未提交修改与未跟踪源码，逐文件固定Assets、Packages、ProjectSettings、Setting、PackageCache及HybridCLR本机工具链。
- 创建真实文件副本，不联接原游戏目录；原目录与副本分别核对指纹。
- 独立复制既有SDK工程，保留Java/Manifest/Gradle/libs。下载构建必需依赖到私有缓存，先核验可解析性。

## 2. 明确测试配置与接入

- 使用Development、IL2CPP及实际hotfix编译的AI_TEST_AGENT_BRIDGE；设置TestServer、Local资源模式、保留代码热更加载能力并关闭资源热更。
- 包版本为67 / 2.1.2。R3已基于本地资源重建专用资源1019并核验最终包；首版1017及R2资源1018的包体和证据保留，不声称专用资源号等于服务器资源版本。
- 在实际Assembly.Load后经`CaptureLoadedBytes(byte[])`捕获同一字节数组。Android使用`CaptureFromLoadedPe`，不读取该IL2CPP工具链未实现有效GUID返回的`RuntimeModule.GetGuidInternal`路径；Editor常规Capture将PE MVID与runtime API交叉核对。旧双参入口只保留Editor兼容。
- 身份日志与manifest升级为schema2并增加`mvid_source`：Android为`loaded_pe_metadata`，Editor为`runtime_api`；外层recording仍为1，observation仍为2。C#与Python PE reader均不依赖第三方，宿主从同次读取DLL字节核对hash及Module MVID。旧schema1记录用对应旧提交复评，不重写原始行。
- 初始化后经`BeginWithTarget(47,215)`显式安装只读Provider。现有账号在7-5，实际配置映射terrain47、默认设备215（Device_7_5_1），正常UI没有返回旧地图的入口；保留存档并按此明确目标验收。首版1/1不匹配时保持钱包不可用，不改成读取其他地块。默认`Begin()`仍为0/0，非正ID拒绝，不自动发现设备。
- R3根据真实配置仅枚举已有Main=100窗口，继续核对UIUpgradeWindow打开、活动及m_Upgradeable目标引用一致；不扫描其他对象、不强制available。已通过Main/PopUp分离回归，证明实际Main窗口可读且错误组、关闭、不活动、同ID不同对象及等级不符均不可用。
- 每次观测成对输出已捕获身份和当前状态，不重新捕获或重置session/seq。Local来源取实际内部`Item2`；Build取热更/内置最大值；Develop或有效版本缺失时保持未知。
- 导出设置必须在任何StripAOT/BuildPlayer前指向私有SDK副本；禁止原目录同步、自动上传与旧AutoBuild续跑。

## 3. Unity与最终包

- 独立执行配置、HybridCLR GenerateAll/最终DLL复制、AOT元数据与资源重建阶段，保留真实日志及阶段结果。
- 新DLL固定SHA256、MVID与输入stamp，复核进入资源的字节一致。
- Development BuildPlayer导出新IL2CPP源，再同步至保留SDK集成的私有Android工程；用真实Gradle构建生成最终AAB/APKS。
- 验证最终包版本、环境、资源、签名和新DLL，不以旧包固定偏移或旧native输出代替新证据。
- 同时核对新导出要求的Java依赖与保留SDK工程。实际Unity IAP 5.4.2要求Billing9.0.0，旧SDK6.2.1缺少类；本次私有SDK精确对齐9.0.0、按AAR要求compileSdk35，并用Kotlin BOM1.8.22消除20个重复类。targetSdk/minSdk及AGP保持原值，保留前后指纹及依据。

可审阅公开编排为[IdleOutpostDedicatedBuild](../../../integrations/unity-test-bridge/Editor/BuildRecipes/README.md)，入口`AiTestAgent.BuildRecipes.IdleOutpostDedicatedBuild.Configure/BuildResources/ExportPlayer`；仅`UNITY_EDITOR && AI_TEST_IDLE_OUTPOST_BUILD`启用，要求必填`AI_TEST_BUILD_ROOT`和标准GameProject/AndroidBuild/UnityExport隔离布局。该项目recipe不属于零配置通用核心。本次实际执行仍使用已冻结的私有编排；公开版本完成规范化body比对和独立编译核验。

## 4. 实机与复跑

- Samsung保留数据更新安装；签名或版本不兼容时先定位原因，不自动清数据。
- 复用已审核场景回调启动里程碑，收集真实安装APK成员与启动证据。
- 本次Local包关闭远端资源更新，M2合同`expected_resource_version=null`，不要求原远端更新流程的localVersion/remoteVersion日志；M2只按实际安装身份、测试环境及场景回调合同验收启动就绪。R3已用独立最终包内文件与两轮M3严格source交叉核验1019，私有policy helper显式传入1019并核对当前项目/包，未复用1018批准记录或伪造远端日志。
- 在同一当次PID及真实设备时间窗口采集配对日志，严格核验DLL/清单/日志/M2引用文件。正常空stdout/stderr仍按真实空SHA校验。
- source与钱包均实际可用；只通过屏幕打开明确设备窗口，没有点击升级。R3两轮分别采到target47/215可用、等级1、cost`8708971337959.25`；各自同窗截图显示等级1及8.71T，与显示粒度相容。R2旧数值未复制成新证据，截图也未作为独立成本期望。目标不在当前地块或窗口不可得仍应标记未验证。
- 重新启动并重复实际采样，区分不同run/session；另对同一输入进行宿主复评，结论一致且assessment不同。

本轮以上设备步骤已完成：两次实际M2安装启动均PASS，同一配置/包体、不同run/attempt/PID/session；两次桥采集source/wallet/target均可用，每轮严格核验170份文件，共340次引用核验。两份独立截图与各自实际时间窗、命令输出及PNG hash绑定；同一录制经标准CLI复评两次，input SHA相同、assessment ID不同、结论一致。两轮精确钱包金额保存在各自本地原始记录中，余额含时间变化及收益，不据此判定扣款或零变化。离线报告保持`scope=provider_runtime_identity, execution=device_free, live_provider=false, business_status=NOT_EVALUATED`。

## 5. 交付

两轮只读闭环已完成，脱敏结果及复跑入口见[实机验证记录](../../verification/2026-10-16-m32-android-device-loop.md)。构建、包体、安装、桥身份与截图的原始证据只留本地。通用修复与脱敏材料提交到当前分支，等待用户验收后合并；原游戏基线保持不动。

最终报告记录本轮实际构建、包体/安装、桥身份、只读可用性和复跑的独立结果。历史验证文件只加版本说明，保留当时测试数、桥类型数和原始命令结果；不能修改旧记录扩大本轮结论。

本环只证明专用包的真实桥接、只读数据与证据链；账本未完备时不宣称升级扣款正确或M3全部业务通过。
