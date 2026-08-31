"""Accepted photo inputs; Pillow is loaded only when preparing local images."""

from __future__ import annotations

import os
from typing import IO, TYPE_CHECKING, TypeAlias, Union

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage

ImageInput: TypeAlias = Union[
    str, os.PathLike[str], bytes, bytearray, memoryview, IO[bytes], "PILImage"
]
