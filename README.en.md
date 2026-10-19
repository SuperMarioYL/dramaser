**English** | [简体中文](./README.md)

<picture>
  <source media="(max-width: 640px) and (prefers-color-scheme: dark)" srcset="assets/presentation/hero-mobile-dark.svg">
  <source media="(max-width: 640px)" srcset="assets/presentation/hero-mobile-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="assets/presentation/hero-dark.svg">
  <img src="assets/presentation/hero-light.svg" width="1000" alt="dramaser particles converge into one locked character: lock your characters, export straight into JianYing.">
</picture>

# dramaser

**One character, many episodes, zero drift — the character-consistency production tool for vertical drama series, exporting straight into JianYing (剪映).**

<img src="https://readme-typing-svg.demolab.com/?font=Fira+Code&weight=600&size=24&pause=1500&color=E656D5&center=true&vCenter=true&random=false&width=680&lines=uv+run+dramaser+demo;%E2%86%92+3+episodes+x+3+shots%2C+replayed+with+no+API+key;%E2%86%92+6%2F9+unlocked+shots+flagged+as+drift;%E2%86%92+JianYing+draft+opens+directly" alt="uv run dramaser demo → 3 episodes x 3 shots, replayed with no API key → 6/9 unlocked shots flagged as drift → JianYing draft opens directly">

`v0.2.0` · `Python 3.12+` · [MIT](LICENSE)

