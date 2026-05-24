from __future__ import annotations

import argparse
import csv
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}
MINERU_EXTS = IMAGE_EXTS | {".pdf", ".docx", ".pptx", ".xlsx"}
TEXT_EXTS = {".txt", ".md", ".csv", ".tsv"}
LEGACY_EXCEL_EXTS = {".xls"}


@dataclass
class Result:
    source: Path
    kind: str
    status: str
    output: Path | None
    message: str = ""


def read_text_lossy(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk", "big5"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def looks_like_text(path: Path, sample_size: int = 4096) -> bool:
    sample = path.read_bytes()[:sample_size]
    if not sample:
        return True
    if b"\x00" in sample:
        return False
    printable = sum((32 <= b <= 126) or b in b"\r\n\t" or b >= 128 for b in sample)
    return printable / max(len(sample), 1) > 0.90


def markdown_escape_cell(value: str) -> str:
    value = value.replace("\r", " ").replace("\n", "<br>").strip()
    return value.replace("|", "\\|")


def delimited_text_to_markdown(text: str) -> str:
    lines = [line.rstrip("\r") for line in text.splitlines() if line.strip()]
    if not lines:
        return ""

    delimiter = "\t" if any("\t" in line for line in lines[:10]) else ","
    rows = [[markdown_escape_cell(cell) for cell in line.split(delimiter)] for line in lines]
    max_cols = max(len(row) for row in rows)
    rows = [row + [""] * (max_cols - len(row)) for row in rows]

    header = rows[0]
    body = rows[1:]
    out = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * max_cols) + " |",
    ]
    out.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(out) + "\n"


def text_to_markdown(path: Path, relative: Path, output_md: Path) -> None:
    text = read_text_lossy(path)
    title = relative.with_suffix("").as_posix()
    output_md.parent.mkdir(parents=True, exist_ok=True)

    if "\t" in text or path.suffix.lower() in {".csv", ".tsv", ".xls"}:
        body = delimited_text_to_markdown(text)
    else:
        body = text.strip() + "\n"

    output_md.write_text(f"# {title}\n\n{body}", encoding="utf-8")


def find_latest_markdown(directory: Path) -> Path | None:
    candidates = list(directory.rglob("*.md"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def run_mineru_local(path: Path, artifact_dir: Path, timeout: int, backend: str | None) -> Path:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    cmd = ["mineru", "-p", str(path), "-o", str(artifact_dir)]
    if backend:
        cmd.extend(["-b", backend])
    completed = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(detail[-1200:] if detail else f"MinerU exited {completed.returncode}")

    md_path = find_latest_markdown(artifact_dir)
    if md_path is None:
        raise RuntimeError("MinerU completed but no Markdown file was found")
    return md_path


def convert_xls_to_xlsx(path: Path, temp_dir: Path) -> Path:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise RuntimeError("true .xls requires LibreOffice/soffice, but it was not found")

    completed = subprocess.run(
        [soffice, "--headless", "--convert-to", "xlsx", "--outdir", str(temp_dir), str(path)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout).strip())

    converted = temp_dir / f"{path.stem}.xlsx"
    if not converted.exists():
        raise RuntimeError("LibreOffice did not produce an .xlsx file")
    return converted


def classify(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in TEXT_EXTS:
        return "text"
    if suffix in LEGACY_EXCEL_EXTS:
        return "text-xls" if looks_like_text(path) else "legacy-xls"
    if suffix in MINERU_EXTS:
        return "mineru"
    return "unsupported"


def iter_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if path.is_file():
            yield path


def should_skip(output_md: Path, source: Path, force: bool) -> bool:
    return (not force) and output_md.exists() and output_md.stat().st_mtime >= source.stat().st_mtime


def process_file(
    source: Path,
    input_root: Path,
    output_root: Path,
    mineru_mode: str,
    mineru_backend: str | None,
    dry_run: bool,
    force: bool,
    timeout: int,
) -> Result:
    relative = source.relative_to(input_root)
    stem_path = relative.with_suffix("")
    output_md = output_root / "markdown" / stem_path.with_suffix(".md")
    artifact_dir = output_root / "artifacts" / stem_path
    kind = classify(source)

    if dry_run:
        return Result(source, kind, "planned", output_md)
    if kind == "unsupported":
        return Result(source, kind, "skipped", None, f"unsupported extension {source.suffix}")
    if should_skip(output_md, source, force):
        return Result(source, kind, "skipped", output_md, "up to date")

    try:
        if kind in {"text", "text-xls"}:
            text_to_markdown(source, relative, output_md)
            return Result(source, kind, "ok", output_md)

        if kind == "legacy-xls":
            with tempfile.TemporaryDirectory(prefix="ticai_xls_") as tmp:
                converted = convert_xls_to_xlsx(source, Path(tmp))
                md_path = run_mineru_local(converted, artifact_dir, timeout, mineru_backend)
        elif kind == "mineru":
            if mineru_mode != "local":
                raise RuntimeError("api mode is reserved; add your MinerU API adapter before running")
            md_path = run_mineru_local(source, artifact_dir, timeout, mineru_backend)
        else:
            raise RuntimeError(f"unknown kind {kind}")

        output_md.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(md_path, output_md)
        return Result(source, kind, "ok", output_md)
    except Exception as exc:
        return Result(source, kind, "failed", None, str(exc))


def write_reports(output_root: Path, results: list[Result]) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    manifest = output_root / "manifest.csv"
    failures = output_root / "failures.csv"

    with manifest.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["source", "kind", "status", "output", "message"])
        for item in results:
            writer.writerow([item.source, item.kind, item.status, item.output or "", item.message])

    failed = [item for item in results if item.status == "failed"]
    with failures.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["source", "kind", "message"])
        for item in failed:
            writer.writerow([item.source, item.kind, item.message])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert D:\\ticai documents to Markdown.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--mineru-mode", choices=["local", "api"], default="local")
    parser.add_argument("--mineru-backend", default=None, help="Optional MinerU backend, e.g. pipeline")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--timeout", type=int, default=900)
    return parser.parse_args()


def load_failed_sources(output_root: Path) -> set[str]:
    path = output_root / "failures.csv"
    if not path.exists():
        return set()
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return {row["source"] for row in csv.DictReader(handle)}


def main() -> int:
    args = parse_args()
    input_root = args.input.resolve()
    output_root = args.output.resolve()
    if not input_root.exists():
        print(f"Input folder does not exist: {input_root}", file=sys.stderr)
        return 2

    retry_sources = load_failed_sources(output_root) if args.retry_failed else None
    files = list(iter_files(input_root))
    if retry_sources is not None:
        files = [path for path in files if str(path) in retry_sources]

    results: list[Result] = []
    total = len(files)
    for index, source in enumerate(files, 1):
        result = process_file(
            source=source,
            input_root=input_root,
            output_root=output_root,
            mineru_mode=args.mineru_mode,
            mineru_backend=args.mineru_backend,
            dry_run=args.dry_run,
            force=args.force,
            timeout=args.timeout,
        )
        results.append(result)
        print(f"[{index}/{total}] {result.status:7} {result.kind:10} {source}")

    write_reports(output_root, results)
    ok = sum(1 for item in results if item.status == "ok")
    failed = sum(1 for item in results if item.status == "failed")
    skipped = sum(1 for item in results if item.status == "skipped")
    planned = sum(1 for item in results if item.status == "planned")
    print(f"Done. ok={ok} failed={failed} skipped={skipped} planned={planned}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
