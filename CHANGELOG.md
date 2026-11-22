# Changelog

本项目的显著变更记录在本文件。版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)，
发布标签格式为 `v<version>`（见 [VERSION](VERSION)）。

## [0.3.0] — 2026-11-23

CLI 与校验层的契约修复：`--series` 不再写错文件，手改模板可以加载，
`check draft` 与 demo 不再静默通过。

### 修复

- `generate` / `regen` / `check consistency` 把血缘写回 `--series` 指定的那个文件：
  以前总是写到同目录的 `series.yaml`，用户改名的系列文件会停留在 pending，
  同时出现两个真相源。
- `init` 接受已存在的空目录：不再因 `shutil.copytree` 拒绝已存在目录而抛出
  `FileExistsError` 裸栈；非空目录仍被拒绝。
- 块式 YAML 子集补齐两处读写不对称：`yaml_dump` 写出的「序列项首键 + 嵌套块」
  现在能被 `yaml_load` 读回；README 配置模板里的键尾行内注释
  （如 `appearance_lock:      # ...`）按注释处理，不再被当成值而导致嵌套键缩进报错。
- `check draft` 增加逐镜头字幕覆盖校验：非空 `line` 的镜头必须在字幕轨上找到对应
  台词；删掉一条字幕不再通过导出依赖的质量门（未提供 series 的裸 draft 校验不变）。
- `demo` 在锁定组未过一致性门时以非零码失败：以前即使输出「0/9 镜头锁定」也退出 0，
  零 key 的招牌路径无法自我验证。

### 其他

- 版本面统一到 0.3.0；双语 README 路线图按实际发布状态重写。
- `dist/` 只保留当前版本的 wheel 与 sdist，移除会被发布流程当作附件上传的
  `dist/.gitignore`。

## [0.2.0] — 2026-10-20

live 闭环修复：`generate → 复评 → export → 剪映` 全链路对真实用户可靠。

### 修复

- `check consistency` 与 generate 路径对齐：视频产物先经 ffmpeg 抽帧再评审，
  不再把 mp4 本体当作图片发给 GLM-4V-Flash；未绑定角色的镜头给出清晰错误提示，
  不再抛裸栈。
- 导出时间线按素材实际时长连续摆放：Vidu 4s 成片落在 5s 计划槽时后续镜头自动前移，
  不再产生 1 秒空洞导致自身 `check draft` 判失败；字幕与画面保持同位。
- `--install-draft` 新增 `--draft-dir` 选项：识别不到剪映草稿目录的平台（如 Linux）
  可直接指定目标目录，报错文案不再指向不存在的参数。
- series.yaml 往返修复：空映射（如清空 appearance_lock）保存后可正常回读，
  `generate → save → regen/check` 不再因校验失败中断。

### 其他

- 新增 `dramaser --version`；版本面统一到 0.2.0。

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

[0.3.0]: https://github.com/SuperMarioYL/dramaser/releases/tag/v0.3.0
[0.2.0]: https://github.com/SuperMarioYL/dramaser/releases/tag/v0.2.0
[0.1.0]: https://github.com/SuperMarioYL/dramaser/releases/tag/v0.1.0
