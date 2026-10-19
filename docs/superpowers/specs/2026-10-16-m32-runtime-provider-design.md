# M3.2：真实只读Provider与运行版本握手

用户已确认推进真实Provider及版本握手。本环实现可审阅接入源码、真实游戏程序集编译验证和宿主身份核验，继承M3.1协议。业务升级、完整事务采集和五次真实复跑留后续。实机通过必须来自新专用测试包；编译/Editor探测不能充当Android运行成功。

## 边界与选择

原游戏工作区存在用户修改，保持不动。读取源码与依赖后在E盘私有目录冻结副本，输出均到新目录。优先用Unity自带Roslyn重编完整游戏程序集，核对真实API和编译宏；标准Unity导入、IL后处理、IL2CPP与APK/AAB验证是独立证据层，不能由Roslyn成功替代。

新增通用 `GameTestLoadedAssemblyIdentity`、项目独立Provider/Bootstrap、隔离输入准备工具和Python握手。核心协议保持游戏无关，项目适配代码只使用明确读取点，不复制私有业务实现/服务器/签名信息到远端。源码快照、响应文件、依赖和新DLL保持本地。

## 真实读取

使用已有GameEntity缓存控制器和原始UserData字典的TryGetValue；禁止调用会插入默认设备/零金币的getter、UpdateData/UpdateCost或懒加载HwGameConfig。显式terrain/device目标，缺失返回available=false；未配置目标时只报告可确认的钱包/来源，不自动选择首个设备。

等级需运行值与已有存档一致，成本读取已计算Device.Cost。钱包遵循已加载金币公共/地块作用域，读取原始GuardDouble.Value；格式化为不含指数的InvariantCulture往返十进制字符串，明确不增加源double精度。非有限/负值及缺键不可用，不填0。

窗口映射只遍历已有Main=100窗口，依据实际升级窗口配置ID4的归属，验证打开/活动/目标同一引用。固定读取已核实的m_Upgradeable成员；无窗口、关闭、不匹配或读取失败时target整体不可用，不能返回缺window_id的半可用目标。不扫描任意对象，不调用私有业务方法。ready只表示本适配器声明的窗口及目标一致，不是完整可点击性证明。账本保持available=false/complete=false，不能用现有无事务ID事件伪造完整流水。

该映射来自R2实机反例：真实屏幕已打开等级1、成本8.71T的升级窗口，旧adapter只枚举PopUp=200却未找到窗口。修正已通过按UI组分离的正反例，并在资源1019的R3包完成两轮目标可用采集；关闭、不活动、同ID不同对象和等级不符仍不可用。画面数据只用于核对读取路径与显示粒度，不代表成本规则或升级扣款正确；旧1018日志未回填target可用。

schema2发布使用固定字段的GameTestObservationJson.Serialize，显式输出所有嵌套section/events。动态Assembly.Load下已观察到Unity JsonUtility静默缺失嵌套字段，因此必须独立解析原始JSON并核对完整结构及事件；FromJson将缺失字段初始化为默认DTO不能当作通过。转义与64位整数边界另做往返验证，字段业务合法性仍由宿主协议校验。

## 接入与真实DLL身份

桥Runtime与Provider归入已有hotfix程序集，避免AOT反向引用。Player实际Assembly.Load的同一byte[]经测试专用单参反射入口传给Bootstrap，记录实际字节SHA256及PE/CLI Module MVID。Editor另外与实际Assembly的RuntimeModule结果交叉核对；Android不调用该API。该工具链的IL2CPP `GetGuidInternal`未提供可用模块GUID，修复使用无第三方依赖的元数据读取器，不修改或公开工具链私有实现。

公开接口：

