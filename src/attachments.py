"""첨부파일 다운로드 + HWP→PDF 변환 + Slack 업로드.

LibreOffice headless로 .hwp / .hwpx → .pdf 변환. soffice가 PATH에 있어야 함.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import requests

logger: logging.Logger = logging.getLogger(__name__)

# Slack files.upload v2는 50MB 제한이지만, 안전점검 첨부는 보통 1-5MB.
# 너무 큰 파일은 스킵 (소요시간 + 메모리 안전).
MAX_FILE_BYTES: int = 30 * 1024 * 1024  # 30MB

HWP_EXTS: frozenset[str] = frozenset({".hwp", ".hwpx"})
CONVERTIBLE_EXTS: frozenset[str] = frozenset({".hwp", ".hwpx", ".doc", ".docx", ".xls", ".xlsx"})

SOFFICE: str = shutil.which("soffice") or "/opt/homebrew/bin/soffice"
# H2Orestart 확장이 설치된 공용 user profile (LibreOffice 격리)
SHARED_LO_PROFILE: str = os.getenv("LIBREOFFICE_PROFILE", "/Users/dev06/.lo-safetybid")


def _sanitize_filename(name: str) -> str:
    name = re.sub(r"[/\\:*?\"<>|]", "_", name).strip()
    return name[:200] or "file"


def download_attachment(url: str, dest_dir: Path, name_hint: str, referer: str = "") -> Path | None:
    """첨부파일 다운로드. 실패 시 None.

    name_hint(우리가 a 태그에서 추출한 정확한 파일명)를 우선 사용.
    Content-Disposition은 한글 인코딩 (EUC-KR을 Latin-1로 읽어서) 깨지는 케이스가 많아 fallback.
    """
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36"}
    if referer:
        headers["Referer"] = referer
    try:
        with requests.get(url, headers=headers, timeout=60, stream=True) as r:
            r.raise_for_status()
            final_name = _sanitize_filename(name_hint or "download.bin")
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / final_name
            total = 0
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_FILE_BYTES:
                        f.close()
                        dest.unlink(missing_ok=True)
                        logger.warning("[attachment] 파일 크기 초과(%dMB), 스킵: %s", total // 1024 // 1024, final_name)
                        return None
                    f.write(chunk)
            return dest
    except requests.RequestException as exc:
        logger.warning("[attachment] 다운로드 실패 %s: %s", url[:80], exc)
        return None


def convert_to_pdf(src: Path) -> Path | None:
    """HWP/DOC/XLS → PDF 변환 (LibreOffice headless). 실패 시 None."""
    if not src.exists():
        return None
    ext = src.suffix.lower()
    if ext == ".pdf":
        return src
    if ext not in CONVERTIBLE_EXTS:
        return None

    out_dir = src.parent
    try:
        # H2Orestart 확장이 있는 공용 profile 사용 — 동시 호출 시 lock 가능하지만 우리 monitor는 순차
        proc = subprocess.run(
            [
                SOFFICE,
                f"-env:UserInstallation=file://{SHARED_LO_PROFILE}",
                "--headless", "--norestore", "--nofirststartwizard",
                "--convert-to", "pdf",
                "--outdir", str(out_dir),
                str(src),
            ],
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0:
            logger.warning(
                "[attachment] LibreOffice 변환 실패 (%s): rc=%d stderr=%s",
                src.name, proc.returncode, proc.stderr[:200].strip(),
            )
            return None
        pdf_path = out_dir / (src.stem + ".pdf")
        return pdf_path if pdf_path.exists() else None
    except subprocess.TimeoutExpired:
        logger.warning("[attachment] LibreOffice timeout (60s): %s", src.name)
        return None
    except FileNotFoundError as exc:
        logger.warning("[attachment] LibreOffice 호출 실패 (%s): %s", src.name, exc)
        return None


def prepare_for_upload(url: str, name_hint: str, referer: str, work_dir: Path) -> tuple[Path | None, Path | None]:
    """다운로드 + (HWP면) PDF 변환. (원본 경로, PDF 경로) 반환."""
    src = download_attachment(url, work_dir, name_hint, referer)
    if src is None:
        return None, None
    if src.suffix.lower() in HWP_EXTS:
        pdf = convert_to_pdf(src)
        return src, pdf  # pdf가 None이면 원본만
    if src.suffix.lower() == ".pdf":
        return src, src  # PDF는 그 자체가 변환된 것
    return src, None


def upload_to_slack(
    bot_token: str,
    channel_id: str,
    files: list[Path],
    initial_comment: str = "",
    title_prefix: str = "",
) -> bool:
    """Slack files_upload_v2로 채널에 파일들 업로드. 실패 시 False."""
    if not files:
        return True
    try:
        from slack_sdk import WebClient
        client = WebClient(token=bot_token)
        # files_upload_v2는 한 번에 여러 파일 업로드 지원
        file_uploads: list[dict[str, Any]] = []
        for f in files:
            if not f.exists():
                continue
            file_uploads.append({
                "file": str(f),
                "title": (title_prefix + f.name) if title_prefix else f.name,
                "filename": f.name,
            })
        if not file_uploads:
            return True
        resp = client.files_upload_v2(
            channel=channel_id,
            file_uploads=file_uploads,
            initial_comment=initial_comment or None,
        )
        return bool(resp.get("ok"))
    except Exception as exc:
        logger.warning("[slack] 파일 업로드 실패: %s", exc)
        return False


def workspace_dir_for(notice_id: str, base: Path) -> Path:
    """notice_id별 작업 폴더."""
    safe = re.sub(r"[^\w\-=]+", "_", notice_id)[:100]
    return base / safe
