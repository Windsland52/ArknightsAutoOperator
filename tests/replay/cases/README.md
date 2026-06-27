# 回放用例素材

每个子目录是一个回放用例，结构：

```plaintext
<case_name>/
  frames/           逐帧 PNG，命名 000001.png、000002.png …（字典序 = 时间序）
  calibration.json  对应校准 profile（直接复制 config/calibration/ 下的文件）
  expected.json     {"frames": [全局帧, ...]}  每帧预期 total_elapsed_frames
```

## 录制素材

1. 游戏内录一段视频（含费用条可见、正常 1x 回费）。
2. 用系统 ffmpeg 抽帧为 PNG（60fps 示例）：

    ```powershell
    ffmpeg -i input.mp4 -vf fps=60 frames/%06d.png
    ```

    抽帧 fps 要与测量 worker 的 `interval_s`（默认 1/60）一致，否则帧序号对不上。

3. 把对应关卡的校准文件复制为 `calibration.json`。

## 生成 expected.json

先跑一次回放，把实际读数作为初稿：

```python
from pathlib import Path
from replay import load_case, run_replay
import json

frames, calib, _ = load_case(Path("cases/边界周期"))
actual = run_replay(frames, calib)
Path("cases/边界周期/expected.json").write_text(
    json.dumps({"frames": actual}), encoding="utf-8"
)
```

然后**人工核对关键帧**（边界周期第 11 周期、负费切换点），确认无误后再固化。
expected 必须是「正确答案」，不是「当前实现输出」——否则回归无意义。

## 推荐用例

- `边界周期`：战斗 > 315 帧，验证第 11 周期多 1 帧、边界后周期不偏移。
- `负费`：可露希尔关卡，验证负费期间周期帧数 ×2、相位重投射。
- `正常回费`：基线，验证未引入回归。
