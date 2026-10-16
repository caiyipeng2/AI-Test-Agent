# M3.2：真实只读Provider与运行版本握手

用户已确认推进真实Provider及版本握手。本环实现可审阅接入源码、真实游戏程序集编译验证和宿主身份核验，继承M3.1协议。业务升级、完整事务采集和五次真实复跑留后续。实机通过必须来自新专用测试包；编译/Editor探测不能充当Android运行成功。

## 边界与选择

原游戏工作区存在用户修改，保持不动。读取源码与依赖后在E盘私有目录冻结副本，输出均到新目录。优先用Unity自带Roslyn重编完整游戏程序集，核对真实API和编译宏；标准Unity导入、IL后处理、IL2CPP与APK/AAB验证是独立证据层，不能由Roslyn成功替代。

新增通用 `GameTestLoadedAssemblyIdentity`、项目独立Provider/Bootstrap、隔离输入准备工具和Python握手。核心协议保持游戏无关，项目适配代码只使用明确读取点，不复制私有业务实现/服务器/签名信息到远端。源码快照、响应文件、依赖和新DLL保持本地。

## 真实读取

使用已有GameEntity缓存控制器和原始UserData字典的TryGetValue；禁止调用会插入默认设备/零金币的getter、UpdateData/UpdateCost或懒加载HwGameConfig。显式terrain/device目标，缺失返回available=false；未配置目标时只报告可确认的钱包/来源，不自动选择首个设备。

等级需运行值与已有存档一致，成本读取已计算Device.Cost。钱包遵循已加载金币公共/地块作用域，读取原始GuardDouble.Value；格式化为不含指数的InvariantCulture往返十进制字符串，明确不增加源double精度。非有限/负值及缺键不可用，不填0。

窗口只遍历已有PopUp窗口，验证打开/活动/目标同一引用。固定读取已核实的m_Upgradeable成员；无窗口、关闭、不匹配或读取失败时target整体不可用，不能返回缺window_id的半可用目标。不扫描任意对象，不调用私有业务方法。ready只表示本适配器声明的窗口及目标一致，不是完整可点击性证明。账本保持available=false/complete=false，不能用现有无事务ID事件伪造完整流水。

schema2发布使用固定字段的GameTestObservationJson.Serialize，显式输出所有嵌套section/events。动态Assembly.Load下已观察到Unity JsonUtility静默缺失嵌套字段，因此必须独立解析原始JSON并核对完整结构及事件；FromJson将缺失字段初始化为默认DTO不能当作通过。转义与64位整数边界另做往返验证，字段业务合法性仍由宿主协议校验。

## 接入与真实DLL身份

桥Runtime与Provider归入已有hotfix程序集，避免AOT反向引用。Player实际Assembly.Load的同一byte[]经测试专用反射入口传给Bootstrap，核对当前Bootstrap所在Assembly的MVID，记录实际字节SHA256。Editor使用已加载程序集时不把资源DLL文件当实际加载字节。

公开接口：

- `AiTestAgent.Bridge.GameTestLoadedAssemblyIdentity(string assemblyName)`；`Capture(byte[] loadedBytes, Assembly loadedAssembly)`；属性Available/AssemblyName/DllSha256/ModuleMvid；`ReadSource(buildId, resourceVersion, server)`；`SerializeEvidence(buildId, runtimeKind)`。
- `AiTestAgent.IdleOutpost.IdleOutpostReadOnlyProvider(identity, buildId, resourceVersion, terrainId, deviceId)`实现IGameTestObservationProvider，ProviderId为`IdleOutpostReadOnlyProvider/v1`。
- `AiTestAgent.IdleOutpost.IdleOutpostBridgeBootstrap.CaptureLoadedAssembly(byte[], string mvid)`与`Begin()`。Begin使用隔离构建工具生成的 `AiTestAgent.Generated.ProviderBuildStamp.Value`，不存在默认生产stamp；无配置目标时使用0/0，不猜测业务对象。Begin仅在Editor或Android Development Player且实际TestServer下启用，按主线程有限帧间隔采样；失败不影响正常游戏启动。异步失败、等待中环境变化或活动宿主销毁后可显式重试；不得因旧宿主销毁释放新宿主的状态。

