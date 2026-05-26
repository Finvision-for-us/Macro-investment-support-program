from __future__ import annotations
import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Optional

from app.deep_research.config import SEC_USER_AGENT
from app.deep_research.models import ExtractedContent, SearchResult
from app.deep_research.sources.base import BaseSource

logger = logging.getLogger(__name__)

SEC_EFTS_URL = "https://efts.sec.gov/LATEST/search-index"

# 임원 거래 관련 키워드
_INSIDER_KEYWORDS = frozenset([
    "insider", "executive", "officer", "director", "form 4",
    "stock sale", "share sale", "shares sold", "insider trading",
    "임원", "내부자", "주식 매도", "지분 변동", "베스팅",
    "insider transaction", "insider purchase", "c-level",
    "ceo sale", "cfo sale", "cto sale", "rsu", "restricted stock unit",
])

# 거래 코드 → 설명
_TXN_CODE: dict[str, str] = {
    "S": "공개시장 매도",
    "P": "공개시장 매수",
    "M": "주식매수선택권 행사 또는 RSU 베스팅",
    "F": "세금납부 원천징수 (sell-to-cover)",
    "A": "보상 수령 (awards/grants)",
    "G": "증여",
    "D": "환수 (return to company)",
    "I": "재량 거래",
    "J": "기타",
    "C": "전환",
    "W": "유산 취득",
    "X": "파생상품 행사",
}


