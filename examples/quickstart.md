# 三步上手：从预置角色集到自己的剪映 draft

前提：Python 3.12+、uv；live 生成需要 `VIDU_API_KEY` 与 `ZHIPU_API_KEY`（[申请 Vidu](https://platform.vidu.cn/) / [申请智谱](https://open.bigmodel.cn/)）。

```bash
# 1. 无 key 先看回放 demo（对比 GIF + 报告 + draft）
uv run dramaser demo

# 2. 以预置角色集阿瑶为模板，创建可编辑的系列工程
uv run dramaser init --preset ayao my-series

# 3. live 闭环：生成 → 只重生成漂移镜头 → 导出剪映 draft
cd my-series
uv run dramaser generate ep01
uv run dramaser regen ep01_s03
uv run dramaser export ep01 --install-draft
```

产物（`my-series/` 内）：

- `series.yaml` — 角色设定集 + 平台规范 + 分镜 + 每镜头血缘（可 git 管理）
- `runs/<run_id>/` — 每次生成的镜头素材
- `reports/consistency-ep01.md` — 逐镜头一致性评分
- `jianying-drafts/dramaser_ep01/` — 剪映可直接打开的 draft

完整字段说明见 [README 配置节](../README.md#配置)。
