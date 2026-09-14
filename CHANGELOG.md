# Changelog

本项目的显著变更记录在本文件。版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)，
发布标签格式为 `v<version>`（见 [VERSION](VERSION)）。

## [0.1.0] — 2026-09-14

首个公开版本：竖屏微短剧的角色一致性产片工具，成片直出剪映。

### m1 — 无 key 回放 demo

- SeriesAsset 数据模型（pydantic）：角色设定集锁定、风格锁、平台规范、分镜与镜头血缘，
  块式 YAML 读写不引入额外依赖。
- 预置角色集 `ayao`（都市情感微短剧《咖啡与晚风》，3 集 × 3 分镜）与确定性 replay 夹具。
- `dramaser demo`：单命令、零 API key，输出 3 集漂移-vs-锁定对比 GIF、逐镜头一致性报告
  与剪映 draft 工程；评分与输入记录于 `docs/demo-results.json`。

### m2 — 剪映 draft 导出

- 基于 pyJianYingDraft 0.3 的导出器：1080×1920 画布、字幕安全区文本轨
  （bottom 12% 基线）、按时长档分段、`--install-draft` 拷入剪映草稿目录。
- `dramaser check draft`：draft 结构与平台规范的机器校验（画布、轨道、素材在盘、
  连续性、时长档、安全区），`tests/test_jianying_export.py` 覆盖。

### m3 — 锁定 live 闭环

- Vidu 主体参考 provider：锁定 prompt 组装、img2video 任务创建与轮询、产物下载，
  线格式隔离待真实 key 验证。
- GLM-4V-Flash 一致性门：prompt 约定 JSON 输出并防御性解析（视觉模型不支持
  `response_format: json_object`），低于阈值标记 `drifted`。
- `dramaser regen <shot>`：分镜级局部重生成，只重烧目标镜头，其余血缘与产物不变。

### 其他

- 中英双语 README（互链）、四组明暗动画 SVG（品牌 hero / 架构 / 流程 / 能力接入，
  由 `docs/render_presentation.py` 从调色板生成）。
- CI（lint + test）与 release workflow（tag → wheel + sdist + checksums），
  PyPI 发布默认关闭、按仓库变量开启。

[0.1.0]: https://github.com/SuperMarioYL/dramaser/releases/tag/v0.1.0
