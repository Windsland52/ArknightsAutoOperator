# resource/base — MAA Bundle

MAA Resource 的加载单元（`resource.post_bundle("resource/base")`）。结构：

- `default_pipeline.json` — 节点默认参数（`rate_limit` / `timeout` / `TemplateMatch` / `OCR` / ...）。
- `pipeline/` — 任务流水线 JSON（里程碑 6 加 `farm.json`：进关 → 开战 → `ExecuteTimeline` → 结算 → 重开）。
- `image/` — OCR / TemplateMatch 模板图（720p 裁剪，里程碑 6 加）。
- `model/ocr/` — PaddleOCR-onnx 模型（`det.onnx` / `rec.onnx` / `keys.txt`）；由根目录 `tools/configure.py` 从 `MaaCommonAssets` 拷入（gitignored，需手动生成）。

生成 OCR 模型：`python tools/configure.py`

详见 [`docs/rewrite-plan.md`](../../docs/rewrite-plan.md)。
