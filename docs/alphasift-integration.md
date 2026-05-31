# AlphaSift 选股集成

AlphaSift 以最小方式接入 DSA：默认关闭，开启后 Web 侧显示“选股”页签，并通过后端直接调用本地 Python 包的 `alphasift.dsa_adapter`。关闭后左侧导航不显示“选股”页签，直接访问 `/screening` 时仍会显示未开启提示。

## 开启

可以直接设置环境变量：

```bash
ALPHASIFT_ENABLED=true
ALPHASIFT_INSTALL_SPEC=git+https://github.com/ZhuLinsen/alphasift.git@2c76b2b6074ae3bae01d52e5e830a4af3e3246b2
```

对应 commit 固定来源：<https://github.com/ZhuLinsen/alphasift/commit/2c76b2b6074ae3bae01d52e5e830a4af3e3246b2>

也可以在 Web 设置页的 AlphaSift 选股卡片中点击“开启选股”，该操作会写入
`ALPHASIFT_ENABLED=true`、重新加载运行时配置，并按 `ALPHASIFT_INSTALL_SPEC`
执行一次自动安装或可用性检查。

同样地，用户在设置页通用配置表单中直接将 `ALPHASIFT_ENABLED` 修改为 `true`
并点击“保存配置”后，也会在保存成功后执行同一套自动安装与可用性检查链路；
当保存为 `false` 时仅更新开关，不会发起安装请求。

`ALPHASIFT_INSTALL_SPEC` 是传给 pip 的安装参数。为避免未认证调用触发任意 pip 安装，并保证部署可复现，默认值固定到当前兼容验证的 AlphaSift commit：

```bash
python -m pip install git+https://github.com/ZhuLinsen/alphasift.git@2c76b2b6074ae3bae01d52e5e830a4af3e3246b2
```

后端自动安装只接受上述受信任来源。如需使用本地开发版本、其他 commit 或 wheel 文件，请先在同一个 Python 环境中手动安装，然后再开启 `ALPHASIFT_ENABLED`：

```bash
python -m pip install -e /path/to/alphasift
```

DSA 调用的 AlphaSift 接口固定为：

```python
alphasift = importlib.import_module("alphasift.dsa_adapter")
alphasift.get_status()
alphasift.list_strategies()
alphasift.screen(strategy, market=market, max_results=max_results, use_llm=False)
```

为了兼容历史签名差异，后端会按优先级透传 `max_results` / `max_output`（均为 `request.max_results`）与可选 `use_llm`，避免由于关键字参数命名差异导致兼容回归。

筛选策略约束：

- 当 `list_strategies()` 返回非空时仍会回显给前端供展示。
- 即使策略不在返回列表中，仍允许透传 `strategy` 到 `screen()`，由适配层负责参数合法性校验（用于兜底手动策略参数场景）。

## 契约与兼容验证

Settings 页面里仅对 AI 配置项做“字段分组与可视化归并”控制：在通道模式已开启时把部分 `LLM_*` 字段交由 LLM 渠道编辑器展示，
其余字段仍按原分类渲染；**不触发任何配置迁移、清洗重写或回退路径**，也不改写当前 `provider/model/base URL` 路由配置。
保存与重载仍通过既有系统配置保存接口完成：`LITELLM_MODEL`、`LLM_CHANNELS`、`LLM_<NAME>_*` 仍按既定优先级和回退语义继续工作；若需回退，仅需将
`ALPHASIFT_ENABLED` 写回 `false` 即可恢复无选股导航状态，既有模型/密钥字段不会被本次特性清空或重置。

LLM 配置兼容边界（独立说明）：

- 通道模式已启用时，仅对 `LLM_CHANNELS`、`LITELLM_MODEL`、`AGENT_LITELLM_MODEL`、`OPENAI_BASE_URL`、`OPENAI_API_KEY`、`OPENAI_MODEL` 及 `LLM_<NAME>_(PROTOCOL|BASE_URL|API_KEY|API_KEYS|MODELS|EXTRA_HEADERS|ENABLED)` 做展示合并；该逻辑仅决定 `SettingsPage` 字段归并与 `LLMChannelEditor` 的承载范围。
- 对这些字段不做持久化迁移：保存仍走 `systemConfigApi.update` 通用逻辑；若用户仅使用 legacy 配置，不会出现字段被删除、清洗为空或改写为默认值。
- 通道模式未启用时，前述字段保持既有通用配置列表展示与保存路径，不受 `ALPHASIFT_ENABLED` 开关影响。
- 回退语义保持不变：用户可随时撤回 `ALPHASIFT_ENABLED`，AlphaSift 关闭仅影响筛选入口显隐，不影响现有 `provider/model/base URL` 的运行时兼容。