class SecEdgarSource(BaseSource):
    """SEC EDGAR 전문 검색 — 미국 공시 (8-K, 10-K, 13D, 13F, Form 4 등)."""

    source_type = "sec"

    def is_available(self) -> bool:
        return True  # API 키 불필요

    async def search(
        self,
        query: str,
        forms: str = "8-K,10-K,13D,13F",
        num_results: int = 10,
        **kwargs,
    ) -> list[SearchResult]:
        # 임원 거래 관련 쿼리이면 Form 4 포함
        q_lower = query.lower()
        if any(kw in q_lower for kw in _INSIDER_KEYWORDS):
            forms = "4,4/A," + forms

        try:
            async with self._make_client() as client:
                headers = {
                    "User-Agent": SEC_USER_AGENT,
                    "Accept-Encoding": "gzip, deflate",
                }
                params = {
                    "q": f'"{query}"',
                    "forms": forms,
                    "hits.hits.total.value": True,
                }
                resp = await self._get_with_retry(
                    client,
                    SEC_EFTS_URL,
                    headers=headers,
                    params=params,
                )
                if resp is None or resp.status_code != 200:
                    return await self._fulltext_search(client, headers, query, forms, num_results)

                data = resp.json()
                return self._parse_hits(data, num_results)
        except Exception as e:
            logger.error(f"[sec] 검색 예외: {e}")
            return []

    async def _fulltext_search(self, client, headers, query, forms, num_results):
        try:
            params = {"q": query, "forms": forms}
            resp = await self._get_with_retry(client, SEC_EFTS_URL, headers=headers, params=params)
            if resp is None or resp.status_code != 200:
                return []
            return self._parse_hits(resp.json(), num_results)
        except Exception as e:
            logger.error(f"[sec] 풀텍스트 검색 실패: {e}")
            return []

    def _parse_hits(self, data: dict, num_results: int) -> list[SearchResult]:
        results = []
        hits = data.get("hits", {}).get("hits", [])
        for hit in hits[:num_results]:
            src = hit.get("_source", {})
            accession = src.get("accession_no", "").replace("-", "")
            cik = src.get("entity_id", "")
            form_type = src.get("file_type", "")
            filed_at = src.get("period_of_report", src.get("file_date", ""))
            entity = src.get("display_names", [{}])
            entity_name = entity[0].get("name", "") if entity else ""

            filing_url = ""
            if accession and cik:
                filing_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{src.get('file_name', '')}"

            content = f"{form_type} - {entity_name} ({filed_at})\n{src.get('file_name', '')}"
            results.append(SearchResult(
                url=filing_url or f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}",
                title=f"[{form_type}] {entity_name}",
                content=content,
                source_type=self.source_type,
                relevance_score=hit.get("_score", 0.0),
                published_date=filed_at,
            ))
        logger.info(f"[sec] 검색 → {len(results)}건")
        return results

    # ── Form 4 전용: 임원 거래 직접 파싱 ──────────────────────

    async def fetch_insider_trades(
        self, ticker: str, limit: int = 5
    ) -> list[ExtractedContent]:
        """SEC Form 4 직접 파싱 — 임원 주식 거래 공식 원본 데이터.

        접근 순서:
        1. EFTS 전문 검색: Form 4 XML에 있는 <issuerTradingSymbol>로 검색
        2. 결과 없으면 회사 CIK 조회 → EDGAR 공시 목록에서 Form 4 추출
        """
        hits = await self._search_form4_by_ticker(ticker, limit * 2)

        # 결과가 적으면 CIK 기반으로도 시도
        if len(hits) < limit:
            cik = await self._get_company_cik(ticker)
            if cik:
                cik_hits = await self._search_form4_by_cik(cik, limit * 2)
                # 중복 accession 제거
                seen_acc = {h.get("accession_no") for h in hits}
                for h in cik_hits:
                    if h.get("accession_no") not in seen_acc:
                        hits.append(h)
                        seen_acc.add(h.get("accession_no"))

        if not hits:
            logger.info(f"[sec] {ticker} Form 4 검색 결과 없음")
            return []

        tasks = [self._fetch_and_parse_form4(h) for h in hits[:limit]]
        parsed_list = await asyncio.gather(*tasks, return_exceptions=True)

        contents: list[ExtractedContent] = []
        for p in parsed_list:
            if isinstance(p, dict) and p:
                text = self._format_form4_text(p)
                if text:
                    url = p.get("filing_url",
                                f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={ticker}&type=4")
                    name = p.get("owner_name", "임원")
                    contents.append(ExtractedContent(
                        url=url,
                        title=f"[SEC Form 4] {name} — {ticker}",
                        content=text,
                        domain="sec.gov",
                        word_count=len(text.split()),
                    ))

        logger.info(f"[sec] Form 4 파싱 완료: {len(contents)}건")
        return contents

    async def _get_company_cik(self, ticker: str) -> Optional[str]:
        """EDGAR 회사 검색으로 ticker → company CIK 조회.

        Form 4는 filer(내부자)가 제출하지만, EDGAR 회사 페이지에서도 조회 가능.
        CIK를 알면 더 정확한 Form 4 조회 가능.
        """
        try:
            async with self._make_client() as client:
                headers = {"User-Agent": SEC_USER_AGENT}
                # EDGAR 회사 검색 (10-K 제출사를 회사 CIK로 활용)
                params = {
                    "q": f'"{ticker}"',
                    "forms": "10-K",
                    "hits.hits.total.value": True,
                }
                resp = await self._get_with_retry(client, SEC_EFTS_URL,
                                                   headers=headers, params=params)
                if resp and resp.status_code == 200:
                    hits = resp.json().get("hits", {}).get("hits", [])
                    for hit in hits[:5]:
                        src = hit.get("_source", {})
                        entity = src.get("display_names", [{}])
                        ent_name = entity[0].get("name", "").upper() if entity else ""
                        # 회사명 또는 티커가 일치하면 CIK 반환
                        if (ticker.upper() in ent_name
                                or src.get("ticker_symbol", "").upper() == ticker.upper()):
                            cik = src.get("entity_id", "")
                            if cik:
                                logger.info(f"[sec] {ticker} CIK 조회: {cik}")
                                return cik
        except Exception as e:
            logger.debug(f"[sec] CIK 조회 실패: {e}")
        return None

    async def _search_form4_by_ticker(self, ticker: str, limit: int = 10) -> list[dict]:
        """EFTS 전문 검색으로 ticker 포함 Form 4 목록 조회.

        Form 4 XML에 <issuerTradingSymbol>INDI</issuerTradingSymbol> 포함 → 티커로 검색 가능.
        """
        today = datetime.utcnow().strftime("%Y-%m-%d")
        six_months_ago = (datetime.utcnow() - timedelta(days=180)).strftime("%Y-%m-%d")
        try:
            async with self._make_client() as client:
                headers = {"User-Agent": SEC_USER_AGENT}
                params = {
                    "q": f'"{ticker}"',
                    "forms": "4",
                    "dateRange": "custom",
                    "startdt": six_months_ago,
                    "enddt": today,
                }
                resp = await self._get_with_retry(client, SEC_EFTS_URL,
                                                   headers=headers, params=params)
                if resp is None or resp.status_code != 200:
                    return []
                hits = resp.json().get("hits", {}).get("hits", [])
                return [h.get("_source", {}) for h in hits[:limit]]
        except Exception as e:
            logger.error(f"[sec] Form 4 EFTS 검색 실패: {e}")
            return []

    async def _search_form4_by_cik(self, cik: str, limit: int = 10) -> list[dict]:
        """company CIK로 EDGAR 공시 목록에서 Form 4 조회.

        data.sec.gov/submissions API: 회사 제출 공시 중 Form 4 필터링.
        (주의: Form 4는 내부자 CIK로 제출되므로 이 방식은 보조 수단)
        """
        cik_padded = cik.zfill(10)
        url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
        try:
            async with self._make_client() as client:
                headers = {"User-Agent": SEC_USER_AGENT}
                resp = await self._get_with_retry(client, url, headers=headers)
                if not resp or resp.status_code != 200:
                    return []
                data = resp.json()
                recent = data.get("filings", {}).get("recent", {})
                forms = recent.get("form", [])
                accessions = recent.get("accessionNumber", [])
                dates = recent.get("filingDate", [])
                docs = recent.get("primaryDocument", [])

                results = []
                for i, form in enumerate(forms):
                    if form in ("4", "4/A") and len(results) < limit:
                        results.append({
                            "entity_id": cik,
                            "accession_no": accessions[i] if i < len(accessions) else "",
                            "file_date": dates[i] if i < len(dates) else "",
                            "period_of_report": dates[i] if i < len(dates) else "",
                            "file_name": docs[i] if i < len(docs) else "",
                        })
                return results
        except Exception as e:
            logger.debug(f"[sec] CIK 기반 Form 4 조회 실패: {e}")
            return []

    async def _fetch_and_parse_form4(self, src: dict) -> dict:
        """Form 4 hit source → XML 가져와 파싱."""
        cik = src.get("entity_id", "")
        accession = src.get("accession_no", "").replace("-", "")
        file_name = src.get("file_name", "")
        filed_date = src.get("period_of_report", src.get("file_date", ""))

        if not (cik and accession):
            return {}

        # XML URL 결정
        if file_name and file_name.lower().endswith(".xml"):
            xml_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{file_name}"
        else:
            xml_url = await self._find_xml_in_filing(cik, accession)

        if not xml_url:
            return {}

        try:
            async with self._make_client() as client:
                headers = {"User-Agent": SEC_USER_AGENT}
                resp = await self._get_with_retry(client, xml_url, headers=headers)
                if not resp or resp.status_code != 200:
                    return {}
                return self._parse_form4_xml(resp.text, xml_url, filed_date)
        except Exception as e:
            logger.debug(f"[sec] Form 4 XML 가져오기 실패 {xml_url}: {e}")
            return {}

    async def _find_xml_in_filing(self, cik: str, accession: str) -> str:
        """공시 인덱스에서 Form 4 XML 파일 찾기."""
        index_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/"
        try:
            async with self._make_client() as client:
                resp = await self._get_with_retry(
                    client, index_url,
                    headers={"User-Agent": SEC_USER_AGENT}
                )
                if not resp:
                    return ""
                xml_matches = re.findall(
                    r'href="(/Archives/edgar/data/[^"]+\.xml)"',
                    resp.text, re.IGNORECASE
                )
                if xml_matches:
                    return f"https://www.sec.gov{xml_matches[0]}"
        except Exception as e:
            logger.debug(f"[sec] 인덱스 파싱 실패: {e}")
        return ""

    def _parse_form4_xml(self, xml_text: str, filing_url: str, filed_date: str) -> dict:
        """Form 4 XML → 구조화된 거래 데이터."""
        try:
            root = ET.fromstring(xml_text)

            # 보고자 정보
            owner_name = owner_title = issuer_name = issuer_ticker = ""
            is_officer = is_director = is_10pct = False

            for owner in root.iter("reportingOwner"):
                name_el = owner.find(".//rptOwnerName")
                if name_el is not None and name_el.text:
                    owner_name = name_el.text.strip()
                title_el = owner.find(".//officerTitle")
                if title_el is not None and title_el.text:
                    owner_title = title_el.text.strip()
                is_officer = owner.findtext(".//isOfficer", "0") == "1"
                is_director = owner.findtext(".//isDirector", "0") == "1"
                is_10pct = owner.findtext(".//isTenPercentOwner", "0") == "1"

            for issuer in root.iter("issuer"):
                issuer_name = issuer.findtext(".//issuerName", "")
                issuer_ticker = issuer.findtext(".//issuerTradingSymbol", "")

            period = root.findtext(".//periodOfReport", filed_date)
            footnotes = [fn.text.strip() for fn in root.iter("footnote") if fn.text]
            fn_text = " ".join(footnotes).lower()

            # 비파생 거래
            transactions = []
            for txn in root.iter("nonDerivativeTransaction"):
                try:
                    security = (txn.findtext(".//securityTitle/value")
                                or txn.findtext(".//securityTitle") or "Common Stock")
                    txn_date = (txn.findtext(".//transactionDate/value")
                                or txn.findtext(".//transactionDate") or "")
                    txn_code = txn.findtext(".//transactionCode", "")

                    def _fval(tag):
                        el = txn.find(tag)
                        if el is not None and el.text:
                            try:
                                return float(el.text)
                            except ValueError:
                                pass
                        return None

                    shares = _fval(".//transactionShares/value") or 0
                    price = _fval(".//transactionPricePerShare/value")
                    post_shares = _fval(".//sharesOwnedFollowingTransaction/value")
                    acq_disp = txn.findtext(".//transactionAcquiredDisposedCode/value", "")

                    if shares > 0:
                        transactions.append({
                            "security": security,
                            "date": txn_date,
                            "code": txn_code,
                            "nature": self._classify_transaction(txn_code, fn_text),
                            "shares": shares,
                            "price": price,
                            "acquired_disposed": acq_disp,
                            "post_shares": post_shares,
                        })
                except Exception:
                    continue

            # 파생 거래 (옵션/RSU)
            deriv_transactions = []
            for dtxn in root.iter("derivativeTransaction"):
                try:
                    security = (dtxn.findtext(".//securityTitle/value")
                                or dtxn.findtext(".//securityTitle") or "")
                    txn_date = dtxn.findtext(".//transactionDate/value", "")
                    txn_code = dtxn.findtext(".//transactionCode", "")

                    def _dfval(tag):
                        el = dtxn.find(tag)
                        if el is not None and el.text:
                            try:
                                return float(el.text)
                            except ValueError:
                                pass
                        return None

                    shares = _dfval(".//transactionShares/value") or 0
                    conv_price = _dfval(".//conversionOrExercisePrice/value")
                    exp_date = dtxn.findtext(".//expirationDate/value", "")

                    if shares > 0 or txn_code in ("A", "M"):
                        deriv_transactions.append({
                            "security": security,
                            "date": txn_date,
                            "code": txn_code,
                            "shares": shares,
                            "exercise_price": conv_price,
                            "expiration_date": exp_date,
                        })
                except Exception:
                    continue

            return {
                "owner_name": owner_name,
                "owner_title": owner_title,
                "is_officer": is_officer,
                "is_director": is_director,
                "is_10pct": is_10pct,
                "issuer_name": issuer_name,
                "issuer_ticker": issuer_ticker,
                "period": period,
                "filed_date": filed_date,
                "transactions": transactions,
                "deriv_transactions": deriv_transactions,
                "footnotes": footnotes,
                "filing_url": filing_url,
            }
        except Exception as e:
            logger.debug(f"[sec] Form 4 XML 파싱 실패: {e}")
            return {}

    def _classify_transaction(self, code: str, footnote_text: str = "") -> str:
        """거래 코드 + 각주로 거래 성격 분류."""
        if code == "F":
            return "세금납부 원천징수 (sell-to-cover, 자발적 매도 아님)"
        if code == "M":
            return "주식매수선택권 행사 또는 RSU 베스팅"
        if code == "A":
            return "보상 수령 (RSU/옵션 grants)"
        if code == "P":
            return "자발적 공개시장 매수"
        if code == "G":
            return "증여"
        if code == "D":
            return "환수 (return to company)"
        if code == "S":
            if "10b5-1" in footnote_text:
                return "공개시장 매도 (Rule 10b5-1 사전 계획 매매)"
            if any(kw in footnote_text for kw in ["tax", "withholding", "cover"]):
                return "공개시장 매도 (세금납부 목적 추정)"
            if any(kw in footnote_text for kw in ["vest", "rsu", "restricted"]):
                return "공개시장 매도 (RSU 베스팅 후 처분)"
            return "공개시장 매도 (자발적)"
        return _TXN_CODE.get(code, f"거래 코드 {code}")

    def _format_form4_text(self, p: dict) -> str:
        """Form 4 파싱 결과 → 보고서용 텍스트."""
        if not p or not p.get("owner_name"):
            return ""

        role_parts = []
        if p.get("owner_title"):
            role_parts.append(p["owner_title"])
        elif p.get("is_officer"):
            role_parts.append("임원 (Officer)")
        elif p.get("is_director"):
            role_parts.append("이사 (Director)")
        elif p.get("is_10pct"):
            role_parts.append("10% 이상 주주")
        role = " | ".join(role_parts) or "관련자"

        company = p.get("issuer_name") or p.get("issuer_ticker", "")
        ticker = p.get("issuer_ticker", "")

        lines = [
            "【SEC Form 4 — 직접 공시 원본】",
            f"보고자: {p['owner_name']} ({role})",
            f"발행사: {company}" + (f" ({ticker})" if ticker and ticker != company else ""),
            f"제출일: {p.get('filed_date', '')} | 거래기간: {p.get('period', '')}",
            "",
        ]

        if p.get("transactions"):
            lines.append("거래 내역:")
            for t in p["transactions"]:
                direction = "취득(A)" if t.get("acquired_disposed") == "A" else "처분(D)"
                price_str = f"주당 ${t['price']:.4f}" if t.get("price") else "가격 미공시"
                post_str = (f" | 거래 후 보유: {t['post_shares']:,.0f}주"
                            if t.get("post_shares") else "")
                value_str = ""
                if t.get("price") and t.get("shares"):
                    value_str = f" (거래금액 약 ${t['price'] * t['shares']:,.0f})"

                lines.append(
                    f"  [{t.get('date', p.get('period', ''))}]"
                    f" {t.get('security', 'Common Stock')} {t['shares']:,.0f}주 {direction}"
                    f" | {t['nature']}"
                    f" | {price_str}{value_str}{post_str}"
                )

        if p.get("deriv_transactions"):
            lines.append("")
            lines.append("파생 거래 (옵션/RSU):")
            for dt in p["deriv_transactions"]:
                lines.append(
                    f"  [{dt.get('date', '')}] {dt.get('security', '')} {dt.get('shares', 0):,.0f}주"
                    + (f" | 행사가: ${dt['exercise_price']:.4f}" if dt.get("exercise_price") else "")
                    + (f" | 만료: {dt.get('expiration_date', '')}" if dt.get("expiration_date") else "")
                )

        if p.get("footnotes"):
            lines.append("")
            lines.append("각주 (10b5-1 계획 여부 등):")
            for fn in p["footnotes"][:5]:
                lines.append(f"  {fn[:400]}")

        lines.append(f"\n출처: {p.get('filing_url', '')}")
        return "\n".join(lines)

    async def get_filing_text(self, cik: str, accession_no: str, max_chars: int = 20000) -> str:
        """특정 공시 전문 가져오기 (레거시 호환)."""
        acc = accession_no.replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/"
        try:
            async with self._make_client() as client:
                headers = {"User-Agent": SEC_USER_AGENT}
                resp = await self._get_with_retry(client, url, headers=headers)
                if resp is None:
                    return ""
                html = resp.text
                doc_match = re.search(r'href="([^"]+\.htm[l]?)"', html, re.IGNORECASE)
                if not doc_match:
                    return ""
                doc_url = f"https://www.sec.gov{doc_match.group(1)}"
                doc_resp = await self._get_with_retry(client, doc_url, headers=headers)
                if doc_resp is None:
                    return ""
                text = re.sub(r'<[^>]+>', ' ', doc_resp.text)
                text = re.sub(r'\s+', ' ', text).strip()
                return text[:max_chars]
        except Exception as e:
            logger.error(f"[sec] 공시 텍스트 가져오기 실패: {e}")
            return ""
