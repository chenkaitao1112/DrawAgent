from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


def _normalize_text(text: str) -> str:
    cleaned = text.replace("\x00", "")
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


class DocumentIngestionService:
    def _read_text_file(self, path: Path) -> str:
        for encoding in ("utf-8", "utf-8-sig", "gbk", "latin-1"):
            try:
                return _normalize_text(path.read_text(encoding=encoding))
            except UnicodeDecodeError:
                continue
        raise UnicodeDecodeError("unknown", b"", 0, 1, "No supported encoding succeeded.")

    def _read_pdf(self, path: Path) -> tuple[str, int, str, str]:
        try:
            from pypdf import PdfReader  # type: ignore
        except ImportError:
            return "", 0, "pypdf_unavailable", "PDF parsing requires the pypdf package."

        try:
            reader = PdfReader(str(path))
            pages: list[str] = []
            for page in reader.pages:
                pages.append(page.extract_text() or "")
            text = _normalize_text("\n\n".join(pages))
            warning = "" if text else "PDF parsed successfully, but no extractable text was found."
            return text, len(reader.pages), "pypdf", warning
        except Exception as exc:
            return "", 0, "pypdf_failed", f"PDF parsing failed: {exc}"

    def _read_docx(self, path: Path) -> str:
        with zipfile.ZipFile(path, "r") as archive:
            xml_bytes = archive.read("word/document.xml")
        root = ET.fromstring(xml_bytes)
        texts = [item.text or "" for item in root.iter() if item.text]
        return _normalize_text("\n".join(texts))

    def ingest_uploaded_files(
        self,
        *,
        uploaded_files: list[dict[str, Any]],
        artifact_dir: Path,
    ) -> dict[str, Any]:
        files: list[dict[str, Any]] = []
        warnings: list[str] = []
        combined_parts: list[str] = []

        for item in uploaded_files:
            path = Path(str(item.get("saved_path", "")))
            extension = path.suffix.lower()
            file_record: dict[str, Any] = {
                "name": item.get("name", path.name),
                "path": str(path),
                "extension": extension,
                "content_type": item.get("content_type", "application/octet-stream"),
                "size_bytes": item.get("size_bytes", 0),
                "parser": "",
                "status": "metadata_only",
                "extracted_text": "",
                "text_excerpt": "",
                "char_count": 0,
                "page_count": 0,
                "warnings": [],
                "chunks": [],
            }
            try:
                if extension == ".pdf":
                    text, page_count, parser, warning = self._read_pdf(path)
                    file_record["parser"] = parser
                    file_record["page_count"] = page_count
                    if text:
                        file_record["status"] = "parsed"
                        file_record["extracted_text"] = text
                    if warning:
                        file_record["warnings"].append(warning)
                elif extension in {".md", ".txt"}:
                    text = self._read_text_file(path)
                    file_record["parser"] = "direct_text"
                    file_record["status"] = "parsed"
                    file_record["extracted_text"] = text
                elif extension == ".docx":
                    text = self._read_docx(path)
                    file_record["parser"] = "docx_xml"
                    file_record["status"] = "parsed"
                    file_record["extracted_text"] = text
                elif extension in {".png", ".jpg", ".jpeg", ".webp"}:
                    file_record["parser"] = "metadata_only"
                    file_record["warnings"].append("OCR is not wired in v2, image metadata only.")
                else:
                    file_record["parser"] = "metadata_only"
                    file_record["warnings"].append(f"Unsupported format: {extension or 'unknown'}")
            except Exception as exc:
                file_record["warnings"].append(str(exc))

            text = str(file_record.get("extracted_text") or "")
            file_record["char_count"] = len(text)
            file_record["text_excerpt"] = text[:400]
            if text:
                combined_parts.append(f"[{file_record['name']}]\n{text}")
            warnings.extend(file_record["warnings"])
            files.append(file_record)

        combined_text = _normalize_text("\n\n".join(combined_parts))
        artifact_path = artifact_dir / "document_context.json"
        result = {
            "file_count": len(uploaded_files),
            "parsed_file_count": len([item for item in files if item.get("status") == "parsed"]),
            "total_characters": len(combined_text),
            "retrieval_ready": bool(combined_text),
            "combined_text": combined_text,
            "combined_excerpt": combined_text[:2000],
            "warnings": warnings,
            "files": files,
            "chunks": [],
            "artifact_path": str(artifact_path),
        }
        artifact_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result
