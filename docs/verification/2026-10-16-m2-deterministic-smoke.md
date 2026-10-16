# M2 工具与真实门禁验证记录

2026-10-16。本轮交付确定性的 Android 包体核验、安装/启动运行器、证据报告及 M0 草稿交接。工具实现供验收；**M2 真实正向安装/启动尚未完成，阶段仍有阻塞**。当前不进入 M3。

## 授权和范围

用户已指定改用当前 Samsung，更新安装并保留数据。运行器只接受测试环境合同，不执行升级、支付、清数据、卸载或删号；不调用模型。`PASS` 仅覆盖 `startup_readiness`，业务始终为 `NOT_EVALUATED`。

当前设备已装 versionCode 66 / versionName 2.1.2。目录中两份新版 v66 经实际 manifest 和配置资产核验均为正式环境；同产品包体清点未找到不低于 66 的测试包。旧 v65 测试包环境可核验，但低于已装版本。文件先后顺序不能作为环境证明。

## 验证结果

| 验证项 | 结果 | 证据与边界 |
| --- | --- | --- |
| 全量自动回归 | PASS：348 项 | `python -m unittest discover -s tests -q`，含原 M0/M1 和新增 M2 测试；最终本地日志保留在 `.local/m2-discovery/m2-full-regression-final.log`。 |
| Python 编译检查 | PASS | `python -m compileall -q src tests`。 |
| 完整合成工程闭环 | PASS | 实际合成 APKS/TOC → 实际解析器 → 运行器 → CLI → 文件哈希复验 → M0 草稿交接，连续两次执行；仅 Android 传输/状态由假设备替代，不是实机成功证据。 |
| Samsung 只读工具 | PASS | 显式设备发现、设备时间锚点、安装元数据、base 与全部 split 的实际 SHA-256；共 3 个已装 APK。 |
| 真实新版正式包预检 | BLOCKED，连续两次 | `environment_mismatch`；整个 APKS、两个 base 变体及 TOC 核验后，实际环境字段与测试合同不符。安装前即停止。 |
| 真实旧测试包预检 | BLOCKED | 环境核验通过，随后 `VERSION_DOWNGRADE_BLOCKED`：65 < 66。没有强制降级、卸载或清数据。 |
| 真实同参数复跑与留证 | PASS（门禁分支） | 两次正式包合同指纹一致，run/attempt 不同，旧目录保留；每份证据重新核对大小/hash。此结果只证明阻塞分支可复跑。 |
| 保留数据约束 | 本轮未发生设备变更 | 三次真实尝试均无安装、force-stop、启动、清数据或删号命令；前后已装版本元数据一致。这不是对游戏数据内容逐项比较的声明。 |
| 真实合法测试包安装、启动及同参复跑 | **待执行** | 需要不低于已装版本、实际环境可核验的测试包，以及经过审核的独立 readiness 信号。 |

原始配置、路径、APK、日志、设备标识及命令输出只保留在被忽略的 `.local/`，不推送到公开仓库。真实执行索引为 `.local/m2-discovery/m2-real-gates-summary.json`，每个条目均能定位原结果、报告和自动生成的交接。

## 已验证的工程门禁

- 包体：整个 APKS hash、实际 binary AXML/XML 身份、全部受支持 base 变体、TOC 安装集合、环境配置来源与批准 hash/字段；错误身份、孤立配置或冲突配置不能放行。
- 设备与安装：显式 serial、设备可用状态、最低 SDK、版本不降级；安装后复核版本、配置及每个已装 APK 的实际字节来源。同版本不同代码不能凭相同环境资产跳过更新。
- 启动：设备时间边界、初始 PID 等待、采样后复查 PID/前台、当次本包异常归属；独立 readiness 条件全部满足并连续稳定，不能凭 PID/PNG/服务器标签通过。
- 预算：Android 命令使用阶段剩余时间，调用和证据写入后复核期限；同步包体解析受大小预算约束，结束后超时不得进入设备阶段。宿主磁盘 I/O 不提供可抢断墙钟保证。
- 证据：唯一 run/attempt、合同指纹、原始命令输出/日志/UI/PNG 的大小及 hash；交接重读文件，拒绝证据篡改、目录越界和覆盖输入。报告原子替换，硬链接输出不会截断原证据。

独立审查发现的同版本身份误跳过、采样后退出误通过、初始无 PID 无法等待、阶段预算逃逸、报告硬链接覆盖及孤立配置证明问题均补充了回归用例并修复。

## 验收入口

工具和结构说明、流程图、字段说明、退出码及验收清单见 [M2 使用文档](../m2/README.md)，通用配置见 [示例合同](../../fixtures/m2-contract.example.json)。

```powershell
python -m ai_test_agent m2-smoke --contract .local/m2-contract.json
python -m ai_test_agent m2-report artifacts/m2/RUN_ID/DEVICE_ALIAS/result.json --contract artifacts/m2/RUN_ID/DEVICE_ALIAS/contract.json --output artifacts/m2/RUN_ID/DEVICE_ALIAS/m0-handoff.review.json
```

`m2-smoke` 自动生成原始结果和 `m0-handoff.json`。`m2-report` 是证据复验/导出操作；其退出 0 不代表原始测试通过，必须查看 `m2_status`。

M0 交接保留草稿注册表及真实外部 M2 run/attempt/证据链。`m0_run_imported=false`，不创建 M0 已执行记录，不增加业务用例执行覆盖率，也不代替 M1 人工审核。

本轮代码与方案提交到 `codex/m2-deterministic-smoke` 供审阅，尚不合并 main。真实正向闭环补齐并验收后，才按既定规则合并和推进下一阶段。
