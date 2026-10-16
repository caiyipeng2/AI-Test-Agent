# M3.2：真实只读 Provider 与运行身份握手

M3.2把M3.1的通用协议接到项目专用读取点，并校验“准备编译的来源、实际加载的DLL、提供的运行记录”是否一致。本轮交付源码、隔离编译和程序集探测链路，尚未生成含新桥的Android专用测试包，不能宣布实机Provider或首次升级验收通过。实际结果见[验证记录](../verification/2026-10-16-m32-runtime-provider.md)。

## 结构与可移植边界

```mermaid
flowchart TB
    subgraph Standard[游戏无关的标准层]
        I[加载身份：实际字节SHA256与程序集MVID]
        P[IGameTestObservationProvider / schema2发布器]
        H[宿主清单与证据文件核验]
        R[JSON / Markdown报告]
        I --> P
        P --> H --> R
    end
    subgraph Adapter[项目适配层：当前为IdleOutpost]
        L[测试专用加载钩子] --> I
        C[已初始化控制器与原始存档字典] --> A[只读Provider]
        A --> P
        B[测试服Bootstrap与显式目标] --> A
    end
    F[冻结源码、依赖与构建输入stamp] --> D[隔离编译的新DLL]
    D --> L
    D --> H
    M[最终包清单、M2已装核验与当次采集日志] -. device模式需要 .-> H
```

| 层 | 主要文件 | 换游戏时的工作 |
| --- | --- | --- |
| 通用C#身份 | `Runtime/GameTestLoadedAssemblyIdentity.cs` | 保留；提供真实加载字节和对应Assembly |
| 通用状态协议 | `Runtime/GameTestObservationPublisher.cs`及DTO | 保留；实现同一Provider接口 |
| 游戏适配 | `Adapters/IdleOutpost/` | 替换读取点、目标/窗口映射、服务器和资源版本读取 |
| 隔离编译 | `tools/compile_bridge_probe.py` | 提供项目编译响应文件、源码范围和明确补丁 |
| 宿主核验 | `m3_handshake.py`、`m32.py` | 保留；提供该项目的预期身份和真实证据 |

C#文件路径均相对于[Unity桥目录](../../integrations/unity-test-bridge/)。迁移并非零配置：需要审核新游戏的getter是否写入默认值、货币精度、窗口语义、热更加载和构建符号。核心不会自动学习出可信业务映射；迭代必须带新的来源、回归和人工验收。

## 当前适配器读取什么

`IdleOutpostReadOnlyProvider/v1`只读已有GameEntity缓存控制器和UserData字典。缺少控制器、键或可信数据时返回不可用，不创建默认设备、补零金币或调用刷新成本/等级的业务方法。

- 目标由显式terrain/device标识指定；默认`0/0`不自动选首个设备。运行等级须与已有存档一致，成本读取已计算的`Device.Cost`。
- 钱包遵循已加载金币配置的公共/地块作用域，读取已有`GuardDouble.Value`。使用引用空值判断，避免游戏类型重载的比较运算触发异常。
- 金额将源double以InvariantCulture往返格式展开为无指数文本；这保留源值的往返表示，不增加源数据本来没有的精度。负值、非有限值、缺键均不可用。
- 窗口只遍历已有PopUp窗口，确认打开、活动及目标对象引用一致；固定读取已核实的`m_Upgradeable`成员。窗口缺失、关闭、目标不匹配或读取失败时target整体不可用，标识/成本为空、level=-1、ready=false，避免发布违反schema2的半可用目标。`ready`只表达适配器声明的窗口/目标条件，不能证明未遮挡、坐标可点或全部业务前置条件满足。
- 账本保持`available=false, complete=false`。现有事件没有本适配器所需的权威事务ID，不能拼出一份看似完整的升级流水。

`IdleOutpostBridgeBootstrap.Begin()`在主线程显式安装Provider，默认每30帧采样。它只支持Unity Editor或Android Development Player，且仅在缓存控制器已指向TestServer时启动；异步资源读取失败或环境改变后释放启动占位，允许后续显式重试，活动宿主销毁时也释放自身状态。

通用发布器使用固定schema的`GameTestObservationJson.Serialize`，显式输出source、target、wallet、ledger及嵌套events。真实动态`Assembly.Load`探测发现Unity JsonUtility会静默省略这些嵌套部分，因而不能以`FromJson`后默认构造的空对象判断数据齐全。验收需独立解析原始JSON、核对完整字段及事件，覆盖引号、反斜线、Unicode、控制字符和64位整数边界；序列化转义通过本身不代表业务字段满足宿主协议。

