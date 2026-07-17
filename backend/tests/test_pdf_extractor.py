"""PDF 로컬 추출(텍스트레이어→OCR 폴백) 단위테스트 — network 없음, OCR 엔진은 fake 주입.

핵심 계약:
- 텍스트레이어가 충분하면 OCR을 부르지 않는다.
- 페이지당 평균 텍스트 임계 미만(스캔 PDF)이면 OCR 폴백.
- rapidocr 불가 시 조용히 None(호출부가 Jina 폴백) — 파이프라인 무사.
- 중국어(무공백) 텍스트도 word_count>50 필터를 통과해야 한다(CJK 인지형 집계).

실행: python backend/tests/test_pdf_extractor.py
"""
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.deep_research.sources import pdf_extractor as px


def make_text_pdf(text: str) -> bytes:
    """수제 최소 PDF(Helvetica 텍스트레이어 1페이지)."""
    stream = f"BT /F1 12 Tf 50 700 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + o + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    )
    return out.getvalue()


TEXT_PDF = make_text_pdf("Revenue was USD 135 million in fiscal 2025. " * 4)
EMPTY_PDF = make_text_pdf(" ")  # 텍스트레이어 사실상 없음 → 이미지 PDF 판정


class _FakeOCR:
    """RapidOCR 흉내: engine(np_img) -> (result, elapsed)."""

    def __init__(self, lines):
        self.lines = lines
        self.calls = 0

    def __call__(self, img):
        self.calls += 1
        return [([0, 0, 1, 1], ln, 0.99) for ln in self.lines], 0.01


class _OcrPatched(unittest.TestCase):
    """_get_ocr 싱글턴을 케이스별로 주입/차단하는 공통 베이스."""

    def setUp(self):
        self._orig = (px._ocr_engine, px._ocr_tried)

    def tearDown(self):
        px._ocr_engine, px._ocr_tried = self._orig

    def _set_engine(self, engine):
        px._ocr_engine, px._ocr_tried = engine, True


class TestIsPdfUrl(unittest.TestCase):

    def test_pdf_suffix(self):
        self.assertTrue(px.is_pdf_url("https://static.cninfo.com.cn/finalpage/x.PDF"))
        self.assertTrue(px.is_pdf_url("https://sec.gov/a/b/report.pdf?query=1"))

    def test_non_pdf(self):
        self.assertFalse(px.is_pdf_url("https://reuters.com/article"))
        self.assertFalse(px.is_pdf_url("https://x.com/pdf-viewer"))  # 경로 확장자 아님
        self.assertFalse(px.is_pdf_url(""))


class TestTextLayer(_OcrPatched):

    def test_text_layer_extracted_without_ocr(self):
        """텍스트레이어 충분 → OCR 미호출."""
        fake = _FakeOCR(["MUST NOT APPEAR"])
        self._set_engine(fake)
        r = px._extract_pdf_bytes(TEXT_PDF, "https://example.com/report.pdf")
        self.assertIsNotNone(r)
        self.assertIn("135 million", r.content)
        self.assertNotIn("MUST NOT APPEAR", r.content)
        self.assertEqual(fake.calls, 0)
        self.assertEqual(r.domain, "example.com")

    def test_short_text_returns_none(self):
        """추출 텍스트가 최소 기준 미만이고 OCR도 없으면 None."""
        self._set_engine(None)
        self.assertIsNone(px._extract_pdf_bytes(EMPTY_PDF, "https://x.com/scan.pdf"))


class TestOcrFallback(_OcrPatched):

    CN_LINES = [
        "关于出售参股公司股权的公告",
        "无锡英迪微电子有限公司",
        "本公司董事会及全体董事保证本公告内容不存在任何虚假记载",
        "交易对价为人民币960,834,355元",
        "占标的公司总股本的34.3769%",
        "首期总对价为人民币27.95亿元整",
        "标的公司100%股权整体作价285,600万元",
        "本次交易尚需取得相关监管机构批准后方可实施",
        "本次交易已经公司董事会审议通过并公告",
    ]

    def test_image_pdf_triggers_ocr(self):
        """텍스트레이어 빈약 → 이미지 PDF 판정 → OCR 텍스트 채택."""
        fake = _FakeOCR(self.CN_LINES)
        self._set_engine(fake)
        r = px._extract_pdf_bytes(EMPTY_PDF, "https://cninfo.example/scan.pdf")
        self.assertIsNotNone(r)
        self.assertGreaterEqual(fake.calls, 1)
        self.assertIn("960,834,355", r.content)
        self.assertIn("无锡", r.content)

    def test_cjk_word_count_passes_downstream_filter(self):
        """중국어(무공백) OCR 결과도 word_count>50 필터를 통과해야 한다."""
        fake = _FakeOCR(self.CN_LINES)
        self._set_engine(fake)
        r = px._extract_pdf_bytes(EMPTY_PDF, "https://cninfo.example/scan.pdf")
        self.assertGreater(r.word_count, 50)

    def test_ocr_engine_error_degrades_to_none(self):
        """OCR 엔진이 페이지마다 터져도 예외 전파 없이 None."""
        class Exploding:
            def __call__(self, img):
                raise RuntimeError("onnx crash")
        self._set_engine(Exploding())
        self.assertIsNone(px._extract_pdf_bytes(EMPTY_PDF, "https://x.com/scan.pdf"))

    def test_garbage_bytes_none(self):
        self._set_engine(None)
        self.assertIsNone(px._extract_pdf_bytes(b"%PDF-1.4 garbage", "https://x.com/a.pdf"))


class TestWordCount(unittest.TestCase):

    def test_english(self):
        self.assertEqual(px._word_count("one two three"), 3)

    def test_chinese_counts_chars(self):
        self.assertEqual(px._word_count("交易对价"), 1 + 4)  # split 1 + CJK 4

    def test_mixed(self):
        self.assertEqual(px._word_count("USD 135 交易"), 3 + 2)


class TestBatch(unittest.TestCase):

    def test_batch_filters_failures(self):
        import asyncio

        async def fake_extract(url):
            if "ok" in url:
                from app.deep_research.models import ExtractedContent
                return ExtractedContent(url=url, title="t", content="c" * 200,
                                        domain="x.com", word_count=100)
            return None

        orig = px.extract_pdf
        px.extract_pdf = fake_extract
        try:
            out = asyncio.run(px.extract_pdf_batch(
                ["https://x.com/ok1.pdf", "https://x.com/bad.pdf", "https://x.com/ok2.pdf"]))
        finally:
            px.extract_pdf = orig
        self.assertEqual(len(out), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
