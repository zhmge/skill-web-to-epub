#!/usr/bin/env python3
"""Package a normalized source directory as a reflowable EPUB 3 book.

Expected layout::

    SOURCE_DIR/
      metadata.json
      toc.json                 # optional
      text/*.xhtml
      styles/*                 # optional
      images/*                 # optional
      fonts/*                  # optional

Run ``python epub_package.py --help`` for the command-line interface.
"""

from __future__ import annotations

import argparse
import html
import json
import mimetypes
import posixpath
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from shutil import copy2
from typing import NoReturn
from urllib.parse import quote, unquote, urlsplit
from xml.etree import ElementTree as ET


CONTENT_DIRS = ("text", "styles", "images", "fonts")
XHTML_SUFFIXES = {".xhtml", ".html"}


def fail(message: str) -> NoReturn:
    raise SystemExit(message)


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"Cannot read {path.name}: {exc}")


def xml_text(value: object) -> str:
    return html.escape(str(value), quote=False)


def xml_attr(value: object) -> str:
    return html.escape(str(value), quote=True)


def encoded_href(value: str) -> str:
    return quote(value, safe="/:#?&=;,+@-._~%")


def media_type(path: Path) -> str:
    known = {
        ".xhtml": "application/xhtml+xml",
        ".html": "application/xhtml+xml",
        ".css": "text/css",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".svg": "image/svg+xml",
        ".webp": "image/webp",
        ".avif": "image/avif",
        ".ttf": "font/ttf",
        ".otf": "font/otf",
        ".woff": "font/woff",
        ".woff2": "font/woff2",
        ".mp3": "audio/mpeg",
        ".mp4": "video/mp4",
    }
    return known.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def normalize_metadata(source_dir: Path) -> dict[str, str]:
    raw = read_json(source_dir / "metadata.json", {})
    if not isinstance(raw, dict):
        fail("metadata.json must contain a JSON object")
    modified = raw.get("modified") or datetime.now(timezone.utc).replace(
        microsecond=0
    ).isoformat().replace("+00:00", "Z")
    return {
        "title": str(raw.get("title") or "Untitled"),
        "identifier": str(raw.get("identifier") or f"urn:uuid:{uuid.uuid4()}"),
        "language": str(raw.get("language") or "und"),
        "creator": str(raw.get("creator") or ""),
        "publisher": str(raw.get("publisher") or ""),
        "description": str(raw.get("description") or ""),
        "rights": str(raw.get("rights") or ""),
        "modified": str(modified),
        "cover": str(raw.get("cover") or ""),
    }


