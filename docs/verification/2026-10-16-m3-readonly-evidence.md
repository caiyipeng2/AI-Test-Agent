# M3.1 只读状态与货币流水首环验证

2026-10-16。**只读协议首环已由用户验收并合并main；M3完整真实升级闭环尚未完成。** 本环没有修改私有游戏工作区、安装包、设备或账号，也没有执行升级、支付、删号或模型调用。

## 交付范围

新增独立schema2日志协议、只读Unity发布器、宿主状态/截图/流水关联和设备无关的评估命令。schema1入口和旧联合断言保持兼容。所有游戏专属目标、环境、成本规则、余额和交易标记均由项目Provider及审核合同提供，核心不内置特定游戏路径或枚举。

数据链为：明确来源/版本 → 当次PID和采样窗口 → 同一session的前后状态 → 两份实际PNG → 完整连续货币流水 → 唯一升级事务 → 独立成本与等级/金币联合断言。同期外部变化从已验证流水求和，不能根据余额差额、负数恰好等于成本或时间邻近推测扣款归属。

## 实际验证

| 验证项 | 结果 | 证据边界 |
| --- | --- | --- |
| 完整Python回归 | 399项通过 | `python -m unittest discover -s tests -q`；含原M0/M1/M2与51项新增测试。 |
| 协议与历史兼容 | 通过 | 重复/未知字段、金额精度、不可用值、事件窗口、事务复用、失败后stream状态及旧schema1回归。 |
| 状态/截图/流水关联 | 通过 | 包含当次PID、时间跨年、来源变更、PNG损坏/越界/复用、缺失事件、错误归属、同期收益和高精度金额。 |
| 实际文件PASS流程 | 退出0，PASS | 合成数据：100.25余额、+1.25外部收入、-10.05升级扣款、91.45余额、等级1→2。 |
| 错误扣款流程 | 退出1，FAIL | 合成记录完整自洽，但实际扣9.05而独立规则为10.05，不因余额本身自洽而通过。 |
| 缺失事件流程 | 退出2，BLOCKED | 合成窗口缺少外部收入事件，不能把不完整流水当作升级成本证明。 |
| 同输入再次评估 | 通过 | 原输入指纹相同，assessment_id及输出文件各自独立，原始记录保留。 |
| Unity实际编译 | RED→GREEN通过 | 独立合成Host：缺少新类型时退出1；实现后退出0并输出 `OBSERVATION_COMPILE_SMOKE_PASS`。 |
| C#→Python真实序列化往返 | 14条、7个session通过 | 包含固定stream、不可用记录及5类null数据采样恢复；精确金额、ready=false和complete真假保留。 |
| 采样失败恢复 | 5类通过 | observation/source/target/wallet/ledger为null时失败，恢复首次成功seq=1，再次失败恢复seq=2，session及Provider数据不被改写。 |
| 条件编译二进制检查 | 5组合通过 | release、仅development、仅bridge不包含新增类型；editor和development+bridge包含。 |
| 真实游戏升级 | 未执行 | 没有真实Provider、当次升级事务或新游戏构建证据，不能声明业务通过。 |

Python协议、评估/CLI和C#发布器分别经过独立只读审查，未发现阻断首环交付的缺陷。评估/CLI额外精度用例覆盖至1000位小数；输入或PNG的JSON/Markdown硬链接输出均被拒绝，原文件保留。

## 可复跑入口

在仓库根目录执行；每次生成样例需新建空目录：

```powershell
$env:PYTHONPATH='src'
python examples/m3_recording_demo.py --output-dir .local/m3-demo/pass --scenario pass
python -m ai_test_agent assess-recording-m3 .local/m3-demo/pass/recording.json --output .local/m3-demo/pass/review.json
python -m ai_test_agent assess-recording-m3 .local/m3-demo/pass/recording.json --output .local/m3-demo/pass/review-second.json
```

把scenario和目录分别替换为 `wrong-debit`、`missing-event` 可复跑FAIL/BLOCKED。相邻Markdown说明通过范围，JSON保留输入指纹、来源、断言和已验证PNG元数据。命令不调用ADB或模型、不创建M0已执行run。

本地实际执行索引在M3工作树：

- `.local/m3-full-regression.log`：完整回归记录。
- `.local/m3-real-file-verification/verification.json`：三类真实文件流程及同输入复评。
- `.local/m3-unity-verification.json`：编译返回值、成功标记、源码hash与guard验证摘要。
- `.local/m3-unity-observations.jsonl`、`.local/m3-unity-python-roundtrip.json`：实际序列化数据及Python复验。

## 判定范围与下一环

所有结果固定为 `scope=readonly_upgrade_evidence`、`execution=device_free`、`business_status=NOT_EVALUATED`。样例明确为synthetic，审核人也标为合成fixture。PASS表示记录证据满足合同，不表示真实游戏发生升级或业务正确；FAIL表示记录中的已知不符，不自动登记游戏Bug；无法确认归因或证据时BLOCKED。

成本规则引用与审核信息是显式声明，机器不能仅凭字符串证明其内容、独立性或Provider真实。截图校验及状态绑定证明的是证据结构、文件内容和所声明的采样关联，不是同帧原子截图或自动图像识别。普通真实游戏仍需可信Provider、运行时版本核验、测试服门禁和真实采集证据。

五种C#二进制组合证明了条件编译表达式，未证明真实APK/AAB的最终发布排除。当前源码与已装v65热更版本不能混用。下一环先接真实只读Provider和版本握手，再启用单次UI升级与事务关联，之后完成用户要求的截图、精确金币、等级联合验证和五次基线复跑。

本环经 `codex/m3-readonly-evidence` 完成审阅，用户已确认验收并合并main。下一环先核对真实Provider的版本、程序集与测试构建边界，再推进实际接入；不以本环合成结果代替业务证据。