[![Version](https://img.shields.io/badge/version-0.2.0-e656d5)](https://github.com/SuperMarioYL/dramaser/releases)
[![Python](https://img.shields.io/badge/python-3.12%2B-3776ab)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-e656d5)](./LICENSE)
[![No-key demo](https://img.shields.io/badge/demo-no--key%20replay-e656d5)](#quickstart)
[![Canvas](https://img.shields.io/badge/vertical-9%3A16%20%C2%B7%2060s%20tier-3776ab)](#configuration)

[Demo record](docs/demo-results.json) · [CHANGELOG](CHANGELOG.md) · [简体中文](./README.md)

## Why dramaser

Once a small team running a vertical-drama IP account starts using an AI generation pipeline as its main capacity, the first thing that breaks is not "we can't generate" — it's "we generated dozens of slightly different versions of the same character". Generic text-to-video generates every clip independently, so face shape, hair color and outfit drift across episodes, while character recognizability is exactly what drives follower growth and brand deals for these accounts.

Agentic video production bases like OpenMontage and hyperframes solved the pipeline and the rendering primitives, but agent-scale batch production removed the human art fallback without adding a series-level consistency layer — continuity there is a scoring item, not stateful asset. dramaser is not their Chinese clone; it is the layer wired to the Chinese delivery surface:

- **Cross-episode character lock** — the `appearance_lock` contract goes into every subject-reference generation, anchored by the reference image;
- **Shot-level partial regeneration** — each shot's lineage records its params hash and score, so `regen` re-burns only drifted shots and reuses everything else;
- **Platform rules as code** — 9:16, the 60s duration tier and the subtitle safe zone live in `series.yaml`, and the finished cut exports as a JianYing-openable draft.

The vertical-series conventions themselves are packaged as a reusable Skill asset — the character sheet is a standard format, and handing over a series means handing over a git repository.

## Recorded demo

<picture>
  <source media="(max-width: 640px) and (prefers-color-scheme: dark)" srcset="assets/presentation/process-mobile-dark.svg">
  <source media="(max-width: 640px)" srcset="assets/presentation/process-mobile-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="assets/presentation/process-dark.svg">
  <img src="assets/presentation/process-light.svg" width="1000" alt="Locked production loop: lock the character sheet, generate and judge per shot, record lineage, regenerate only drifted shots and export a fresh draft. Drifted shots fail the gate; locked shots keep their assets.">
</picture>

One character across 3 episodes, unlocked drift vs dramaser lock (recorded from the v0.1 demo replay; inputs and versions in [docs/demo-results.json](docs/demo-results.json)):

![drift vs locked](docs/assets/demo-drift-vs-locked.gif)

In replay mode both lanes come from the same preset character parameters: the un-locked lane accumulates drift per episode (episode 1 squeaks by, episodes 2 and 3 fail the consistency gate) while the locked lane passes 9/9. The actual command output:

```text
$ uv run dramaser demo
demo 完成（replay 模式，无 API key）
  对比 GIF   : dramaser-demo/demo-drift-vs-locked.gif
  一致性报告 : dramaser-demo/consistency-report.md
  剪映 draft : dramaser-demo/jianying/dramaser_demo/draft_content.json
  无锁定     : 6/9 镜头漂移（门限 0.85）
  dramaser   : 9/9 镜头锁定
  拷入剪映   : 重跑加 --install-draft
```

Machine verification of the exported project via `check draft`:

```text
9:16 canvas (1080x1920)   pass   1080x1920
video + subtitle tracks   pass   video=1 text=1
material files on disk    pass   all present
segments chained          pass   43.0s total
per-episode tier (60s)    pass   3 episodes within budget
subtitles in safe zone    pass   9 cues (bottom≥12%)
```

## Quickstart

You need Python 3.12+ and [uv](https://docs.astral.sh/uv/). The demo path needs zero API keys and zero ffmpeg (deterministic Pillow replay).

```bash
git clone https://github.com/SuperMarioYL/dramaser.git
cd dramaser
uv run dramaser demo            # <10s: comparison GIF + consistency report + JianYing draft
```

Skip the clone and run straight from git (all preset assets are bundled):

```bash
uvx --from git+https://github.com/SuperMarioYL/dramaser dramaser demo
```

Once published to PyPI, plain `uvx dramaser demo` works the same way (see [Releases](https://github.com/SuperMarioYL/dramaser/releases)).

**Install the draft into JianYing** (with JianYing installed locally):

```bash
uv run dramaser demo --install-draft   # copies into the drafts folder; open JianYing and see the 3-episode timeline
```

**Start your own series** (live generation needs a [Vidu](https://www.vidu.cn/) and a [Zhipu](https://open.bigmodel.cn/) key):

```bash
export VIDU_API_KEY=... ZHIPU_API_KEY=...
uv run dramaser init --preset ayao my-series   # scaffold an editable series.yaml
cd my-series
uv run dramaser generate ep01                  # subject-reference generation + per-shot consistency gate
uv run dramaser regen ep01_s03                 # regenerate only the drifted shot, lineage untouched elsewhere
uv run dramaser export ep01 --install-draft    # export a fresh draft into JianYing
```

Full inputs and recorded outputs live in [docs/demo-results.json](docs/demo-results.json); a three-step walkthrough is in [examples/quickstart.md](examples/quickstart.md).

## Architecture

<picture>
  <source media="(max-width: 640px) and (prefers-color-scheme: dark)" srcset="assets/presentation/architecture-mobile-dark.svg">
  <source media="(max-width: 640px)" srcset="assets/presentation/architecture-mobile-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="assets/presentation/architecture-dark.svg">
  <img src="assets/presentation/architecture-light.svg" width="1000" alt="Data flow: SeriesAsset (series.yaml) feeds the pipeline core, which dispatches to the provider layer (replay renderer / Vidu subject reference) and the GLM-4V-Flash judge; verdicts write back into lineage and fan out to the JianYing exporter and the report renderer.">
</picture>

A single-process Python CLI — no services, no daemons. One `series.yaml` project file drives the whole pipeline: expand shots → generate/replay → consistency gate → lineage on disk. The only external dependency is the ffmpeg binary (frame extraction when judging live video shots).

| Module | Responsibility |
| --- | --- |
| [series.py](src/dramaser/series.py) | SeriesAsset schema (pydantic) + block-style YAML I/O + preset loading |
| [storyboard.py](src/dramaser/storyboard.py) | shot/lineage structures + platform rule engine (9:16 geometry, safe zone, tiers) |
| [providers/base.py](src/dramaser/providers/base.py) | Provider interface + deterministic replay renderer (the no-key demo) |
| [providers/vidu.py](src/dramaser/providers/vidu.py) | Vidu subject-reference live generation (img2video + task polling) |
| [consistency.py](src/dramaser/consistency.py) | character-lock prompt assembly + GLM-4V-Flash structured judge + gate |
| [jianying.py](src/dramaser/jianying.py) | JianYing draft export / install / `check draft` verification |
| [report.py](src/dramaser/report.py) | drift-vs-lock comparison GIF + consistency report + demo record |
| [cli.py](src/dramaser/cli.py) | demo / init / generate / regen / export / check |

Shot lineage is what makes partial regeneration possible: every shot persists `run_id / provider / params_hash / judge_score / status`, `regen` replaces only `status=drifted` shots, and the rest reuse their locked assets without re-burning API credits.

## Capabilities

<picture>
  <source media="(max-width: 640px) and (prefers-color-scheme: dark)" srcset="assets/presentation/integrations-mobile-dark.svg">
  <source media="(max-width: 640px)" srcset="assets/presentation/integrations-mobile-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="assets/presentation/integrations-dark.svg">
  <img src="assets/presentation/integrations-light.svg" width="1000" alt="Implemented routes: Vidu subject-reference generation, GLM-4V-Flash structured judging, JianYing draft export via pyJianYingDraft 0.3, the Douyin 60s preset (9:16 / tier / safe zone), the no-key replay demo, and a git-friendly series project.">
</picture>

| Capability | Status | Notes |
| --- | --- | --- |
| No-key replay demo | implemented | preset character sheet + deterministic render fixtures, fully reproducible |
| Vidu subject-reference generation | implemented (key required) | built from the public ent/v2 docs; wire format isolated in two methods, not yet validated against a live key |
| GLM-4V-Flash consistency gate | implemented (key required) | prompt-agreed JSON output with defensive parsing; below-threshold shots are marked drifted |
| JianYing draft export | implemented | pyJianYingDraft 0.3: 9:16 canvas, safe-zone subtitle track, tier segmentation, `--install-draft` |
| Shot-level regen | implemented | re-burns only target/drifted shots, lineage and assets preserved per shot |
| Drift GIF and report | implemented | Pillow compositing + Markdown report; README and demo share the same artifacts |

Honest boundaries:

- Replay frames are deterministically rendered from preset character parameters with Pillow — a deliberate visualization of drift vs. lock, **not model output**; their score is the weighted distance between the render signature and the locked baseline, behind the same judge interface as GLM-4V-Flash.
- Judging live video shots requires ffmpeg on PATH.
- The consistency score is a quality gate, not ground truth; the threshold is configurable and spot-checking drifted verdicts is recommended.

## Configuration

`series.yaml` is the single project file (the one `dramaser init` scaffolds is a complete template):

```yaml
characters:
  - id: ayao
    appearance_lock:      # free-form lock attributes (any labels), sent with every prompt
      脸型: oval face, soft jawline, small silver star earring on the left ear
      发型: long straight black-brown hair, center part, slightly curled ends
    reference_images: [reference/ayao.png]
style:
  palette_lock: ["#2b2340", "#a98f6f", "#e9e1d3"]
  negative_lock: [extra fingers, facial distortion, blur]
platform:
  preset: douyin_60s      # 9:16 / 60s tier / subtitle safe zone (top 8%, bottom 12%, right 10%)
  duration_tier: 60
gates:
  consistency_threshold: 0.85
```

- Environment variables: `VIDU_API_KEY` (live generation), `ZHIPU_API_KEY` (GLM-4V-Flash judging), `DRAMASER_ASSETS` (custom preset asset root; defaults to the repo `assets/`, with an identical copy bundled inside the installed package).
- Shot fields: `id / characters / line / action / camera / duration_s`; `camera` supports 远景/中景/近景/特写 (wide/medium/close/extreme-close).
- Changing a character means editing `appearance_lock` and the reference image; the preset lives in [assets/presets/ayao/](assets/presets/ayao/) with instructions for regenerating its reference image.

## Pricing

dramaser targets commercial production teams, and it does not monetize by locking the CLI:

- **CLI and the SeriesAsset format**: MIT open source, free forever. The character sheet is a standard asset format and the git repo stays in your hands.
- **Series pilot package (currently sold)**: ¥9,800 / series (3 episodes) — build the character sheet from your real character, wire up your Vidu/Zhipu keys, deliver 3 JianYing-openable drafts, plus a 2-hour onboarding session. Reach out via [Issues](https://github.com/SuperMarioYL/dramaser/issues).
- **Hosted team workbench (planned for v0.2+)**: ¥99/user/month — character asset library, series-project collaboration and permissions; the CLI stays free. Pricing anchor: re-burning the drifted footage of 3 episodes already costs about as much in API credits.

Open format plus closed in-the-loop control is deliberate: the moat is the combination of the character-lock contract, shot lineage and platform rules — not protocol invention.

## Roadmap

- **v0.1 (current)**: replay demo, JianYing draft export and verification, Vidu live generation + GLM-4V-Flash consistency gate + shot-level regen.
- **v0.2 plans**: local CLIP-embedding judge (same gate interface), a Kling adapter (the provider interface already leaves the extension point), hosted team workbench.
- **Explicitly out of scope for v0.1**: web workbench (CLI first — teams hand over git repos), LLM script-to-storyboard splitting (hand-written YAML storyboards), TTS and voice cloning (the sheet only stores a voice field), ASR subtitles (subtitle text comes from the shot `line` field), BGM selection, auto-publishing to Douyin/Channels, multi-user accounts.

## License

[MIT](LICENSE) · the character sheets and preset assets ship under MIT as well.

<p align="center"><sub><a href="./LICENSE">MIT</a> © 2026 SuperMarioYL</sub></p>
