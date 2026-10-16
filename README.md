# AI-Test-Agent

面向游戏项目组的 AI 辅助测试流程与 TestAgent 实验仓库，首个被测项目为 Idle_Outpost。

## 当前状态

**M0、M1及M2均已由用户验收，M2已合并main；M3.1只读协议首环已实现，等待首环验收。** M1处理需求/风险、测试点、用例、人工审核与结果/缺口，支持从原始DOCX直接调用模型生成草稿。M2完成同一合同下两轮实机验证，范围为已批准的场景回调里程碑。M3.1新增schema2状态/流水协议与无设备证据判定；真实游戏Provider、升级操作及五次业务复跑尚未完成，不能视为M3完整完成。

| 文档 | 用途 |
| --- | --- |
| [项目需求与架构设计](docs/superpowers/specs/2026-10-09-idle-outpost-ai-testing-design.md) | JD 映射、架构、数据对象、Agent 边界、验收和确认项 |
| [实施阶段与最小闭环](docs/roadmap/2026-10-09-implementation-framework.md) | 按阶段说明怎么做、交付什么、怎样判定通过 |
| [项目勘察和安装证据摘要](docs/discovery/2026-10-09-baseline-and-install.md) | 本轮真实发现、安装结果与尚未验证事项 |
| [首次升级实测报告](docs/verification/2026-10-09-first-loop.md) | 测试服运行、删号恢复、联合断言和剩余阻塞 |
| [M0 数据基础](docs/m0/README.md) | 设备无关的注册表、追溯报告、覆盖统计和证据保留规则 |
| [M1 需求分析](docs/m1/README.md) | 来源锚点、风险/测试点、用例草稿和人工审核状态 |
| [M1 真实文档实测](docs/verification/2026-10-16-m1-real-document.md) | 真实 DOCX 的会话 AI 分析、来源引用、导入门禁和待人工审核状态 |
| [M2 Android 确定性冒烟](docs/m2/README.md) | 严格测试包/设备门禁、readiness合同、同参复跑及经过证据验证的M0交接 |
| [M2 验证记录](docs/verification/2026-10-16-m2-deterministic-smoke.md) | 自动回归、真实环境门禁、场景回调里程碑与同参复跑证据及验收入口 |
| [M3.1 只读状态与流水证据](docs/m3/README.md) | schema2、来源/PID/PNG/事务绑定、精确金额、合成正负例与无设备复跑 |
| [M3.1 验证记录](docs/verification/2026-10-16-m3-readonly-evidence.md) | Python回归、合成文件流程、Unity Host与编译guard验证及尚未接入边界 |

## 推荐推进顺序

1. 建立统一需求、用例、运行、证据和版本标识。
2. AI 分析真实需求并产出有来源的用例草稿，由测试人员审核。
3. 跑通确定性的 Android 启动冒烟，并在测试构建中接入 Unity 状态桥完成金币流水与业务状态断言。
4. 在同一工具和断言基础上加入受限 AI 规划；失败进入复核和回归。
5. 用黄金集评估模型、Prompt、工具变更；按需求扩展专项测试。

每阶段都必须留下输入、执行轨迹、证据、结果和复跑方法。后续实现按用户确认的方案提交本仓库。

## 仓库与数据边界

- 本仓库承载方案、测试运行器、适配器、测试桥源码、用例模板与脱敏报告。
- 被测游戏仓库作为独立输入，本轮保护其现有修改，未提交或推送游戏源码。
- 包体、签名材料、设备标识、账号、凭据及原始截图/日志留在本地；`artifacts/` 用于本地证据，已忽略。
- 公开文档中的配置和构建信息必须区分“当前源码”“实际安装包”“运行时热更”三种事实。

## 实施入口

M1支持离线规则对照、会话AI导入和全自动文档分析。全自动入口：`analyze-document-m1 INPUT --output-dir DIR --provider-config CONFIG --allow-model`，支持Codex CLI与可配置OpenAI兼容API。用法和新增验收要求见[M1文档](docs/m1/README.md)；模型生成的用例始终等待人工审核。

M2入口为 `m2-smoke --contract PATH`，运行后自动生成同目录 `m0-handoff.json`；已有结果用 `m2-report INPUT --contract PATH --output PATH` 重新验证证据并导出交接与相邻Markdown报告。配置从[通用示例](fixtures/m2-contract.example.json)复制到本地，示例的readiness默认未批准。详细用法、退出码和验收流程见[M2文档](docs/m2/README.md)。

M3.1入口为 `assess-recording-m3 INPUT --output PATH.json`，读取输入父目录中的实际PNG并导出JSON/Markdown。`python examples/m3_recording_demo.py --output-dir DIR --scenario pass` 可在新目录生成可移植合成样例；场景另支持 `wrong-debit` 和 `missing-event`，三者分别由CLI得到 `PASS/FAIL/BLOCKED`（退出码0/1/2）。PASS只代表记录一致性，固定 `business_status=NOT_EVALUATED`、`execution=device_free`；不创建M0已执行run，也不证明真实游戏升级。来源与独立成本引用仍须由审核者确认。完整命令及验收边界见[M3.1文档](docs/m3/README.md)。

截至2026-10-16，当前Samsung已装用户指定的v65测试包，两轮标准M2命令均完成保留数据更新安装，并观测到TestServer及本地/远端资源1016/1016，结果均为 `PASS / READINESS_CONFIRMED`。用户批准本轮以登录、玩家初始化、场景加载回调日志及连续3次进程/前台稳定、无归属本包的崩溃或ANR作为M2验收范围；它不证明初始化完成、主界面可交互或业务正确。M2的PASS仅为合同约定的 `startup_readiness`，业务为 `NOT_EVALUATED`；M0交接保持草稿及外部M2执行链，`m0_run_imported=false`，导出成功不等于测试通过。用户已最终验收M2并完成main合并；M3.1已实现只读协议、合成Unity发布和离线证据复核，本环未修改真实游戏或执行设备操作，真实接入与业务升级仍待后续阶段。
