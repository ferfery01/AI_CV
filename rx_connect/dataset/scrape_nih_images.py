import concurrent.futures
from functools import partial
from pathlib import Path
from typing import List, Tuple, Union
from urllib.parse import urlparse

import click
import requests
from bs4 import BeautifulSoup
from tqdm import tqdm, trange

from rx_connect.core.images.io import IMAGE_EXTENSIONS, download_image
from rx_connect.dataset.utils import Layouts, load_consumer_image_df_by_layout
from rx_connect.tools.logging import setup_logger

logger = setup_logger()

CHECK_EXTENSIONS = IMAGE_EXTENSIONS + [".wmv"]
"""Some of the image links are to videos, so we need to check for these extensions as well.
"""

"""This script downloads all the consumer images from the NIH Pill Image Recognition Challenge
aviailable at https://data.lhncbc.nlm.nih.gov/public/Pills/index.html.

The downloaded images
are filtered based on the RxImage Layout and saved to the specified directory. The folder structure
of the downloaded images is as follows:
    {data_dir}
        ├── AllXML
        │   ├── PillProjectDisc1.xml
        │   ├── PillProjectDisc2.xml
        │   ├── ...
        │   └── PillProjectDisc110.xml
        └── images
            ├── C3PI_Reference
            │   ├── 00000001.jpg
            │   ├── 00000002.jpg
            │   ├── ...
            ├── C3PI_Test
            │   ├── 00000001.jpg
            │   ├── 00000002.jpg
            │   ├── ...
            ├── ...
            └── SPL_SPLIMAGE_V3.0
                ├── 00000001.jpg
                ├── 00000002.jpg
                ├── ...
"""


def download_xml(url: str, download_dir: Union[str, Path]) -> None:
    """Download all the XML file from the provided URL."""
    file_name = Path(urlparse(url).path).name
    try:
        response = requests.get(url)
        with (Path(download_dir) / file_name).open("wb") as f:
            f.write(response.content)
    except requests.RequestException as e:
        logger.exception("Error downloading XML file:", e)


def extract_image_urls(url: str) -> List[str]:
    """Extracts all image URLs from the provided URL."""
    image_urls: List[str] = []
    try:
        response = requests.get(url)
        soup = BeautifulSoup(response.text, "html.parser")
    except requests.RequestException as e:
        logger.exception("Error accessing the website:", e)
    else:
        for link in soup.find_all("a"):
            img_url = link.get("href")

            # We need to make sure that the links are to images (ends with .jpg, .png etc)
            if any(extension in img_url.lower() for extension in CHECK_EXTENSIONS):
                if not img_url.startswith("http"):
                    image_urls.append(f"{url.rsplit('/', 1)[0]}/{img_url}")

    return image_urls


@click.command()
@click.option("-d", "--download-dir", default="./data/Pill_Images", help="Directory to download images to.")
@click.option(
    "-i",
    "--indices",
    nargs=2,
    type=int,
    default=[1, 110],
    help="Start and end indices of the projects to download.",
)
@click.option(
    "-l",
    "--layout",
    required=True,
    type=click.Choice(Layouts.members()),
    help="""Select the Image layout to download. More information about the layout can be
    obtained at https://data.lhncbc.nlm.nih.gov/public/Pills/RxImageImageLayouts.docx""",
)
@click.option(
    "-x",
    "--xml-download",
    is_flag=True,
    help="Download the XML files as well.",
)
def main(download_dir: str, indices: Tuple[int, int], layout: str, xml_download: bool) -> None:
    # Set the start and end indices of the projects to download
    start, end = indices
    assert end >= start, "End index must be greater than or equal to start index."
    # The projects are named as PillProjectDisc1, PillProjectDisc2, ..., PillProjectDisc110.
    # Hence, the start and end indices must be between 1 and 110.
    start, end = max(1, min(110, start)), max(1, min(110, end))
    logger.info(f"Downloading projects {start} to {end} for layout {layout} to {download_dir}")

    # Load the consumer grade images csv file and filter it based on the layout
    df = load_consumer_image_df_by_layout(download_dir, layout=Layouts[layout])
    filenames = set(df.FileName.unique())

    # ===== Download all the XML files =====
    if xml_download:
        # Get all the XML URLs from the project page to download
        xml_urls = [
            f"https://data.lhncbc.nlm.nih.gov/public/Pills/ALLXML/PillProjectDisc{idx}.xml"
            for idx in range(start, end + 1)
        ]

        # Use a ThreadPoolExecutor to download all the XML files concurrently
        with concurrent.futures.ThreadPoolExecutor() as executor:
            list(
                tqdm(
                    executor.map(partial(download_xml, download_dir=Path(download_dir) / "AllXML"), xml_urls),
                    desc="Downloading XML files",
                    total=len(xml_urls),
                )
            )

    # ===== Download all the pill images =====

    # Loop through all the image pages and download the images
    for idx in trange(start, end + 1, desc="Project Index"):
        project_url = f"https://data.lhncbc.nlm.nih.gov/public/Pills/PillProjectDisc{idx}/images/index.html"

        # Extract all the image URLs from the project page
        image_urls = extract_image_urls(project_url)

        # Create a dictionary of filenames and URLs
        filenames_url = {Path(urlparse(url).path).name: url for url in image_urls}

        # Get the filenames that belong to the RxImage Layout
        filenames_layout = set(filenames_url.keys()) & filenames

        # Filter the image URLs based on the filenames
        urls_to_download = [filenames_url[filename] for filename in filenames_layout]

        # Parse project name from one of the image URLs
        parsed_url = urlparse(urls_to_download[0])
        folder_name = parsed_url.path.split("/")[-3]

        # Create project folder if it doesn't exist
        image_dir = Path(download_dir) / "images" / layout
        image_dir.mkdir(parents=True, exist_ok=True)

        # Create a list of tuples containing the image URL and the path to save the image to
        # Ignore images that have already been downloaded
        img_info = []
        for url in urls_to_download:
            image_name = url.rsplit("/", 1)[-1]
            if not (image_dir / image_name).exists():
                img_info.append((url, image_dir / image_name))

        # Use a ThreadPoolExecutor to download the images concurrently
        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = [executor.submit(lambda p: download_image(*p), info) for info in img_info]

            # Display the progress bar while the images are being downloaded
            for _ in tqdm(
                concurrent.futures.as_completed(futures),
                desc=f"Downloading {folder_name} images",
                total=len(futures),
            ):
                # results from future are ignored in this case
                pass


if __name__ == "__main__":
    main()
