"""PDF 전문 추출 — 텍스트레이어 우선, 이미지(스캔) PDF는 OCR 폴백.

배경: 추출은 Jina Reader에 전적으로 의존했는데, 스캔 이미지 PDF(중국 공시
cninfo/SZSE의 구형 공시가 대표적)는 텍스트레이어가 없어 추출이 빈손이 됐다
(Fable brief §5-4 "이미지 PDF는 실패"). 이 모듈은 로컬 2단 추출을 제공한다:

1) pypdfium2 텍스트레이어 (경량·결정론) — 대부분의 현대 PDF는 여기서 끝.
2) 페이지당 평균 텍스트가 임계 미만이면 '이미지 PDF'로 판정 →
   RapidOCR(onnxruntime, 중국어+영어 내장, 시스템 바이너리 불필요)로 페이지
   비트맵을 OCR. 페이지 수·해상도 상한으로 비용 제어.

의존성·폴백 계약:
- pypdfium2 미설치 → 모듈 비활성(None 반환) → 기존 Jina 경로가 그대로 동작.
- rapidocr 미설치 → 텍스트레이어만 (이미지 PDF는 이전과 동일하게 실패).
- 다운로드/파싱/OCR 어떤 실패도 None + 경고 로그. 파이프라인은 죽지 않는다.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional
from urllib.parse import urlparse

from app.deep_research.common import domain_of
from app.deep_research.models import ExtractedContent

logger = logging.getLogger(__name__)

try:
    import pypdfium2 as _pdfium
    _PDFIUM_OK = True
except ImportError:
    _PDFIUM_OK = False
    logger.warning("[pdf] pypdfium2 미설치 — 로컬 PDF 추출 비활성(Jina 폴백)")

_MAX_PDF_BYTES = 20 * 1024 * 1024   # 20MB 초과 PDF는 스킵(공시 PDF는 수 MB대)
_MAX_TEXT_PAGES = 40                # 텍스트레이어 읽을 최대 페이지
_MAX_OCR_PAGES = 8                  # OCR은 비싸다 — 앞쪽 페이지만(공시 핵심은 전반부)
_OCR_SCALE = 2.0                    # 렌더 배율(~144dpi) — 중문 작은 글자 인식용
_IMAGE_PDF_CHARS_PER_PAGE = 60      # 페이지당 평균 추출문자 이 미만이면 이미지 PDF로 판정
_MIN_CONTENT_CHARS = 120            # 이 미만이면 추출 실패로 간주(기존 Jina 기준과 유사)
_DOWNLOAD_TIMEOUT = 30.0

# OCR 엔진은 무겁다(모델 로드 수백 ms) — 프로세스 싱글턴, 실패 시 영구 비활성.
_ocr_engine = None
_ocr_tried = False


def is_pdf_url(url: str) -> bool:
    """URL이 PDF를 가리키는지 (경로 확장자 기준, 쿼리스트링 무시)."""
    try:
        return urlparse(url).path.lower().endswith(".pdf")
    except Exception:
        return False


def _get_ocr():
    global _ocr_engine, _ocr_tried
    if _ocr_tried:
        return _ocr_engine
    _ocr_tried = True
    try:
        from rapidocr_onnxruntime import RapidOCR
        _ocr_engine = RapidOCR()
        logger.info("[pdf] RapidOCR 초기화 완료 (이미지 PDF OCR 활성)")
    except Exception as e:
        logger.warning(f"[pdf] RapidOCR 불가 — 이미지 PDF OCR 비활성: {e}")
        _ocr_engine = None
    return _ocr_engine


def _pdf_text_layer(doc) -> tuple[str, int]:
    """텍스트레이어 추출 → (전체 텍스트, 읽은 페이지 수)."""
    parts: list[str] = []
    n = min(len(doc), _MAX_TEXT_PAGES)
    for i in range(n):
        try:
            page = doc[i]
            textpage = page.get_textpage()
            parts.append(textpage.get_text_range() or "")
            textpage.close()
            page.close()
        except Exception:
            parts.append("")
    return "\n".join(parts).strip(), n


def _ocr_pages(doc) -> str:
    """이미지 PDF 페이지를 렌더링해 OCR. rapidocr 없으면 빈 문자열."""
    engine = _get_ocr()
    if engine is None:
        return ""
    import numpy as np

    parts: list[str] = []
    n = min(len(doc), _MAX_OCR_PAGES)
    for i in range(n):
        try:
            page = doc[i]
            bitmap = page.render(scale=_OCR_SCALE)
            pil_img = bitmap.to_pil()
            page.close()
            result, _elapsed = engine(np.asarray(pil_img.convert("RGB")))
            if result:
                # result: [(box, text, score), ...] — 읽기 순서대로 텍스트만
                parts.append("\n".join(str(item[1]) for item in result if len(item) > 1))
        except Exception as e:
            logger.warning(f"[pdf] OCR 페이지 {i} 실패(계속): {e}")
    return "\n".join(p for p in parts if p).strip()


def _extract_pdf_bytes(data: bytes, url: str) -> Optional[ExtractedContent]:
    """PDF 바이트 → ExtractedContent. 텍스트레이어 → (부족 시) OCR 순."""
    if not _PDFIUM_OK:
        return None
    try:
        doc = _pdfium.PdfDocument(data)
    except Exception as e:
        logger.warning(f"[pdf] 파싱 실패 {url}: {e}")
        return None

    try:
        text, pages_read = _pdf_text_layer(doc)
        method = "text-layer"
        # 페이지당 평균 문자수가 임계 미만 → 스캔 이미지 PDF로 판정 → OCR
        if pages_read > 0 and len(text) / pages_read < _IMAGE_PDF_CHARS_PER_PAGE:
            ocr_text = _ocr_pages(doc)
            if len(ocr_text) > len(text):
                text, method = ocr_text, "ocr"
    finally:
        try:
            doc.close()
        except Exception:
            pass

    if len(text) < _MIN_CONTENT_CHARS:
        logger.info(f"[pdf] 추출 텍스트 부족({len(text)}자) {url}")
        return None

    filename = urlparse(url).path.rsplit("/", 1)[-1] or "document.pdf"
    first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    title = (first_line[:120] or filename)
    logger.info(f"[pdf] 추출 완료({method}) {len(text)}자: {url}")
    return ExtractedContent(
        url=url,
        title=title,
        content=text[:50000],  # Jina 경로와 동일 상한
        domain=domain_of(url),
        word_count=_word_count(text),
    )


def _word_count(text: str) -> int:
    """CJK 인지형 단어 수 — 중국어/일본어는 공백이 없어 split() 기준으로는
    수천 자 공시가 '단어 몇 개'로 집계돼 하류의 word_count>50 필터에서 전멸한다.
    공백 단어 수 + CJK 문자 수(문자당 1단어 근사)로 계산."""
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿" or "㐀" <= ch <= "䶿")
    return len(text.split()) + cjk


async def extract_pdf(url: str) -> Optional[ExtractedContent]:
    """PDF URL 다운로드 → 로컬 추출. 어떤 실패도 None(호출부가 Jina 폴백)."""
    if not _PDFIUM_OK:
        return None
    try:
        import httpx
        async with httpx.AsyncClient(
            timeout=_DOWNLOAD_TIMEOUT, follow_redirects=True,
            headers={"User-Agent": "FinVision research admin@finvision.app"},
        ) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                logger.info(f"[pdf] HTTP {resp.status_code}: {url}")
                return None
            data = resp.content
        if len(data) > _MAX_PDF_BYTES:
            logger.info(f"[pdf] 크기 초과({len(data) // 1024}KB) 스킵: {url}")
            return None
        if not data.startswith(b"%PDF"):
            return None  # PDF 아님(HTML 에러페이지 등) → Jina에 맡김
        # 파싱·OCR은 CPU 바운드 — 이벤트루프 블로킹 방지
        return await asyncio.to_thread(_extract_pdf_bytes, data, url)
    except Exception as e:
        logger.warning(f"[pdf] 추출 실패 {url}: {e}")
        return None


async def extract_pdf_batch(urls: list[str], max_concurrent: int = 3) -> list[ExtractedContent]:
    """여러 PDF 병렬 추출(동시 3 — OCR 메모리/CPU 고려)."""
    sem = asyncio.Semaphore(max_concurrent)

    async def _one(u: str) -> Optional[ExtractedContent]:
        async with sem:
            return await extract_pdf(u)

    results = await asyncio.gather(*(_one(u) for u in urls), return_exceptions=True)
    return [r for r in results if isinstance(r, ExtractedContent)]
