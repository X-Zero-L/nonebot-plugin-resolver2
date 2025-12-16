import asyncio
from collections import defaultdict
import re
import time

from nonebot import logger, on_command, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent
from nonebot.adapters.onebot.v11.exception import ActionFailed
from nonebot.params import CommandArg

from ..config import DURATION_MAXIMUM, NEED_UPLOAD, NICKNAME, plugin_cache_dir
from ..download import (
    download_file_by_stream,
    download_img,
    download_imgs_without_raise,
    download_video,
    encode_video_to_h264,
    merge_av,
)
from ..download.utils import keep_zh_en_num
from ..exception import ParseException, handle_exception
from ..parsers import BilibiliParser, get_redirect_url
from .filter import (
    is_in_bili_auto_download_when_disabled_groups,
    is_not_in_disabled_groups,
    is_not_in_disabled_groups_by_bilibili,
    is_not_in_do_not_download_media_groups,
)
from .helper import get_file_seg, get_img_seg, get_record_seg, get_video_seg, send_segments
from .preprocess import ExtractText, Keyword, r_keywords

# ==================== Matchers ====================

bilibili = on_message(
    rule=is_not_in_disabled_groups_by_bilibili
    & r_keywords("bilibili", "bili2233", "b23", "BV", "av")
    & is_not_in_disabled_groups,
    priority=5,
    block=False,
)

bili_music = on_command(cmd="bm", block=True)


def bili_auto_download_rule(event: MessageEvent) -> bool:
    """B站解析关闭但开启自动下载的规则"""
    return (
        not is_not_in_disabled_groups_by_bilibili(event)  # b站解析已关闭
        and is_in_bili_auto_download_when_disabled_groups(event)  # 但该群开启了自动下载
        and is_not_in_disabled_groups(event)
        and is_not_in_do_not_download_media_groups(event)
    )


# 当b站解析关闭时，仍然可以下载视频的matcher
bilibili_auto_download = on_message(
    rule=bili_auto_download_rule & r_keywords("bilibili", "bili2233", "b23", "BV", "av"),
    priority=6,
    block=False,
)

# ==================== Patterns ====================

PATTERNS: dict[str, re.Pattern] = {
    "BV": re.compile(r"(BV[1-9a-zA-Z]{10})(?:\s)?(\d{1,3})?"),
    "av": re.compile(r"av(\d{6,})(?:\s)?(\d{1,3})?"),
    "/BV": re.compile(r"/(BV[1-9a-zA-Z]{10})()"),
    "/av": re.compile(r"/av(\d{6,})()"),
    "b23": re.compile(r"https?://b23\.tv/[A-Za-z\d\._?%&+\-=/#]+()()"),
    "bili2233": re.compile(r"https?://bili2233\.cn/[A-Za-z\d\._?%&+\-=/#]+()()"),
    "bilibili": re.compile(r"https?://(?:space|www|live|m|t)?\.?bilibili\.com/[A-Za-z\d\._?%&+\-=/#]+()()"),
}

parser = BilibiliParser()

# ==================== 并发控制和速率限制 ====================

# 处理锁，防止同一群组同时处理同一视频
processing_locks: dict[tuple[int, str], asyncio.Lock] = defaultdict(asyncio.Lock)
# 记录最后处理时间
last_processed: dict[tuple[int, str], float] = {}
RATE_LIMIT_SECONDS = 300  # 5分钟


def should_process_video(group_id: int, video_id: str) -> bool:
    """检查是否应该处理这个视频"""
    key = (group_id, video_id)
    current_time = time.time()

    # 清理过期记录
    expired_keys = [k for k, t in last_processed.items() if current_time - t > RATE_LIMIT_SECONDS]
    for k in expired_keys:
        del last_processed[k]

    # 检查是否在限制时间内
    if key in last_processed:
        time_since_last = current_time - last_processed[key]
        if time_since_last < RATE_LIMIT_SECONDS:
            logger.info(f"视频 {video_id} 在群 {group_id} 中 {int(time_since_last)}秒前已处理，跳过")
            return False

    return True


def mark_as_processed(group_id: int, video_id: str):
    """标记视频已处理"""
    key = (group_id, video_id)
    last_processed[key] = time.time()


# ==================== 工具函数 ====================


async def parse_video_id_from_text(text: str, keyword: str) -> tuple[str, str, int, str] | None:
    """从文本中解析视频ID

    Returns:
        tuple[url, video_id, page_num, video_type] 或 None
        video_type: "BV" 或 "av"
    """
    matched = PATTERNS[keyword].search(text)
    if not matched:
        logger.info(f"{text} 中的链接或 BV/av 号无效")
        return None

    url, video_id, page_num = str(matched.group(0)), str(matched.group(1)), matched.group(2)
    video_type = "BV"  # 默认类型

    # 短链重定向
    if keyword in ("b23", "bili2233"):
        b23url = url
        url = await get_redirect_url(url, parser.headers)
        if url == b23url:
            logger.info(f"链接 {url} 无效")
            return None

    # 链接中是否包含BV，av号
    if id_type := next((i for i in ("/BV", "/av") if i in url), None):
        if matched := PATTERNS[id_type].search(url):
            video_id = str(matched.group(1))
            video_type = "av" if "av" in id_type else "BV"
    else:
        # 根据原始 keyword 判断类型
        if keyword in ("av", "/av"):
            video_type = "av"

    # 获取分集数
    page_num = int(page_num) if page_num else 1
    if url and (matched := re.search(r"(?:&|\?)p=(\d{1,3})", url)):
        page_num = int(matched.group(1))

    return url, video_id, page_num, video_type


