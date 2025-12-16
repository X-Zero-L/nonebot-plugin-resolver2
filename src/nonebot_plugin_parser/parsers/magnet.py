import re
from typing import ClassVar
from urllib.parse import quote

import httpx

from ..exception import ParseException
from .base import BaseParser, Platform, PlatformEnum, handle


class MagnetParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name=PlatformEnum.MAGNET, display_name="磁力链")

    _MAGNET_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"(magnet:\?xt=urn:btih:[a-fA-F0-9]{32,40}[^\s]*)")

    @handle("magnet", r"(magnet:\?xt=urn:btih:[a-fA-F0-9]{32,40}[^\s]*)")
    async def _parse(self, searched: re.Match[str]):
        magnet_url = searched.group(1) if searched.groups() else searched.group(0)
        if not self._MAGNET_PATTERN.match(magnet_url):
            raise ParseException("无效磁力链接")

        api_url = f"https://whatslink.info/api/v1/link?url={quote(magnet_url, safe='')}"

        try:
            async with httpx.AsyncClient(
                headers=self.headers,
                timeout=self.timeout,
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

        title = name
        if file_type == "folder":
            title = f"{title} (文件夹，{count}个文件，{size_str})"
        else:
            title = f"{title} ({size_str})"

        pic_urls = [
            shot.get("screenshot")
            for shot in screenshots
            if isinstance(shot, dict) and shot.get("screenshot")
        ][:5]

        contents = self.create_image_contents(pic_urls) if pic_urls else []

        return self.result(
            title=title,
            url=magnet_url,
            contents=contents,
        )

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        if size_bytes <= 0:
            return "未知大小"

        size = float(size_bytes)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1024.0:
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} PB"
