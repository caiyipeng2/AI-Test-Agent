# Idle_Outpost Android 专用测试包构建 recipe

这是 Idle_Outpost 当前构建 API 的项目适配 recipe，供审阅和复现 M3.2 专用测试包流程。它调用该项目已有的资源、HybridCLR 和 SDK 导出工具；通用桥的运行时协议及其他游戏不依赖这些 API。适配其他游戏时应新增对应 recipe，并核对该游戏的构建合同。

源码由本轮私有 `DedicatedBridgeBuild` 编排去掉固定机器路径后整理而来。构建 body 保持对应关系；公开版本新增必填 `AI_TEST_BUILD_ROOT`、标准目录布局、明确类名/命名空间及编译守卫。本次实际执行使用私有编排字节；公开 recipe 的独立编译检查不代表再次执行了完整打包。

## 隔离目录与先决条件

`AI_TEST_BUILD_ROOT` 必须指向一个新的、使用绝对路径的专用构建目录；未填写、普通相对路径、Windows 当前盘路径和盘符相对路径均拒绝。结构为：

```text
<AI_TEST_BUILD_ROOT>/
  GameProject/                 当前游戏基线的独立 Unity 副本
  AndroidBuild/                已接 SDK 的独立 Android Studio 工程副本
  UnityExport/                 本轮 Unity/IL2CPP 导出
  Artifacts/Resources/<id>/     冻结的真实热更新 DLL
  Harness/results/             三个构建阶段的 JSON 摘要
```

只在副本中修改和构建。使用前应记录原基线与副本来源、未提交改动、源文件 hash，并确认目录没有指回原工程的链接。此 recipe 的路径检查是完整路径字符串边界检查，不提供文件系统链接隔离。原游戏、SDK 工程、签名材料和构建产物都不应提交到公共仓库。

将本目录的 `.cs` 源码复制到副本的 `Assets/Editor/AiTestAgent/`。文件只有在 `UNITY_EDITOR && AI_TEST_IDLE_OUTPOST_BUILD` 同时成立时才参与编译，默认导入通用桥不会引用项目构建 API。在首次编译前，仅在副本的 Android scripting defines 中加入 `AI_TEST_IDLE_OUTPOST_BUILD`、`AI_TEST_AGENT_BRIDGE`，保留项目原有的 `USE_HYBIRDCLR` 等符号；不要打开原项目修改这些设置。

副本还须先完成桥运行时和 IdleOutpost adapter 源码接入、生成唯一的 `AiTestAgent.Generated.ProviderBuildStamp.Value`，并接入实际 DLL 加载身份捕获与游戏初始化 hook。热更新 DLL 必须在 Android Development 设置生效后重新编译；该 recipe 会检查 bootstrap 类型和真实 DLL SHA/MVID/build stamp，不负责生成这些接入补丁。明确目标由测试合同传入 `BeginWithTarget(terrainId, deviceId)`；默认 `Begin()` 保持目标未配置。

recipe 固定本轮已审核构建参数：versionCode **67**、versionName **2.1.2**、资源 **1019**、**TestServer / Local 资源**、禁用资源热更新、启用代码热更加载入口、Development、IL2CPP、ARM64＋ARMv7。Local 模式使资源来自本包，避免远端资源覆盖本轮冻结的 DLL。`AI_TEST_BUILD_VERSION_CODE` 和 `AI_TEST_BUILD_VERSION_NAME` 仅可重复指定上述值；新需求须修改并重新审核版本合同。

## 按阶段执行

以下路径仅为示例，应使用匹配原项目版本的本机 Unity。每个命令完成并核验后再运行下一条，不并行启动同一项目的 Unity。

```powershell
$env:GIT_OPTIONAL_LOCKS = '0'
$env:AI_TEST_BUILD_ROOT = 'E:\Builds\m32-example'
$unity = 'E:\Tools\Unity\Editor\Unity.exe'
$project = Join-Path $env:AI_TEST_BUILD_ROOT 'GameProject'
& $unity -batchmode -nographics -quit -buildTarget Android -projectPath $project -executeMethod AiTestAgent.BuildRecipes.IdleOutpostDedicatedBuild.Configure -logFile (Join-Path $env:AI_TEST_BUILD_ROOT 'configure.log')
```

`Configure` 校验项目及 SDK 输出边界，只修改隔离副本的配置；设置测试环境、Local 资源及 Android 构建参数，关闭原有自动构建续跑。缺少构建根目录时会在类型初始化阶段失败，不能期待生成阶段摘要。

