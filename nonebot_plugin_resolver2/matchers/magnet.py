from nonebot import on_message

from ..config import NICKNAME
from ..download import download_img
from ..exception import handle_exception
from ..parsers.magnet import MagnetParser
from .filter import is_not_in_disabled_groups
from .helper import get_img_seg, send_segments
from .preprocess import ExtractText, Keyword, r_keywords

magnet = on_message(rule=is_not_in_disabled_groups & r_keywords("magnet:?xt=urn:btih:"))

parser = MagnetParser()


@magnet.handle()
@handle_exception()
async def _(text: str = ExtractText(), keyword: str = Keyword()):
    result = await parser.parse_magnet(text)

    # 基本信息
    detail = f"{NICKNAME}解析 | {result.title}"

    # 准备要发送的消息段列表
    segments = [detail]

    # 如果有截图，下载并添加到消息段
    if result.pic_urls:
        for pic_url in result.pic_urls:
            try:
                img_path = await download_img(pic_url)
                img_seg = get_img_seg(img_path)
                segments.append(img_seg)
            except Exception as e:
                segments.append(f"图片下载失败: {str(e)}")
    else:
        segments.append("暂无预览图片")

    # 使用合并转发发送（因为可能包含敏感内容）
    await send_segments(segments)