## 编译身份与接入钩子

所有Runtime、Provider、Bootstrap及调用钩子使用：

```csharp
#if UNITY_EDITOR || (DEVELOPMENT_BUILD && AI_TEST_AGENT_BRIDGE)
// test bridge
#endif
```

Development和`AI_TEST_AGENT_BRIDGE`必须同时到达实际hotfix DLL编译过程。稍后Player选择Development不证明此前单独编译的DLL具备这些符号。运行时还检查平台，其他平台Player不会报告Android身份。

```mermaid
sequenceDiagram
    participant Build as 隔离构建
    participant Load as 游戏测试加载钩子
    participant Bridge as Bootstrap / Identity
    participant Host as inspect-provider-m3
    Build->>Build: 冻结输入并生成build stamp
    Build->>Build: 编译后固定DLL SHA256和MVID
    Load->>Load: Assembly.Load(loadedBytes)
    Load->>Bridge: 同一loadedBytes与实际Assembly的MVID
    Bridge->>Bridge: 计算字节SHA256并核对程序集
    Bridge-->>Host: AI_TEST_LOADED_DLL_JSON
    Host->>Host: 重新hash实际DLL并核对清单/日志
    Host-->>Host: 限定scope的证据报告
```

`GameTestLoadedAssemblyIdentity.Capture(byte[], Assembly)`记录确切字节hash和实际Assembly的MVID；已接受身份后发生冲突会令上下文失效，旧来源不可继续使用。调用边界仍须审核，确保传入的是加载时同一个byte[]；hash和MVID字段不能认证记录者身份。

Player接入点位于实际`Assembly.Load`之后，经保留的反射入口`CaptureLoadedAssembly(byte[], string)`传递身份，避免AOT反向引用hotfix类型。初始化钩子位于已有控制器初始化之后，调用`Begin()`；两处钩子在本轮仅应用于私有冻结副本，失败捕获后不阻断正常游戏启动。Editor程序集探测直接加载新DLL，不把资源目录中未实际加载的DLL冒充运行字节。

`build_id`是编译前生成的输入stamp/nonce，由`AiTestAgent.Generated.ProviderBuildStamp.Value`提供；不是最终APK自哈希。编译后清单关联DLL实际SHA256/MVID，最终打包后再关联包SHA256。`source.dll_version`使用`sha256:<loaded DLL hash>`；资源号取实际本地热更/内置版本的最大值，服务器来自已初始化的缓存控制器，未知时source不可用。

## 宿主输入合同

`inspect-provider-m3`不连接设备、不调用模型、不执行游戏操作。输入JSON严格拒绝缺失、未知、重复字段和非有限数，金额沿用schema2规则。根字段精确为：

```text
schema_version=1, evidence_mode, mode, manifest, dll_path,
identity_line, observation_line, transport
```

`evidence_mode`为`synthetic`或`recorded`；改成recorded不会自动认证记录来源。manifest字段精确为：

```text
schema_version=1, runtime_build_id, provider, assembly_name,
dll_sha256, module_mvid, source_snapshot_sha256, runtime_kind,
resource_version, server, artifact
```

DLL须为证据根内的实际文件，最多256MiB，宿主重新计算其SHA256。`source_snapshot_sha256`是清单提供的来源指纹，需结合隔离编译清单审核；宿主不据此重新构建游戏。身份行包含唯一`AI_TEST_LOADED_DLL_JSON:`标记，JSON字段精确为`schema=1, runtime_kind, build_id, assembly_name, dll_sha256, module_mvid`，逐项与manifest相等。宿主核对实际文件hash和提供的元数据，不单靠日志声明认证运行来源。

| 模式 | 必要条件 | 成功范围 |
| --- | --- | --- |
| `assembly_probe` | `runtime_kind=editor_probe`；resource_version/server为空；artifact、observation_line、transport为null | `compiled_assembly_identity`，可用于复核新编译DLL的Editor加载身份 |
| `device` | `runtime_kind=android_player`；完整来源、最终artifact、schema2观测、采集日志和M2已装证明 | `provider_runtime_identity`，仅核验提供的设备记录 |

device的`artifact`精确含`sha256, package, version_code, version_name`；`transport`精确含：

```text
device_alias, pid, log_start, log_end, installed_proof,
log_evidence={path, bytes, sha256}
```

