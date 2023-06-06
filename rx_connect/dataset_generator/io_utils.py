import random
from pathlib import Path
from typing import Any, List, NamedTuple, Optional, Sequence, Tuple, Union

import numpy as np
from skimage import io

from rx_connect.dataset_generator.transform import resize_bg
from rx_connect.wbaml.utils.logging import setup_logger

logger = setup_logger()


class PillMaskPaths(NamedTuple):
    """The paths to the pill image and mask."""

    imgs_path: Sequence[Path]
    masks_path: Sequence[Path]


class PillMask(NamedTuple):
    """The pill image and mask."""

    img: np.ndarray
    mask: np.ndarray


def load_pill_mask_paths(data_dir: Union[str, Path]) -> PillMaskPaths:
    """Load all the pill images and the corresponding masks path.

    Args:
        data_dir: The directory containing all the pill images and the corresponding
            masks.

    Returns:
        pill_mask_paths: The paths to the pill image and mask.
    """
    imgs_path = tuple((Path(data_dir) / "images").glob("*.jpg"))
    masks_path = tuple((Path(data_dir) / "masks").glob("*.jpg"))

    if len(imgs_path) == 0:
        raise FileNotFoundError(f"Could not find any pill images in {data_dir}/images.")

    if len(imgs_path) != len(masks_path):
        raise ValueError(f"Number of images ({len(imgs_path)}) and masks ({len(masks_path)}) do not match.")

    logger.info(f"Found {len(imgs_path)} pill images and masks.")

    return PillMaskPaths(imgs_path, masks_path)


def load_image_and_mask(image_path: Path, mask_path: Path, thresh: int = 25) -> PillMask:
    """Get the image and mask from the pill mask paths.

    Args:
        image: The path to the pill image.
        mask: The path to the pill mask.
        thresh: The threshold at which to binarize the mask.

    Returns:
        img: The pill image.
        mask: The pill mask.
    """
    # Load the pill image
    image = io.imread(image_path)

    # Load the pill mask
    try:
        mask = io.imread(mask_path, as_gray=True)
        # Binarize the mask
        mask[mask <= thresh] = 0
        mask[mask > thresh] = 1
    except Exception as e:
        raise FileNotFoundError(
            f"Could not find mask for image {image_path.name}. Did you run `python -m mask_generator`?"
        ) from e

    return PillMask(img=image, mask=mask)


def load_random_pills_and_masks(
    images_path: Sequence[Path],
    masks_path: Sequence[Path],
    *,
    pill_types: int = 1,
    thresh: int = 25,
) -> Tuple[List[np.ndarray], List[np.ndarray]]:
    """Load `pill_types` random pills and masks from the given paths.

    Args:
        images_path: The paths to the pill images.
        masks_path: The paths to the pill masks.
        pill_types: The number of pills to sample.
        thresh: The threshold at which to binarize the mask.

    Returns:
        Tuple of lists of all pill images and masks.
    """
    pill_images: List[np.ndarray] = []
    pill_masks: List[np.ndarray] = []

    # Randomly sample `pill_types` pills
    for _ in range(pill_types):
        # Randomly sample a pill image and mask
        idx = np.random.randint(len(images_path))
        image_path, mask_path = images_path[idx], masks_path[idx]

        pill_img, pill_mask = load_image_and_mask(image_path, mask_path, thresh=thresh)
        pill_images.append(pill_img)
        pill_masks.append(pill_mask)

    return pill_images, pill_masks


def load_bg_image(path: Path, min_dim: int = 1024, max_dim: int = 1920) -> np.ndarray:
    """Load and resize the background image.

    Args:
        path: The path to the background image.
        min_dim: The minimum dimension of the background image.
        max_dim: The maximum dimension of the background image.

    Returns:
        bg_img: The background image as a numpy array.
    """
    # Load the background image
    bg_img = io.imread(path)

    # Resize the background image
    bg_img = resize_bg(bg_img, max_dim, min_dim)

    return bg_img


def generate_random_bg(height: int, width: int, color_tint: int = 10) -> np.ndarray:
    """Generate a random background image.

    Args:
        height (int): height of the background image.
        width (int): width of the background image.
        color_tint (int): Controls the aggressiveness of the color tint applied to the
            background. The higher the value, the more aggressive the color tint. The value
            should be between 0 and 10.

    Returns:
        np.ndarray: random background image.
    """
    assert 0 <= color_tint <= 10, "color_tint should be between 0 and 10."

    # Generate a random color for the background
    background_color = np.random.randint(max(0, 200 - color_tint * 10), 256, size=(3,)).tolist()

    # Create a black background image
    background_image = np.zeros((height, width, 3), np.uint8)

    # Fill the background image with the random color
    background_image[:] = background_color

    return background_image


def get_background_image(
    path: Optional[Union[str, Path]] = None,
    min_bg_dim: int = 2160,
    max_bg_dim: int = 3840,
    **kwargs: Any,
) -> np.ndarray:
    """Get the background image.
        1. If no background image path is provided, generate a random color background
        2. If a background image path is provided, load it directly
        3. If a directory of background images is provided, choose a random image

    Args:
        path: Path to the background image or directory of background images
        min_bg_dim: Minimum dimension of the background image
        max_bg_dim: Maximum dimension of the background image

    Returns:
        Background image as a numpy array resized to the specified dimensions
    """
    if path is None:
        # Generate random color background if no background image is provided
        bg_image = generate_random_bg(min_bg_dim, max_bg_dim, **kwargs)
    else:
        path = Path(path)
        if path.is_file():
            # If a background image path is provided, load it
            bg_image = load_bg_image(path, min_bg_dim, max_bg_dim)
        elif path.is_dir():
            # If a directory of background images is provided, choose a random image
            paths = list(path.glob("*.jpg"))
            bg_image = load_bg_image(random.choice(paths), min_bg_dim, max_bg_dim)
        else:
            raise FileNotFoundError(f"Could not find background image file or directory at {path}.")

    return bg_image
