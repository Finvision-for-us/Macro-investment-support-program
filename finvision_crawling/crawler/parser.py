"""
Telethon Message 객체 → dict 변환.
텍스트, 발신자, 포워드, 엔티티(URL/mention/hashtag 등) 완전 추출.
"""
from datetime import timezone
from telethon.tl.types import (
    Message, MessageMediaPhoto, MessageMediaDocument,
    MessageMediaWebPage, MessageMediaGeo, MessageMediaContact,
    MessageMediaPoll, MessageMediaDice, MessageMediaGame,
    DocumentAttributeVideo, DocumentAttributeAudio,
    DocumentAttributeFilename, DocumentAttributeSticker,
    DocumentAttributeAnimated,
    MessageEntityUrl, MessageEntityTextUrl, MessageEntityMention,
    MessageEntityHashtag, MessageEntityCashtag, MessageEntityBotCommand,
    MessageEntityEmail, MessageEntityPhone,
    WebPage, GeoPoint, PeerUser, PeerChannel, PeerChat,
)
from telethon.utils import get_display_name


def _utc_str(dt) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def parse_sender(msg: Message) -> tuple[int | None, str | None, str | None]:
    """(sender_id, sender_name, sender_username)"""
    if msg.sender is None:
        return None, None, None
    sender_id = msg.sender_id
    name = get_display_name(msg.sender)
    username = getattr(msg.sender, "username", None)
    return sender_id, name, username


def parse_forward(msg: Message) -> tuple[int | None, str | None]:
    """(fwd_from_id, fwd_from_name)"""
    if msg.fwd_from is None:
        return None, None
    fwd = msg.fwd_from
    fwd_id = None
    fwd_name = None
    if hasattr(fwd, "from_id") and fwd.from_id is not None:
        peer = fwd.from_id
        if isinstance(peer, PeerChannel):
            fwd_id = peer.channel_id
        elif isinstance(peer, PeerUser):
            fwd_id = peer.user_id
        elif isinstance(peer, PeerChat):
            fwd_id = peer.chat_id
    if hasattr(fwd, "from_name"):
        fwd_name = fwd.from_name
    return fwd_id, fwd_name


def parse_entities(msg: Message, channel_id: int) -> list[dict]:
    """메시지 내 URL, mention, hashtag 등 추출."""
    result = []
    if not msg.entities:
        return result

    text = msg.raw_text or ""
    type_map = {
        MessageEntityUrl:        "url",
        MessageEntityTextUrl:    "url",
        MessageEntityMention:    "mention",
        MessageEntityHashtag:    "hashtag",
        MessageEntityCashtag:    "cashtag",
        MessageEntityBotCommand: "bot_command",
        MessageEntityEmail:      "email",
        MessageEntityPhone:      "phone",
    }
    for ent in msg.entities:
        etype = type_map.get(type(ent))
        if etype is None:
            continue
        value = text[ent.offset: ent.offset + ent.length]
        if etype == "url" and hasattr(ent, "url") and ent.url:
            value = ent.url
        result.append({
            "message_id": msg.id,
            "channel_id": channel_id,
            "type":       etype,
            "value":      value,
            "offset":     ent.offset,
            "length":     ent.length,
        })
    return result


def parse_message(msg: Message, channel_id: int) -> dict:
    sender_id, sender_name, sender_username = parse_sender(msg)
    fwd_id, fwd_name = parse_forward(msg)
    # grouped_id 있으면 앨범 묶음 → post_id = grouped_id
    # 없으면 단일 메시지 → post_id = message_id
    post_id = msg.grouped_id if msg.grouped_id else msg.id
    return {
        "id":               msg.id,
        "channel_id":       channel_id,
        "post_id":          post_id,
        "date":             _utc_str(msg.date),
        "sender_id":        sender_id,
        "sender_name":      sender_name,
        "sender_username":  sender_username,
        "text":             msg.text or "",
        "raw_text":         msg.raw_text or "",
        "reply_to_msg_id":  msg.reply_to_msg_id,
        "fwd_from_id":      fwd_id,
        "fwd_from_name":    fwd_name,
        "grouped_id":       msg.grouped_id,
        "views":            msg.views,
        "forwards":         msg.forwards,
        "edit_date":        _utc_str(msg.edit_date),
        "pinned":           int(bool(msg.pinned)),
    }


