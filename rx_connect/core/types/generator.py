from pathlib import Path
from typing import NamedTuple

import numpy as np

YOLO_LABELS = "labels"
SEGMENTATION_LABELS = "comp_masks"
COCO_LABELS = "COCO"


class PillMaskPaths(NamedTuple):
    """The paths to the pill image and its corresponding mask."""

    img_path: Path
    mask_path: Path


class PillMask(NamedTuple):
    """The pill image and mask."""

    image: np.ndarray
    mask: np.ndarray
