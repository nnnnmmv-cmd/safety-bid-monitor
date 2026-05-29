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

from .adapters.base import _LegacyHTTPSAdapter

logger: logging.Logger = logging.getLogger(__name__)

# Slack files.upload v2는 50MB 제한이지만, 안전점검 첨부는 보통 1-5MB.
# 너무 큰 파일은 스킵 (소요시간 + 메모리 안전).
MAX_FILE_BYTES: int = 30 * 1024 * 1024  # 30MB

# legacy SSL 한국 정부 사이트(예: www.anyang.go.kr, www.yongin.go.kr) 호환 세션.
# requests.get()을 직접 쓰면 SSLV3_ALERT_HANDSHAKE_FAILURE — base.Adapter와 동일 어댑터 mount 필요.
_legacy_session_cache: requests.Session | None = None


def _legacy_session() -> requests.Session:
    global _legacy_session_cache
    if _legacy_session_cache is None:
        s = requests.Session()
        s.mount("https://", _LegacyHTTPSAdapter())
        s.verify = False  # 한국 정부 사이트 SSL 체인 누락 대응 (base.Adapter와 동일)
        _legacy_session_cache = s
    return _legacy_session_cache

HWP_EXTS: frozenset[str] = frozenset({".hwp", ".hwpx"})
CONVERTIBLE_EXTS: frozenset[str] = frozenset({".hwp", ".hwpx", ".doc", ".docx", ".xls", ".xlsx"})

SOFFICE: str = shutil.which("soffice") or "/opt/homebrew/bin/soffice"
# H2Orestart 확장이 설치된 공용 user profile (LibreOffice 격리)
SHARED_LO_PROFILE: str = os.getenv("LIBREOFFICE_PROFILE", "/Users/dev06/.lo-safetybid")


def _sanitize_filename(name: str) -> str:
    name = re.sub(r"[/\\:*?\"<>|]", "_", name).strip()
    return name[:200] or "file"