```powershell
& $unity -batchmode -nographics -quit -buildTarget Android -projectPath $project -executeMethod AiTestAgent.BuildRecipes.IdleOutpostDedicatedBuild.BuildResources -logFile (Join-Path $env:AI_TEST_BUILD_ROOT 'resources.log')
```

`BuildResources` 重新编译热更新 DLL，执行 HybridCLR 生成后再复制最终 DLL，校验其与打包 asset 的字节一致，记录 SHA-256、MVID 和 build stamp；核对 AOT metadata，构建资源并写入 1019 版本文件，复制到 StreamingAssets。资源版本的共享 EditorPrefs 在 `finally` 中恢复；项目构建步骤状态保存在该副本自身的 `AutoBuild/Build_Record.txt`。

```powershell
& $unity -batchmode -nographics -quit -buildTarget Android -projectPath $project -executeMethod AiTestAgent.BuildRecipes.IdleOutpostDedicatedBuild.ExportPlayer -logFile (Join-Path $env:AI_TEST_BUILD_ROOT 'export.log')
```

`ExportPlayer` 要求资源阶段 PASS、版本文件正确且冻结 DLL 未变化，执行完整 Android Development/IL2CPP 导出，并检查 `BuildReport.Succeeded`、IL2CPP 工程及 Gradle 输出。SDK 后处理目标必须是该根目录下的 `AndroidBuild`。

导出回调保留既有 SDK 的 Java/Gradle 集成，因此还必须对照新导出的托管插件要求核对 Java 依赖；原生参数一致不证明依赖兼容。本轮实际 Unity IAP 5.4.2 要求 Billing 9.0.0，保留 SDK 的 6.2.1 缺少运行时类；私有工程按此精确对齐 Billing，按 AAR 要求使用 compileSdk35，并以 Kotlin BOM1.8.22 消除旧 jdk7/jdk8 的重复类。此处保留 targetSdk/minSdk/AGP，不把本次版本组合当其他项目的通用默认值。

本次旧D8 4.0.52还在实际Core1.15.0 JAR上崩溃；经同输入隔离对比，按[R8官方方式](https://r8.googlesource.com/r8/+/refs/heads/main/README.md#replacing-r8-in-android-gradle-plugin)在私有SDK的settings.gradle/pluginManagement中覆盖buildscript classpath为`com.android.tools:r8:8.1.56`，保持AGP/Gradle/JDK版本。须从实际Android插件classloader核验工具来源与版本，再以完整bundle构建验收；单独运行新D8成功不足以证明Gradle已使用它。依赖预取应保留完整project传递图，必要时让真实Gradle构建联网解析，不手工伪造缓存或跳任务。

## 验证边界与后续交接

三个阶段分别写 `configure.summary.json`、`resources.summary.json`、`export.summary.json`；异常或捕获到 Error/Exception/Assert 日志时标记 FAIL 并退出 1，成功退出 0。摘要文件名按阶段固定，因此复跑必须同时核对本轮命令退出码、`AI_TEST_DEDICATED_BUILD_PHASE` 日志和摘要的开始/结束时间，不能只读取遗留 PASS。

资源阶段 PASS 尚未证明最终包中的 DLL，导出阶段 PASS 仅证明 Unity 导出。随后仍需按项目 SDK 工程约定完成 Gradle 构建、签名和 APKS 生成，核验最终包身份、测试环境配置以及实际安装的 APK 字节，再在设备采集当次 DLL identity 与只读 observation。此 recipe 不执行上传、发布、安装、启动、点击、升级或账号清理。

最终设备闭环应分别保存：源码快照 → 真实 DLL SHA/MVID/build stamp → 最终 APKS SHA/包版本 → M2 安装就绪证据 → 同 PID、同短时间窗内的桥记录。只有完成相应验证后才能扩大结论范围；业务升级、金币扣减和流水正确性需要独立业务断言。

## 公开版本的独立核验

本轮对照实际私有编排做了规范化比较：只消除编译守卫、说明注释、类名/命名空间、四个根目录声明及新增根目录读取函数，剩余构建 body 完全一致。公开源码不含固定机器路径。

使用 Unity 自带 Mono C# 编译器和真实 Unity Editor/项目 DLL 引用，三组编译均通过：同时启用两个守卫符号时包含 recipe，仅启用 Editor 或仅启用项目 opt-in 时 recipe 类型均不存在。另通过七项独立运行探测：缺失、普通相对、盘符相对、当前盘相对根目录拒绝，绝对根目录及标准子目录匹配，两组未启用 recipe 的类型排除。编译引用在验证前后重新核对 hash，全程未启动额外 Unity 进程或执行构建阶段。
