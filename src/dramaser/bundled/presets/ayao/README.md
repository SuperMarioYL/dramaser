# ayao 角色设定集（预置）

都市情感微短剧《咖啡与晚风》的女主角「阿瑶」，是 dramaser 的第一个预置角色集。

## 文件

- `series.yaml` — 完整 SeriesAsset：角色锁定契约（`appearance_lock`）、风格锁、
  平台规范（抖音 60s / 9:16 / 字幕安全区）与 3 集 × 3 分镜示例。
- `reference/ayao.png` — 角色参考图（主体参考与一致性评审的锚点）。

## 用法

```bash
# 无 key 回放整套系列（使用仓库根目录的 assets/demo-replay 夹具）
uv run dramaser demo

# 以该角色集为模板创建你自己的系列工程
uv run dramaser init --preset ayao my-series
```

## 修改与替换

- `series.yaml` 中的 `appearance_lock` 是给 Vidu 主体参考与 GLM-4V-Flash 评审的
  锁定契约，改这里即可换设定；`reference_images` 换成你的真实角色参考图。
- `reference/ayao.png` 是用 replay 渲染器从上述参数确定性生成的示意参考图，
  可用任何真实设定图替换。重新生成：

```bash
uv run python -c "from pathlib import Path; from dramaser.series import load_preset; from dramaser.providers.base import GenerationRequest, ReplayProvider, SceneSpec, render_frame; s,_ = load_preset('ayao'); p = ReplayProvider(); params = p.character_params(GenerationRequest(shot=s.all_shots()[0], series=s, out_path=Path('/dev/null'))); render_frame(params, SceneSpec((43,35,52),(74,58,80),'none',0), 0.82).save('reference/ayao.png')"
```

替换角色时建议保留 9:16 竖构图与近似景别，主体参考效果最好。