def discover_files(source_dir: Path, folder: str) -> list[Path]:
    base = source_dir / folder
    if not base.exists():
        return []
    if not base.is_dir():
        fail(f"Expected a directory: {base}")
    return sorted(
        (path for path in base.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(source_dir).as_posix(),
    )


def package_href(source_dir: Path, path: Path) -> str:
    return path.relative_to(source_dir).as_posix()


def discover_documents(source_dir: Path) -> list[Path]:
    documents = [path for path in discover_files(source_dir, "text") if path.suffix.lower() in XHTML_SUFFIXES]
    if not documents:
        fail(f"No XHTML documents found below {source_dir / 'text'}")
    return documents


def validate_local_href(href: str, source_dir: Path, context: str) -> None:
    parsed = urlsplit(href)
    if parsed.scheme or parsed.netloc:
        fail(f"{context} must use a local EPUB href: {href}")
    decoded = unquote(parsed.path)
    normalized = posixpath.normpath(decoded)
    if decoded.startswith("/") or normalized == ".." or normalized.startswith("../"):
        fail(f"{context} escapes the EPUB content root: {href}")
    if not decoded:
        fail(f"{context} has an empty href")
    target = source_dir.joinpath(*PurePosixPath(normalized).parts)
    if not target.is_file():
        fail(f"{context} target does not exist: {href}")


def load_toc(source_dir: Path, documents: list[Path]) -> list[dict]:
    toc_path = source_dir / "toc.json"
    if not toc_path.exists():
        return [{"title": path.stem, "href": package_href(source_dir, path)} for path in documents]
    toc = read_json(toc_path, [])
    if not isinstance(toc, list) or not toc:
        fail("toc.json must contain a non-empty JSON array")

    def validate(entries: list, trail: str = "toc") -> None:
        for index, item in enumerate(entries, 1):
            where = f"{trail}[{index}]"
            if not isinstance(item, dict):
                fail(f"{where} must be an object")
            if not str(item.get("title") or "").strip():
                fail(f"{where} needs a non-empty title")
            href = str(item.get("href") or "")
            children = item.get("children", [])
            if href:
                validate_local_href(href, source_dir, where)
                if Path(unquote(urlsplit(href).path)).suffix.lower() not in XHTML_SUFFIXES:
                    fail(f"{where} must target an XHTML document: {href}")
            if not href and not children:
                fail(f"{where} needs an href or children")
            if not isinstance(children, list):
                fail(f"{where}.children must be an array")
            validate(children, f"{where}.children")

    validate(toc)
    return toc


def order_documents(source_dir: Path, documents: list[Path], toc: list[dict]) -> list[Path]:
    """Use each TOC target's first appearance as spine order, then retain unlisted files."""
    by_href = {package_href(source_dir, path): path for path in documents}
    ordered: list[Path] = []
    seen: set[Path] = set()
    for item in flatten_toc(toc):
        href = posixpath.normpath(unquote(urlsplit(str(item["href"])).path))
        document = by_href.get(href)
        if document is not None and document not in seen:
            ordered.append(document)
            seen.add(document)
    ordered.extend(path for path in documents if path not in seen)
    return ordered


def render_toc(entries: list[dict]) -> str:
    parts: list[str] = []
    for item in entries:
        title = xml_text(item["title"])
        href = str(item.get("href") or "")
        children = item.get("children") or []
        label = f'<a href="{xml_attr(encoded_href(href))}">{title}</a>' if href else f"<span>{title}</span>"
        nested = f"<ol>{render_toc(children)}</ol>" if children else ""
        parts.append(f"<li>{label}{nested}</li>")
    return "".join(parts)


def flatten_toc(entries: list[dict]) -> list[dict]:
    result: list[dict] = []
    for item in entries:
        if item.get("href"):
            result.append(item)
        result.extend(flatten_toc(item.get("children") or []))
    return result


def build_nav(toc: list[dict], language: str) -> str:
    return f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{xml_attr(language)}" lang="{xml_attr(language)}">
<head><meta charset="utf-8"/><title>Table of Contents</title></head>
<body><nav epub:type="toc" id="toc"><h1>Contents</h1><ol>{render_toc(toc)}</ol></nav></body>
</html>
'''


def build_ncx(toc: list[dict], metadata: dict[str, str]) -> str:
    points = []
    for index, item in enumerate(flatten_toc(toc), 1):
        points.append(
            f'<navPoint id="navPoint-{index}" playOrder="{index}">'
            f'<navLabel><text>{xml_text(item["title"])}</text></navLabel>'
            f'<content src="{xml_attr(encoded_href(str(item["href"])))}"/></navPoint>'
        )
    return f'''<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
<head><meta name="dtb:uid" content="{xml_attr(metadata['identifier'])}"/></head>
<docTitle><text>{xml_text(metadata['title'])}</text></docTitle>
<navMap>{''.join(points)}</navMap>
</ncx>
'''


def xhtml_properties(path: Path) -> list[str]:
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        fail(f"Invalid XHTML XML in {path}: {exc}")
    properties: list[str] = []
    tags = {element.tag.rsplit("}", 1)[-1].lower() for element in root.iter()}
    if "svg" in tags:
        properties.append("svg")
    if "math" in tags:
        properties.append("mathml")
    if "script" in tags:
        properties.append("scripted")
    return properties


def metadata_element(name: str, value: str) -> str:
    return f"<dc:{name}>{xml_text(value)}</dc:{name}>" if value else ""


def build_opf(source_dir: Path, metadata: dict[str, str], documents: list[Path], assets: list[Path], include_ncx: bool) -> str:
    manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>']
    if include_ncx:
        manifest.append('<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>')
    spine: list[str] = []
    cover = posixpath.normpath(unquote(metadata["cover"])) if metadata["cover"] else ""
    for index, path in enumerate(documents, 1):
        href = package_href(source_dir, path)
        item_id = f"doc-{index}"
        props = xhtml_properties(path)
        prop_attr = f' properties="{xml_attr(" ".join(props))}"' if props else ""
        manifest.append(f'<item id="{item_id}" href="{xml_attr(encoded_href(href))}" media-type="application/xhtml+xml"{prop_attr}/>')
        spine.append(f'<itemref idref="{item_id}"/>')
    for index, path in enumerate(assets, 1):
        href = package_href(source_dir, path)
        props = "cover-image" if cover and href == cover else ""
        prop_attr = f' properties="{props}"' if props else ""
        manifest.append(f'<item id="asset-{index}" href="{xml_attr(encoded_href(href))}" media-type="{xml_attr(media_type(path))}"{prop_attr}/>')
    optional = "".join(metadata_element(name, metadata[name]) for name in ("creator", "publisher", "description", "rights"))
    toc_attr = ' toc="ncx"' if include_ncx else ""
    return f'''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="book-id">{xml_text(metadata['identifier'])}</dc:identifier><dc:title>{xml_text(metadata['title'])}</dc:title><dc:language>{xml_text(metadata['language'])}</dc:language>
{optional}<meta property="dcterms:modified">{xml_text(metadata['modified'])}</meta>
</metadata><manifest>{''.join(manifest)}</manifest><spine{toc_attr}>{''.join(spine)}</spine></package>
'''


def copy_content(source_dir: Path, oebps: Path, files: list[Path]) -> None:
    for source in files:
        destination = oebps / source.relative_to(source_dir)
        destination.parent.mkdir(parents=True, exist_ok=True)
        copy2(source, destination)


def write_package(source_dir: Path, output: Path, include_ncx: bool = True) -> None:
    if not source_dir.is_dir():
        fail(f"Source directory not found: {source_dir}")
    metadata = normalize_metadata(source_dir)
    documents = discover_documents(source_dir)
    all_files = [path for folder in CONTENT_DIRS for path in discover_files(source_dir, folder)]
    assets = [path for path in all_files if path not in documents]
    toc = load_toc(source_dir, documents)
    documents = order_documents(source_dir, documents, toc)
    if metadata["cover"]:
        validate_local_href(metadata["cover"], source_dir, "metadata.cover")
        if posixpath.normpath(unquote(metadata["cover"])) not in {package_href(source_dir, path) for path in assets}:
            fail("metadata.cover must point to a non-document asset")

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="web-to-epub-") as temporary:
        build_dir = Path(temporary)
        oebps = build_dir / "OEBPS"
        (build_dir / "META-INF").mkdir(parents=True)
        oebps.mkdir()
        copy_content(source_dir, oebps, all_files)
        write_text(build_dir / "mimetype", "application/epub+zip")
        write_text(build_dir / "META-INF" / "container.xml", '''<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>
''')
        write_text(oebps / "nav.xhtml", build_nav(toc, metadata["language"]))
        if include_ncx:
            write_text(oebps / "toc.ncx", build_ncx(toc, metadata))
        write_text(oebps / "content.opf", build_opf(source_dir, metadata, documents, assets, include_ncx))

        temporary_output = output.with_name(output.name + ".tmp")
        try:
            with zipfile.ZipFile(temporary_output, "w") as archive:
                archive.write(build_dir / "mimetype", "mimetype", compress_type=zipfile.ZIP_STORED)
                for path in sorted(build_dir.rglob("*"), key=lambda item: item.relative_to(build_dir).as_posix()):
                    if path.is_file() and path.name != "mimetype":
                        archive.write(path, path.relative_to(build_dir).as_posix(), compress_type=zipfile.ZIP_DEFLATED)
            temporary_output.replace(output)
        finally:
            if temporary_output.exists():
                temporary_output.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_dir", type=Path, help="Normalized EPUB source directory")
    parser.add_argument("output_epub", type=Path, help="Destination .epub file")
    parser.add_argument("--no-ncx", action="store_true", help="Omit the optional EPUB 2 NCX compatibility file")
    args = parser.parse_args()
    output = args.output_epub.expanduser().resolve()
    if output.suffix.lower() != ".epub":
        fail("Output file must use the .epub extension")
    write_package(args.source_dir.expanduser().resolve(), output, not args.no_ncx)
    print(f"Created {output}")


if __name__ == "__main__":
    main()
