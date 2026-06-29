"""SEC EDGAR 수집 어댑터 (trust_tier=1, 최상위 신뢰).

EDGAR "getcurrent" Atom 피드(폼타입별 최신 제출)를 폼타입마다 가져온다.
주의:
- SEC는 연락처가 담긴 User-Agent를 요구한다(없으면 403). env SEC_USER_AGENT로 교체 가능.
- getcurrent의 type= 는 접두어 과매칭이 있다(type=4 가 425를 끌어옴) → 카테고리 term으로
  정확 매칭해 거른다. type 값의 공백은 URL 인코딩 필요("SC 13D").
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
from dataclasses import dataclass, field
from datetime import UTC, datetime

import feedparser

from ..schema import NewsItem, RawRecord
from .base import BaseCollector

_GETCURRENT = (
    "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent"
    "&type={type}&company=&dateb=&owner=include&count=100&output=atom"
)

# SEC 공정접근 정책상 식별 가능한 연락처 필요. 실제 운영 시 env로 본인 연락처 지정 권장.
DEFAULT_UA = "finvision/0.1 ingest2 (contact: pqqpqqpqqpqq7663@gmail.com)"
UA = os.environ.get("SEC_USER_AGENT", DEFAULT_UA)

_TITLE_RE = re.compile(r"^(?P<form>.+?) - (?P<company>.+?) \((?P<cik>\d{10})\)")


@dataclass(frozen=True)
class FormSpec:
    query: str                              # getcurrent type= 파라미터
    exact: frozenset[str] = field(default_factory=frozenset)   # 정확 매칭 term
    prefixes: tuple[str, ...] = ()          # 접두어 매칭 term

    def accepts(self, form: str) -> bool:
        return form in self.exact or any(form.startswith(p) for p in self.prefixes)


# 넓게(D 결정): 8-K, S-1, 4(내부자), SC 13D(대량보유), 10-Q
FORMS: list[FormSpec] = [
    FormSpec("8-K", prefixes=("8-K",)),
    FormSpec("S-1", prefixes=("S-1",)),
    FormSpec("4", exact=frozenset({"4", "4/A"})),
    FormSpec("SC 13D", prefixes=("SC 13D",)),
    FormSpec("10-Q", prefixes=("10-Q",)),
]


def _parsed_dt(entry) -> datetime | None:
    t = entry.get("updated_parsed") or entry.get("published_parsed")
    return datetime(*t[:6], tzinfo=UTC) if t else None


def _form_term(entry) -> str:
    tags = entry.get("tags") or []
    if tags:
        return tags[0].get("term", "")
    m = _TITLE_RE.match(entry.get("title", ""))
    return m.group("form") if m else ""


def _accession(entry) -> str:
    eid = entry.get("id", "")
    if "accession-number=" in eid:
        return eid.split("accession-number=")[-1]
    return entry.get("link", "")


def _entry_to_dict(entry) -> dict:
    """원본 보관용 JSON-safe dict."""
    m = _TITLE_RE.match(entry.get("title", ""))
    dt = _parsed_dt(entry)
    return {
        "accession": _accession(entry),
        "form": _form_term(entry),
        "company": m.group("company") if m else "",
        "cik": m.group("cik") if m else "",
        "title": entry.get("title", ""),
        "link": entry.get("link", ""),
        "summary": entry.get("summary", ""),
        "updated": dt.isoformat() if dt else None,
    }


def _event_for_form(form: str) -> str:
    if form.startswith("S-1"):
        return "ipo"
    if form.startswith("10-Q"):
        return "earnings"
    return "filing"


class SecEdgarCollector(BaseCollector):
    source_id = "sec_edgar"
    trust_tier = 1

    def __init__(self, forms: list[FormSpec] | None = None, user_agent: str = UA) -> None:
        self.forms = forms if forms is not None else FORMS
        self.user_agent = user_agent

    def fetch(self, since: datetime, until: datetime) -> list[RawRecord]:
        now = datetime.now(UTC)
        out: list[RawRecord] = []
        seen: set[str] = set()
        for spec in self.forms:
            url = _GETCURRENT.format(type=urllib.parse.quote(spec.query))
            parsed = feedparser.parse(url, agent=self.user_agent)
            for entry in parsed.entries:
                if not spec.accepts(_form_term(entry)):   # 접두어 과매칭 제거
                    continue
                dt = _parsed_dt(entry)
                if dt is not None and not (since <= dt < until):
                    continue
                acc = _accession(entry)
                if not acc or acc in seen:
                    continue
                seen.add(acc)
                out.append(
                    RawRecord(
                        source_id=self.source_id,
                        source_native_id=acc,
                        content_type="json",
                        payload=json.dumps(_entry_to_dict(entry), ensure_ascii=False),
                        url=entry.get("link"),
                        fetched_at=now,
                    )
                )
        return out

    def normalize(self, raw: RawRecord) -> NewsItem:
        d = json.loads(raw.payload)
        published = datetime.fromisoformat(d["updated"]) if d.get("updated") else None
        return NewsItem(
            item_id=self.make_item_id(raw.source_id, raw.source_native_id),
            source_id=raw.source_id,
            source_native_id=raw.source_native_id,
            trust_tier=self.trust_tier,
            title=d.get("title", ""),
            summary=d.get("summary", ""),
            url=d.get("link") or (raw.url or ""),
            canonical_url=d.get("link") or raw.url,
            source_name="SEC EDGAR",
            published_at=published,
            collected_at=raw.fetched_at,
            language="en",
            raw_category=d.get("form", ""),
            companies=[d["company"]] if d.get("company") else [],
            event_type=_event_for_form(d.get("form", "")),
            source_meta={"cik": d.get("cik", "")},  # 분류 단계의 CIK→티커용
        )
