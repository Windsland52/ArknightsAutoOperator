import shutil
from pathlib import Path

assets_dir = Path(__file__).parent.parent


# PP-OCRv6 混合档：
# - det.onnx 用 small：检测框更准（按钮/干员名/关卡名定位更稳）
# - rec.onnx + keys.txt 用 tiny：识别轻量，包体积小
_OCR_SRC = assets_dir / "MaaCommonAssets" / "OCR" / "ppocr_v6"
_OCR_DST = assets_dir / "resource" / "base" / "model" / "ocr"


def configure_ocr_model():
    shutil.rmtree(_OCR_DST, ignore_errors=True)
    _OCR_DST.mkdir(parents=True, exist_ok=True)

    shutil.copy2(_OCR_SRC / "small" / "det.onnx", _OCR_DST / "det.onnx")
    shutil.copy2(_OCR_SRC / "tiny" / "rec.onnx", _OCR_DST / "rec.onnx")
    shutil.copy2(_OCR_SRC / "tiny" / "keys.txt", _OCR_DST / "keys.txt")

    (_OCR_DST / "README.md").write_text(
        "PP-OCRv6 mixed model:\n- det.onnx: ppocr_v6/small\n- rec.onnx, keys.txt: ppocr_v6/tiny\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    configure_ocr_model()
    print("OCR model configured.")