- `AiTestAgent.Bridge.GameTestLoadedAssemblyIdentity(string assemblyName)`；Editor使用`Capture(byte[] loadedBytes, Assembly loadedAssembly)`，PE MVID与运行时API交叉核对并标记`runtime_api`；Android使用`CaptureFromLoadedPe(byte[], Assembly)`，不读RuntimeModule并标记`loaded_pe_metadata`。属性为Available/AssemblyName/DllSha256/ModuleMvid/MvidSource；提供`ReadSource(buildId, resourceVersion, server)`及`SerializeEvidence(buildId, runtimeKind)`。
- `AiTestAgent.Bridge.GameTestPeModuleIdentity.ReadMvid(byte[])`从同一加载字节的PE/CLI Module表及GUID堆读取MVID；C#与宿主Python分别实现并交叉验证，无第三方依赖。
- `AiTestAgent.IdleOutpost.IdleOutpostReadOnlyProvider(identity, buildId, resourceVersion, terrainId, deviceId)`实现IGameTestObservationProvider，ProviderId为`IdleOutpostReadOnlyProvider/v1`。
- `AiTestAgent.IdleOutpost.IdleOutpostBridgeBootstrap.CaptureLoadedBytes(byte[])`为实际加载钩子的保留入口，按Editor/Android选择上述捕获API；AOT侧不读取ModuleVersionId。旧双参`CaptureLoadedAssembly(byte[], string mvid)`仅支持Editor签名兼容，Android直接拒绝。
- `Begin()`使用生成的`AiTestAgent.Generated.ProviderBuildStamp.Value`，不存在默认生产stamp；无配置目标时使用0/0，不猜测业务对象。`BeginWithTarget(terrainId, deviceId)`只接受两个正ID，与默认入口共用宿主。仅Editor或Android Development Player且实际TestServer下启用，按主线程有限帧间隔先输出已捕获身份、再输出当前observation，session/seq保持连续。异步失败、等待中环境变化或活动宿主销毁后可显式重试；不得因旧宿主销毁释放新宿主的状态。

运行时build_id是编译前输入stamp/nonce，不是最终APK自哈希。DLL实际SHA256和MVID在编译后由外部清单固定，最终包SHA由最终清单关联，避免把自身hash嵌回同一产物。source.dll_version为`sha256:<actual loaded bytes hash>`。resource_version按实际BM模式缓存：Local取内置`Item2`，Build取热更/内置最大值，Develop或无有效正版本保持未知；server来自已初始化WebRequest缓存。

身份日志标记`AI_TEST_LOADED_DLL_JSON:`，JSON精确字段：`schema=2, runtime_kind, build_id, assembly_name, dll_sha256, module_mvid, mvid_source`。`editor_probe`仅接受`runtime_api`，`android_player`仅接受`loaded_pe_metadata`，其他平台Player不能冒充Android。序列化失败、重复捕获不同字节/程序集/MVID/来源、未Capture或空身份不产生可信来源；已接受身份后出现冲突须令原上下文永久失效，不能继续发布旧来源或切换来源恢复。该组件依赖已审核加载边界传入同一实际byte[]，不能认证不可信调用者。

所有Runtime、Provider、Bootstrap和调用钩子使用同一 `UNITY_EDITOR || (DEVELOPMENT_BUILD && AI_TEST_AGENT_BRIDGE)` guard，反射入口保留。需检查实际hotfix编译宏，不能因随后Player选择Development而推定先前DLL启用了该宏。

## 宿主握手

Python `inspect-provider-m3 INPUT --output PATH.json`只核验证据，不操作设备或调用模型。外层根字段保持`schema_version=1, evidence_mode, mode, manifest, dll_path, identity_line, observation_line, transport`；evidence_mode为synthetic/recorded，mode为assembly_probe/device。manifest升级为精确字段`schema_version=2, runtime_build_id, provider, assembly_name, dll_sha256, module_mvid, source_snapshot_sha256, runtime_kind, resource_version, server, artifact, mvid_source`。observation仍为schema2。宿主从同一次实际DLL读取的字节计算hash并解析PE Module MVID，再核对manifest及身份行的hash/MVID/stamp/来源；source_snapshot_sha256须结合编译清单审核，不表示宿主重建源码或认证记录来源。

旧身份/manifest schema1记录绑定提交`20bd0ff`，需要用对应旧版本复评并保留原件；不得重写历史日志或添加新来源标签后冒充schema2采集。