def parse_media_meta(msg: Message, channel_id: int) -> dict | None:
    """미디어 메타 dict 반환. 미디어 없으면 None."""
    base = {
        "message_id":     msg.id,
        "channel_id":     channel_id,
        "media_type":     None,
        "mime_type":      None,
        "file_name":      None,
        "file_size":      None,
        "duration_sec":   None,
        "width":          None,
        "height":         None,
        "local_path":     None,
        "remote_file_id": None,
        "thumbnail_path": None,
        "url":            None,
        "title":          None,
        "description":    None,
    }
    media = msg.media
    if media is None:
        return None

    if isinstance(media, MessageMediaPhoto):
        base["media_type"] = "photo"
        if media.photo:
            base["remote_file_id"] = str(media.photo.id)
            # 최대 해상도 size
            sizes = getattr(media.photo, "sizes", [])
            for s in reversed(sizes):
                if hasattr(s, "w") and hasattr(s, "h"):
                    base["width"]  = s.w
                    base["height"] = s.h
                    break
        return base

    if isinstance(media, MessageMediaDocument):
        doc = media.document
        if doc is None:
            return None
        base["remote_file_id"] = str(doc.id)
        base["mime_type"]      = doc.mime_type
        base["file_size"]      = doc.size
        base["media_type"]     = "document"

        for attr in doc.attributes:
            if isinstance(attr, DocumentAttributeSticker):
                base["media_type"] = "sticker"
            elif isinstance(attr, DocumentAttributeAnimated):
                if base["media_type"] != "sticker":
                    base["media_type"] = "gif"
            elif isinstance(attr, DocumentAttributeVideo):
                base["media_type"] = "video"
                base["duration_sec"] = attr.duration
                base["width"]  = attr.w
                base["height"] = attr.h
                if attr.round_message:
                    base["media_type"] = "video_note"
            elif isinstance(attr, DocumentAttributeAudio):
                base["media_type"] = "voice" if attr.voice else "audio"
                base["duration_sec"] = attr.duration
            elif isinstance(attr, DocumentAttributeFilename):
                base["file_name"] = attr.file_name
        return base

    if isinstance(media, MessageMediaWebPage):
        wp = media.webpage
        if isinstance(wp, WebPage):
            base["media_type"]   = "webpage"
            base["url"]         = wp.url
            base["title"]       = wp.title
            base["description"] = wp.description
            if wp.photo:
                base["remote_file_id"] = str(wp.photo.id)
            return base
        return None  # WebPageEmpty / WebPageNotModified — 저장할 정보 없음

    if isinstance(media, MessageMediaGeo):
        geo: GeoPoint = media.geo
        base["media_type"] = "geo"
        base["url"] = f"geo:{geo.lat},{geo.long}"
        return base

    if isinstance(media, MessageMediaContact):
        base["media_type"] = "contact"
        base["title"]      = f"{media.first_name} {media.last_name}".strip()
        base["url"]        = media.phone_number
        return base

    if isinstance(media, MessageMediaPoll):
        base["media_type"] = "poll"
        base["title"]      = media.poll.question if media.poll else None
        return base

    if isinstance(media, MessageMediaDice):
        base["media_type"] = "dice"
        base["title"]      = f"{media.emoticon}={media.value}"
        return base

    if isinstance(media, MessageMediaGame):
        base["media_type"] = "game"
        base["title"]      = media.game.title if media.game else None
        return base

    # 미처리 미디어 타입도 타입명만 기록
    base["media_type"] = type(media).__name__
    return base
