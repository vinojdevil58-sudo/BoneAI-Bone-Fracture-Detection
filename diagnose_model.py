"""Check model loading and optionally run inference on an image."""
import argparse
from pathlib import Path
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("image", nargs="?", help="Path to an image to test")
parser.add_argument("--conf", type=float, default=0.25, help="Detection confidence threshold (default: 0.25)")
args = parser.parse_args()

ROOT = Path(__file__).resolve().parent
CHECKPOINT = ROOT / "models" / "best.pt"

print(f"Python: {sys.version.split()[0]}")
try:
    import torch
    import ultralytics
    from ultralytics import YOLO
    print(f"PyTorch: {torch.__version__}")
    print(f"Ultralytics: {ultralytics.__version__}")
except Exception as exc:
    sys.exit(f"Dependency import failed: {exc}\nRun: python -m pip install -r requirements.txt")

print(f"Checkpoint: {CHECKPOINT}")
if not CHECKPOINT.is_file():
    sys.exit("Checkpoint is missing. Place your model at models/best.pt.")
try:
    model = YOLO(str(CHECKPOINT))
    print("Model loaded: yes")
    print(f"Task: {getattr(model, 'task', 'unknown')}")
    print(f"Class names: {model.names}")
except Exception as exc:
    sys.exit(f"Model load failed: {exc}")

if not args.image:
    print("No image supplied; model load only was tested, not prediction accuracy.")
    raise SystemExit(0)
image = Path(args.image).expanduser()
if not image.is_file():
    sys.exit(f"Image file not found: {image}\nTip: put a space between diagnose_model.py and the quoted image path.")
try:
    result = model.predict(source=str(image), conf=args.conf, verbose=False)[0]
    count = len(result.boxes) if result.boxes is not None else 0
    print(f"Inference completed: yes\nDetection count: {count}")
    if count:
        for box in result.boxes:
            cid = int(box.cls[0].item())
            print(f"- {model.names.get(cid, cid)}: {float(box.conf[0]):.4f}; bbox={box.xyxy[0].tolist()}")
    else:
        print("No boxes passed the threshold. This is inconclusive, not evidence of no fracture.")
    output = ROOT / "diagnostic_result.jpg"
    result.save(filename=str(output))
    print(f"Annotated output saved to: {output}")
    print("Technical check only; predictions are not clinically validated by this script.")
except Exception as exc:
    sys.exit(f"Inference failed: {exc}")
