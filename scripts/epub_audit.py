#!/usr/bin/env python3
"""Audit an EPUB for common package, link, anchor, and offline-reading faults."""

from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import re
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET


NS = {
    "container": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
}
EPUB_TYPE = "{http://www.idpf.org/2007/ops}type"
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
REMOTE_SCHEMES = {"http", "https", "ftp", "//"}
RESOURCE_ATTRS = {"src", "poster", "data"}
CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.I)
CSS_IMPORT_RE = re.compile(r"@import\s+(?:url\(\s*)?['\"]([^'\"]+)", re.I)


def add_error(errors: list[str], condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


def parse_xml(data: bytes, label: str, errors: list[str]):
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        errors.append(f"Invalid XML in {label}: {exc}")
        return None


def archive_target(base: str, reference: str) -> tuple[str, str]:
    parsed = urlsplit(reference)
    path = unquote(parsed.path)
    target = posixpath.normpath(posixpath.join(posixpath.dirname(base), path)) if path else base
    return target, unquote(parsed.fragment)


def is_remote(reference: str) -> bool:
    parsed = urlsplit(reference)
    return parsed.scheme.lower() in REMOTE_SCHEMES or bool(parsed.netloc) or reference.startswith("//")


def is_unsafe_archive_path(name: str) -> bool:
    normalized = posixpath.normpath(name.replace("\\", "/"))
    return name.startswith(("/", "\\")) or normalized == ".." or normalized.startswith("../") or ":" in normalized.split("/", 1)[0]


def element_ids(root: ET.Element) -> set[str]:
    return {value for element in root.iter() if (value := element.attrib.get("id"))}


def check_reference(
    reference: str,
    attribute: str,
    document: str,
    names: set[str],
    xml_roots: dict[str, ET.Element],
    errors: list[str],
    warnings: list[str],
) -> None:
    if not reference or reference.startswith("data:"):
        return
    if is_remote(reference):
        message = f"External {attribute} in {document}: {reference}"
        if attribute in RESOURCE_ATTRS or attribute == "css-url":
            errors.append("Offline dependency: " + message)
        else:
            warnings.append(message)
        return
    parsed = urlsplit(reference)
    if parsed.scheme and parsed.scheme not in {""}:
        if parsed.scheme not in {"mailto", "tel"}:
            warnings.append(f"Unverified URI scheme in {document}: {reference}")
        return
    target, fragment = archive_target(document, reference)
    if target == ".." or target.startswith("../"):
        errors.append(f"Reference escapes package root in {document}: {reference}")
        return
    if target not in names:
        errors.append(f"Broken {attribute} in {document}: {reference}")
        return
    if fragment:
        root = xml_roots.get(target)
        if root is None and target.lower().endswith((".xhtml", ".html", ".svg")):
            errors.append(f"Cannot verify fragment target in invalid XML {target}: #{fragment}")
        elif root is not None and fragment not in element_ids(root):
            errors.append(f"Missing fragment in {document}: {reference}")


def source_files(source_dir: Path) -> list[Path]:
    result: list[Path] = []
    for folder in ("text", "styles", "images", "fonts"):
        base = source_dir / folder
        if base.is_dir():
            result.extend(path for path in base.rglob("*") if path.is_file())
    return result


def audit(epub_path: Path, source_dir: Path | None = None) -> tuple[list[str], list[str], dict[str, int]]:
    errors: list[str] = []
    warnings: list[str] = []
    stats: dict[str, int] = {}
    if not epub_path.is_file():
        return [f"File not found: {epub_path}"], warnings, stats

    try:
        archive = zipfile.ZipFile(epub_path)
    except (OSError, zipfile.BadZipFile) as exc:
        return [f"Cannot open EPUB ZIP: {exc}"], warnings, stats

    with archive:
        infos = archive.infolist()
        ordered_names = [info.filename for info in infos]
        names = set(ordered_names)
        stats["archive_entries"] = len(infos)
        add_error(errors, bool(infos), "Archive is empty")
        duplicates = sorted({name for name in ordered_names if ordered_names.count(name) > 1})
        for name in duplicates:
            errors.append(f"Duplicate ZIP entry: {name}")
        for name in ordered_names:
            if is_unsafe_archive_path(name):
                errors.append(f"Unsafe ZIP entry path: {name}")

        if infos:
            add_error(errors, infos[0].filename == "mimetype", "mimetype is not the first ZIP entry")
            if infos[0].filename == "mimetype":
                add_error(errors, infos[0].compress_type == zipfile.ZIP_STORED, "mimetype is compressed")
        if "mimetype" in names:
            add_error(errors, archive.read("mimetype") == b"application/epub+zip", "mimetype has invalid content")
        else:
            errors.append("Missing mimetype")
        if "META-INF/container.xml" not in names:
            errors.append("Missing META-INF/container.xml")
            return errors, warnings, stats

        container = parse_xml(archive.read("META-INF/container.xml"), "META-INF/container.xml", errors)
        if container is None:
            return errors, warnings, stats
        rootfile = container.find("container:rootfiles/container:rootfile", NS)
        if rootfile is None or not rootfile.attrib.get("full-path"):
            errors.append("container.xml has no usable rootfile")
            return errors, warnings, stats
        opf_path = unquote(rootfile.attrib["full-path"])
        if opf_path not in names:
            errors.append(f"Missing OPF: {opf_path}")
            return errors, warnings, stats
        opf = parse_xml(archive.read(opf_path), opf_path, errors)
        if opf is None:
            return errors, warnings, stats

        add_error(errors, opf.attrib.get("version") == "3.0", "OPF package version is not 3.0")
        metadata = opf.find("opf:metadata", NS)
        if metadata is None:
            errors.append("OPF metadata section is missing")
        else:
            for field in ("title", "language", "identifier"):
                node = metadata.find(f"dc:{field}", NS)
                add_error(errors, node is not None and bool((node.text or "").strip()), f"Missing dc:{field}")
            modified = metadata.find("opf:meta[@property='dcterms:modified']", NS)
            add_error(errors, modified is not None and bool((modified.text or "").strip()), "Missing dcterms:modified")

        manifest: dict[str, dict[str, str]] = {}
        hrefs: set[str] = set()
        for item in opf.findall("opf:manifest/opf:item", NS):
            item_id = item.attrib.get("id", "")
            href = item.attrib.get("href", "")
            if not item_id:
                errors.append("Manifest item has no id")
                continue
            if item_id in manifest:
                errors.append(f"Duplicate manifest id: {item_id}")
            target, _ = archive_target(opf_path, href)
            if target in hrefs:
                errors.append(f"Duplicate manifest target: {target}")
            hrefs.add(target)
            manifest[item_id] = {
                "target": target,
                "media_type": item.attrib.get("media-type", ""),
                "properties": item.attrib.get("properties", ""),
            }
            add_error(errors, bool(href), f"Manifest item {item_id} has no href")
            add_error(errors, target in names, f"Manifest target missing: {target}")
            add_error(errors, bool(item.attrib.get("media-type")), f"Manifest item {item_id} has no media-type")

        stats["manifest_items"] = len(manifest)
        nav_items = [item for item in manifest.values() if "nav" in item["properties"].split()]
        add_error(errors, len(nav_items) == 1, f"Expected exactly one EPUB navigation document; found {len(nav_items)}")
        spine = opf.findall("opf:spine/opf:itemref", NS)
        stats["spine_items"] = len(spine)
        add_error(errors, bool(spine), "EPUB spine is empty")
        for itemref in spine:
            item_id = itemref.attrib.get("idref", "")
            if item_id not in manifest:
                errors.append(f"Spine item missing from manifest: {item_id}")
            elif manifest[item_id]["media_type"] != "application/xhtml+xml":
                errors.append(f"Spine item is not XHTML: {item_id}")

        xml_roots: dict[str, ET.Element] = {}
        for name in sorted(names):
            if name.lower().endswith((".xhtml", ".html", ".svg")):
                root = parse_xml(archive.read(name), name, errors)
                if root is not None:
                    xml_roots[name] = root
        stats["xhtml_files"] = sum(name.lower().endswith((".xhtml", ".html")) for name in names)
        stats["css_files"] = sum(name.lower().endswith(".css") for name in names)
        stats["image_files"] = sum(name.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".avif")) for name in names)

        for name, root in xml_roots.items():
            ids = [element.attrib["id"] for element in root.iter() if "id" in element.attrib]
            for duplicate in sorted({value for value in ids if ids.count(value) > 1}):
                errors.append(f"Duplicate id in {name}: {duplicate}")
            for element in root.iter():
                local_name = element.tag.rsplit("}", 1)[-1].lower()
                for attribute in ("href", "src", "poster", "data", XLINK_HREF):
                    value = element.attrib.get(attribute)
                    if value:
                        kind = "href" if attribute in {"href", XLINK_HREF} else attribute
                        check_reference(value, kind, name, names, xml_roots, errors, warnings)
                epub_types = element.attrib.get(EPUB_TYPE, "").split()
                if "noteref" in epub_types:
                    stats["noteref_count"] = stats.get("noteref_count", 0) + 1
                if "footnote" in epub_types:
                    stats["footnote_count"] = stats.get("footnote_count", 0) + 1
                if local_name == "pre":
                    stats["code_block_count"] = stats.get("code_block_count", 0) + 1
                if local_name in {f"h{level}" for level in range(1, 7)}:
                    stats["heading_count"] = stats.get("heading_count", 0) + 1

        for name in sorted(value["target"] for value in manifest.values() if value["media_type"] == "text/css" and value["target"] in names):
            css = archive.read(name).decode("utf-8", errors="replace")
            references = [match[1] for match in CSS_URL_RE.findall(css)] + CSS_IMPORT_RE.findall(css)
            for reference in references:
                if reference and not reference.startswith(("#", "data:")):
                    check_reference(reference, "css-url", name, names, xml_roots, errors, warnings)

        if stats.get("noteref_count", 0) != stats.get("footnote_count", 0):
            warnings.append("Noteref and footnote counts differ; inspect annotation mapping")

        packaged_content = {value["target"] for value in manifest.values()}
        unmanifested = sorted(
            name for name in names
            if name.startswith(posixpath.dirname(opf_path) + "/")
            and name not in packaged_content
            and name not in {opf_path}
        )
        for name in unmanifested:
            errors.append(f"Content file is not declared in manifest: {name}")

        if source_dir is not None:
            if not source_dir.is_dir():
                errors.append(f"Source directory not found: {source_dir}")
            else:
                compared = 0
                for source in source_files(source_dir):
                    target = posixpath.join(posixpath.dirname(opf_path), source.relative_to(source_dir).as_posix())
                    if target not in names:
                        errors.append(f"Source file missing from EPUB: {source.relative_to(source_dir).as_posix()}")
                        continue
                    source_hash = hashlib.sha256(source.read_bytes()).digest()
                    epub_hash = hashlib.sha256(archive.read(target)).digest()
                    if source_hash != epub_hash:
                        errors.append(f"Packaged file differs from source: {source.relative_to(source_dir).as_posix()}")
                    compared += 1
                stats["source_files_compared"] = compared

    return errors, warnings, stats


