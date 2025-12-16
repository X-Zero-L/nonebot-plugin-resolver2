import re
from urllib.parse import quote

import httpx

from ..constants import COMMON_HEADER, COMMON_TIMEOUT
from ..exception import ParseException
from .data import ImageContent, ParseResult


class MagnetParser:
    """磁力链解析器"""

    def __init__(self) -> None:
        self.magnet_pattern = re.compile(r"magnet:\?xt=urn:btih:[a-fA-F0-9]{32,40}[^\s]*")

    async def parse_magnet(self, magnet_url: str) -> ParseResult:
        """解析磁力链"""
        if not self.magnet_pattern.match(magnet_url):
            raise ParseException("无效磁力链接")

        api_url = f"https://whatslink.info/api/v1/link?url={quote(magnet_url, safe='')}"

        try:
            async with httpx.AsyncClient(
                headers=COMMON_HEADER,
                timeout=COMMON_TIMEOUT,
                follow_redirects=True,
                verify=False,
            ) as client:
                resp = await client.get(api_url)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as e:
            raise ParseException(f"网络请求失败: {e}") from e
        except Exception as e:
            raise ParseException(f"磁力链解析失败: {e}") from e

        if err := data.get("error"):
            err = str(err)
            if "quota_limited" in err:
                raise ParseException("API请求次数限制，请稍后再试")
            raise ParseException(f"解析失败: {err}")

        name = data.get("name") or "未知"
        file_type = data.get("file_type") or "unknown"
        size = int(data.get("size") or 0)
        count = int(data.get("count") or 0)
        screenshots = data.get("screenshots") or []

        size_str = self._format_size(size)

        title = f"[磁力链] {name}"
        if file_type == "folder":
            title += f" (文件夹，{count}个文件，{size_str})"
        else:
            title += f" ({size_str})"

        pic_urls = [
            shot.get("screenshot")
            for shot in screenshots
            if isinstance(shot, dict) and shot.get("screenshot")
        ]

        content = ImageContent(pic_urls=pic_urls[:5]) if pic_urls else None
        return ParseResult(title=title, author="磁力链分享", content=content)

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        """格式化文件大小"""
        if size_bytes <= 0:
            return "未知大小"

        size = float(size_bytes)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1024.0:
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} PB"
