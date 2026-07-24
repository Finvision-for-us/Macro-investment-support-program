"""접근 가능본 리졸버(available→CDX 체인) 단위테스트 — network 없음(응답 fake 주입).

핵심 계약:
- available 성공 시 CDX를 부르지 않는다(체인 순서).
- available 빈손 → CDX 최신 200 스냅샷(마지막 행 = 최신, 라이브 실측 확정) 채택.
- 정확 URL 빈손 + 쿼리스트링 존재 → 경로만으로 1회 재시도.
- 모든 실패는 None — 파이프라인은 죽지 않는다.

실행: python backend/tests/test_accessible_resolver.py
"""
import asyncio
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.deep_research.discovery.accessible_resolver import (
    WAYBACK_AVAILABLE_API, WAYBACK_CDX_API, AccessibleResolver,
)


def _resp(json_data, status=200):
    return SimpleNamespace(status_code=status, json=lambda: json_data)


CDX_ROWS = [
    ["timestamp", "original", "statuscode"],
    ["20240829133853", "https://www.wsj.com/articles/nvda", "200"],
    ["20240921174052", "https://www.wsj.com/articles/nvda", "200"],  # 최신(마지막 행)
]


class _FakeResolver(AccessibleResolver):
    """_get_with_retry를 라우팅 테이블로 대체 — 호출 기록을 남긴다."""

    def __init__(self, available_json=None, cdx_by_url=None):
        self.available_json = available_json
        self.cdx_by_url = cdx_by_url or {}
        self.calls: list[tuple[str, str]] = []  # (api, url)

    async def _get_with_retry(self, client, url, headers=None, params=None, max_retries=3):
        if url == WAYBACK_AVAILABLE_API or url.startswith(WAYBACK_AVAILABLE_API):
            self.calls.append(("available", url))
            return _resp(self.available_json) if self.available_json is not None else None
        if url == WAYBACK_CDX_API:
            target = (params or {}).get("url", "")
            self.calls.append(("cdx", target))
            data = self.cdx_by_url.get(target)
            return _resp(data) if data is not None else _resp([["h"]])  # 헤더만=빈손
        raise AssertionError(f"unexpected url {url}")


def _run(coro):
    return asyncio.run(coro)


class TestChainOrder(unittest.TestCase):

    def test_available_hit_skips_cdx(self):
        r = _FakeResolver(available_json={
            "archived_snapshots": {"closest": {
                "available": True, "status": "200",
                "url": "http://web.archive.org/web/2024/https://x.com/a",
                "timestamp": "20240101000000"}}})
        out = _run(r.find_accessible_url("https://x.com/a"))
        self.assertEqual(out.method, "wayback")
        self.assertFalse(any(api == "cdx" for api, _ in r.calls))

    def test_available_empty_falls_to_cdx_latest(self):
        """available 빈손 → CDX 마지막 행(최신 200) 채택."""
        r = _FakeResolver(
            available_json={"archived_snapshots": {}},
            cdx_by_url={"https://www.wsj.com/articles/nvda": CDX_ROWS})
        out = _run(r.find_accessible_url("https://www.wsj.com/articles/nvda"))
        self.assertIsNotNone(out)
        self.assertEqual(out.method, "cdx")
        self.assertEqual(out.timestamp, "20240921174052")  # 마지막 행 = 최신
        self.assertEqual(
            out.accessible_url,
            "https://web.archive.org/web/20240921174052/https://www.wsj.com/articles/nvda")
        self.assertEqual(out.original_url, "https://www.wsj.com/articles/nvda")


class TestQueryVariant(unittest.TestCase):

    def test_query_stripped_retry(self):
        """정확 URL 빈손 + 쿼리스트링 → 경로만으로 재시도해 회수."""
        base = "https://www.wsj.com/articles/nvda"
        r = _FakeResolver(
            available_json={"archived_snapshots": {}},
            cdx_by_url={base: CDX_ROWS})  # 쿼리 붙은 원형은 빈손, 경로만 존재
        out = _run(r.find_accessible_url(base + "?mod=hp_lead_pos1"))
        self.assertIsNotNone(out)
        self.assertEqual(out.method, "cdx")
        # 원본 URL은 사용자가 요청한 원형 유지
        self.assertEqual(out.original_url, base + "?mod=hp_lead_pos1")
        cdx_targets = [u for api, u in r.calls if api == "cdx"]
        self.assertEqual(cdx_targets, [base + "?mod=hp_lead_pos1", base])

    def test_no_query_no_extra_retry(self):
        """쿼리스트링 없으면 CDX는 1회만 시도."""
        r = _FakeResolver(available_json={"archived_snapshots": {}})
        out = _run(r.find_accessible_url("https://x.com/plain"))
        self.assertIsNone(out)
        cdx_targets = [u for api, u in r.calls if api == "cdx"]
        self.assertEqual(cdx_targets, ["https://x.com/plain"])


class TestDefensive(unittest.TestCase):

    def test_all_fail_returns_none(self):
        r = _FakeResolver(available_json={"archived_snapshots": {}})
        self.assertIsNone(_run(r.find_accessible_url("https://x.com/a")))

    def test_cdx_header_only_none(self):
        r = _FakeResolver(
            available_json={"archived_snapshots": {}},
            cdx_by_url={"https://x.com/a": [["timestamp", "original", "statuscode"]]})
        self.assertIsNone(_run(r.find_accessible_url("https://x.com/a")))

    def test_cdx_malformed_rows_none(self):
        r = _FakeResolver(
            available_json={"archived_snapshots": {}},
            cdx_by_url={"https://x.com/a": [["h"], ["not-a-ts", "not-a-url"]]})
        self.assertIsNone(_run(r.find_accessible_url("https://x.com/a")))

    def test_cdx_non_list_json_none(self):
        r = _FakeResolver(
            available_json={"archived_snapshots": {}},
            cdx_by_url={"https://x.com/a": {"error": "blocked"}})
        self.assertIsNone(_run(r.find_accessible_url("https://x.com/a")))

    def test_available_none_response_still_tries_cdx(self):
        """available 자체가 실패(None 응답)해도 CDX는 시도된다."""
        r = _FakeResolver(available_json=None,
                          cdx_by_url={"https://x.com/a": CDX_ROWS[:2] + [
                              ["20250101000000", "https://x.com/a", "200"]]})
        out = _run(r.find_accessible_url("https://x.com/a"))
        self.assertIsNotNone(out)
        self.assertEqual(out.method, "cdx")


class TestVariantHelper(unittest.TestCase):

    def test_variants(self):
        v = AccessibleResolver._cdx_url_variants("https://a.com/p?x=1#f")
        self.assertEqual(v, ["https://a.com/p?x=1#f", "https://a.com/p"])
        self.assertEqual(AccessibleResolver._cdx_url_variants("https://a.com/p"),
                         ["https://a.com/p"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