async def download_and_send_video(matcher, video_info, video_id: str, page_num: int) -> bool:
    """下载并发送视频

    Returns:
        是否成功发送
    """
    if video_info.video_duration > DURATION_MAXIMUM:
        logger.info(f"video duration > {DURATION_MAXIMUM}, ignore download")
        return False

    file_name = f"{video_id}-{page_num}"
    video_path = plugin_cache_dir / f"{file_name}.mp4"

    if not video_path.exists():
        # 下载视频和音频
        if video_info.audio_url:
            v_path, a_path = await asyncio.gather(
                download_file_by_stream(
                    video_info.video_url, file_name=f"{file_name}-video.m4s", ext_headers=parser.headers
                ),
                download_file_by_stream(
                    video_info.audio_url, file_name=f"{file_name}-audio.m4s", ext_headers=parser.headers
                ),
            )
            await merge_av(v_path=v_path, a_path=a_path, output_path=video_path)
        else:
            video_path = await download_video(
                video_info.video_url, video_name=f"{file_name}.mp4", ext_headers=parser.headers
            )

    # 发送视频
    try:
        await matcher.send(get_video_seg(video_path))
    except ActionFailed as e:
        message: str = e.info.get("message", "")
        # 无缩略图错误
        if not message.endswith(".png'"):
            raise
        # 重新编码为 h264
        logger.warning("视频上传出现无缩略图错误，将重新编码为 h264 进行上传")
        h264_video_path = await encode_video_to_h264(video_path)
        await matcher.send(get_video_seg(h264_video_path))

    return True


async def handle_non_video_content(url: str) -> bool:
    """处理非视频内容（动态、直播、专栏、收藏夹）

    Returns:
        是否已处理
    """
    pub_prefix = f"{NICKNAME}解析 | 哔哩哔哩 - "

    # 动态
    if "t.bilibili.com" in url or "/opus" in url:
        matched = re.search(r"/(\d+)", url)
        if not matched:
            logger.info(f"链接 {url} 无效 - 没有获取到动态 id, 忽略")
            return False
        opus_id = int(matched.group(1))
        img_lst, text = await parser.parse_opus(opus_id)
        await bilibili.send(f"{pub_prefix}动态")
        segs = [text]
        if img_lst:
            paths = await download_imgs_without_raise(img_lst)
            segs.extend(get_img_seg(path) for path in paths)
        await send_segments(segs)
        return True

    # 直播间
    elif "/live" in url:
        matched = re.search(r"/(\d+)", url)
        if not matched:
            logger.info(f"链接 {url} 无效 - 没有获取到直播间 id, 忽略")
            return False
        room_id = int(matched.group(1))
        title, cover, keyframe = await parser.parse_live(room_id)
        if not title:
            await bilibili.send(f"{pub_prefix}直播 - 未找到直播间信息")
            return True
        res = f"{pub_prefix}直播 {title}"
        res += get_img_seg(await download_img(cover)) if cover else ""
        res += get_img_seg(await download_img(keyframe)) if keyframe else ""
        await bilibili.send(res)
        return True

    # 专栏
    elif "/read" in url:
        matched = re.search(r"read/cv(\d+)", url)
        if not matched:
            logger.info(f"链接 {url} 无效 - 没有获取到专栏 id, 忽略")
            return False
        read_id = int(matched.group(1))
        texts, urls = await parser.parse_read(read_id)
        await bilibili.send(f"{pub_prefix}专栏")
        paths = await download_imgs_without_raise(urls)
        paths.reverse()
        segs = []
        for text in texts:
            if text:
                segs.append(text)
            else:
                segs.append(get_img_seg(paths.pop()))
        if segs:
            await send_segments(segs)
        return True

    # 收藏夹
    elif "/favlist" in url:
        matched = re.search(r"favlist\?fid=(\d+)", url)
        if not matched:
            logger.info(f"链接 {url} 无效 - 没有获取到收藏夹 id, 忽略")
            return False
        fav_id = int(matched.group(1))
        texts, urls = await parser.parse_favlist(fav_id)
        await bilibili.send(f"{pub_prefix}收藏夹\n正在为你找出相关链接请稍等...")
        paths = await download_imgs_without_raise(urls)
        segs = []
        for path, text in zip(paths, texts):
            segs.append(get_img_seg(path) + text)
        await send_segments(segs)
        return True

    return False


