import random
from typing import List, NamedTuple, Optional, Sequence, Tuple, Union

import numpy as np

from rx_connect.core.utils.func_utils import to_tuple
from rx_connect.generator.object_overlay import (
    check_overlap,
    is_pill_within_background,
    overlay_image_onto_background,
)
from rx_connect.generator.sampler import sample_pill_location
from rx_connect.generator.transform import rescale_pill_and_mask, transform_pill
from rx_connect.tools.logging import setup_logger

logger = setup_logger()

__all__: Sequence[str] = ("random_partition", "generate_image", "ImageComposition")


class ImageComposition(NamedTuple):
    """The image, mask, and label IDs of the composed image."""

    image: np.ndarray
    """Image containing the composed pills.
    """
    mask: np.ndarray
    """Mask containing the composed pills.
    """
    labels: List[int]
    """List of label IDs of the composed pills.
    """
    gt_bbox: List[Tuple[int, int, int, int]] = []
    """Ground Truth Bounding Boxes. (xmin, xmax, ymin, ymax)"""
    pills_per_type: List[int] = []
    """Ground Truth pills per type."""


def random_partition(number: int, num_parts: int) -> List[int]:
    """Generates a list of random integers that add up to a specified number,
    with at least one count for each part.

    Args:
        number (int): The number to be divided into multiple parts.
        num_parts (int): The number of parts to divide the number into.

    Returns:
        A list of num_parts random integers that add up to number.
    """
    assert num_parts <= number, "The number of parts cannot be greater than the number to be divided"

    # subtract num_parts from number to ensure at least 1 for each part
    number -= num_parts

    parts: List[int] = [0] * num_parts
    for i in range(num_parts - 1):
        parts[i] = random.randint(0, number)
        number -= parts[i]
    parts[num_parts - 1] = number

    # Add 1 to each part to ensure at least one count per part
    parts = [part + 1 for part in parts]

    # Sort the parts in descending order.
    parts = sorted(parts, reverse=True)

    return parts


def partition_by_fraction(number: int, fractions: Sequence[float]) -> List[int]:
    """Generates a list of integers that add up to a specified number.

    Args:
        number (int): The number to be divided into multiple parts.
        fractions (Sequence[float]): The fractions of the number to be divided into.

    Returns:
        A list of integers that add up to number.
    """
    if abs(sum(fractions) - 1) > 1e-3:
        raise ValueError("The fractions must sum to 1.")

    parts: List[int] = [int(number * fraction) for fraction in fractions]

    # Sort the parts in descending order.
    parts = sorted(parts, reverse=True)

    # Add the remaining number to the last part.
    parts[-1] += number - sum(parts)

    return parts


def densify_groundtruth(
    multilabel_mask: np.ndarray,
    focus_area: Optional[Tuple[int, int, int, int]] = None,  # xmin, xmax, ymin, ymax (exclusive max)
    target_label: int = 1,
) -> Tuple[int, int, int, int]:
    """
    Make compact representation of the ground truth bounding boxes.

    Args:
        multilabel_mask (np.ndarray): The multilabel mask.
        focus_area (Optional[Tuple[int, int, int, int]], optional):
            The focus area to skip full scan. Defaults to None for a full scan.
            The format is (xmin, xmax, ymin, ymax).
        target_label (int, optional): The target label. Defaults to 1, assuming mask in binary representation.

    Returns:
        Tuple[int, int, int, int]:
            The compact representation of the ground truth bounding boxes.
            The format is (xmin, xmax, ymin, ymax).
    """
    xmin, xmax = 0, multilabel_mask.shape[0]
    ymin, ymax = 0, multilabel_mask.shape[1]
    if focus_area is not None:
        xmin, xmax, ymin, ymax = focus_area
        xmin = max(0, xmin)
        xmax = min(xmax, multilabel_mask.shape[0])
        ymin = max(0, ymin)
        ymax = min(ymax, multilabel_mask.shape[1])

    while xmin < xmax - 1 and np.max(multilabel_mask[xmin, ymin:ymax]) < target_label:
        xmin += 1
    while xmax - 1 > xmin and np.max(multilabel_mask[xmax - 1, ymin:ymax]) < target_label:
        xmax -= 1
    while ymin < ymax - 1 and np.max(multilabel_mask[xmin:xmax, ymin]) < target_label:
        ymin += 1
    while ymax - 1 > ymin and np.max(multilabel_mask[xmin:xmax, ymax - 1]) < target_label:
        ymax -= 1

    return (xmin, xmax, ymin, ymax)