assembly_probe要求editor_probe、resource_version/server为空、artifact/observation_line/transport为null，只得到`scope=compiled_assembly_identity`。实际程序集探测须另有可信Host执行证据，不能单凭输入行宣称加载成功。

device要求android_player、完整source和`artifact={sha256, package, version_code, version_name}`；transport精确含`device_alias, pid, log_start, log_end, installed_proof, log_evidence`，其中`log_evidence={path, bytes, sha256}`。两条日志保留同一当次PID的threadtime前缀，位于最多10秒设备时间括号内，身份不晚于观测；实际采集文件的hash/大小需匹配，两条完整行各出现一次且物理行顺序正确。已装证明要求M2测试环境startup_readiness PASS、最终包身份、实际安装APK成员、环境、当前PID及所引用证据文件全部匹配，不能仅接受一份声明PASS的JSON。

M2引用的成功命令输出允许0字节，但文件存在、准确大小和空文件SHA仍需核验；DLL与采集日志保持非空。本轮Local专包的M2合同使用`expected_resource_version=null`，不要求未执行的远端更新流程生成版本日志。R2实际资源1018保留原证明；R3新包版本文件及两轮M3严格source已交叉核对1019，未复用旧artifact清单。M2 PASS仅表示批准的启动就绪里程碑。

DLL、日志和已装证明及其证据均须解析到evidence_root内，逐份读取并核对边界/指纹；JSON/Markdown输出不能覆盖输入证据或其文件别名。M2证据的path与相对于证明父目录的relative_path两种声明都受保护，即使声明冲突导致BLOCKED也不能覆写任一原件。完整字段、字节预算及命令见[M3.2实现说明](../../m3/runtime-provider.md)。缺少必要证据为BLOCKED。所有模式固定`execution=device_free, live_provider=false, business_status=NOT_EVALUATED`；device仅描述输入记录，不证明Provider此刻在线，不创建M0已执行run，不扩大到升级通过。

## 验证与交付

先失败测试再实现：通用身份精确hash/MVID/重复捕获、Provider缺数据不写入、真实游戏API编译、测试/发布宏类型排除、宿主清单篡改和Editor/Player混用拒绝。真实源码快照及依赖逐项hash，重定向所有-out/-refout/PDB输出；旧Player响应文件源码表过期，必须重新生成当前列表。

交付M3.2分支供验收，报告逐层区分合成验证、真实源码编译、Editor程序集probe与实际Android包。测试加载钩子涉及AOT，需固定兼容源码/资源基线，经过Unity导入、AOT/IL2CPP及原Android Studio SDK工程最终打包，先核验最终包及实际安装，再采集当次Provider。可审阅的[项目构建recipe](../../../integrations/unity-test-bridge/Editor/BuildRecipes/README.md)依赖IdleOutpost构建API，有独立opt-in守卫及隔离目录要求；它不进入通用核心依赖。

此前[验证记录](../../verification/2026-10-16-m32-runtime-provider.md)保持旧提交的442项等真实结果。R3 Main窗口修正已通过Editor/Android各10项行为及10项独立边界、发布/无opt-in守卫及真实源码编译验证；最终全量测试数以[实机验证记录](../../verification/2026-10-16-m32-android-device-loop.md)为准。

明确目标47/215、资源1019的R3专包已完成两轮实际安装启动及只读采集，两轮source/wallet/target均可用、等级1、cost`8708971337959.25`，截图显示等级1/8.71T。每轮核验170份文件，两轮配置相同且run/attempt/PID/session不同；同输入复评input SHA相同、assessment ID不同、结论一致。两轮只读闭环已完成，等待用户验收后合并；离线报告仍保持`scope=provider_runtime_identity, execution=device_free, live_provider=false, business_status=NOT_EVALUATED`，账本不可用/不完整，未扩大到业务升级或扣款通过。构建与证据链见[专用包计划](../plans/2026-10-16-m32-device-package.md)及上述实机记录。
