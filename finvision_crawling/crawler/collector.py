"""
채널/그룹 메시지 수집 본체.
- 과거 메시지 일괄 수집 (history)
- 실시간 이벤트 핸들러 (new_message)
- 미디어 파일 다운로드
"""
import os
import asyncio
import logging
from datetime import datetime, timezone

from telethon import TelegramClient, events
from telethon.tl.types import Channel, Chat, User, Message
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.messages import GetFullChatRequest
from telethon.errors import FloodWaitError, ChannelPrivateError

import config
from crawler.parser import parse_message, parse_media_meta, parse_entities
from crawler import storage

log = logging.getLogger(__name__)

MB = 1024 * 1024


# ── 채널 정보 저장 ─────────────────────────────────────────────────────────────

async def register_channel(client: TelegramClient, target: str) -> dict | None:
    try:
        entity = await client.get_entity(target)
    except Exception as e:
        log.error(f"채널 조회 실패 [{target}]: {e}")
        return None

    if isinstance(entity, Channel):
        ch_type = "channel" if entity.broadcast else "supergroup"
        ch = {
            "id":        entity.id,
            "username":  entity.username,
            "title":     entity.title,
            "type":      ch_type,
            "joined_at": datetime.now(timezone.utc).isoformat(),
            "added_by":  config.PHONE,
        }
    elif isinstance(entity, Chat):
        ch = {
            "id":        entity.id,
            "username":  None,
            "title":     entity.title,
            "type":      "group",
            "joined_at": datetime.now(timezone.utc).isoformat(),
            "added_by":  config.PHONE,
        }
    elif isinstance(entity, User):
        ch = {
            "id":        entity.id,
            "username":  entity.username,
            "title":     f"{entity.first_name or ''} {entity.last_name or ''}".strip(),
            "type":      "user",
            "joined_at": datetime.now(timezone.utc).isoformat(),
            "added_by":  config.PHONE,
        }
    else:
        log.warning(f"알 수 없는 엔티티 타입: {type(entity)}")
        return None

    await storage.upsert_channel(ch)
    log.info(f"채널 등록: [{ch['type']}] {ch['title']} (id={ch['id']})")
    return ch


# ── 미디어 다운로드 ────────────────────────────────────────────────────────────

def _media_save_dir(channel_id: int, post_id: int) -> str:
    """포스트별 폴더: media/{channel_id}/{post_id}/"""
    d = os.path.join(config.MEDIA_DIR, str(channel_id), str(post_id))
    os.makedirs(d, exist_ok=True)
    return d


async def download_media(client: TelegramClient, msg: Message,
                         meta: dict) -> str | None:
    """미디어 다운로드 후 로컬 경로 반환. 실패/크기초과 시 None."""
    if not config.COLLECT_MEDIA:
        return None

    size = meta.get("file_size") or 0
    if size and size > config.MEDIA_MAX_SIZE_MB * MB:
        log.info(f"크기 초과 스킵 (msg={msg.id}, size={size//MB}MB)")
        return None

    media_type = meta.get("media_type", "etc")
    if media_type in ("webpage", "geo", "contact", "poll", "dice", "game"):
        return None

    save_dir = _media_save_dir(meta["channel_id"], meta["post_id"])

    try:
        path = await client.download_media(msg, file=save_dir)
        if path:
            log.debug(f"다운로드 완료: {path}")
        return path
    except FloodWaitError as e:
        log.warning(f"FloodWait {e.seconds}초 대기")
        await asyncio.sleep(e.seconds)
        return await download_media(client, msg, meta)
    except Exception as e:
        log.error(f"미디어 다운로드 실패 (msg={msg.id}): {e}")
        return None




# ── 단일 메시지 처리 ───────────────────────────────────────────────────────────

async def process_message(client: TelegramClient, msg: Message, channel_id: int):
    if not isinstance(msg, Message):
        return

    msg_dict = parse_message(msg, channel_id)
    post_id = msg_dict["post_id"]
    await storage.insert_message(msg_dict)

    entities = parse_entities(msg, channel_id)
    for ent in entities:
        ent["post_id"] = post_id
        await storage.insert_entity(ent)

    if msg.media:
        meta = parse_media_meta(msg, channel_id)
        if meta:
            meta["post_id"] = post_id
            meta["thumbnail_path"] = None  # 썸네일 사용 안 함
            local_path = await download_media(client, msg, meta)
            meta["local_path"] = local_path
            await storage.insert_media(meta)


# ── 과거 메시지 일괄 수집 ──────────────────────────────────────────────────────

async def collect_history(client: TelegramClient, channel: dict):
    channel_id = channel["id"]
    last_id = await storage.get_last_message_id(channel_id)

    log.info(f"과거 수집 시작: {channel['title']} (last_id={last_id})")
    count = 0
    max_id_seen = last_id

    try:
        entity = await client.get_entity(channel_id)
        async for msg in client.iter_messages(
            entity,
            limit=config.HISTORY_LIMIT or None,
        ):
            await process_message(client, msg, channel_id)
            count += 1
            if msg.id > max_id_seen:
                max_id_seen = msg.id

            if count % 10 == 0:
                await storage.update_collection_state(channel_id, max_id_seen, 0)
            if count % 200 == 0:
                log.info(f"  [{channel['title']}] {count}개 수집 중...")
                await asyncio.sleep(0.5)   # rate limit 배려

    except FloodWaitError as e:
        log.warning(f"FloodWait {e.seconds}초")
        await asyncio.sleep(e.seconds)
    except ChannelPrivateError:
        log.error(f"비공개 채널 접근 불가: {channel['title']}")
    except Exception as e:
        log.error(f"수집 오류 [{channel['title']}]: {e}")

    await storage.update_collection_state(channel_id, max_id_seen, count)
    log.info(f"과거 수집 완료: {channel['title']} → {count}개")


# ── 실시간 이벤트 등록 ─────────────────────────────────────────────────────────

def register_realtime_handler(client: TelegramClient, channel_ids: list[int]):
    """지정 채널들의 신규 메시지를 실시간으로 수집."""

    @client.on(events.NewMessage(chats=channel_ids))
    async def _handler(event):
        msg: Message = event.message
        channel_id = event.chat_id
        # supergroup은 음수 ID를 사용하기도 함 → 절대값 통일 없이 원본 사용
        log.info(f"[실시간] msg={msg.id} channel={channel_id}")
        await process_message(client, msg, channel_id)
        await storage.update_collection_state(channel_id, msg.id, 1)

    log.info(f"실시간 핸들러 등록 완료 ({len(channel_ids)}개 채널)")
