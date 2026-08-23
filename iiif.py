"""IIIF Presentation 3.0 manifests for artworks, backed by Wikimedia Commons.

A manifest exposes the painting as a IIIF canvas so any IIIF viewer (Mirador,
OpenSeadragon, Universal Viewer) — and the in-app deep-zoom lightbox — can pan
and zoom it. The image body is the full-resolution Commons file; its required
pixel dimensions come from one cached call to the Commons `imageinfo` API.

This is the sister standard to Linked Art: the object record's `representation`
points here via `conforms_to` IIIF, and this manifest points back at the image.
"""

import os
import urllib.parse
from collections import OrderedDict

import requests

_COMMONS_API = "https://commons.wikimedia.org/w/api.php"
_CONTACT = os.environ.get("AOTD_CONTACT", "https://metahistorybook.com")
_UA = {"User-Agent": f"ArtworkOfTheDay/1.0 ({_CONTACT})", "Accept": "application/json"}
_IIIF_CONTEXT = "http://iiif.io/api/presentation/3/context.json"

_info_cache: "OrderedDict[str, tuple]" = OrderedDict()
_CACHE_MAX = 256

# Commons serves JPEG, PNG, TIFF, WebP and SVG under the same Special:FilePath
# shape, so the media type has to come from the file rather than be assumed.
# The API reports it directly; the extension is the fallback when it does not.
_FALLBACK_FORMAT = "image/jpeg"

# Deliberately explicit rather than `mimetypes.guess_type`, whose table is
# platform-dependent: on Windows it does not know .webp, so the same file would
# be described differently in development and on the deployed host. A manifest
# is data other people consume, so it should not vary by where it was built.
_EXTENSION_FORMATS = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "tif": "image/tiff",
    "tiff": "image/tiff",
    "webp": "image/webp",
    "svg": "image/svg+xml",
}


def _image_format(reported_mime, url):
    """The manifest body `format` for a Commons file.

    Prefers the API's own `mime`, falls back to the file extension, and only
    then to JPEG. A body that declares the wrong media type is a conformance
    problem a strict viewer is entitled to reject, so guessing is the last
    resort rather than the default.
    """
    if isinstance(reported_mime, str) and reported_mime.startswith("image/"):
        return reported_mime
    ext = (url or "").split("?")[0].rsplit(".", 1)
    if len(ext) == 2:
        return _EXTENSION_FORMATS.get(ext[1].lower(), _FALLBACK_FORMAT)
    return _FALLBACK_FORMAT


def _filename(filepath_url):
    """The Commons File: title from a `Special:FilePath/<name>` URL."""
    if not filepath_url:
        return ""
    path = filepath_url.split("?")[0]
    marker = "Special:FilePath/"
    if marker in path:
        return urllib.parse.unquote(path.split(marker, 1)[1])
    return ""


def image_info(filepath_url):
    """(full_url, width, height, format) for a Commons image.

    Returns ('', 0, 0, '') on failure.
    """
    name = _filename(filepath_url)
    if not name:
        return "", 0, 0, ""
    if name in _info_cache:
        return _info_cache[name]
    params = {
        "action": "query",
        "titles": f"File:{name}",
        "prop": "imageinfo",
        "iiprop": "url|size|mime",
        "format": "json",
    }
    out = ("", 0, 0, "")
    try:
        r = requests.get(_COMMONS_API, headers=_UA, params=params, timeout=15)
        if r.status_code == 200:
            pages = r.json().get("query", {}).get("pages", {})
            info = next(iter(pages.values()), {}).get("imageinfo", [{}])[0]
            url = info.get("url", "")
            out = (
                url,
                int(info.get("width", 0)),
                int(info.get("height", 0)),
                _image_format(info.get("mime"), url),
            )
    except Exception as e:
        print(f"iiif image_info error: {e}")
    if len(_info_cache) >= _CACHE_MAX:
        _info_cache.popitem(last=False)
    _info_cache[name] = out
    return out


def _lang(values):
    return {"en": [v for v in values if v]}


def manifest(*, title, manifest_uri, image_filepath, summary="", metadata=None, attribution=""):
    """A IIIF Presentation 3.0 Manifest, or None if the image has no dimensions.

    `metadata` is an iterable of (label, value) pairs; empty values are dropped.
    """
    full_url, w, h, image_format = image_info(image_filepath)
    if not full_url or not w or not h:
        return None

    canvas = f"{manifest_uri}/canvas/1"
    page = f"{canvas}/page/1"
    anno = f"{page}/anno/1"
    man = {
        "@context": _IIIF_CONTEXT,
        "id": manifest_uri,
        "type": "Manifest",
        "label": _lang([title]),
        "items": [
            {
                "id": canvas,
                "type": "Canvas",
                "label": _lang([title]),
                "height": h,
                "width": w,
                "items": [
                    {
                        "id": page,
                        "type": "AnnotationPage",
                        "items": [
                            {
                                "id": anno,
                                "type": "Annotation",
                                "motivation": "painting",
                                "body": {
                                    "id": full_url,
                                    "type": "Image",
                                    "format": image_format or _FALLBACK_FORMAT,
                                    "height": h,
                                    "width": w,
                                },
                                "target": canvas,
                            }
                        ],
                    }
                ],
            }
        ],
    }
    if summary:
        man["summary"] = _lang([summary])
    md = [{"label": _lang([k]), "value": _lang([v])} for k, v in (metadata or []) if v]
    if md:
        man["metadata"] = md
    if attribution:
        man["requiredStatement"] = {"label": _lang(["Attribution"]), "value": _lang([attribution])}
    return man
