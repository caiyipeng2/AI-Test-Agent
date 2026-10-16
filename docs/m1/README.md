# M1：设备无关的需求分析与用例草稿

M1 建立在 M0 标准记录之上，负责把已授权来源资料转换成带来源锚点的风险、测试点和用例草稿。

## 当前边界

- 默认使用 RuleBasedAnalyzer 作为离线基线，保证没有模型 API 时也能验证数据协议。
- 来源先经过 requirements.normalize_source：保留原文、计算 section/content SHA-256，并拒绝重复键、NaN、哈希篡改和未授权权限。
- 后续模型只需实现同样的结构化输出，不改变 source_anchors、review_status 和人工审核规则。
- M1 不安装包、不连接设备、不执行游戏动作；设备动作属于 M3 以后。
- M1 生成的是草稿。没有人工审核，不能进入执行集。
- M1 审核通过也不等于可执行；promote_to_m0 会保留分析来源和 provenance，并在缺少 build/environment/device/account/initial state 时返回 BLOCKED。

## 输入与输出

```mermaid
flowchart LR
    S[已授权来源\nsource_id/version/sections] --> A[分析提供者\nrule-based or model adapter]
    A --> R[风险草稿\nrisk_id + source_anchor]
    A --> T[测试点草稿\ntestpoint_id + source_anchor]
    A --> C[用例草稿\ncase_id + source_anchor]
    R --> H[人工审核]
    T --> H
    C --> H
    H -->|approved| E[可进入M0执行登记]
    H -->|changes_requested/rejected| Q[保留草稿与修改意见]
```

每个风险、测试点和用例必须引用 source_id、source_version、section_id、locator。分析器不能补造价格、等级、账号状态或环境信息；未知值必须保留为 null 并要求人工补充。

结果还会记录 analysis_id、分析器版本、输入摘要、confidence、coverage_gaps 和 review history。相同来源内容哈希、分析器版本会产生稳定 ID，来源内容变化会生成新的分析结果。

M1 同时支持两种输入：离线规则基线和本次会话生成的 session_ai 结构化结果。两者都必须通过 import-m1 严格导入；不能把 JSON 中的 approved 标记当成真实审核。

promote-m1 有两种结果：没有真实 M0 绑定时生成 BLOCKED handoff；提供 build、环境、设备别名、账号别名、初始状态和 M0Registry 后，才会生成可再次由 M0 校验的 draft case，且不会创建 run。

## 人工结果与缺口报告

M1 现在提供两条设备无关的后续闭环：

- `record-m1-result` 将人工观察登记到分析结果的 append-only `result_history`。它校验用例、断言、分析输出摘要和证据 ID 的回链；`PASS`/`FAIL` 只能登记到已审核用例，`BLOCKED` 可以记录设备执行尚未开始的阻塞原因。它不会创建 M0 run，也不会连接设备。
- `report-m1-gaps` 读取历史缺陷数组并生成稳定的覆盖缺口报告，统计 normal/boundary/exception 计划、审核、人工结果、未执行用例、未覆盖来源章节、无测试点需求、无用例风险和缺陷链路。凭据、设备标识、悬空来源锚点和未知缺陷会被拒绝或保持阻塞；只有确认且可公开的 `known_bug` 才能列入 `m0_defect_ids`。

示例：

```powershell
$env:PYTHONPATH = "src"
python -m ai_test_agent record-m1-result .local/m1-draft.json .local/manual-result.json --output .local/m1-recorded.json
python -m ai_test_agent report-m1-gaps .local/m1-recorded.json --defects .local/historical-defects.json --output .local/m1-gap-report.json
```

## 本地验收

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -p "test_m1*.py" -v
python -m ai_test_agent analyze-m1 fixtures/m1-source.example.json --output .local/m1-draft.json
python -m ai_test_agent import-m1 .local/m1-draft.json --output .local/m1-imported.json
python -m ai_test_agent review-m1 .local/m1-draft.json --output .local/m1-reviewed.json --reviewer qa-owner --decision changes_requested --comment "bind exact independent expectation"
python -m ai_test_agent promote-m1 .local/m1-reviewed.json --output .local/m1-promotion.json
python -m ai_test_agent record-m1-result .local/m1-draft.json .local/manual-result.json --output .local/m1-recorded.json
python -m ai_test_agent report-m1-gaps .local/m1-recorded.json --defects .local/historical-defects.json --output .local/m1-gap-report.json
```

approved 只有在人工补齐独立预期并明确审核人后才能为 true。M1 验收通过后，才进入下一阶段；M3 设备自动化仍保持暂停。

`import-m1` 是会话分析导入边界：它不调用模型，也不相信输入文件的“已批准”标记；输入必须重新通过来源哈希、section 锚点、confidence 和审核结构校验。`promote-m1` 只生成 M0 handoff，缺少真实构建、环境、账号和初态绑定时保持 BLOCKED。

`report-m1-gaps` 的 `BLOCKED` 是报告结论，不代表命令异常；命令会写出完整报告并返回退出码 2，供 CI 或人工验收识别未闭环项。
