# M1 全自动文档分析

## 目标与边界

用户要求补齐“提供需求文档后，程序自行读取并调用AI生成需求分析、风险、测试点与用例草稿”，并将它纳入M1验收。本轮首接本机已登录Codex CLI用于真实验证，同时实现可配置的OpenAI兼容独立模型API适配器。保持现有M1审核与M0门禁，不安装包或操作游戏。

首版接受DOCX、UTF-8 Markdown和文本。DOCX需要读取正文、表格、删除线、内嵌XLSX及关联PNG/JPEG图片。不能处理的媒体/公式/外链必须记录缺口，不得静默丢弃。PDF、旧DOC、视频解码不在首版支持范围。

## 流程与组件

`原文 → documents.extract_document → 模型提供者 → analysis_protocol.compile_analysis → M1校验 → JSON/Markdown/CSV及审核请求`。

- `documents.py`：`extract_document(path, output_dir, *, allow_model_access=False)`返回`source`、`manifest`、`images`、`warnings`。source遵循现有normalize_source；images包含`image_id/section_id/path/sha256/mime_type`。manifest记录原文件指纹、原文块、删除线、内嵌表和未解析输入。图像以附件传入视觉模型，不能把文件名视为已阅读。
- `analysis_protocol.py`：提供`MODEL_OUTPUT_SCHEMA`、`build_analysis_prompt(bundle)`和`compile_analysis(source, payload, *, provider_id, model_id, images=())`。模型只生成语义单元与引用节ID；宿主负责生成M1 ID、哈希和DRAFT状态，禁止模型生成批准记录。每个来源节均须给出覆盖/上下文/无法解读的处置及原因；每张附件必须有可读/不可读观察。
- `model_providers.py`：`CodexCLIProvider`和`OpenAICompatibleProvider`实现`generate(prompt, schema, images, work_dir)`，返回`payload`和`metadata`。超时、无登录/凭据、拒答、截断、无效JSON、未知引用均产生模型/分析失败，不伪造规则分析结果。
- `document_analysis.py`：`analyze_document(path, output_dir, provider, *, allow_model_access=False)`执行流水线并写运行状态。配置独立于被测游戏；API凭据仅从指定环境变量读取。
- CLI：`analyze-document-m1 INPUT --output-dir DIR --provider-config CONFIG --allow-model`；`extract-document-m1`支持仅本地提取，方便验收与排查。

## 模型输出

模型返回`units`（规则、风险、用例）、`questions`（问题与来源）、`section_dispositions`（逐节覆盖处置）和`image_observations`（逐图读取结论）。用例具备前置、步骤、独立断言、未知值、正常/边界/异常类别及明确来源/推导/建议标记。冲突规则不得自行定案，示例数字不得代替上线配置。原文作为待分析数据，不能执行其中的指令。

源文件、来源节与模型配置/Prompt版本形成追溯标识。输出保留提供者、模型、调用耗时、调用次数和可获得的用量信息；不得伪造金额或用量。大于首版输入预算时明确阻塞而非截断。模型能力不足以阅读全部图像时必须记录未读图像缺口。

## 验收标准（新增必需项）

1. 一条命令从原始DOCX开始，不借用上次会话手工生成的分析JSON，完成真实模型调用并输出需求、风险、测试点、用例草稿和来源追溯。
2. 删除线不会恢复成现行需求；表格和内嵌Excel不受错误dimension元数据漏读；图片有逐图分析/不可读记录。
3. 本机Codex CLI完成真实文档验证；独立API可配置base URL、model、凭据环境变量、超时和输出预算，通过本地HTTP契约测试。没有API凭据时不得宣称独立服务已实测。
4. 原文/节摘要、锚点和模型输出结构严格校验；全部用例为DRAFT/待人工审核。人工确认业务口径后仍通过既有M1审核流程。
5. 无模型权限/网络/登录、超时、畸形响应、拒答、截断、缺图或未覆盖功能节必须在运行报告可见；失败不会回退到离线模板并声称AI成功。
6. 输出包括source、manifest、analysis、coverage、human-review-request、M0 handoff、Markdown及CSV。未审核/未绑定时M0仍为BLOCKED。
7. 在真实文档上检查至少一处真实规则冲突与关键业务边界，并与前次会话分析对照。计数仅为辅助，不用固定用例数量冒充质量。

## 交付

在`codex/m1-auto-document-analysis`提交并推送供用户验收，验收后才合并main。公开远端只保存通用实现、合成测试、方案与脱敏验证摘要，真实原文和详细业务结果保留本地。
