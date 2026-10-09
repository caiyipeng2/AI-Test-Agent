# Android 首次升级闭环 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. 本方案已获用户确认，按以下任务持续执行，普通开发、测试和提交无需再次确认。

**Goal:** 实现可复用的 Android 测试运行器与首次升级联合断言，在测试服完成真实观测、升级与游戏内删号恢复，保存可追溯证据。

**Architecture:** Python 标准库提供数据契约、ADB/bundletool 适配和命令行入口。截图、等级、金币必须分别证明；宿主测试工具不提供任意 shell 或直接改业务状态的接口。Unity 测试桥后续在隔离测试构建接入，先用真实画面和日志验证本次流程，不能将未接入桥说成已接入。

**Tech Stack:** Python 3.11+、unittest、ADB、bundletool、Unity C# 专用测试桥。

**实施状态（2026-10-09）：** Task 1—3 已实现并通过 80 项测试；Task 4 已在测试服包和 moto g 2025 上执行，删除账号重启成功，首次升级等级断言通过但金币联合断言 BLOCKED；Task 5 通用桥已通过 Unity 临时 Host 编译 smoke，尚未接入 Idle_Outpost 测试构建；Task 6 待提交。

## 已确认规则（2026-10-09）

1. 同一产品同一版本的两份包：最新为正式服，前一个为测试服；按唯一 AAB 构建去重后选择，不能把最新 APKS/AAB 两个文件误作两份构建。本次测试候选是 v65 2.1.1 的 `21_12` 构建，`21_55` 为正式服。
2. 首次升级以升级前后快照、金币消耗、设备等级联合判断。具体升级对象和单次升级模式在实际界面确认。
3. 账号由 deviceID 登录生成；每轮测试后允许在测试服游戏内删除账号，验证重登生成新账号/初始进度。该授权限定本次选定的测试设备和测试包，不删除其他设备或正式服账号。
4. 首轮不要求模型API可用。AI分析正式需求仍需真实资料及部署设置；工具执行与规则断言先完成，未连接模型时明确标记。

## Task 1：数据契约与联合断言

**Files:**
- Create: `src/ai_test_agent/contracts.py`, `src/ai_test_agent/upgrade.py`
- Test: `tests/test_contracts.py`, `tests/test_upgrade.py`

- [ ] 写失败测试：完整截图证据、同一设备/版本、等级单次递增、余额与已知价格/收益一致时通过。
- [ ] 写失败测试：缺截图、金额只有缩写且无法精确还原、价格未知、收益未知、窗口目标不符均 BLOCKED；有可信输入但等级或扣款不符则 FAIL。
- [ ] 运行 `python -m unittest discover -s tests -p "test_upgrade.py" -v`，观察新增行为缺失的失败。
- [ ] 实现 `assess_upgrade(before, after, expectation)`，返回 status、分类、三项断言、原因和证据引用。金额使用十进制字符串/整数转换，拒绝 float、NaN、Infinity。
- [ ] 实现 ID、source、case、run、attempt、build、evidence 的结构化记录和必填字段校验；真实证据校验文件存在、PNG签名和哈希，不能用非空路径当截图有效。
- [ ] 重跑上述测试；正常/失败/无法判断例子全部通过。

联合判定契约：

```text
等级：目标对象一致，单次升级后等级 = 原等级 + 已审核增量。
金币：after = before - approved_cost + observed_non_upgrade_delta。
快照：前后截图有效，观察字段和目标窗口相符；无法阅读字段时不猜测。
总结果：三个断言均 PASS 才 PASS；可信反例 FAIL；缺真值 BLOCKED。
```

## Task 2：Android 工具网关

**Files:**
- Create: `src/ai_test_agent/android.py`
- Test: `tests/test_android.py`

- [ ] 测试先行：所有设备命令必须含 `-s <serial>`；缺device/错误包/非零返回/超时/截图截断不得成功。
- [ ] 测试先行：tap坐标必须在已知分辨率内；升级等动作不得自动重试；账号删除不能成为默认 smoke 动作。
- [ ] 运行 `python -m unittest discover -s tests -p "test_android.py" -v` 验证缺行为时失败。
- [ ] 实现 `CommandExecutor.run(argv, timeout)`（shell=False）、`AndroidDevice` 的 metadata、launch、screenshot、pid、logcat、tap、dump_ui、package_info、install_apks。可注入执行器用于命令边界测试；实际命令只来自白名单方法。
- [ ] 保存原始工具输出、退出码、超时和时间；可查询shell数据但不给用户任意shell入口。
- [ ] 重跑测试，覆盖带空格/中文路径的参数传递。