def _fix_extension_from_url(name_hint: str, url: str) -> str:
    # URL의 query/path에서 실제 확장자 찾아 name_hint와 다르면 교체.
    # name_hint='15).hwp' + url='...sfn=83471_1.hwpx' → '15).hwpx'
    from urllib.parse import urlparse, parse_qs
    try:
        parsed = urlparse(url)
        # query에서 파일명 후보
        candidates: list[str] = []
        qs = parse_qs(parsed.query)
        for k in ("sfn", "user_file_nm", "fileName", "fileNm", "file_name"):
            if qs.get(k):
                candidates.append(qs[k][0])
        candidates.append(parsed.path)  # path 자체
        for cand in candidates:
            if "." in cand:
                actual_ext = cand.rsplit(".", 1)[-1].lower().split("?")[0][:5]
                if not actual_ext or len(actual_ext) > 5:
                    continue
                hint_ext = name_hint.rsplit(".", 1)[-1].lower() if "." in name_hint else ""
                if hint_ext != actual_ext and actual_ext in {"hwp", "hwpx", "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "zip"}:
                    base = name_hint.rsplit(".", 1)[0] if "." in name_hint else name_hint
                    return f"{base}.{actual_ext}"
                break
    except Exception:
        pass
    return name_hint


def _ascii_safe_url(u: str) -> str:
    # HTTP 헤더(Referer)나 URL에 percent-encoded되지 않은 한글이 들어오면
    # requests/urllib3가 latin-1 인코딩 시도하다 UnicodeEncodeError 발생.
    # 한국 정부 사이트는 list URL의 searchKrwd 등에 한글이 그대로 박힌 경우가 흔함.
    if not u:
        return u
    try:
        u.encode("latin-1")
        return u
    except UnicodeEncodeError:
        from urllib.parse import quote
        # %는 safe에 포함 — 이미 percent-encoded된 부분 보존
        return quote(u, safe="/:?&=#%+,;@")


def download_attachment(
    url: str,
    dest_dir: Path,
    name_hint: str,
    referer: str = "",
    session: requests.Session | None = None,
) -> Path | None:
    """첨부파일 다운로드. 실패 시 None.

    name_hint(우리가 a 태그에서 추출한 정확한 파일명)를 우선 사용.
    session: 어댑터가 list/detail에서 쿠키를 받은 세션을 전달하면 그 쿠키로 다운로드.
             eminwon(용인·수원 등)은 SESSION_NTIS 쿠키 없으면 "잘못된 경로" 거부.
             None이면 모듈 레벨 legacy session으로 폴백.
    """
    url = _ascii_safe_url(url)
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36"}
    if referer:
        headers["Referer"] = _ascii_safe_url(referer)

    # name_hint의 확장자가 URL query의 실제 파일명 확장자와 다르면 URL 우선 보정.
    # 안양시처럼 a 태그 text가 잘려서 "15).hwp"로 들어오는데 실제는 ".hwpx"인 경우.
    name_hint = _fix_extension_from_url(name_hint, url)

    # session 우선 (어댑터 쿠키 보존), 없으면 모듈 legacy session 폴백
    sess = session if session is not None else _legacy_session()
    try:
        with sess.get(url, headers=headers, timeout=60, stream=True, verify=False) as r:
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
    # HWP 변환은 환경변수로 켤 수 있게 — Java/H2Orestart 호환성 미해결 상태에서는 기본 OFF
    if ext in HWP_EXTS and os.getenv("ENABLE_HWP_CONVERT", "false").lower() not in ("1", "true", "yes"):
        logger.debug("[attachment] HWP 변환 스킵 (ENABLE_HWP_CONVERT=false): %s", src.name)
        return None
    try:
        proc = subprocess.run(
            [
                SOFFICE,
                f"-env:UserInstallation=file://{SHARED_LO_PROFILE}",
                "--headless", "--norestore", "--nofirststartwizard",
                "--convert-to", "pdf",
                "--outdir", str(out_dir),
                str(src),
            ],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            logger.warning(
                "[attachment] 변환 실패 (%s): %s",
                src.name, proc.stderr[:150].strip(),
            )
            return None
        pdf_path = out_dir / (src.stem + ".pdf")
        return pdf_path if pdf_path.exists() else None
    except subprocess.TimeoutExpired:
        logger.warning("[attachment] 변환 timeout: %s", src.name)
        return None
    except FileNotFoundError as exc:
        logger.warning("[attachment] LibreOffice 호출 실패 (%s): %s", src.name, exc)
        return None


def prepare_for_upload(
    url: str,
    name_hint: str,
    referer: str,
    work_dir: Path,
    session: requests.Session | None = None,
) -> tuple[Path | None, Path | None]:
    """다운로드 + (HWP/HWPX면) PDF 변환. (원본 경로, PDF 경로) 반환.
    session: 어댑터 쿠키 보존용 — eminwon 등 세션 필요 사이트 대응.
    """
    src = download_attachment(url, work_dir, name_hint, referer, session=session)
    if src is None:
        return None, None
    ext = src.suffix.lower()
    if ext in (".hwp", ".hwpx"):
        # 1차: LibreOffice (보통 막힘) → 2차: pyhwp/zipfile + reportlab
        pdf = convert_to_pdf(src) or hwp_to_text_pdf(src, title=src.stem)
        return src, pdf
    if ext == ".pdf":
        return src, src
    return src, convert_to_pdf(src)


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


HWP5TXT: str = shutil.which("hwp5txt") or str(Path(__file__).resolve().parent.parent / ".venv/bin/hwp5txt")
HWP5ODT: str = shutil.which("hwp5odt") or str(Path(__file__).resolve().parent.parent / ".venv/bin/hwp5odt")


def _extract_hwp_via_odt(hwp_path: Path) -> str:
    """hwp5odt로 ODT 변환 후 content.xml 텍스트 추출.
    hwp5txt가 못 잡는 표 셀 내용(시공자·규모·비용 등)까지 포함."""
    import tempfile, zipfile
    odt_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".odt", delete=False) as tmp:
            odt_path = Path(tmp.name)
        proc = subprocess.run(
            [HWP5ODT, "--output", str(odt_path), str(hwp_path)],
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0 or not odt_path.exists():
            return ""
        with zipfile.ZipFile(odt_path) as z:
            if "content.xml" not in z.namelist():
                return ""
            content = z.read("content.xml").decode("utf-8", errors="ignore")
        texts: list[str] = []
        for m in re.finditer(r">([^<>]+)<", content):
            t = m.group(1).strip()
            if t:
                texts.append(t)
        return "\n".join(texts)
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as exc:
        logger.debug("[attachment] hwp5odt 변환 실패 (%s): %s", hwp_path.name, exc)
        return ""
    finally:
        if odt_path is not None:
            try:
                odt_path.unlink(missing_ok=True)
            except Exception:
                pass


def _detect_file_format(path: Path) -> str:
    """파일 매직 넘버로 실제 포맷 감지 — 확장자 무관.

    수원시처럼 파일 이름은 .hwpx인데 실제는 OLE Compound(.hwp)인 케이스 대응.
    반환: 'hwp' / 'hwpx' / 'pdf' / 'zip' / 'unknown'.
    """
    try:
        with open(path, "rb") as f:
            head = f.read(8)
    except Exception:
        return "unknown"
    if head == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "hwp"  # OLE Compound Document
    if head[:4] == b"PK\x03\x04":
        return "hwpx"  # ZIP-based (실제로는 docx/xlsx/zip일 수도 있지만 hwp 도메인에선 hwpx)
    if head[:4] == b"%PDF":
        return "pdf"
    return "unknown"


def extract_hwp_text(hwp_path: Path) -> str:
    """.hwp 파일에서 텍스트 추출 (확장자 아니라 OLE 매직 넘버로 판별).

    1차: hwp5odt → ODT(ZIP+XML) 변환 후 content.xml에서 추출 — 표 셀까지 포함.
    2차: hwp5txt 폴백 (본문만, 표는 placeholder).
    """
    if not hwp_path or not hwp_path.exists():
        return ""
    if _detect_file_format(hwp_path) != "hwp":
        return ""
    # 1차: ODT 변환 (표 셀 포함)
    odt_text = _extract_hwp_via_odt(hwp_path)
    if odt_text.strip():
        return odt_text
    # 2차: hwp5txt 폴백
    try:
        proc = subprocess.run(
            [HWP5TXT, str(hwp_path)],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode == 0:
            return proc.stdout or ""
        return ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def extract_hwpx_text(hwpx_path: Path) -> str:
    """.hwpx (ZIP+XML 기반)에서 텍스트 추출. 표준 라이브러리만 사용.
    매직 넘버(ZIP)로 판별 — 확장자가 .hwp여도 ZIP이면 처리 가능.
    section?.xml 안의 텍스트 노드를 단순 추출 — 표·이미지 위치는 손실.
    """
    if not hwpx_path or not hwpx_path.exists():
        return ""
    if _detect_file_format(hwpx_path) != "hwpx":
        return ""
    try:
        import zipfile
        with zipfile.ZipFile(hwpx_path) as z:
            sections = sorted(
                n for n in z.namelist()
                if n.startswith("Contents/section") and n.endswith(".xml")
            )
            texts: list[str] = []
            for name in sections:
                with z.open(name) as f:
                    content = f.read().decode("utf-8", errors="ignore")
                # XML 태그 사이 텍스트만 추출. 빈/공백/숫자만 짧은 토큰 필터링.
                for m in re.finditer(r">([^<]+)<", content):
                    t = m.group(1).strip()
                    if t and len(t) >= 1:
                        texts.append(t)
            return "\n".join(texts)
    except Exception as exc:
        logger.debug("[attachment] HWPX 텍스트 추출 실패 (%s): %s", hwpx_path.name, exc)
        return ""


def extract_attachment_text(file_path: Path) -> str:
    """파일 시그니처(매직 넘버)로 실제 포맷 감지 후 적절한 추출 함수 호출.
    확장자가 잘못된 경우(예: 수원시 .hwpx인데 실제 .hwp)에도 정상 추출."""
    if not file_path or not file_path.exists():
        return ""
    fmt = _detect_file_format(file_path)
    if fmt == "pdf":
        return extract_pdf_text(file_path)
    if fmt == "hwp":
        return extract_hwp_text(file_path)
    if fmt == "hwpx":
        return extract_hwpx_text(file_path)
    # 시그니처 모르면 확장자 폴백
    ext = file_path.suffix.lower()
    if ext == ".pdf":
        return extract_pdf_text(file_path)
    if ext == ".hwp":
        return extract_hwp_text(file_path)
    if ext == ".hwpx":
        return extract_hwpx_text(file_path)
    return ""


# 한글 PDF 생성용 폰트
KO_FONT_PATH: str = "/System/Library/Fonts/AppleSDGothicNeo.ttc"


def hwp_to_text_pdf(hwp_path: Path, title: str = "") -> Path | None:
    """HWP/HWPX를 텍스트 추출 → reportlab PDF로 변환.
    표·이미지는 손실되지만 모바일에서도 텍스트 본문 확인 가능. LibreOffice/Java 의존 X.
    파일 시그니처로 실제 포맷 감지 — 확장자 잘못된 케이스(수원시 등) 대응.
    """
    if not hwp_path or not hwp_path.exists():
        return None
    fmt = _detect_file_format(hwp_path)
    if fmt == "hwp":
        text = extract_hwp_text(hwp_path)
    elif fmt == "hwpx":
        text = extract_hwpx_text(hwp_path)
    else:
        return None
    if not text.strip():
        return None
    pdf_path = hwp_path.with_suffix(".pdf")
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.pdfgen import canvas
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        try:
            pdfmetrics.registerFont(TTFont("KO", KO_FONT_PATH, subfontIndex=0))
        except Exception:
            try:
                pdfmetrics.registerFont(TTFont("KO", "/System/Library/Fonts/Supplemental/AppleGothic.ttf"))
            except Exception as exc:
                logger.warning("[attachment] 한글 폰트 등록 실패: %s", exc)
                return None

        c = canvas.Canvas(str(pdf_path), pagesize=A4)
        width, height = A4
        margin = 20 * mm
        y = height - margin
        line_height = 14
        max_width = width - 2 * margin

        # 제목
        if title:
            c.setFont("KO", 14)
            for line in _wrap_text(title, 35):
                c.drawString(margin, y, line)
                y -= 20
            y -= 10

        c.setFont("KO", 10)
        for raw_line in text.split("\n"):
            for line in _wrap_text(raw_line, 60):
                if y < margin:
                    c.showPage()
                    c.setFont("KO", 10)
                    y = height - margin
                c.drawString(margin, y, line)
                y -= line_height
        c.save()
        return pdf_path if pdf_path.exists() else None
    except Exception as exc:
        logger.warning("[attachment] reportlab PDF 생성 실패 (%s): %s", hwp_path.name, exc)
        return None


def _wrap_text(s: str, max_chars: int) -> list[str]:
    """단순 글자 수 기준 줄바꿈. 한글 비례폭 약식 (정확하진 않지만 실용)."""
    s = s.rstrip()
    if not s:
        return [""]
    out: list[str] = []
    while s:
        out.append(s[:max_chars])
        s = s[max_chars:]
    return out


def extract_pdf_text(pdf_path: Path, max_pages: int = 20) -> str:
    """PDF에서 텍스트 추출. 시그니처(매직 넘버)로 판별 — 확장자 무관.

    1차: pdfplumber — 표 셀 텍스트까지 추출 (안양·과천 같이 PDF 표 위주 사이트 대응).
    2차: pypdf 폴백 — pdfplumber가 못 다루는 PDF.
    텍스트가 짧으면(<200자) 1·2차 결과 중 긴 쪽 채택.
    """
    if not pdf_path or not pdf_path.exists():
        return ""
    if _detect_file_format(pdf_path) != "pdf":
        return ""

    # 1차: pdfplumber (표 셀 포함)
    text_plumber = ""
    try:
        import pdfplumber
        parts: list[str] = []
        with pdfplumber.open(str(pdf_path)) as pdf:
            for i, page in enumerate(pdf.pages):
                if i >= max_pages:
                    break
                try:
                    # 일반 텍스트
                    t = page.extract_text() or ""
                    if t.strip():
                        parts.append(t)
                    # 표 셀 추출 — 일반 추출에서 빠진 표 데이터 보강
                    for tbl in (page.extract_tables() or []):
                        for row in tbl:
                            cells = [c.strip() for c in row if c and c.strip()]
                            if cells:
                                parts.append(" | ".join(cells))
                except Exception:
                    continue
        text_plumber = "\n".join(parts)
    except Exception as exc:
        logger.debug("[attachment] pdfplumber 실패 (%s): %s", pdf_path.name, exc)

    # 2차: pypdf 폴백 (pdfplumber 결과가 짧을 때)
    text_pypdf = ""
    if len(text_plumber.strip()) < 200:
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(pdf_path))
            parts2: list[str] = []
            for i, page in enumerate(reader.pages):
                if i >= max_pages:
                    break
                try:
                    t = page.extract_text() or ""
                    if t.strip():
                        parts2.append(t)
                except Exception:
                    continue
            text_pypdf = "\n".join(parts2)
        except Exception as exc:
            logger.debug("[attachment] pypdf 실패 (%s): %s", pdf_path.name, exc)

    # 더 긴 결과 채택
    return text_plumber if len(text_plumber) >= len(text_pypdf) else text_pypdf


def workspace_dir_for(notice_id: str, base: Path) -> Path:
    """notice_id별 작업 폴더."""
    safe = re.sub(r"[^\w\-=]+", "_", notice_id)[:100]
    return base / safe
