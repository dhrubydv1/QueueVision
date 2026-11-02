"""Export YOLO weights to CoreML (``.mlpackage``) for Apple Silicon inference.

Example::

    pip install -r requirements-coreml.txt
    python ai_engine/tools/export_model.py --model yolo11n.pt --imgsz 640
    python ai_engine/run_tracking.py --video datasets/raw/mot17_09.mp4 \\
        --model ai_engine/models/yolo11n.mlpackage

The CoreML model has a fixed input size; run it with the same ``--imgsz``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path[0] = str(Path(__file__).resolve().parents[2])

from ai_engine.detection.detector import ModelLoadError, load_yolo_model, resolve_model_path


def export_coreml(model: str | Path, image_size: int = 640, half: bool = False) -> Path:
    """Export ``model`` to CoreML next to the weights and return the package path."""
    yolo = load_yolo_model(model)
    exported = yolo.export(format="coreml", imgsz=image_size, half=half, nms=False)
    return Path(exported)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export YOLO weights to CoreML.")
    parser.add_argument("--model", default="yolo11n.pt", help="Weights to export (default: %(default)s)")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--half", action="store_true", help="FP16 weights (smaller, may be faster)")
    args = parser.parse_args(argv)
    try:
        import coremltools  # noqa: F401  (fail early with a clear message)
    except ImportError:
        print("ERROR: coremltools is not installed; pip install -r requirements-coreml.txt", file=sys.stderr)
        return 1
    try:
        path = export_coreml(args.model, args.imgsz, args.half)
    except ModelLoadError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"Exported {resolve_model_path(args.model).name} -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
