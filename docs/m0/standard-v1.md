# M0 Standard v1

## 核心对象

| 对象 | 稳定主键 | 必须表达 |
| --- | --- | --- |
| Source | source_id + source_version | 来源、权限、引用、内容摘要 |
| Requirement | requirement_id + version | 需求规则、审核状态、来源回链 |
| Risk | risk_id | 风险等级、需求回链 |
| Testpoint | testpoint_id | 可验证问题、风险回链 |
| Case | case_id + version | 前置、步骤、独立预期、环境、恢复授权 |
| Build | build_id | artifact 哈希、包版本、环境和运行时版本链 |
| Run | run_id + attempt_id | 计划/执行状态、初态、设备/账号别名、阻塞原因 |
| Evidence | evidence_id | 类型、运行回链、相对引用、采集时间、哈希、保留策略 |
| Defect | defect_id | 分类、确认、复现、修复构建、回归运行 |

## 状态规则

- PLANNED：已登记但没有设备执行。
- BLOCKED：输入、环境、账号、规则或证据不足；不得计入执行通过。
- PASS：独立预期成立，且所需证据已采集并能校验。
- FAIL：有足够证据证明独立预期不成立。

未知值统一保留为 unknown、null 或 BLOCKED，不能用当前源码默认值、模型猜测值或界面缩写填充。

## 可移植性规则

1. 标准层不能依赖 Unity、Unreal、Laya、Appium、ADB 或特定模型。
2. 标准层不能直接读取游戏数据库、调用业务函数或假设 UI 路径。
3. 所有游戏差异由适配器转换为标准字段，专属数据进入扩展命名空间。
4. 适配器可以增加字段，但不能改变标准字段含义和状态口径。
5. 新项目先使用同一 fixture、validator 和 trace 报告，再增加项目适配器和证据类型。
6. 迭代只增加 schema 版本或扩展字段，不覆盖旧记录；报告必须能指出适配器版本。

## 适配新游戏的最小工作

新游戏只需提供：

1. 一个 source 导入器。
2. 一个 build manifest 适配器。
3. 一个账号和初始状态 fixture 适配器。
4. 一个把游戏观测、日志和截图映射为 Evidence 的适配器。
5. 一份审核后的 Case，不要求先有自动化工具。

M0 验收通过后，M1 才能对这些标准记录做 AI 需求分析；M3 以后才允许把适配器接入设备和游戏操作。