def _compose_pill_on_bg(
    bg_image: np.ndarray,
    comp_mask: np.ndarray,
    pill_image: np.ndarray,
    pill_mask: np.ndarray,
    n_pills: int,
    scale: float,
    max_overlap: float,
    max_attempts: int,
    start_index: int = 0,
    enable_defective_pills: bool = False,
    enable_edge_pills: bool = False,
) -> ImageComposition:
    """Compose n_pills pills on a background image.

    Args:
        bg_image: The background image.
        comp_mask: The composition mask.
        pill_image: The pill image.
        pill_mask: The pill mask.
        n_pills: The number of pills to compose.
        scale: The scaling factor for rescaling the pill image and mask.
        max_overlap: The maximum allowed overlap between pills.
        max_attempts: The maximum number of attempts to compose a pill.
        enable_defective_pills: Whether to allow defective pills to be placed on the background image.
        enable_edge_pills: Whether to allow pills to be placed on the border of the background image.
        start_index: The starting index for the pill labels.

    Returns:
        The composed image, mask, and label IDs.
    """
    h_bg, w_bg = bg_image.shape[:2]
    count: int = 1
    label_ids: List[int] = []
    gt_bbox: List[Tuple[int, int, int, int]] = []

    # Rescale the pill image and mask to a certain size.
    pill_image, pill_mask = rescale_pill_and_mask(pill_image, pill_mask, scale=scale)
    h_pill, w_pill = pill_mask.shape

    for _ in range(n_pills):
        # Transform the pill image and mask.
        pill_img_t, pill_mask_t = transform_pill(pill_image, pill_mask, allow_defects=enable_defective_pills)

        # Attempt to compose the pill on the background image.
        for _ in range(max_attempts):
            top_left = sample_pill_location(pill_size=(h_pill, w_pill), bg_size=(h_bg, w_bg))

            # Check if the pill can fit inside the background image.
            if not is_pill_within_background(bg_image, pill_mask_t, top_left, enable_edge_pills):
                continue

            # Verify that the new pill does not overlap with the existing pills.
            if not check_overlap(pill_mask_t, comp_mask, top_left, max_overlap):
                continue

            # Add the pill to the background image.
            bg_image, comp_mask = overlay_image_onto_background(
                bg_image, comp_mask, pill_img_t, pill_mask_t, top_left, start_index + count
            )
            label_ids.append(start_index + count)
            gt_bbox.append(
                densify_groundtruth(
                    multilabel_mask=comp_mask,
                    focus_area=(
                        top_left[1],
                        top_left[1] + w_pill,
                        top_left[0],
                        top_left[0] + h_pill,
                    ),
                    target_label=start_index + count,
                )
            )
            count += 1
            break

    return ImageComposition(bg_image, comp_mask, label_ids, gt_bbox)


def generate_image(
    bg_image: np.ndarray,
    pill_images: List[np.ndarray],
    pill_masks: List[np.ndarray],
    min_pills: int = 5,
    max_pills: int = 15,
    fraction_pills_type: Optional[Sequence[float]] = None,
    scale: Union[float, Tuple[float, float]] = 1.0,
    max_overlap: float = 0.2,
    max_attempts: int = 10,
    enable_defective_pills: bool = False,
    enable_edge_pills: bool = False,
) -> ImageComposition:
    """Create a composition of pills on a background image.

    Args:
        bg_image: The background image.
        pill_images: A list of pill images.
        pill_masks: A list of pill masks.
        min_pills: The minimum number of pills to compose.
        max_pills: The maximum number of pills to compose.
        fraction_pills_type: The fraction of pills per type.
        scale: The scaling factor to use for rescaling the pill image and mask. If a tuple is provided,
            then the scaling factor is randomly sampled from the range (min, max). If a float is
            provided, then the scaling factor is fixed.
        max_overlap: The maximum allowed overlap between pills.
        max_attempts: The maximum number of attempts to compose a pill.
        enable_defective_pills: Whether to allow defective pills to be placed on the background image.
        enable_edge_pills: whether to allow the pill object to be on the border of
            the background image.

    Returns:
        bg_image: The background image with pills.
        composition_mask: The mask of the composition.
            - If it is detection mode, all pills will be labeled as 1;
            - It it is segmentation mode, pills will be labeled as it's index, from 1, 2, 3..., n_pills.
        pill_labels: List of labels of the pills.
    """

    bg_image = bg_image.copy()
    comp_mask = np.zeros(bg_image.shape[:2], dtype=np.uint8)

    # Randomly sample the number of pills to compose.
    num_pills = np.random.randint(min_pills, max_pills + 1)

    # Sample the number of pills per type.
    pills_per_type = (
        random_partition(num_pills, len(pill_images))
        if fraction_pills_type is None
        else partition_by_fraction(num_pills, fraction_pills_type)
    )

    # Randomly sample the scaling factor from the given range
    scale_factor = random.uniform(*to_tuple(scale))

    label_ids: List[int] = []
    gt_bbox: List[Tuple[int, int, int, int]] = []

    for idx, n_pills in enumerate(pills_per_type):
        # Compose the pill on the background image.
        bg_image, comp_mask, labels, bboxes, _ = _compose_pill_on_bg(
            bg_image,
            comp_mask,
            pill_image=pill_images[idx],
            pill_mask=pill_masks[idx],
            n_pills=n_pills,
            scale=scale_factor,
            max_overlap=max_overlap,
            max_attempts=max_attempts,
            enable_defective_pills=enable_defective_pills,
            enable_edge_pills=enable_edge_pills,
            start_index=len(label_ids),
        )
        label_ids += labels
        gt_bbox += bboxes

    return ImageComposition(bg_image, comp_mask, label_ids, gt_bbox, pills_per_type)
