---
name: web-to-epub
description: 把网页文章或网页书转换为内容保真、可离线阅读的可重排 EPUB 3（自适应排版、语义化目录导航、内嵌离线资源、内部链接与脚注引用校验）。随附两个零依赖脚本：epub_package.py 按规范化源目录确定性打包，epub_audit.py 做 ZIP 结构、manifest 与 spine、链接锚点、重复 ID 与离线资源依赖的本地审计。当用户提供网页或文章链接并要求保存为可离线阅读的 EPUB 时使用。不适用于只做网页摘要、生成 PDF、单纯保存网页 HTML，以及修改已有 epub 排版或把字幕整理成笔记的场景。
---

# Web to EPUB

Create a readable EPUB 3 directly from web sources. Preserve the source's meaning and reading order while adapting presentation to a reflowable ebook.

## Confirm consequential choices

Before collection or conversion, resolve only choices that materially affect the result:

- which pages, chapters, sidebars, comments, or navigation text belong in the book, and their order;
- reflowable versus fixed layout (default: reflowable EPUB 3);
- ambiguous ownership or placement of notes and annotations;
- whether external hyperlinks and meaningful non-body content should remain;
- whether to run official EPUBCheck when it is available.

Do not block on metadata the user has not supplied. Use a page title, source language, and a generated UUID where needed, and disclose material defaults.

## Preserve content

- Treat source prose, headings and levels, lists, tables, quotations, code characters and whitespace, images and captions, links, and notes as authoritative. Do not silently summarize, rewrite, or omit them.
- Remove advertisements, consent controls, share widgets, tracking, and behavior-only scripts. Preserve text or links created by scripts when they are part of the requested reading content.
- Download every resource needed for offline reading. External reference links may remain, but images, styles, fonts, audio, or video required to understand the book must not depend on the network.
- Use semantic XHTML and relative CSS units. Avoid desktop-only positioning, fixed text heights, and layouts that overlap when reader fonts are enlarged.
- Map explicit notes to EPUB 3 `noteref` and `footnote` semantics. Ask rather than guessing when a visual marker cannot be reliably assigned to content.
- A reflowable EPUB cannot reproduce a webpage pixel-for-pixel. Explain this only when fidelity expectations make it relevant.

## Build workflow

1. Inspect the source and establish the body-content boundary, language, reading order, chapter split, navigation structure, media, code, tables, and notes.
2. Create a normalized source directory:

   ```text
   source/
   |-- metadata.json
   |-- toc.json                 optional hierarchical table of contents
   |-- text/*.xhtml
   |-- styles/*                 optional
   |-- images/*                 optional
   `-- fonts/*                  optional
   ```

3. Keep content documents valid XML/XHTML with stable, unique IDs. Use relative links between files. `metadata.json` may contain `title`, `identifier`, `language`, `creator`, `publisher`, `description`, `rights`, `modified`, and `cover`; `cover` is a source-relative asset path such as `images/cover.jpg`.
4. When `toc.json` is present, represent it as an array of `{title, href, children}` objects. Targets are source-relative paths such as `text/chapter-1.xhtml#section`; children may be nested. Without it, the packager uses sorted XHTML filenames as the reading order and navigation labels.
5. Package with the bundled deterministic helper:

   ```powershell
   python scripts/epub_package.py SOURCE_DIR OUTPUT.epub
   ```

   It generates the container, OPF, EPUB navigation document, optional compatibility NCX, manifest, spine, and a ZIP whose uncompressed `mimetype` entry is first. Use `--no-ncx` only when EPUB 2 compatibility is deliberately unnecessary.
6. Audit the result and compare packaged files with the normalized source:

   ```powershell
   python scripts/epub_audit.py OUTPUT.epub --source-dir SOURCE_DIR --report EPUB_AUDIT.txt
   ```

   The local audit checks ZIP/package structure, manifest and spine, XML, internal files and fragments, duplicate IDs, offline resource dependencies, CSS URLs, notes, and byte preservation. It is not a substitute for official EPUBCheck.
7. If the user approved EPUBCheck and it is available, run it after the local audit and preserve its complete output. If it is unavailable or declined, label the result as local/non-EPUBCheck validation and never claim official conformance.

## Delivery

Deliver the `.epub` plus a concise report containing scope, excluded page chrome, metadata defaults, local audit status, EPUBCheck status, remaining external hyperlinks, and any known fidelity or annotation limitations. Do not call the book complete while broken links, missing offline resources, invalid XML, or unexplained content loss remain.
