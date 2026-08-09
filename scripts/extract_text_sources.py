#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Extract readable text from HTML and common Office/PDF files.

The extractor is intentionally conservative: it extracts existing text but
never invents missing content. Unsupported or failed files stay in the
manifest so the batch can continue without blocking other sources.
"""

import argparse
import html
import io
import json
import re
import shutil
import subprocess
import zipfile
from html.parser import HTMLParser
from pathlib import Path
import xml.etree.ElementTree as ET

from compat import ensure_dir, expand_path


SUPPORTED = {".html", ".htm", ".pdf", ".docx", ".xlsx", ".pptx"}


class TextParser(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self)
        self.parts = []

    def handle_data(self, data):
        value = html.unescape(data).strip()
        if value:
            self.parts.append(value)


def html_text(path):
    parser = TextParser()
    with io.open(str(path), "r", encoding="utf-8", errors="replace") as handle:
        parser.feed(handle.read())
    return "\n".join(parser.parts)


def xml_text(path, tags):
    texts = []
    with zipfile.ZipFile(str(path), "r") as archive:
        for name in archive.namelist():
            if not name.endswith(".xml"):
                continue
            if not any(tag in name for tag in tags):
                continue
            try:
                root = ET.fromstring(archive.read(name))
            except ET.ParseError:
                continue
            for element in root.iter():
                if element.tag.rsplit("}", 1)[-1] in ("t", "v") and element.text:
                    value = re.sub(r"\s+", " ", element.text).strip()
                    if value:
                        texts.append(value)
    return "\n".join(texts)


def pdf_text(path):
    executable = shutil.which("pdftotext")
    if not executable:
        raise RuntimeError("pdftotext unavailable")
    proc = subprocess.run([executable, "-layout", str(path), "-"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace").strip() or "pdftotext failed")
    return proc.stdout.decode("utf-8", "replace")


def extract(path):
    suffix = path.suffix.lower()
    if suffix in (".html", ".htm"):
        return html_text(path), "html_parser"
    if suffix == ".pdf":
        return pdf_text(path), "pdftotext"
    if suffix == ".docx":
        return xml_text(path, ("word/",)), "docx_xml"
    if suffix == ".xlsx":
        return xml_text(path, ("worksheets/", "sharedStrings")), "xlsx_xml"
    if suffix == ".pptx":
        return xml_text(path, ("ppt/slides/",)), "pptx_xml"
    raise RuntimeError("unsupported extension")


def main():
    parser = argparse.ArgumentParser(description="Extract text from HTML, PDF and Office documents.")
    parser.add_argument("input")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    root = expand_path(args.input)
    output = expand_path(args.output_dir)
    ensure_dir(output)
    rows = []
    files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED)
    for source in files:
        rel = source.name if root.is_file() else str(source.relative_to(root))
        target = output / Path(rel).with_suffix(".txt")
        try:
            text, engine = extract(source)
            text = text.strip()
            if not text:
                raise RuntimeError("empty extracted text")
            ensure_dir(target.parent)
            with io.open(str(target), "w", encoding="utf-8") as handle:
                handle.write(text + "\n")
            rows.append({"source": str(source), "text": str(target), "engine": engine, "status": "ok"})
        except Exception as exc:
            rows.append({"source": str(source), "status": "failed", "error": str(exc)})
    manifest = output / "extraction_manifest.jsonl"
    with io.open(str(manifest), "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps({"total": len(rows), "ok": sum(1 for row in rows if row["status"] == "ok"),
                      "failed": sum(1 for row in rows if row["status"] == "failed"), "manifest": str(manifest)}, ensure_ascii=False))
    return 1 if rows and not any(row["status"] == "ok" for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
