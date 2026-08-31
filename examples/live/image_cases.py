"""Reproducible photo inputs for live and offline HTTP tests; no credentials or network.

Local variants use the checked-in food.png. Encoded variants are generated in a
test-owned temporary directory, never over the original fixture. The URL case
uses an existing public image; no local fixture is uploaded to external storage.
"""

import base64
import io
from contextlib import ExitStack, contextmanager
from pathlib import Path

from PIL import Image

# Fixed photo ID and output format; no random-image endpoint or redirect.
PUBLIC_IMAGE_URL = (
    "https://images.unsplash.com/photo-1546069901-ba9599a7e63c?w=1024&q=85&fit=crop&fm=jpg"
)
VALID_CASES = (
    "url",
    "data_uri_png",
    "data_uri_jpeg",
    "path",
    "path_string",
    "bytes",
    "bytearray",
    "memoryview",
    "binary_file",
    "bytes_io",
    "pillow",
    "oversized_jpeg",
    "exif_rotated_jpeg",
    "transparent_png",
    "cmyk_jpeg",
    "webp",
    "still_gif",
    "preprocess_false",
)
INVALID_CASES = (
    "empty_bytes",
    "non_image",
    "truncated_jpeg",
    "missing_path",
    "directory",
    "text_file",
    "closed_file",
    "file_at_eof",
    "unsupported_type",
    "file_uri",
    "unsupported_scheme",
    "raw_base64",
    "animated_gif",
    "animated_webp",
    "animated_without_preprocessing",
    "oversize_without_preprocessing",
    "pillow_without_preprocessing",
    "unsupported_heic",
)


def encoded(image, format, **kwargs):
    buffer = io.BytesIO()
    image.save(buffer, format=format, **kwargs)
    return buffer.getvalue()


@contextmanager
def photo_case(name, fixture: Path, directory: Path, *, url=PUBLIC_IMAGE_URL):
    """Yield analyze_photo kwargs with file/image lifetimes spanning the request."""
    data = fixture.read_bytes()
    with ExitStack() as stack:
        source = stack.enter_context(Image.open(io.BytesIO(data))).convert("RGB")
        image = data
        preprocess = True
        if name == "url":
            image = url
        elif name == "data_uri_png":
            image = "data:image/png;base64," + base64.b64encode(data).decode("ascii")
        elif name == "data_uri_jpeg":
            image = "data:image/jpeg;base64," + base64.b64encode(encoded(source, "JPEG")).decode(
                "ascii"
            )
        elif name in {"path", "path_string", "binary_file", "text_file", "closed_file"}:
            path = directory / "food photo ü.png"
            path.write_bytes(data)
            image = path if name == "path" else str(path)
            if name in {"binary_file", "text_file", "closed_file"}:
                image = stack.enter_context(path.open("r" if name == "text_file" else "rb"))
                if name == "closed_file":
                    image.close()
        elif name == "bytes":
            image = data
        elif name == "bytearray":
            image = bytearray(data)
        elif name == "memoryview":
            image = memoryview(data)
        elif name in {"bytes_io", "file_at_eof"}:
            image = stack.enter_context(io.BytesIO(data))
            if name == "file_at_eof":
                image.seek(0, 2)
        elif name in {"pillow", "pillow_without_preprocessing"}:
            image = source
            preprocess = name == "pillow"
        elif name == "oversized_jpeg":
            image = encoded(source.resize((2048, 1152)), "JPEG", quality=95)
        elif name == "exif_rotated_jpeg":
            rotated = source.transpose(Image.Transpose.ROTATE_90)
            exif = Image.Exif()
            exif[274] = 6
            image = encoded(rotated, "JPEG", exif=exif)
        elif name == "transparent_png":
            transparent = source.convert("RGBA")
            transparent.paste((255, 255, 255, 0), (0, 0, 32, 32))
            image = encoded(transparent, "PNG")
        elif name == "cmyk_jpeg":
            image = encoded(source.convert("CMYK"), "JPEG")
        elif name in {"webp", "still_gif", "preprocess_false"}:
            source.thumbnail((512, 512))
            image = encoded(
                source, {"webp": "WEBP", "still_gif": "GIF", "preprocess_false": "JPEG"}[name]
            )
            preprocess = name != "preprocess_false"
        elif name in {"animated_gif", "animated_webp", "animated_without_preprocessing"}:
            source.thumbnail((100, 100))
            frame = Image.new("RGB", source.size, "red")
            image = encoded(
                source,
                "WEBP" if name == "animated_webp" else "GIF",
                save_all=True,
                append_images=[frame],
                duration=100,
                loop=0,
            )
            preprocess = name != "animated_without_preprocessing"
        elif name == "oversize_without_preprocessing":
            image = encoded(source, "JPEG") + b"\0" * 3_500_001
            preprocess = False
        elif name == "empty_bytes":
            image = b""
        elif name == "non_image":
            image = b"This is not an image."
        elif name == "truncated_jpeg":
            image = encoded(source, "JPEG")[:40]
        elif name == "missing_path":
            image = directory / "missing.jpg"
        elif name == "directory":
            image = directory
        elif name == "unsupported_type":
            image = 123
        elif name == "file_uri":
            image = fixture.as_uri()
        elif name == "unsupported_scheme":
            image = "ftp://example.invalid/photo.jpg"
        elif name == "raw_base64":
            image = base64.b64encode(b"not-a-data-uri").decode("ascii")
        elif name == "unsupported_heic":
            image = b"\x00\x00\x00\x18ftypheic" + b"\0" * 32
        else:
            raise ValueError("Unknown photo test case")
        yield {"image": image, "preprocess": preprocess}
