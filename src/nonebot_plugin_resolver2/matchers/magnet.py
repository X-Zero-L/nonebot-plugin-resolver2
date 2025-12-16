import re

from nonebot.adapters.onebot.v11 import MessageSegment

from ..config import NICKNAME
from ..download import DOWNLOADER
from ..exception import handle_exception
from ..parsers import MagnetParser
from ..parsers.data import ImageContent
from .helper import obhelper
from .preprocess import KeyPatternMatched, on_keyword_regex

magnet = on_keyword_regex(("magnet", r"(magnet:\?xt=urn:btih:[a-fA-F0-9]{32,40}[^\s]*)"))

parser = MagnetParser()


@magnet.handle()
@handle_exception()
async def _(searched: re.Match[str] = KeyPatternMatched()):
    magnet_url = searched.group(1) or searched.group(0)

    await magnet.send(f"{NICKNAME}解析 | 磁力链")

    result = await parser.parse_magnet(magnet_url)

    segs: list[str | MessageSegment] = [result.title]
    if isinstance(result.content, ImageContent) and result.content.pic_urls:
        paths = await DOWNLOADER.download_imgs_without_raise(result.content.pic_urls)
        segs.extend(obhelper.img_seg(path) for path in paths)
    await obhelper.send_segments(segs)