def render_report(epub: Path, errors: list[str], warnings: list[str], stats: dict[str, int]) -> str:
    lines = ["EPUB audit report", "=================", f"File: {epub}", "", "Statistics:"]
    lines.extend(f"- {key}: {stats[key]}" for key in sorted(stats))
    if not stats:
        lines.append("- None")
    lines.extend(["", "Errors:"])
    lines.extend(f"- {item}" for item in errors or ["None"])
    lines.extend(["", "Warnings:"])
    lines.extend(f"- {item}" for item in warnings or ["None"])
    lines.extend(["", "Official EPUBCheck: not executed by this script.", "Run EPUBCheck separately when requested and available."])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("epub", type=Path, help="EPUB file to inspect")
    parser.add_argument("--source-dir", type=Path, help="Compare normalized source files byte-for-byte")
    parser.add_argument("--report", type=Path, help="Write the text report to this path")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    args = parser.parse_args()
    epub = args.epub.expanduser().resolve()
    source = args.source_dir.expanduser().resolve() if args.source_dir else None
    errors, warnings, stats = audit(epub, source)
    if args.json:
        output = json.dumps({"file": str(epub), "errors": errors, "warnings": warnings, "statistics": stats}, ensure_ascii=False, indent=2) + "\n"
    else:
        output = render_report(epub, errors, warnings, stats)
    if args.report:
        report = args.report.expanduser().resolve()
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(output, encoding="utf-8", newline="\n")
        print(f"Wrote {report}")
    else:
        print(output, end="")
    raise SystemExit(1 if errors else 0)


if __name__ == "__main__":
    main()