两条设备日志须保留threadtime Unity前缀、同一当次PID，落在不超过10秒的设备时间括号内，身份先于或同刻于观测。`log_evidence`指向根内实际采集文件，宿主校验字节数/hash，要求两条完整日志各出现一次且物理行顺序正确；支持LF/CRLF。日志文件最多64MiB，单行约4MiB。

`installed_proof`引用同一证据根内的M2结果，要求test环境、startup_readiness PASS、同设备替代标识/包版本/当前PID、最终包hash、实际安装的base和split APK hash及环境证明相符；其非空证据列表中每份文件也须重新hash。M2结果最多8MiB，单证据64MiB，总证据256MiB。所有路径解析后必须留在根内；原始数据只保留本地。

证据声明最多4096条。为避免保护范围被截断，已装证明超过8MiB或4096条时，CLI直接以INVALID_INPUT拒绝导出，不创建或覆盖报告；边界内仍按合同输出PASS/BLOCKED。单个非法路径声明不会遮蔽其他证据的输出保护。

所有成功结果仍固定`execution=device_free, live_provider=false, business_status=NOT_EVALUATED`。device模式名称只描述输入记录类型，不表示命令当下连接了设备或Provider仍在线；不创建M0已执行run。

## 运行与验收

在仓库根目录设置`$env:PYTHONPATH='src'`。本轮工作树已准备的实际Editor探测记录可这样复评：

```powershell
python -m ai_test_agent inspect-provider-m3 .local/m32-provider-recording-postwire.json --output .local/m32-provider-review-user.json
python -m ai_test_agent inspect-provider-m3 .local/m32-provider-recording-postwire.json --output .local/m32-provider-review-user-repeat.json
```

上述私有输入及其DLL/日志未推送到远端，其他环境须按合同准备自己的输入与证据。成功退出0；证据不完整或身份不符为BLOCKED、退出2。输出JSON和同名Markdown各自原子替换，不能覆盖输入、DLL、采集日志、已装证明及其已声明证据或文件别名。M2证据的`path`及相对于已装证明父目录的`relative_path`声明均受保护，即使二者不符导致证明无效，也不能用BLOCKED报告覆盖任何一份原件。每次复评使用新输出名，核对相同input_sha256、不同assessment_id及相同结论。

隔离编译参数见`python tools/compile_bridge_probe.py --help`。该工具要求明确的项目/源码范围、响应文件、编译器、额外桥源码和新输出目录；冻结依赖，重建当前源码列表，拒绝嵌套响应文件、未支持开关和输出路径逃逸，将DLL/PDB等输出重定向到新目录。补丁必须匹配原文件hash及唯一锚点。编译清单及日志包含私有路径和来源，不能提交远端。

本轮验收依次检查：

1. 只读与恢复：缺数据不写入、钱包作用域/金额、目标/窗口、线程/平台/服务器门禁，以及Bootstrap失败和销毁后的恢复。
2. 真实源码编译：Editor与Android测试符号能够编译；release、仅development、仅bridge的二进制不含桥类型；原工作区来源和依赖hash未变。
3. 实际程序集探测：独立Unity Host读取新编译DLL字节、实际加载、调用保留入口并检查hash/MVID/stamp；独立解析序列化的完整嵌套字段，冷态Provider应保持不可用，不能据此宣布游戏初始化成功。
4. 宿主文件闭环：实际探测记录复核与同输入复评；改DLL/元数据、缺日志、过时已装证明或Editor/Android混用均阻塞。
5. 用户审核本轮源码与上述证据后再合并。新Android包、实机只读观测、完整事务、一次安全升级和连续五次业务复跑独立验收。

## 下一步专用测试包

本轮包含AOT加载钩子变化，不能直接将新hotfix DLL替换进已装旧v65。需要固定兼容源码/资源基线，在隔离副本接入桥和两个测试钩子，完成Unity导入、真实hotfix构建、AOT/IL2CPP及原Android Studio SDK工程的最终打包；固定签名、包版本、TestServer和资源号，生成最终包清单。

随后用M2核验最终包与实际安装，再采集当次PID的加载身份和schema2状态，将相应文件交给device模式核验。真实目标、钱包和窗口读值还须由截图及人工基线复核；补齐权威事务后才能开始首次升级业务闭环。发布包须另做最终包内容检查，当前Roslyn的类型排除证据不能代替最终APK/AAB验收。
