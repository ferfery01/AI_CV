from enum import Enum
from pathlib import Path
from typing import ClassVar, Dict, Optional, Tuple, Union

import torch
from skimage.io import imread
from torch.utils.data import Dataset

from rx_connect.core.augments import BasicAugTransform
from rx_connect.core.images.types import img_to_tensor
from rx_connect.core.utils.io_utils import get_matching_files_in_dir


class DatasetSplit(Enum):
    """Enum class for dataset split."""

    TRAIN = 0
    VAL = 1
    TEST = 2


class SegDataset(Dataset):
    """A PyTorch Dataset class for semantic segmentation tasks.

    This class is designed to handle the dataset with the specific directory structure containing training,
    val, and test splits with corresponding image and mask directories. The images are loaded and resized,
    and optional transformations can be applied.

    Expected Directory Structure:
    root_dir/
        ├── train
        │   ├── images
        │   │   ├── 0001.jpg
        │   │   ├── ...
        │   └── comp_masks
        │       ├── 0001.png
        │       ├── ...
        │
        ├── val
        │   ├── images
        │   │   ├── 0001.jpg
        │   │   ├── ...
        │   └── comp_masks
        │       ├── 0001.png
        │       ├── ...
        │
        └─ test
            ├── images
            │   ├── 0001.jpg
            │   ├── ...
            └── comp_masks
                ├── 0001.png
                ├── ...

    Attributes:
        root_dir (Union[str, Path]): Path to the root directory containing the dataset splits.
        data_split (DatasetSplit): Specifies the split of the dataset (TRAIN, VAL, or TEST).
        image_size (Tuple[int, int]): The target size for the images and masks.
        transforms (Optional[A.Compose]): The augmentations to be applied.

    Example:
        >> dataset = SegDataset(root_dir="./data", data_split=DatasetSplit.TRAIN, image_size=(512, 512))
        >> sample = dataset[0]
        >> image, mask = sample["image"], sample["mask"]
    """

    SUBDIR_IMAGES: ClassVar[str] = "images"
    SUBDIR_MASKS: ClassVar[str] = "masks"
    SUBDIR_SPLIT: ClassVar[Dict[DatasetSplit, str]] = {
        DatasetSplit.TRAIN: "train",
        DatasetSplit.VAL: "val",
        DatasetSplit.TEST: "test",
    }
    _repr_indent: int = 4

    def __init__(
        self,
        root_dir: Union[str, Path],
        data_split: DatasetSplit,
        image_size: Tuple[int, int],
        transforms: Optional[BasicAugTransform] = None,
    ) -> None:
        self.root_dir = Path(root_dir)
        self.data_split = data_split
        self.image_size = image_size
        self.transforms = transforms

        # Variables containing list of all input image and ground truth mask paths
        self.image_paths = get_matching_files_in_dir(
            dir_path=self.root_dir / self.SUBDIR_SPLIT[self.data_split] / self.SUBDIR_IMAGES,
            wildcard_patterns="*.jpg",
        )
        self.mask_paths = get_matching_files_in_dir(
            dir_path=self.root_dir / self.SUBDIR_SPLIT[self.data_split] / self.SUBDIR_MASKS,
            wildcard_patterns="*.png",
        )
        assert len(self.image_paths) == len(
            self.mask_paths
        ), f"Number of images ({len(self.image_paths)}) and masks ({len(self.mask_paths)}) must be equal"

    def __len__(self) -> int:
        return len(self.image_paths)

    def __getitem__(self, idx: int) -> Dict[str, torch.Tensor]:
        # Read input image and ground truth mask
        image = imread(self.image_paths[idx])
        mask = imread(self.mask_paths[idx])

        # The dataset contains different pixel values for the different pills,
        # and hence we need to convert them to binary masks
        mask[mask > 0] = 1

        # Apply augmentations
        if self.transforms is not None:
            image_tensor, mask_tensor = self.transforms(image, mask)
        else:
            image_tensor, mask_tensor = img_to_tensor(image), img_to_tensor(mask)

        return {
            "image": image_tensor,  # (B, 3, H, W)
            "mask": mask_tensor,  # (B, 1, H, W)
        }

    def __repr__(self) -> str:
        head = self.__class__.__name__
        body = [
            f"Number of datapoints: {self.__len__()}",
            f"Root location: {self.root_dir}",
            f"Split: {self.data_split.name}",
        ]
        if self.transforms is not None:
            body += [f"Transform: {repr(self.transforms)}"]
        lines = [head] + [f"{' ' * self._repr_indent}{line}" for line in body]
        return "\n".join(lines)
