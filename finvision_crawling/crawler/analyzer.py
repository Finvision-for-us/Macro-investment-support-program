# -*- coding: utf-8 -*-
"""
Gemini 멀티모달 분석기.
포스트 단위로 텍스트 + 이미지를 하나의 Gemini 호출로 분석.
- 영상(video) 무시
- 썸네일 무시
- 이미지(photo)만 텍스트와 함께 전송
"""
import os
import asyncio
import logging
from datetime import datetime, timezone

from google import genai
from google.genai import types
from PIL import Image

import config
from crawler import storage

log = logging.getLogger(__name__)

GEMINI_MODEL = "gemini-2.0-flash"

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

DEFAULT_PROMPT = """아래는 텔레그램 채널의 포스트입니다.
텍스트와 첨부 이미지를 함께 분석해서 다음을 한국어로 정리해주세요:

1. 핵심 내용 요약 (3줄 이내)
2. 이미지에서 읽힌 주요 정보 (텍스트, 수치, 차트 등)
3. 전체 포스트의 의미/맥락
4. 투자/시장 관련 시사점 (있다면)"""


def _is_image(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower()
    return ext in IMAGE_EXTENSIONS


def _extract_image_paths(media_list: str) -> list[str]:
    """media_list 문자열에서 photo 타입 이미지 경로만 추출."""
    if not media_list:
        return []
    paths = []
    for item in media_list.split("|"):
        if not item:
            continue
        # 형식: "photo:/path/to/file.jpg"
        if item.startswith("photo:"):
            path = item[6:]
            if path and os.path.exists(path) and _is_image(path):
                paths.append(path)
    return paths


def _load_images(paths: list[str]) -> list[Image.Image]:
    images = []
    for p in paths:
        try:
            img = Image.open(p)
            img.load()
            images.append(img)
        except Exception as e:
            log.warning(f"이미지 로드 실패 [{p}]: {e}")
    return images


def _analyze_sync(client: genai.Client, parts: list) -> str:
    """동기 Gemini 호출 (asyncio.to_thread 로 감쌈)."""
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=parts,
    )
    return response.text


async def analyze_post(client: genai.Client, post: dict,
                       prompt: str = DEFAULT_PROMPT) -> str | None:
    """포스트 하나를 Gemini로 분석. 결과 텍스트 반환."""
    text = (post.get("text") or "").strip()
    image_paths = _extract_image_paths(post.get("media_list") or "")
    images = _load_images(image_paths)

    if not text and not images:
        log.info(f"post_id={post['post_id']} 내용 없음 스킵")
        return None

    # Gemini parts: 프롬프트 → 텍스트(있으면) → 이미지들
    parts: list = [prompt]
    if text:
        parts.append(f"\n\n[포스트 텍스트]\n{text}")
    parts.extend(images)

    try:
        result = await asyncio.to_thread(_analyze_sync, client, parts)
        return result
    except Exception as e:
        log.error(f"Gemini 분석 실패 (post_id={post['post_id']}): {e}")
        return None


async def run_analysis(
    channel_id: int | None = None,
    prompt: str = DEFAULT_PROMPT,
    delay_sec: float = 2.0,
):
    """미분석 포스트 전체 순차 분석."""
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError(".env 에 GOOGLE_API_KEY 또는 GEMINI_API_KEY 가 없습니다.")

    client = genai.Client(api_key=api_key)
    posts = await storage.get_unanalyzed_posts(channel_id)
    log.info(f"미분석 포스트 {len(posts)}개 분석 시작")

    for i, post in enumerate(posts, 1):
        post_id = post["post_id"]
        image_paths = _extract_image_paths(post.get("media_list") or "")
        image_count = len(_load_images(image_paths))
        has_text = int(bool((post.get("text") or "").strip()))

        log.info(f"[{i}/{len(posts)}] post_id={post_id} | 텍스트={bool(has_text)} | 이미지={image_count}개")

        result = await analyze_post(client, post, prompt)
        if result is None:
            continue

        await storage.upsert_analysis({
            "post_id":     post_id,
            "channel_id":  post["channel_id"],
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
            "model":       GEMINI_MODEL,
            "prompt":      prompt,
            "result":      result,
            "image_count": image_count,
            "has_text":    has_text,
        })
        log.info(f"  분석 저장 완료 (post_id={post_id})")

        if i < len(posts):
            await asyncio.sleep(delay_sec)

    log.info("전체 분석 완료")
