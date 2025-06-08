import json
from pathlib import Path
import re
from typing import Any

import aiohttp
from nonebot import logger, on_keyword
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.params import PausePromptResult
from nonebot.rule import Rule
from nonebot.typing import T_State
from nonebot_plugin_alconna import UniMessage

from ..config import NEED_UPLOAD, NICKNAME, PROXY, ytb_cookies_file
from ..download.utils import keep_zh_en_num
from ..download.ytdlp import get_video_info, ytdlp_download_audio, ytdlp_download_video
from ..exception import handle_exception
from .filter import is_not_in_disabled_groups, is_not_in_do_not_download_media_groups
from .helper import get_file_seg, get_record_seg, get_video_seg

ytb = on_keyword(keywords={"youtube.com", "youtu.be"}, rule=Rule(is_not_in_disabled_groups))


@ytb.handle()
@handle_exception()
async def _(event: MessageEvent, state: T_State):
    message = event.message.extract_plain_text().strip()
    pattern = (
        # https://youtu.be/EKkzbbLYPuI?si=K_S9zIp5g7DhigVz
        # https://www.youtube.com/watch?v=1LnPnmKALL8&list=RD8AxpdwegNKc&index=2
        r"(?:https?://)?(?:www\.)?(?:youtube\.com|youtu\.be)/[A-Za-z\d\._\?%&\+\-=/#]+"
    )
    matched = re.search(pattern, message)
    if not matched:
        logger.warning(f"{message} 中的链接不支持，已忽略")
        await ytb.finish()

    url = matched.group(0)
    try:
        info_dict = await get_video_info(url, ytb_cookies_file)
        # logger.info(info_dict)
        # with open(Path(__file__).parent / "info_dict.json", "w") as f:
        #     json.dump(info_dict, f)

        # 提取视频信息
        title = info_dict.get("title", "未知")
        channel = info_dict.get("channel", "未知频道")
        uploader = info_dict.get("uploader", channel)
        view_count = info_dict.get("view_count", 0)
        like_count = info_dict.get("like_count", 0)
        duration = info_dict.get("duration", 0)
        upload_date = info_dict.get("upload_date", "")
        description = info_dict.get("description", "")
        formats = info_dict.get("formats", [])
        height, width = 0, 0
        for format in formats:
            height = format.get("height", 0)
            width = format.get("width", 0)
            if height and width:
                break
        thumbnail = info_dict.get("thumbnail", "")
        if height > width:
            thumbnail = thumbnail.replace("maxresdefault", "oardefault")

        # 格式化数字
        def format_count(count):
            if count >= 1000000:
                return f"{count / 1000000:.1f}M"
            elif count >= 1000:
                return f"{count / 1000:.1f}K"
            return str(count)

        # 格式化时长
        def format_duration(seconds):
            if seconds <= 0:
                return "未知"
            minutes, seconds = divmod(int(seconds), 60)
            hours, minutes = divmod(minutes, 60)
            if hours > 0:
                return f"{hours}:{minutes:02d}:{seconds:02d}"
            else:
                return f"{minutes}:{seconds:02d}"

        # 格式化上传日期
        def format_upload_date(date_str):
            if len(date_str) == 8:  # YYYYMMDD
                return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
            return date_str

    except Exception as e:
        await ytb.finish(f"油管 - 标题获取出错: {e}")

    # 构建丰富的消息内容
    async def download_thumbnail(url: str) -> bytes:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, proxy=PROXY) as resp:
                return await resp.read()

    # 构建详细信息文本
    title_text = f"🎬 {title}"
    description_text = f"📝 简介：{description}\n"
    channel_info = f"""📺 频道：{uploader}
👀 观看：{format_count(view_count)} 次
👍 点赞：{format_count(like_count)}
⏱️ 时长：{format_duration(duration)}
📅 发布：{format_upload_date(upload_date)}
🔗 链接：{url}
"""

    msg = (
        UniMessage(title_text)
        + UniMessage.image(raw=await download_thumbnail(thumbnail))
        + UniMessage(description_text)
        + UniMessage(channel_info)
    )
    await msg.send()
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
    except Exception as e:
        media_type = "视频" if is_video else "音频"
        logger.error(f"{media_type}下载失败 | {url} | {e}", exc_info=True)
        await ytb.finish(f"{media_type}下载失败", reply_message=True)
    # 发送视频或音频
    if video_path:
        await ytb.send(get_video_seg(video_path))
    elif audio_path:
        await ytb.send(get_record_seg(audio_path))
        if NEED_UPLOAD:
            file_name = f"{keep_zh_en_num(title)}.flac"
            await ytb.send(get_file_seg(audio_path, file_name))
