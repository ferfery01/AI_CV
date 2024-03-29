from pathlib import Path
from typing import Any, Optional, Union

import lightning as L
import numpy as np
import pandas as pd
import torch
from albumentations.pytorch.transforms import ToTensorV2
from lightning.pytorch.utilities.types import EVAL_DATALOADERS, TRAIN_DATALOADERS
from skimage import io
from sklearn.preprocessing import LabelEncoder
from torch.utils.data import DataLoader, Dataset

from rx_connect.core.types.verification.dataset import ePillIDDataset
from rx_connect.tools.logging import setup_logger
from rx_connect.tools.serialization import read_pickle, write_pickle
from rx_connect.verification.augments import RefConsTransform

logger = setup_logger()


class SingleImagePillID(Dataset):
    """A PyTorch Dataset subclass, SingleImagePillID, dedicated to efficiently loading
    and preprocessing data for machine learning models, specifically designed for pill
    verification tasks.

    This class manages image loading, optional image rotation, data augmentation, and
    label encoding using the provided LabelEncoder. The image transformations are performed
    using the Albumentations library.

    Attributes:
        root (Union[str, Path]): The root directory where the ePillID dataset resides.
        df (pd.DataFrame): A DataFrame that encapsulates details about the pill images,
            such as 'image_path', 'is_ref', 'is_front', and 'pilltype_id'.
        label_encoder (LabelEncoder): An instance of LabelEncoder that converts pill type
            IDs into a format suitable for machine learning models.
        train (bool): A flag indicating if the dataset is used for model training or evaluation.
        transforms (A.Compose): An instance of Albumentations Compose that holds the
            pipeline of image transformations to be applied to the loaded images.
        rotate_aug (Optional[int]): The degree of rotation applied to images for augmentation
            purposes. This is utilized only during testing or evaluation.
        return_ref (bool) :  A flag indicating if reference images are loaded.

    Methods:
        rotate_df(df: pd.DataFrame, n_rotations: int = 24) -> pd.DataFrame:
            Enhances the original DataFrame by creating various rotation states of each image.
        load_img(df_row: pd.Series) -> torch.Tensor:
            Loads an image given a DataFrame row, applying the prescribed transformations.
        __len__() -> int:
            Provides the total count of images within the dataset.
        __getitem__(idx: int) -> ePillIDDataset:
            Facilitates the loading and returning of an image, its corresponding label, and
            associated metadata from the dataset given an index.
    """

    def __init__(
        self,
        root: Union[str, Path],
        df: pd.DataFrame,
        label_encoder: LabelEncoder,
        train: bool,
        transforms: RefConsTransform,
        rotate_aug: Optional[int] = None,
        return_ref: bool = False,
    ) -> None:
        self.root = Path(root)
        self.label_encoder = label_encoder
        self.train = train
        self.rotate_aug = rotate_aug
        self.transforms = transforms
        self.df = self.rotate_df(df, 360 // self.rotate_aug) if self.rotate_aug is not None else df
        self.return_ref = return_ref

    def rotate_df(self, df: pd.DataFrame, n_rotations: int = 24) -> pd.DataFrame:
        """Generate a new DataFrame that represents various rotation states of the original data.

        This method should be used only for evaluation, not during training.
        The method adds a new column 'rot_degree' to the DataFrame which represents the rotation angle.

        Args:
            df: DataFrame with columns ['image_path', 'is_ref', 'is_front', 'pilltype_id']
            n_rotations: Number of rotations to apply to each image.

        Returns:
            A new DataFrame with additional column representing the rotation angle.

        Raises:
            AssertionError: if the method is called during training, or if `rotate_aug` is None.
        """
        assert not self.train, "`rotate_aug` should only be used for eval"
        assert self.rotate_aug is not None, "`rotate_aug` should be an integer"

        new_df = df.loc[df.index.repeat(n_rotations)].reset_index(drop=True)
        new_df["rot_degree"] = np.tile(np.arange(n_rotations) * self.rotate_aug, len(df))

        return new_df

    def load_ref_image(self, df_row: pd.Series) -> torch.Tensor:
        """Returns a reference image of the given image matching with front/back side
        of the image; filtered out by Pill Type"""
        new_row = self.df[
            (self.df.pilltype_id == df_row.pilltype_id)
            & (self.df.is_ref)
            & (self.df.is_front == df_row.is_front)
        ].iloc[0]
        ref_image: np.ndarray = io.imread(self.root / new_row.image_path)

        return ToTensorV2()(image=ref_image)["image"]

    def load_img(self, df_row: pd.Series) -> torch.Tensor:
        """Load image and apply transforms"""
        img_path, is_ref = df_row.image_path, df_row.is_ref
        image: np.ndarray = io.imread(self.root / img_path)
        rot_degree: int = df_row.rot_degree if self.rotate_aug is not None else 0

        return self.transforms(image, is_ref=is_ref, rot_degree=rot_degree)

    def __len__(self) -> int:
        return len(self.df)

    def __getitem__(self, idx: int) -> ePillIDDataset:
        df_row = self.df.iloc[idx]

        # Load image and apply transforms
        image: torch.Tensor = self.load_img(df_row)
        ref_image: torch.Tensor = self.load_ref_image(df_row)
        ndc_code: str = df_row.pilltype_id

        return {
            "image": image,
            "ref_image": ref_image if self.return_ref else None,
            "label": int(self.label_encoder.transform([ndc_code])[0]),
            "image_name": str(df_row.image_path),
            "is_ref": bool(df_row.is_ref),
            "is_front": bool(df_row.is_front),
        }


class PillIDDataModule(L.LightningDataModule):
    def __init__(
        self,
        root: Path,
        df: pd.DataFrame,
        label_encoder: LabelEncoder,
        batch_size: int,
        num_workers: int = 8,
        pin_memory: bool = True,
        return_ref: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self.root = root
        self.df = df
        self.label_encoder = label_encoder
        self.batch_size = batch_size
        self.num_workers = num_workers
        self.pin_memory = pin_memory
        self.return_ref = return_ref
        self.kwargs = kwargs

        # Initialize transforms
        self.train_transforms = RefConsTransform(train=True, normalize=True, **self.kwargs)
        self.val_transforms = RefConsTransform(train=False, normalize=True, **self.kwargs)

        self.init_dataframes()

    def init_dataframes(self) -> None:
        train_df = self.df[self.df.split == "train"]
        val_df = self.df[self.df.split == "val"]

        ref_only_df, cons_train_df = train_df[train_df.is_ref], train_df[~train_df.is_ref]
        cons_val_df = val_df[~val_df.is_ref]

        self.train_df = pd.concat([ref_only_df, cons_train_df], sort=False)
        self.val_df = pd.concat([ref_only_df, cons_val_df])

        labels_df = pd.DataFrame({"pilltype_id": self.label_encoder.classes_})
        self.eval_df = pd.merge(cons_val_df, labels_df, on=["pilltype_id"], how="inner")
        self.ref_df = pd.merge(ref_only_df, labels_df, on=["pilltype_id"], how="inner")

    def setup(self, stage: Optional[str] = None) -> None:
        if stage == "fit" or stage is None:
            self.train_dataset = SingleImagePillID(
                self.root,
                self.train_df,
                self.label_encoder,
                return_ref=self.return_ref,
                train=True,
                transforms=self.train_transforms,
            )
            self.val_dataset = SingleImagePillID(
                self.root,
                self.val_df,
                self.label_encoder,
                return_ref=self.return_ref,
                train=False,
                transforms=self.val_transforms,
            )
            self.eval_dataset = SingleImagePillID(
                self.root,
                self.eval_df,
                self.label_encoder,
                return_ref=self.return_ref,
                train=False,
                transforms=self.val_transforms,
                rotate_aug=24,
            )
            self.ref_dataset = SingleImagePillID(
                self.root,
                self.ref_df,
                self.label_encoder,
                train=False,
                transforms=self.val_transforms,
                rotate_aug=24,
                return_ref=self.return_ref,
            )

    def train_dataloader(self) -> TRAIN_DATALOADERS:
        return DataLoader(
            self.train_dataset,
            batch_size=self.batch_size,
            shuffle=True,
            num_workers=self.num_workers,
            pin_memory=self.pin_memory,
            drop_last=True,
        )

    def val_dataloader(self) -> EVAL_DATALOADERS:
        return DataLoader(
            self.val_dataset,
            batch_size=self.batch_size,
            shuffle=False,
            num_workers=self.num_workers,
            pin_memory=self.pin_memory,
            drop_last=False,
        )

    def test_dataloader(self) -> EVAL_DATALOADERS:
        """Return a list of test dataloaders, one for reference images and one for
        consumer images."""
        test_dl = DataLoader(
            self.eval_dataset,
            batch_size=self.batch_size,
            shuffle=False,
            num_workers=self.num_workers,
            pin_memory=self.pin_memory,
            drop_last=False,
        )
        ref_dl = DataLoader(
            self.ref_dataset,
            batch_size=self.batch_size,
            shuffle=False,
            num_workers=self.num_workers,
            pin_memory=self.pin_memory,
            drop_last=False,
        )
        return [test_dl, ref_dl]


def load_label_encoder(path: Union[str, Path], encoder_path: Union[str, Path]) -> LabelEncoder:
    """Create a label encoder from a dataframe or load a saved encoder. If the encoder
    is saved, load it. If not, create a new one and save it.
    """
    if Path(encoder_path).exists():
        label_encoder = read_pickle(encoder_path)
    else:
        df = pd.read_csv(path)
        label_encoder = LabelEncoder()
        label_encoder.fit(df.label)
        write_pickle(label_encoder, encoder_path)

    logger.info(f"Number of classes: {len(label_encoder.classes_)}")

    return label_encoder