# ==================== 主处理函数 ====================


@bilibili.handle()
@handle_exception()
async def _(event: MessageEvent, text: str = ExtractText(), keyword: str = Keyword()):
    """完整的B站解析处理"""
    # 解析视频信息
    parsed_result = await parse_video_id_from_text(text, keyword)
    if not parsed_result:
        return

    url, video_id, page_num, video_type = parsed_result

    # 如果不是视频，处理其他内容
    if not video_id:
        handled = await handle_non_video_content(url)
        if handled:
            await bilibili.finish()
        else:
            logger.info(f"不支持的链接: {url}")
            await bilibili.finish()

    # 获取群组ID
    group_id = event.group_id if isinstance(event, GroupMessageEvent) else 0

    # 检查是否应该处理
    if not should_process_video(group_id, video_id):
        return

    # 如果正在处理，则直接返回
    def is_processing(group_id: int, video_id: str) -> bool:
        lock = processing_locks.get((group_id, video_id))
        return lock is not None and lock.locked()

    if is_processing(group_id, video_id):
        return
    # 使用处理锁
    async with processing_locks[(group_id, video_id)]:
        # 双重检查
        if not should_process_video(group_id, video_id):
            return

        # 标记开始处理
        mark_as_processed(group_id, video_id)

        pub_prefix = f"{NICKNAME}解析 | 哔哩哔哩 - "

        # 构建链接
        need_join_link = keyword != "bilibili"
        join_link = ""
        if need_join_link:
            url_id = f"av{video_id}" if video_type == "av" else video_id
            join_link = f" https://www.bilibili.com/video/{url_id}"

        await bilibili.send(f"{pub_prefix}视频{join_link}")

        # 获取视频信息
        if video_type == "av":
            video_info = await parser.parse_video_info(avid=int(video_id), page_num=page_num)
        else:
            video_info = await parser.parse_video_info(bvid=video_id, page_num=page_num)

        # 发送视频信息
        segs = [
            video_info.title,
            get_img_seg(await download_img(video_info.cover_url)),
            video_info.display_info,
            video_info.ai_summary,
        ]
        if video_info.video_duration > DURATION_MAXIMUM:
            segs.append(
                f"⚠️ 当前视频时长 {video_info.video_duration // 60} 分钟, "
                f"超过管理员设置的最长时间 {DURATION_MAXIMUM // 60} 分钟!"
            )
        await send_segments(segs)

        # 下载并发送视频
        await download_and_send_video(bilibili, video_info, video_id, page_num)


@bilibili_auto_download.handle()
@handle_exception()
async def _(event: MessageEvent, text: str = ExtractText(), keyword: str = Keyword()):
    """仅下载视频，不进行完整解析"""
    # 解析视频信息
    parsed_result = await parse_video_id_from_text(text, keyword)
    if not parsed_result:
        return

    url, video_id, page_num, video_type = parsed_result

    # 如果不是视频链接，忽略
    if not video_id:
        return

    # 获取群组ID
    group_id = event.group_id if isinstance(event, GroupMessageEvent) else 0

    # 检查是否应该处理
    if not should_process_video(group_id, video_id):
        return

    # 使用处理锁
    async with processing_locks[(group_id, video_id)]:
        # 双重检查
        if not should_process_video(group_id, video_id):
            return

        # 标记开始处理
        mark_as_processed(group_id, video_id)

        # 获取视频信息
        if video_type == "av":
            video_info = await parser.parse_video_info(avid=int(video_id), page_num=page_num)
        else:
            video_info = await parser.parse_video_info(bvid=video_id, page_num=page_num)

        # 下载并发送视频
        await download_and_send_video(bilibili_auto_download, video_info, video_id, page_num)


@bili_music.handle()
@handle_exception()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    text = args.extract_plain_text().strip()
    matched = re.match(r"^(BV[1-9a-zA-Z]{10})(?:\s)?(\d{1,3})?$", text)
    if not matched:
        await bili_music.finish("命令格式: bm BV1LpD3YsETa [集数](中括号表示可选)")

    # 回应用户
    await bot.call_api("set_msg_emoji_like", message_id=event.message_id, emoji_id="282")
    bvid, p_num = str(matched.group(1)), matched.group(2)

    # 处理分 p
    p_num = int(p_num) if p_num else 1
    video_info = await parser.parse_video_info(bvid=bvid, page_num=p_num)
    if not video_info.audio_url:
        raise ParseException("没有可供下载的音频流")
    # 音频文件名
    video_title = keep_zh_en_num(video_info.title)
    audio_name = f"{video_title}.mp3"
    audio_path = plugin_cache_dir / audio_name
    # 下载
    if not audio_path.exists():
        await download_file_by_stream(video_info.audio_url, file_name=audio_name, ext_headers=parser.headers)

    # 发送音频
    await bili_music.send(get_record_seg(audio_path))
    # 上传音频
    if NEED_UPLOAD:
        await bili_music.send(get_file_seg(audio_path))
