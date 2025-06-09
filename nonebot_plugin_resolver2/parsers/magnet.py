import re
from urllib.parse import quote

import aiohttp
from nonebot import logger
from nonebot.exception import FinishedException

from ..exception import ParseException
from .data import COMMON_HEADER, ParseResult


class MagnetParser:
    """
    磁力链解析器
    """

    def __init__(self):
        # 匹配磁力链的正则表达式
        self.magnet_pattern = re.compile(r"magnet:\?xt=urn:btih:[a-fA-F0-9]{32,40}[^\s]*")

    async def parse_magnet(self, magnet_url: str):
        """解析磁力链

        Args:
            magnet_url: 磁力链接

        Returns:
            ParseResult: 解析结果
        """
        if not self.magnet_pattern.match(magnet_url):
            logger.warning(f"无效磁力链接: {magnet_url}, 忽略")
            raise FinishedException

        # 调用API解析磁力链
        api_url = f"https://whatslink.info/api/v1/link?url={quote(magnet_url)}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url, headers=COMMON_HEADER) as resp:
                    resp.raise_for_status()
                    data = await resp.json()

            # 检查API响应
            if data.get("error"):
                if "quota_limited" in data.get("error", ""):
                    raise ParseException("API请求次数限制，请稍后再试")
                else:
                    raise ParseException(f"解析失败: {data.get('error')}")

            # 提取信息
            name = data.get("name", "未知")
            file_type = data.get("file_type", "unknown")
            size = data.get("size", 0)
            count = data.get("count", 0)
            screenshots = data.get("screenshots", [])

            # 格式化大小
            size_str = self._format_size(size)

            # 构建标题
            title = f"[磁力链] {name}"
            if file_type == "folder":
                title += f" (文件夹，{count}个文件，{size_str})"
            else:
                title += f" ({size_str})"

            # 提取截图链接
            pic_urls = [shot.get("screenshot", "") for shot in screenshots if shot.get("screenshot")]

            return ParseResult(
                title=title,
                author="磁力链分享",
                pic_urls=pic_urls[:5],  # 限制最多5张图片
            )

        except aiohttp.ClientError as e:
            raise ParseException(f"网络请求失败: {e}")
        except Exception as e:
            raise ParseException(f"磁力链解析失败: {e}")

    def _format_size(self, size_bytes: int) -> str:
        """格式化文件大小"""
        if size_bytes == 0:
            return "未知大小"

        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} PB"
