# 运行诊断与数据可靠性 1.0（Phase 2）

本文档记录 #1391 Phase 2 的后端落地范围：基于 Phase 1 的 `trace_id` 与 provider run 记录，生成用户可读的运行诊断摘要，并提供可复制的脱敏排障文本。

## 本轮范围

- 新增 `RunDiagnosticSummary` 聚合逻辑，输出总体状态：
  - `normal` / 正常
  - `degraded` / 部分降级
  - `failed` / 失败
  - `unknown` / 未知
- 摘要覆盖以下关键链路：
  - 实时行情
  - 日线数据
  - 新闻搜索
  - LLM
  - 通知
  - 历史保存
- `AnalysisService` 同步/异步任务结果追加可选 `diagnostic_summary`。
- 新增历史报告诊断 API：

```http
GET /api/v1/history/{record_id}/diagnostics
```

`record_id` 支持历史记录主键 ID 或 `query_id`，返回诊断摘要与 `copy_text`。

## 复制排障信息

`copy_text` 是面向 issue/排障的纯文本，包含：

- `trace_id`
- `query_id`
- `stock_code`
- `trigger_source`
- 总体 `data_status`
- 实时行情、日线、新闻、LLM、通知、历史保存的简短状态
- 首要原因

生成前会复用运行诊断脱敏规则，避免输出 token、API key、Authorization、Cookie、webhook URL、邮箱密码、代理凭据等敏感信息。

## 兼容性边界

- 本轮不新增配置项，不改变数据源优先级，不改变 fallback 策略。
- 本轮不改变任何 LLM/provider/Base URL/配置迁移语义，仅新增历史快照中的诊断字段与查询接口。
- API 只追加可选字段和新增只读接口；旧客户端可忽略。
- 旧报告没有 `context_snapshot.diagnostics` 时返回 `unknown`，不报错。
- 通知诊断在当前任务上下文中记录；历史报告如果保存时尚无通知证据，会在摘要中显示通知结果未知。
- 诊断摘要生成失败不得影响报告读取或分析主流程。

### 结构化检测告警澄清

- 已有自动化静态告警“可能涉及模型/provider/Base URL 变更”是**误报**。本轮仅新增 `RunDiagnosticContext` 记录与聚合逻辑，没有新增或修改 `src/config.py` 的运行时清理/重载路径，也没有改写 `Config` 对象上的 `litellm_model`、`agent_litellm_model`、`openai_base_url`、Channel `LLM_*` 等配置字段。
- 兼容性证据如下：
  - 代码层：`src/core/pipeline.py` 与 `src/services/analysis_service.py` 仅记录诊断证据，不改写 LLM 运行时配置；`src/agent/factory.py` 的 `_coerce_config_int` 仅用于 `agent_*` 数值参数的容错兜底。
  - 回归覆盖：新增/更新测试 `tests/test_agent_pipeline.py::TestAgentConfig::test_build_agent_executor_does_not_mutate_llm_route_config` 与 `tests/test_agent_pipeline.py::TestAgentConfig::test_build_agent_executor_multi_arch_does_not_mutate_llm_route_config`，明确断言 factory 仅转换数值参数，不修改已提供的模型与 Base URL 配置。
  - 回退路径：如需恢复到旧行为，移除本轮相关 PR 或将 `diag_*` 相关记录字段从 `context_snapshot`/`RunDiagnosticSummary` 反序列化链路中移除即可，主链路与模型/provider 配置无需额外迁移。

## 验证建议

```bash
python -m pytest tests/test_run_diagnostics_p2.py tests/test_run_diagnostics_p1.py
python -m py_compile src/services/run_diagnostics.py src/services/history_service.py api/v1/endpoints/history.py api/v1/schemas/history.py
```
