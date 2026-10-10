# Idle_Outpost 首次升级实测报告

日期：2026-10-09（Asia/Shanghai）
状态：**部分通过，金币联合断言 BLOCKED**。

本报告记录已授权的测试服真机执行，不把工具输入送达当成业务通过。原始截图、日志和运行目录在本地 `artifacts/`，没有加入 Git。

## 测试基线

| 项目 | 值 |
| --- | --- |
| 测试构建 | v65 / 2.1.1，文件名时间 `2026_09_23_21_12` |
| 环境 | 包内 `_server=2`，运行日志 `TestServer` |
| 资源 | 包内/运行日志 `localVersion:995 / remoteVersion:995` |
| 设备 | moto g 2025，API 35，arm64；设备别名 `reference-low` |
| 账号恢复 | 游戏内“删除存档”→确认“删除账户成功”→宿主重新启动 |
| 工具 | ADB、bundletool、Python runner；无模型调用 |

用户确认：设备 ID 用于生成游戏账号；同一测试设备完成一轮后可通过游戏内删除账号生成新的测试进度。本轮没有执行 `pm clear` 代替删号，也没有触碰另一台设备或正式服账号。

## 运行证据

1. 测试服安装命令退出码为 0，安装后包为 versionCode `65` / versionName `2.1.1`。
2. 启动日志包含 `curAppVersion 2.1.1`、`localVersion:995 remoteVersion:995`、`Hot InitRequestUrl Server Type TestServer` 和 `login success`。
3. 删除前账号详情显示已有进度 `Lv.1 / 关卡 1-2`；执行删除后界面显示“删除账户成功”，宿主重新启动后出现测试服新手教程首屏，UID 发生变化。UID 原文只保留在本地截图/日志，不进入报告。
4. 新账号完成首次建造路径后，升级面板显示：升级前等级 `1`、本次费用 `3`、升级前金币截图读数 `161`。一次短点击后，面板显示等级 `2`。

关键本地证据目录：

- `artifacts/run_1f5109b22d8d4d3ea8324e3c4467b6fc/reference-low/`：删除后宿主重启启动证据。
- `artifacts/run_c070cf37e5f7417e8805212aad91341d/reference-low/after.png`：升级前等级 1、费用 3、金币 161 的面板截图。
- `artifacts/run_bd4cb07b596b4646b2faef79de3ad1d5/reference-low/after.png`：单次升级后等级 2 的面板截图。
- `artifacts/upgrade-assessment.json`：结构化联合断言结果。

## 联合断言

| 断言 | 结果 | 依据 |
| --- | --- | --- |
| 截图与观察来源 | PASS（人工确认） | 前后 PNG 有哈希、同一设备别名、同一测试构建、同一升级窗口标识 |
| 设备等级 | PASS | 1 → 2，符合单次升级 `+1` |
| 金币扣款 | BLOCKED | 升级后截图金币为 `218`；期间战斗/放置收益持续增加，无法从两张截图分离“扣 3”与同期收益 |
| 总体首次升级 | BLOCKED | 三项联合断言必须全部可判定；当前缺少金币消费事件或测试桥流水 |

这不是业务失败。当前证据足以证明等级发生了预期变化，但不足以证明金币扣款正确。下一步需要测试构建状态桥输出 `Event_Currency_Change` 级别的消费/收益流水，或提供可冻结收益的测试基线，再重跑同一用例。

## 已实现能力

- `src/ai_test_agent/contracts.py`：ID、用例、PNG证据和引用校验。
- `src/ai_test_agent/android.py`：显式设备、包/分片校验、安装、启动、前台保护、截图、日志、UI dump、一次性 tap。
- `src/ai_test_agent/runner.py`：运行目录、trace、命令原始输出、启动/观察/tap结果和测试环境证据。
- `src/ai_test_agent/upgrade.py`：同设备/构建/窗口、截图哈希、等级和精确金额联合判定；未知流水保持 BLOCKED。
- `integrations/unity-test-bridge/`：只读 Unity 状态桥通用协议与 Python 解析器。临时 Unity Host 的 `BRIDGE_COMPILE_SMOKE_PASS` 已通过；尚未接入 Idle_Outpost 测试构建。

不能直接把当前工作区编译出的 hotfix DLL 塞进现成 v65：勘察显示现有源码 DLL/AndroidBuild DllBundle 与 v65 包内 DllBundle 的大小、哈希和 VersionLogs 不一致。若要接入桥，必须在隔离游戏工作区执行 HybridCLR 编译、DllBundle 重建、资源覆盖和 Android AAB/APKS 重建，再重新核验包内配置、签名和运行版本；本轮没有改动游戏工作区或重打包。

自动化验证：在指向可写任务临时目录的 Python 环境下执行 `80 tests, OK`；默认系统临时目录存在 ACL 限制时应显式设置 `TEMP/TMP` 后再运行。真实业务结果仍以本报告的 PASS/BLOCKED 范围为准。

## 后续最小动作

1. 在专用测试构建中接入已有设备等级、金币和升级事件读取点。
2. 发布构建排除桥；启动时回报资源版本和热更 DLL 身份。
3. 以同一测试账号删除/重启流程重新进入首次升级。
4. 以事件流水确认 `coins_after = coins_before - cost + non_upgrade_delta`，三项通过后再把总体结果改为 PASS。
