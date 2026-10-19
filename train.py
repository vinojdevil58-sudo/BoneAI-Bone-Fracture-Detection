"""Train a YOLO object detector after adding a properly labeled dataset."""
from pathlib import Path
import sys
from ultralytics import YOLO

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "dataset" / "data.yaml"

if not DATA.is_file():
    sys.exit(f"Dataset config not found: {DATA}\nRead dataset/README.md and add your labeled dataset first.")

# Validate that configured train/val image folders exist before starting a long run.
try:
    import yaml
    config = yaml.safe_load(DATA.read_text(encoding="utf-8"))
    dataset_root = Path(config.get("path", DATA.parent))
    if not dataset_root.is_absolute():
        dataset_root = (DATA.parent / dataset_root).resolve()
    missing = []
    for key in ("train", "val"):
        entry = config.get(key)
        if not entry:
            missing.append(f"missing '{key}' entry in data.yaml")
            continue
        p = Path(entry)
        if not p.is_absolute():
            p = dataset_root / p
        if not p.exists():
            missing.append(str(p))
    if missing:
        sys.exit("Dataset folders/config are incomplete:\n- " + "\n- ".join(missing) + "\nRead dataset/README.md before training.")
except ImportError:
    print("PyYAML is not importable; Ultralytics normally installs it. Continuing to YOLO validation.")

model = YOLO("yolov8n.pt")
model.train(
    data=str(DATA),
    epochs=50,
    imgsz=640,
    batch=8,
    project=str(ROOT / "runs" / "detect"),
    name="boneai_fracture",
)

best = ROOT / "runs" / "detect" / "boneai_fracture" / "weights" / "best.pt"
if best.exists():
    target = ROOT / "models" / "best.pt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(best.read_bytes())
    print(f"Copied trained checkpoint to: {target}")
else:
    print(f"Training ended, but expected checkpoint was not found at {best}")