运行时build_id是编译前输入stamp/nonce，不是最终APK自哈希。DLL实际SHA256和MVID在编译后由外部清单固定，最终包SHA由最终清单关联，避免把自身hash嵌回同一产物。source.dll_version为`sha256:<actual loaded bytes hash>`。resource_version由实际本地热更/内置资源号读取的最大值缓存，未知时source不可用；server来自已初始化WebRequest缓存。

身份日志标记 `AI_TEST_LOADED_DLL_JSON:`，JSON精确字段：`schema=1, runtime_kind, build_id, assembly_name, dll_sha256, module_mvid`。runtime_kind区分`editor_probe`与`android_player`，其他平台Player不能冒充Android。序列化失败、重复Capture不同字节/程序集、未Capture或空身份不产生可信来源；已接受身份后出现冲突须令原上下文永久失效，不能继续发布旧来源或重新捕获恢复同一上下文。该组件依赖已审核加载边界传入同一实际byte[]，不能认证不可信调用者。

所有Runtime、Provider、Bootstrap和调用钩子使用同一 `UNITY_EDITOR || (DEVELOPMENT_BUILD && AI_TEST_AGENT_BRIDGE)` guard，反射入口保留。需检查实际hotfix编译宏，不能因随后Player选择Development而推定先前DLL启用了该宏。

## 宿主握手

Python `inspect-provider-m3 INPUT --output PATH.json`只核验证据，不操作设备或调用模型。根字段精确为`schema_version=1, evidence_mode, mode, manifest, dll_path, identity_line, observation_line, transport`；evidence_mode为synthetic/recorded，mode为assembly_probe/device。manifest精确含`schema_version=1, runtime_build_id, provider, assembly_name, dll_sha256, module_mvid, source_snapshot_sha256, runtime_kind, resource_version, server, artifact`。宿主重新hash实际DLL，并对照manifest和身份行中的hash/MVID/stamp等元数据；source_snapshot_sha256须结合编译清单审核，不表示宿主重建源码或认证记录来源。

assembly_probe要求editor_probe、resource_version/server为空、artifact/observation_line/transport为null，只得到`scope=compiled_assembly_identity`。实际程序集探测须另有可信Host执行证据，不能单凭输入行宣称加载成功。

device要求android_player、完整source和`artifact={sha256, package, version_code, version_name}`；transport精确含`device_alias, pid, log_start, log_end, installed_proof, log_evidence`，其中`log_evidence={path, bytes, sha256}`。两条日志保留同一当次PID的threadtime前缀，位于最多10秒设备时间括号内，身份不晚于观测；实际采集文件的hash/大小需匹配，两条完整行各出现一次且物理行顺序正确。已装证明要求M2测试环境startup_readiness PASS、最终包身份、实际安装APK成员、环境、当前PID及所引用证据文件全部匹配，不能仅接受一份声明PASS的JSON。

DLL、日志和已装证明及其证据均须解析到evidence_root内，逐份读取并核对边界/指纹；JSON/Markdown输出不能覆盖输入证据或其文件别名。M2证据的path与相对于证明父目录的relative_path两种声明都受保护，即使声明冲突导致BLOCKED也不能覆写任一原件。完整字段、字节预算及命令见[M3.2实现说明](../../m3/runtime-provider.md)。缺少必要证据为BLOCKED。所有模式固定`execution=device_free, live_provider=false, business_status=NOT_EVALUATED`；device仅描述输入记录，不证明Provider此刻在线，不创建M0已执行run，不扩大到升级通过。

## 验证与交付

先失败测试再实现：通用身份精确hash/MVID/重复捕获、Provider缺数据不写入、真实游戏API编译、测试/发布宏类型排除、宿主清单篡改和Editor/Player混用拒绝。真实源码快照及依赖逐项hash，重定向所有-out/-refout/PDB输出；旧Player响应文件源码表过期，必须重新生成当前列表。

交付M3.2分支供验收，报告逐层区分合成验证、真实源码编译、Editor程序集probe与实际Android包。本轮测试加载钩子涉及AOT，不能把新DLL直接替换进旧v65；后续需固定兼容源码/资源基线，经过Unity导入、AOT/IL2CPP及原Android Studio SDK工程最终打包，先核验最终包及实际安装，再采集当次Provider。不能把编译成功写成实机闭环；本轮实际完成项与待执行项以[验证记录](../../verification/2026-10-16-m32-runtime-provider.md)为准。
