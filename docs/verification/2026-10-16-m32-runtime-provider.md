# M3.2 真实只读 Provider 与运行身份验证

> 版本说明：本记录对应提交[`20bd0ff`](https://github.com/caiyipeng2/AI-Test-Agent/commit/20bd0ffcf810b7ee10e47479ade6398b55de7979)，加载身份及manifest为schema1，observation为schema2。以下442项测试、21个桥类型等均保留当时实测数据。后续Android专包针对IL2CPP MVID不可用新增schema2身份/manifest及`mvid_source`；当前合同见[M3.2说明](../m3/runtime-provider.md)，专包执行见[独立计划](../superpowers/plans/2026-10-16-m32-device-package.md)。旧记录应使用对应提交复评，不重写历史日志或补来源标签；本文不证明后续schema2专包或实机已通过。

2026-10-16。**本轮实现、真实源码隔离编译、严格Editor程序集探测及宿主文件复核闭环已完成，供用户验收后合并。尚无含新桥的Android专用测试包，未进行M3.2实机验收。** 原游戏工作区保持不动，没有设备、账号、升级或支付操作。

## 交付及证据层级

新增游戏无关的加载身份组件、IdleOutpost只读Provider/Bootstrap、隔离编译工具及`inspect-provider-m3`宿主命令。结构、精确字段和验收入口见[M3.2用法](../m3/runtime-provider.md)。账本明确不可用/不完整，默认目标为未配置；本轮不会输出真实升级通过。

| 验证层 | 实际结果 | 证明与边界 |
| --- | --- | --- |
| Provider合成API回归 | Editor和Android opt-in各9类行为及4类独立资源/生命周期边界通过 | 缺数据不插入、GuardDouble比较、钱包/目标/窗口、金额、主线程、平台/服务器及重试；使用形状对应的合成API，不能替代真实运行 |
| 发布/未显式启用guard | 合成release及no-optin通过 | 新桥类型被排除；最终APK/AAB仍待检查 |
| 真实hotfix源码：Editor | 最终3861份当前源码+6份额外桥源码编译成功，含21个桥类型 | Unity自带Roslyn，包含窗口/固定序列化修正；不是Unity导入、IL后处理或游戏启动 |
| 真实hotfix源码：Android测试符号 | 最终3861+6份源码编译成功，含21个桥类型 | 验证真实API引用及Development+Bridge编译条件；没有制作Android包 |
| 真实hotfix源码：release/仅development/仅bridge | 最终三组均编译成功，各0个桥类型 | 实际DLL元数据检查；不推定最终发布包已排除 |
| AOT测试加载钩子 | 33份源码的私有补丁副本此前编译成功，本次复核钩子未变 | 仅语法/API编译，不是IL2CPP原生构建 |
| 原输入保全 | 最终各变体hash审计通过 | 原源码成员、已有修改、响应文件、依赖及编译器均未变化 |
| 通用身份Unity Smoke | 通过，实际输出`LOADED_IDENTITY_SMOKE_PASS` | 覆盖精确hash/MVID及冲突后上下文失效；不表示游戏启动 |
| 通用观测Unity Smoke | 通过，实际输出RECOVERY、WIRE、COMPILE三个成功标记 | 覆盖空数据恢复和固定schema序列化；独立Python JSON复验Unicode、控制符/NUL、long最大值与嵌套事件 |
| 真实程序集Editor Probe | PASS；实际Assembly.Load最终新DLL并调用保留入口，SHA/MVID/stamp一致 | 固定writer输出完整冷态观测；未启动游戏，source/target/wallet/ledger保持不可用 |
| 冷态观测严格宿主解析 | PASS；ObservationStream接受完整观测，三个缺字段变体均拒绝 | 分别删除source.available、target.level或ledger.events；防止默认DTO掩盖缺字段 |
| 宿主真实文件复核与同输入复评 | 两次PASS；相同input_sha256、不同assessment_id、结论一致 | compiled_assembly_identity、device_free、live_provider=false、业务NOT_EVALUATED |
| 身份反例文件流程 | 篡改MVID、Editor记录冒充device均BLOCKED | 不允许编辑器证据升级为Android证明 |
| 完整Python回归 | 442项，9.821秒，OK | `python -m unittest discover -s tests -q`；含23项握手、14项CLI及6项隔离编译测试和既有阶段回归 |
| 新专用包/Android实机Provider | 未执行 | 没有新的最终包、实际安装或当次只读状态证据 |
| 真实升级与五次业务复跑 | 未执行 | 尚缺完整权威事务、独立成本基线及操作证据 |

旧Player响应文件仅列3757份源码，其中12份已不存在；本轮按当前范围重建3861份源码列表，没有沿用旧DLL作为新源码的编译结果。输入及DLL输出分别固定指纹，测试stamp为输入关联标识，不是最终包hash。

## 发现并修正的问题

- 游戏的GuardDouble重载空值比较会解引用操作数；先复现异常，再改为ReferenceEquals，合成API纳入对应重载形状。
- 已接受加载身份后再次捕获冲突，旧身份不能继续作为可信来源；补失效及禁止复活回归。
- Bootstrap异步初始化失败、等待中切换服务器或活动宿主销毁后，必须释放本次启动占位；增加独立进程边界用例。
- 非Editor的其他平台Player不能被标记为android_player，读值与启动均加平台门禁。
- 首次真实程序集探测遇到编译参考DLL无法执行；运行探测须冻结实现DLL作为依赖，保留编译引用与运行依赖两类清单。
- 无窗口、关闭或目标不匹配时，原先会返回缺少window_id的半可用target；改为target整体未知，补充schema2一致性回归。
- M2证明即使因声明冲突无效，其相对于证明父目录的relative_path原件仍须保护；补充JSON/Markdown直接及硬链接覆盖回归。超过8MiB或4096条声明时直接拒绝导出，单个非法路径不遮蔽后续原件；边界内仍正常处理。
- Unity动态加载程序集时JsonUtility静默省略嵌套section/events；改用固定schema的`GameTestObservationJson.Serialize`，不增加模型、外部库或业务写入。Smoke和真实探测须核对原始JSON中的完整嵌套部分，不能把FromJson默认初始化的空DTO当作序列化成功。

## 本地验收索引

下列路径相对于本轮M3.2工作树，均被忽略，不进入公开远端：

- `.local/provider-verification/`：Provider合成行为、边界、guard及已复现失败日志。
- `.local/m32-identity-postwire.log`：最新身份Smoke及实际成功标记。
- `.local/m32-observation-wire-green.log`：最新观测恢复/序列化Smoke和供独立JSON复验的原始行。
- `.local/m32-compile-postwire-evidence.json`：最终编译矩阵、程序集元数据与原输入hash审计。
- `.local/m32-editor-provider-postwire-01/`、`.local/m32-android-provider-postwire-01/`：最终私有冻结输入、编译清单/日志、stamp与DLL。
- `.local/m32-release-postwire-01/`、`.local/m32-dev-only-postwire-01/`、`.local/m32-bridge-only-postwire-01/`：最终三个桥类型排除变体。
- `.local/m32-aot-android-test-02/`：AOT加载钩子私有副本及编译记录。
- `.local/m32-real-provider-probe-postwire.json`、`.local/m32-real-provider-probe-postwire.log`：实际DLL加载、身份与完整冷态观测。
- `.local/m32-strict-observation-evidence.json`：严格观测解析及三个缺字段拒绝结果。
- `.local/m32-provider-recording-postwire.json`：宿主实际文件输入；两次复核输出为`.local/m32-provider-review-first.json`、`.local/m32-provider-review-second.json`及各自同名Markdown。
- `.local/m32-host-pipeline-evidence.json`：复评一致性及MVID篡改/模式冒充反例。
- `.local/m32-regression-evidence.json`：最终442项回归概要及原始日志/Unity日志hash。
- `.local/m32-python-regression-final.log`：最终完整Python回归原始日志。

完整Python回归的最终实际输出为442 tests、9.821秒、OK，原始日志已保留在上述本地索引。此前438项结果保留为历史概要，当前验收以442项为准；复核者可使用下列命令重新执行。

## 可复验入口

在本轮M3.2工作树根目录执行，实际DLL和原始证据须保留在本地；公开克隆不包含这些私有文件：

```powershell
$env:PYTHONPATH='src'
python -m unittest discover -s tests -q
python -m ai_test_agent inspect-provider-m3 .local/m32-provider-recording-postwire.json --output .local/m32-provider-review-user.json
python -m ai_test_agent inspect-provider-m3 .local/m32-provider-recording-postwire.json --output .local/m32-provider-review-user-repeat.json
```

两次身份复核应退出0、status为PASS，input_sha256相同、assessment_id不同，始终保留compiled_assembly_identity/device_free/live_provider=false/业务NOT_EVALUATED。输出使用新名字，保留本轮first/second报告便于审阅。

## 验收出口

本轮可审核的是标准协议/身份边界、项目只读映射、源码编译与程序集文件闭环。宿主无论输入为assembly_probe还是device，都固定`execution=device_free, live_provider=false, business_status=NOT_EVALUATED`；它检查所提供记录与文件一致性，不认证记录者，不创建M0业务运行，不证明Provider此刻在线。

后续必须固定兼容源码/资源基线，制作含AOT钩子的全量专用测试包，经Unity/IL2CPP和原Android Studio SDK工程最终打包，再由M2核验实际安装与当次PID日志。不能将本轮新DLL直接塞入旧v65，也不能用Editor探测或编译PASS替代Android验收。最终包发布排除、真实钱包/目标/窗口、权威流水、首次升级和五次复跑各有独立验收证据。
