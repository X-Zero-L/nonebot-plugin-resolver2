from pathlib import Path
import re
from typing import Any

import httpx
from nonebot import logger
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, MessageSegment
from nonebot.params import PausePromptResult
from nonebot.typing import T_State

from ..config import NEED_UPLOAD, NICKNAME, PROXY, ytb_cookies_file
from ..constants import COMMON_TIMEOUT
from ..download.ytdlp import get_video_info, ytdlp_download_audio, ytdlp_download_video
from ..exception import handle_exception
from ..utils import keep_zh_en_num
from .filter import is_not_in_disabled_groups_by_ytb, is_not_in_do_not_download_media_groups
from .helper import obhelper
from .preprocess import KeyPatternMatched, on_keyword_regex

# https://youtu.be/EKkzbbLYPuI?si=K_S9zIp5g7DhigVz
# https://www.youtube.com/watch?v=1LnPnmKALL8&list=RD8AxpdwegNKc&index=2
ytb = on_keyword_regex(
    ("youtube.com", r"https?://(?:www\.)?youtube\.com/[A-Za-z\d\._\?%&\+\-=/#]+"),
    ("youtu.be", r"https?://(?:www\.)?youtu\.be/[A-Za-z\d\._\?%&\+\-=/#]+"),
)


def _format_count(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}K"
    return str(count)


def _format_duration(seconds: int) -> str:
    if seconds <= 0:
        return "未知"
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _format_upload_date(date_str: str) -> str:
    if len(date_str) == 8:
        return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    return date_str


async def _fetch_thumbnail(url: str) -> bytes | None:
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=COMMON_TIMEOUT, proxy=PROXY, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content
    except Exception:
        logger.warning(f"油管封面下载失败 | {url}")
        return None


@ytb.handle()
@handle_exception()
async def _(event: MessageEvent, state: T_State, searched: re.Match[str] = KeyPatternMatched()):
    if not is_not_in_disabled_groups_by_ytb(event):
        return

    url = searched.group(0)

    try:
        info_dict = await get_video_info(url, ytb_cookies_file)
        title: str = info_dict.get("title", "未知")
        channel: str = info_dict.get("channel") or "未知频道"
        uploader: str = info_dict.get("uploader") or channel
        view_count = int(info_dict.get("view_count") or 0)
        like_count = int(info_dict.get("like_count") or 0)
        duration = int(info_dict.get("duration") or 0)
        upload_date: str = info_dict.get("upload_date") or ""
        description: str = info_dict.get("description") or ""

        thumbnail: str = info_dict.get("thumbnail") or ""
        height = int(info_dict.get("height") or 0)
        width = int(info_dict.get("width") or 0)
        if not (height and width):
            for fmt in info_dict.get("formats") or []:
                h = fmt.get("height")
                w = fmt.get("width")
                if h and w:
                    height = int(h)
                    width = int(w)
                    break
        if height and width and height > width and "maxresdefault" in thumbnail:
            thumbnail = thumbnail.replace("maxresdefault", "oardefault")

    except Exception:
        logger.exception(f"油管标题获取失败 | {url}")
        await ytb.finish(f"{NICKNAME}解析 | 油管 - 标题获取出错")

    segments: list[str | MessageSegment] = [f"{NICKNAME}解析 | 油管 - {title}"]
    if thumb := await _fetch_thumbnail(thumbnail):
        segments.append(MessageSegment.image(thumb))

    desc = description.strip()
    if desc:
        segments.append(f"📝 简介：{desc[:200]}{'…' if len(desc) > 200 else ''}")

    segments.append(
        "\\n".join(
            [
                f"📺 频道：{uploader}",
                f"👀 观看：{_format_count(view_count)} 次",
                f"👍 点赞：{_format_count(like_count)}",
                f"⏱️ 时长：{_format_duration(duration)}",
                f"📅 发布：{_format_upload_date(upload_date)}" if upload_date else "📅 发布：未知",
                f"🔗 链接：{url}",
            ]
        )
    )
    await obhelper.send_segments(segments)

    state["url"] = url
    state["title"] = title

    if is_not_in_do_not_download_media_groups(event):
        await ytb.pause("您需要下载音频(0)，还是视频(1)")


@ytb.handle()
async def _(
    bot: Bot,
    event: MessageEvent,
    state: T_State,
    pause_result: Any = PausePromptResult(),
):
    # 回应用户
    await bot.call_api("set_msg_emoji_like", message_id=event.message_id, emoji_id="282")
    # 撤回 选择类型 的 prompt
    await bot.delete_msg(message_id=pause_result["message_id"])
    # 获取 url 和 title
    url: str = state["url"]
    title: str = state["title"]
    # 下载视频或音频
    video_path: Path | None = None
    audio_path: Path | None = None
    # 判断是否下载视频
    type = event.message.extract_plain_text().strip()
    is_video = type == "1"
    try:
        if is_video:
            video_path = await ytdlp_download_video(url, ytb_cookies_file)
        else:
            audio_path = await ytdlp_download_audio(url, ytb_cookies_file)
    except Exception:
        media_type = "视频" if is_video else "音频"
        logger.exception(f"{media_type}下载失败 | {url}")
        await ytb.finish(f"{media_type}下载失败", reply_message=True)
    # 发送视频或音频
    if video_path:
        await ytb.send(obhelper.video_seg(video_path))
    elif audio_path:
        await ytb.send(obhelper.record_seg(audio_path))
        if NEED_UPLOAD:
            file_name = f"{keep_zh_en_num(title)}.flac"
            await ytb.send(obhelper.file_seg(audio_path, file_name))
