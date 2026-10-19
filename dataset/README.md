# Dataset setup (required before training)

This directory is a template. It contains no real training images or labels.

Expected structure:

- `images/train/` training images
- `images/val/` validation images
- `labels/train/` YOLO label text files matching training image basenames
- `labels/val/` YOLO label text files matching validation image basenames
- `data.yaml` dataset configuration

For every image, create a same-named `.txt` label file. Each non-empty line must use YOLO detection format:

`class_id x_center y_center width height`

Coordinates must be normalized to 0–1. Example only (not a real annotation):

`2 0.52 0.48 0.20 0.16`

The class IDs in labels must match `names` in `data.yaml`. Use medically appropriate, permissioned X-ray data and reliable expert annotations. Do not train on decorative/stock website graphics. Do not use this template as if it were a dataset.