后端 `/api/v1/alphasift/status` 与 `/api/v1/alphasift/install` 只返回非敏感字段，不会回传原始 `ALPHASIFT_INSTALL_SPEC`，并在响应中给出 `install_spec_is_default` 是否为默认可信来源。
在自动化测试中通过 `tests/test_alphasift_api.py` 固化以下约束（以便将该 commit 与 DSA 调用契约解耦验证）：

- 状态接口不返回 `install_spec` 明文。
- 安装接口返回 `installed`/`already_installed`/`install_spec_is_default`，不返回 `install_spec` 明文。
- `alphasift.get_status()` 用于可用性判断，`alphasift.list_strategies()` 用于动态策略下发，`alphasift.screen(strategy, market=..., max_results=..., use_llm=False)` 用于候选执行。

锁定 commit 的契约依据是该提交内的 Python 模块 `alphasift.dsa_adapter`（包内路径 `alphasift/dsa_adapter.py`）。当前 DSA 后端仍只调用上述三个函数，Web 侧仍只向后端提交 `market`、`strategy`、`max_results`，因此与主线配置页和 API 结构合并后不需要 AlphaSift 暴露额外字段或内部 pipeline。

当前自动化环境不执行联网安装与运行时真库验收；若需线上复核，请在可访问目标提交的同一 Python 环境手动完成 `pip install` 并访问 `/api/v1/alphasift/screen`，确认上述签名仍可成功执行。

本地复核建议（同一 Python 环境）：

```bash
python -m pip install --upgrade "git+https://github.com/ZhuLinsen/alphasift.git@2c76b2b6074ae3bae01d52e5e830a4af3e3246b2"
python -m pytest tests/test_alphasift_api.py -q
python - <<'PY'
import importlib

alphasift = importlib.import_module("alphasift.dsa_adapter")
print(
    f"adapter callable: {hasattr(alphasift, 'get_status')} "
    f"{hasattr(alphasift, 'list_strategies')} {hasattr(alphasift, 'screen')}"
)
PY
```

若 AlphaSift 接口不兼容或自动安装失败，可将 `ALPHASIFT_ENABLED=false` 回退为关闭状态；已手动安装的包由运行环境自行管理。

## 接口

```text
GET  /api/v1/alphasift/status
POST /api/v1/alphasift/screen
GET  /api/v1/alphasift/strategies
```

请求示例：

```json
{
  "market": "cn",
  "strategy": "dual_low",
  "max_results": 20
}
```

当前不做通用插件系统、插件市场、CLI/Bot/Scheduler/MCP 集成，也不新增持久化表。DSA 只负责开关、页签、接口透传和结果展示；策略、数据处理与排序逻辑仍由 AlphaSift 自身负责。

## 风险提示

AlphaSift 选股结果仅用于研究和辅助判断，不构成投资建议；市场有风险，交易决策和损益由使用者自行承担。

## 最终验证结论（当前修订版）

- 已确认工作树无未合并文件与未解析 merge marker（`git diff --name-only --diff-filter=U` 无输出，`rg -n "^(<<<<<<< .+|=======$|>>>>>>> .+)"` 无命中），当前结论基于无冲突状态重跑。
- 关键后端契约与安装状态链路（本轮重跑）：
  - `timeout 300s python -m pytest tests/test_alphasift_api.py tests/test_system_config_service.py tests/test_llm_channel_config.py tests/test_config_validate_structured.py tests/test_config_env_compat.py tests/test_task_queue_config_sync.py -q`（256 passed）。
- Web 侧兼容性回归：
  - `cd apps/dsa-web && timeout 600s npm ci --ignore-scripts`（通过）。
  - `cd apps/dsa-web && timeout 240s npm run test -- src/api/__tests__/alphasift.test.ts src/hooks/__tests__/useSystemConfig.test.tsx src/pages/__tests__/SettingsPage.test.tsx src/components/layout/__tests__/SidebarNav.test.tsx`（4 files passed，47 tests passed）。
  - `cd apps/dsa-web && timeout 240s npm run lint`（通过）。
  - `cd apps/dsa-web && timeout 300s npm run build`（通过）。
- 与 LLM 配置相关：
  - 仅做 `settings` 展示分组与可视化归并，不会清理、迁移或改写 `LITELLM_MODEL`、`OPENAI_BASE_URL` 等 Legacy 字段；
  - `ALPHASIFT_INSTALL_SPEC` 仅用于安装动作，不在状态响应中明文返回（仅返回 `install_spec_is_default`）；
  - 兼容性回归已有覆盖：后端侧通过 `test_llm_channel_config.py`、`test_config_validate_structured.py`、`test_config_env_compat.py`、`test_system_config_service.py` 验证渠道/模型/基础 URL 的既有优先级与回退不变；Web 侧通过 Settings 页面用例保证切换 AlphaSift 仅影响页签显隐与开关路径，不触发模型字段重写。
