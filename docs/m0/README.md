# M0 数据、版本与场景基础

M0 是设备无关的数据闭环，不安装包、不点击游戏、不调用模型，也不修改 Idle_Outpost 源码。

## 验收出口

- `source → requirement → case → build → run/attempt → evidence → defect` 引用无悬空。
- 构建记录包含包身份、版本、环境来源和运行时 server/resource/DLL 字段；未知值不会被填成通过。
- 用例包含版本、审核状态、构建/环境/设备别名/账号别名、初始状态、独立预期和恢复授权。
- 运行记录区分 `PLANNED`、`BLOCKED`、`PASS`、`FAIL`，阻塞任务不计入执行通过。
- 报告同时给出计划、执行、阻塞和证据完整数量。

## 本地验证

```powershell
$env:PYTHONPATH = "src"
python -m ai_test_agent validate-m0 fixtures/m0-registry.example.json
python -m ai_test_agent report-m0 fixtures/m0-registry.example.json --output .local/m0-report.json
```

预期：`validate-m0` 返回 `PASS`；报告中的 `planned_cases=1`、`executed_cases=0`、`blocked_cases=1`、`evidence_complete_cases=0`。这证明 M0 能表达“已登记但尚未执行”，不会把后续 M3 设备动作提前算作通过。

本轮实现验收：`test_m0*.py` 共 8 项通过，`compileall src` 通过，CLI validate/report 均返回 `PASS`。M0 只验证数据链，不把 BLOCKED 的计划运行计入执行通过，也没有触碰设备或游戏账号。
