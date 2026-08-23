"""Media types for Wikimedia Commons files.

Both serializations describe the same image file: the IIIF manifest body and
the Linked Art `shown_by` DigitalObject. Each has to declare its format, and
both previously asserted `image/jpeg` regardless of the file, so a PNG painting
was published with the wrong media type in two places at once.

This module is deliberately free of imports so `linked_art.py`, which stays
dependency-free in order to validate against the official Linked Art JSON
Schemas, can use it without acquiring a network dependency.
"""

FALLBACK_FORMAT = "image/jpeg"

# Explicit rather than `mimetypes.guess_type`, whose table is platform
# dependent: it does not know `.webp` on Windows, so the same Commons file
# would be described one way in development and another on the deployed host.
# These records are consumed by other people and should not vary by build host.
EXTENSION_FORMATS = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "tif": "image/tiff",
    "tiff": "image/tiff",
    "webp": "image/webp",
    "svg": "image/svg+xml",
}


def image_format(reported_mime: object, url: str) -> str:
    """The declared media type for a Commons image.

    Prefers the API's own `mime`, falls back to the file extension, and only
    then to JPEG. A record that declares the wrong media type is a conformance
    problem a strict consumer is entitled to reject, so guessing is the last
    resort rather than the default.
    """
    if isinstance(reported_mime, str) and reported_mime.startswith("image/"):
        return reported_mime
    parts = (url or "").split("?")[0].rsplit(".", 1)
    if len(parts) == 2:
        return EXTENSION_FORMATS.get(parts[1].lower(), FALLBACK_FORMAT)
    return FALLBACK_FORMAT
