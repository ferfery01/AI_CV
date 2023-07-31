from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional, Union, cast, overload

import albumentations as A
import cv2
import numpy as np
import torch
from albumentations.pytorch.transforms import ToTensorV2
from sklearn.metrics.pairwise import euclidean_distances
from vlad import VLAD

from rx_connect import CACHE_DIR, SHARED_REMOTE_CKPT_DIR
from rx_connect.core.utils.cv_utils import equalize_histogram
from rx_connect.tools.data_tools import fetch_from_remote
from rx_connect.tools.logging import setup_logger
from rx_connect.tools.serialization import read_pickle
from rx_connect.tools.timers import timer
from rx_connect.verification.classification.model import EmbeddingModel

logger = setup_logger()


class RxVectorizer(ABC):
    def __init__(
        self,
        model_path: Optional[Union[str, Path]] = None,
        device: Union[str, torch.device] = torch.device("cpu"),
    ) -> None:
        """Initializes the RxVectorizer object.

        Args:
            model_path (str): Path to the vectorizer model.
            device (str, torch.device): Device to run the model on.

        Raises:
            AssertionError: If the model path does not exist.
        """
        self._device = device
        if model_path is not None:
            # If remote model path is provided, fetch the model from the remote
            self._model_path = fetch_from_remote(model_path, cache_dir=CACHE_DIR / "vectorization")
            logger.assertion(self._model_path.exists(), f"Model path {self._model_path} does not exist.")
            self._load_model()

    @abstractmethod
    def _load_model(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def _preprocess(self, image: np.ndarray) -> Union[np.ndarray, torch.Tensor]:
        raise NotImplementedError

    @abstractmethod
    def _predict(self, image: Any) -> np.ndarray:
        raise NotImplementedError

    def __call__(self, image: np.ndarray) -> np.ndarray:
        """Encodes a given image using the vectorizer model."""
        image_preproc = self._preprocess(image)
        image_predict = self._predict(image_preproc)
        return image_predict / np.linalg.norm(image_predict)

    @overload
    def encode(self, images: List[np.ndarray]) -> List[np.ndarray]:
        ...

    @overload
    def encode(self, images: np.ndarray) -> np.ndarray:
        ...

    @timer()
    def encode(self, images: Union[np.ndarray, List[np.ndarray]]) -> Union[np.ndarray, List[np.ndarray]]:
        """Encodes the given image(s) using the vectorizer model. If the input is a single image,
        the output will be a 1D array. If the input is a list of images, the output will be a list
        of 1D arrays.
        """
        if isinstance(images, np.ndarray):
            if images.ndim == 3:
                return self(images)
            logger.assertion(
                images.ndim == 4, "Images must be 3-dimensional (single) or 4-dimensional (stacked)."
            )
        return [self(image) for image in images]

    def __getstate__(self) -> Dict[str, Any]:
        """Return the state of the object for pickling."""
        return self.__dict__

    def __setstate__(self, state: Dict[str, Any]):
        """Restore the state of the object from pickling."""
        self.__dict__.update(state)


class RxVectorizerSift(RxVectorizer):
    num_features: ClassVar[int] = 1000

    def __init__(
        self,
        model_path: Union[str, Path] = f"{SHARED_REMOTE_CKPT_DIR}/verification/vlad_1000_rn_16_v1.pkl",
    ) -> None:
        """Initializes the RxVectorizerSift object.

        Args:
            model_path (str): Path to the VLAD model.

        Raises:
            AssertionError: If the model path does not exist.
        """
        super().__init__(model_path)

    def _load_model(self) -> None:
        """Loads the VLAD model from the given path."""
        self._model = cast(VLAD, read_pickle(self._model_path))
        self._model.verbose = False
        self._SIFT = cv2.SIFT_create(self.num_features)  # type: ignore

    def _preprocess(self, image: np.ndarray) -> np.ndarray:
        """Extracts the image descriptors using SIFT. The histogram of the colored image is
        equalized before extracting the SIFT descriptors.
        """
        logger.assertion(image.ndim == 3, f"Image should be a 3D array, but got a {image.ndim}D array.")

        # Equalize the histogram of the image
        image = equalize_histogram(image)

        # Extract the SIFT image descriptor
        _, descs = self._SIFT.detectAndCompute(image, None)
        desc = descs[: self.num_features]

        return desc

    def _predict(self, descriptor: np.ndarray) -> np.ndarray:
        """Encodes the image descriptor using the VLAD model.

        The input descriptor should be a 2D array with shape (num_features, 128).
        The output vector will be a 1D array with shape (num_clusters * 128).
        """
        return self._model.transform([descriptor]).flatten()


class RxVectorizerColorhist(RxVectorizer):
    def _load_model(self) -> None:
        pass

    def _preprocess(self, image: np.ndarray) -> np.ndarray:
        return image

    def _predict(self, image: np.ndarray) -> np.ndarray:
        """Encodes the image using the colorhist technique."""

        # Compute image vector
        # image pixel values array
        # which channels do you (all RGB channels here)
        # how many descriptors do you want per channel?
        # what is the range of values per color channel?
        hist = cv2.calcHist(
            [image],
            [0, 1, 2],
            cv2.bitwise_not(cv2.inRange(image, image[0][0], image[0][0])),  # mask
            [32, 32, 32],
            [0, 256, 0, 256, 0, 256],
        ).flatten()
        final_vector = hist / np.linalg.norm(hist)

        return final_vector


class RxVectorizerColorMomentHash(RxVectorizer):
    def _load_model(self) -> None:
        pass

    def _preprocess(self, image: np.ndarray) -> np.ndarray:
        return image

    def _predict(self, image: np.ndarray) -> np.ndarray:
        """Encodes the image using the ColorMomentHash."""

        hash_embedding = cv2.img_hash.ColorMomentHash_create().compute(image).flatten()  # type: ignore
        final_vector = hash_embedding / np.linalg.norm(hash_embedding)

        return final_vector


class RxVectorizerDB(RxVectorizer):
    def _load_model(self) -> None:
        loaded_model = read_pickle(self._model_path)
        self._model = cast(np.ndarray, loaded_model["vectorSpace"])
        self._preprocessor = loaded_model["vectorizer"]()

    def _preprocess(self, image: np.ndarray) -> np.ndarray:
        try:
            preprocessed_image = self._preprocessor.encode(image)
        except AttributeError:
            logger.exception(
                f"Preprocessing vectorizer type {type(self._preprocessor)} has no attribute 'encode()'.",
            )
        return preprocessed_image

    def _predict(self, preproc_vector: np.ndarray) -> np.ndarray:
        logger.assertion(
            preproc_vector.shape[0] == self._model.shape[1],
            f"Shape mismatch - input-vector: {preproc_vector.shape}, VectorDB: {self._model.shape}.",
        )
        return np.dot(preproc_vector, self._model.T)


class RxVectorizerML(RxVectorizer):
    """RxVectorizerML is a wrapper class using the EmbeddingModel for inference. It loads the model from
    the given path and generates the embedding vector for the given image. The embedding vector is
    normalized to unit length.
    """

    def __init__(
        self,
        model_path: Union[str, Path] = f"{SHARED_REMOTE_CKPT_DIR}/verification/resnet_34_GAvP.pth",
        device: Union[str, torch.device] = "cpu",
    ) -> None:
        super().__init__(model_path, device)

        # Define the pre-processing transforms
        self._transforms = A.Compose(
            [
                # Resize the longest side to 224, maintaining the aspect ratio
                A.LongestMaxSize(224, always_apply=True),
                # Pad the image on the sides to make it square
                A.PadIfNeeded(min_height=224, min_width=224, always_apply=True, border_mode=0),
                # Normalize the image
                A.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
                # Convert to PyTorch tensor
                ToTensorV2(),
            ]
        )

    def _load_model(self) -> None:
        """Loads the embedding model on the given device and sets it to eval mode."""
        data = torch.load(self._model_path, map_location=torch.device("cpu"))
        weights, params = data["model"], data["params"]

        # Initialize the EmbeddingModel and set it to eval mode
        self._model = EmbeddingModel(**params).to(self._device)
        self._model.load_state_dict(weights)
        self._model.eval()
        logger.info(f"Loaded Embedding model from {self._model_path} on device {self._device}.")

    def _preprocess(self, image: np.ndarray) -> torch.Tensor:
        """Preprocesses the image for inference. This includes resizing, padding, normalization
        and conversion to PyTorch tensor.
        """
        return self._transforms(image=image)["image"]

    def _predict(self, image_tensor: torch.Tensor) -> np.ndarray:
        """Generate the embedding vector for the given image and return it as a flatten
        numpy array.
        """
        # Add batch dimension and move to device
        image_tensor = image_tensor.unsqueeze(0).to(self._device)
        return self._model(image_tensor).cpu().detach().numpy().flatten()


def custom_similarity_fn_L2(v1: np.ndarray, v2: np.ndarray) -> np.ndarray:
    """Calculates L2 norm in range [0,1]"""

    d = euclidean_distances(v1, v2)
    return 1 - d / 2
