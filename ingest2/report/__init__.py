"""§10 리포트 — 최종 Top-N 후보를 사람이 보는 화면(HTML)·구조(JSON)로 출력.

랭킹 결과를 콘솔 텍스트가 아니라 자기완결 HTML 대시보드로 렌더링해, 스토리/시그널이
실제로 의미 있게 뽑혔는지 눈으로 검증한다. 외부 의존 없음(인라인 CSS).
"""
from .render import render_html, write_report

__all__ = ["render_html", "write_report"]