## Task 3：运行器与 CLI

**Files:**
- Create: `src/ai_test_agent/__init__.py`, `src/ai_test_agent/__main__.py`, `src/ai_test_agent/runner.py`, `src/ai_test_agent/config.py`, `pyproject.toml`
- Create: `config.example.json`, `cases/first-upgrade.example.json`
- Test: `tests/test_runner.py`, `tests/test_config.py`

- [ ] 测试配置不允许未指定设备、正式服或缺少环境来源；运行目标需显式选择，不自动操作所有连接手机。
- [ ] 测试先行：smoke有启动返回、两个进程观测、有效截图/日志后只能标记安装/进程层通过；不能冒称游戏业务通过。
- [ ] 实现 `python -m ai_test_agent --config <local config> smoke`：创建独立run目录，读取版本，启动并观测，收集证据，写 result.json。
- [ ] 实现 `observe`、`tap` 和 `assess-upgrade` 命令：每次动作带窗口截图证据/追溯；业务断言输入必须经校验，人工标注必须记录 observer 来源。
- [ ] 实现 `validate-case` 检查来源/预期/审核字段；没有模型API时不输出虚构的模型版本或AI结果。
- [ ] 运行 `python -m unittest discover -s tests -v` 与 `python -m ai_test_agent --help`。

所有测试命令设置 `PYTHONPATH=src`。CLI exit约定：PASS=0，FAIL=1，BLOCKED=2；工具错误写入结果，不能只有堆栈没有报告。

## Task 4：真实测试服启动与首次升级

**Files:** 本地 `.local/idle-outpost.json` 与 `artifacts/<run_id>/`（忽略）；公开 `docs/verification/2026-10-09-first-loop.md`。

- [ ] 校验本次 `21_12` AAB/APKS的包名、版本和哈希；更新安装到参考设备，不清数据。
- [ ] 用smoke真实运行；读取快照和本地日志，记录实际服务器/热更证据或明确unknown。
- [ ] 观察实际升级入口，固定对象、模式、等级、价格和金币显示精度；无法排除并发收益则把金币项保留BLOCKED，继续建立更可靠观测。
- [ ] 执行一次已观察的升级并保存前后状态、截图和日志；运行联合判定，不让手动判断覆盖未知项。
- [ ] 在确认测试包/测试环境后，通过游戏设置中的删除账号入口完成用户授权的测试后恢复；记录删除确认和重登结果。不要使用 `pm clear` 代替删除账号。
- [ ] 用户协议/无法定位/不确定环境等不能由猜测坐标越过；保留证据并继续不依赖该条件的实现工作。

## Task 5：状态桥与接入验收

**Files:** 新编写模块位于 `integrations/unity-test-bridge/`，不复制整份游戏源码。

- [ ] 从实际窗口/Device/Player状态设计只读接口，采用明确版本、快照时间、目标ID、等级、金币和资源/代码版本。
- [ ] 宿主先验证Schema、快照一致性和错误返回；业务期望来自独立配置，不调用同一价格函数自证。
- [ ] 仅专用测试构建启用，发布构建排除；在隔离工作区接入，不覆盖游戏脏工作区。
- [ ] 编译/运行证据充分才报告桥可用；未打入真机包时公开标为未部署，不把桥源码当部署完成。

## Task 6：审查、提交与远端核验

- [ ] 独立审查规格符合性，再审查代码正确性与错误分类；修复全部影响结论的问题。
- [ ] 全量相关测试通过，并核对真实result与原始证据；报告给出PASS/FAIL/BLOCKED的准确作用范围。
- [ ] 更新已确认设计、README、使用说明、脱敏验收报告和未完成能力。
- [ ] 仅暂存本次模块/文档/合成用例，不提交本地配置、包体、真实账号/设备ID、凭据和原始日志。
- [ ] 提交到功能分支并推送指定 `origin`；核对远端SHA与本地HEAD一致。
