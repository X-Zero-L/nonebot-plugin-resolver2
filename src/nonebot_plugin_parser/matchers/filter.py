import json
from pathlib import Path
from dataclasses import dataclass, field

from nonebot import logger, on_command
from nonebot.rule import to_me
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.adapters import Message
from nonebot.permission import SUPERUSER
from nonebot_plugin_uninfo import ADMIN, Session, UniSession

from ..config import pconfig
from ..constants import PlatformEnum

_DISABLED_GROUPS_PATH: Path = pconfig.data_dir / "disabled_groups.json"
_FILTER_CONFIG_PATH: Path = pconfig.data_dir / "filter_config.json"


@dataclass(slots=True)
class FilterItem:
    disabled_groups: set[str] = field(default_factory=set)


@dataclass(slots=True)
class FilterConfig:
    disable_all: bool = False
    enabled_groups: set[str] = field(default_factory=set)
    filter_dict: dict[str, FilterItem] = field(default_factory=dict)
    do_not_download_media_groups: set[str] = field(default_factory=set)
    bili_auto_download_when_disabled_groups: set[str] = field(default_factory=set)

    def to_json(self) -> dict[str, object]:
        return {
            "disable_all": self.disable_all,
            "enabled_groups": sorted(self.enabled_groups),
            "filter_dict": {k: {"disabled_groups": sorted(v.disabled_groups)} for k, v in self.filter_dict.items()},
            "do_not_download_media_groups": sorted(self.do_not_download_media_groups),
            "bili_auto_download_when_disabled_groups": sorted(self.bili_auto_download_when_disabled_groups),
        }

    @classmethod
    def from_json(cls, data: dict[str, object]) -> "FilterConfig":
        cfg = cls()
        cfg.disable_all = bool(data.get("disable_all", False))
        cfg.enabled_groups = set(data.get("enabled_groups", []) or [])

        filter_dict = data.get("filter_dict", {}) or {}
        if isinstance(filter_dict, dict):
            for platform, item in filter_dict.items():
                if not isinstance(platform, str) or not isinstance(item, dict):
                    continue
                disabled = item.get("disabled_groups", []) or []
                if isinstance(disabled, list):
                    cfg.filter_dict[platform] = FilterItem(disabled_groups=set(map(str, disabled)))

        dnd = data.get("do_not_download_media_groups", []) or []
        if isinstance(dnd, list):
            cfg.do_not_download_media_groups = set(map(str, dnd))

        bili_auto = data.get("bili_auto_download_when_disabled_groups", []) or []
        if isinstance(bili_auto, list):
            cfg.bili_auto_download_when_disabled_groups = set(map(str, bili_auto))
        return cfg


def _default_filter_dict() -> dict[str, FilterItem]:
    return {str(platform): FilterItem() for platform in PlatformEnum}


def load_filter_config() -> FilterConfig:
    if not _FILTER_CONFIG_PATH.exists():
        cfg = FilterConfig(filter_dict=_default_filter_dict())
        _FILTER_CONFIG_PATH.write_text(json.dumps(cfg.to_json(), ensure_ascii=False, indent=2))
        return cfg

    try:
        data = json.loads(_FILTER_CONFIG_PATH.read_text() or "{}")
        if not isinstance(data, dict):
            raise ValueError("invalid filter config json")
        cfg = FilterConfig.from_json(data)
    except Exception:
        logger.exception("过滤配置文件解析失败，将重置为默认配置")
        cfg = FilterConfig(filter_dict=_default_filter_dict())

    # ensure all platforms exist
    for platform in PlatformEnum:
        cfg.filter_dict.setdefault(str(platform), FilterItem())
    return cfg


def save_filter_config() -> None:
    _FILTER_CONFIG_PATH.write_text(json.dumps(_FILTER_CONFIG.to_json(), ensure_ascii=False, indent=2))


def load_or_initialize_set() -> set[str]:
    """加载或初始化关闭解析的名单"""
    # 判断是否存在
    if not _DISABLED_GROUPS_PATH.exists():
        _DISABLED_GROUPS_PATH.write_text(json.dumps([]))
    return set(json.loads(_DISABLED_GROUPS_PATH.read_text()))


def save_disabled_groups():
    """保存关闭解析的名单"""
    _DISABLED_GROUPS_PATH.write_text(json.dumps(list(_DISABLED_GROUPS_SET)))


# 内存中关闭解析的名单，第一次先进行初始化
_DISABLED_GROUPS_SET: set[str] = load_or_initialize_set()
_FILTER_CONFIG: FilterConfig = load_filter_config()


# 命令参数别名映射：参数 -> 平台名称
_PLATFORM_ALIASES: dict[str, set[str]] = {
    str(PlatformEnum.BILIBILI): {"bilibili", "b23", "bv", "av", "b站", "B站"},
    str(PlatformEnum.DOUYIN): {"douyin", "v.douyin", "抖音"},
    str(PlatformEnum.YOUTUBE): {"youtube", "ytb", "youtube.com", "youtu.be", "油管"},
    str(PlatformEnum.MAGNET): {"magnet", "磁力", "磁力链"},
}


def resolve_platform_name(arg: str) -> str | None:
    arg = arg.strip()
    if not arg:
        return None
    lowered = arg.lower()
    # direct enum value
    if lowered in {str(p) for p in PlatformEnum}:
        return lowered
    for platform, aliases in _PLATFORM_ALIASES.items():
        if arg in aliases or lowered in {a.lower() for a in aliases}:
            return platform
    return None


def get_group_key(session: Session) -> str:
    """获取群组的唯一标识符

    由平台名称和会话场景 ID 组成，例如 `QQClient_123456789`。
    """
    return f"{session.scope}_{session.scene_path}"


# Rule
def is_enabled(session: Session = UniSession()) -> bool:
    """判断当前会话是否在关闭解析的名单中"""
    if session.scene.is_private:
        return True

    group_key = get_group_key(session)
    if _FILTER_CONFIG.disable_all:
        return group_key in _FILTER_CONFIG.enabled_groups

    return group_key not in _DISABLED_GROUPS_SET


def is_download_enabled(session: Session = UniSession()) -> bool:
    if session.scene.is_private:
        return True
    return get_group_key(session) not in _FILTER_CONFIG.do_not_download_media_groups


def is_platform_enabled(platform: str, session: Session = UniSession()) -> bool:
    if session.scene.is_private:
        return True
    item = _FILTER_CONFIG.filter_dict.get(platform)
    if item is None:
        return True
    return get_group_key(session) not in item.disabled_groups


def is_bili_auto_download_when_disabled(session: Session = UniSession()) -> bool:
    if session.scene.is_private:
        return False
    return get_group_key(session) in _FILTER_CONFIG.bili_auto_download_when_disabled_groups


@on_command("开启解析", rule=to_me(), permission=SUPERUSER | ADMIN(), block=True).handle()
async def _(matcher: Matcher, session: Session = UniSession(), message: Message = CommandArg()):
    """开启解析"""
    group_key = get_group_key(session)
    arg = message.extract_plain_text().strip()
    platform = resolve_platform_name(arg)

    if platform:
        item = _FILTER_CONFIG.filter_dict.setdefault(platform, FilterItem())
        if group_key in item.disabled_groups:
            item.disabled_groups.remove(group_key)
            save_filter_config()
            await matcher.finish("解析已开启")
        await matcher.finish("解析已开启，无需重复开启")

    # global enable
    if _FILTER_CONFIG.disable_all:
        if group_key not in _FILTER_CONFIG.enabled_groups:
            _FILTER_CONFIG.enabled_groups.add(group_key)
            save_filter_config()
            await matcher.finish("解析已开启")
        await matcher.finish("解析已开启，无需重复开启")

    if group_key in _DISABLED_GROUPS_SET:
        _DISABLED_GROUPS_SET.remove(group_key)
        save_disabled_groups()
        await matcher.finish("解析已开启")
    await matcher.finish("解析已开启，无需重复开启")


@on_command("关闭解析", rule=to_me(), permission=SUPERUSER | ADMIN(), block=True).handle()
async def _(matcher: Matcher, session: Session = UniSession(), message: Message = CommandArg()):
    """关闭解析"""
    group_key = get_group_key(session)
    arg = message.extract_plain_text().strip()
    platform = resolve_platform_name(arg)

    if platform:
        item = _FILTER_CONFIG.filter_dict.setdefault(platform, FilterItem())
        if group_key not in item.disabled_groups:
            item.disabled_groups.add(group_key)
            save_filter_config()
            await matcher.finish("解析已关闭")
        await matcher.finish("解析已关闭，无需重复关闭")

    # global disable
    if _FILTER_CONFIG.disable_all:
        if group_key in _FILTER_CONFIG.enabled_groups:
            _FILTER_CONFIG.enabled_groups.remove(group_key)
            save_filter_config()
            await matcher.finish("解析已关闭")
        await matcher.finish("解析已关闭，无需重复关闭")

    if group_key not in _DISABLED_GROUPS_SET:
        _DISABLED_GROUPS_SET.add(group_key)
        save_disabled_groups()
        await matcher.finish("解析已关闭")
    await matcher.finish("解析已关闭，无需重复关闭")


@on_command("开启所有解析", permission=SUPERUSER, block=True).handle()
async def _(matcher: Matcher):
    """开启所有解析"""
    _DISABLED_GROUPS_SET.clear()
    save_disabled_groups()
    _FILTER_CONFIG.disable_all = False
    _FILTER_CONFIG.enabled_groups.clear()
    for item in _FILTER_CONFIG.filter_dict.values():
        item.disabled_groups.clear()
    _FILTER_CONFIG.do_not_download_media_groups.clear()
    _FILTER_CONFIG.bili_auto_download_when_disabled_groups.clear()
    save_filter_config()
    await matcher.finish("所有解析已开启")


@on_command("关闭所有解析", permission=SUPERUSER, block=True).handle()
async def _(matcher: Matcher):
    """关闭所有解析（对所有群生效，可用“开启解析”对单群豁免）"""
    _FILTER_CONFIG.disable_all = True
    _FILTER_CONFIG.enabled_groups.clear()
    save_filter_config()
    await matcher.finish("所有解析已关闭")


@on_command("关闭下载解析", rule=to_me(), permission=SUPERUSER | ADMIN(), block=True).handle()
async def _(matcher: Matcher, session: Session = UniSession()):
    """关闭媒体下载（保留文本/渲染）"""
    group_key = get_group_key(session)
    if group_key in _FILTER_CONFIG.do_not_download_media_groups:
        await matcher.finish("下载已关闭, 无需重复关闭")
    _FILTER_CONFIG.do_not_download_media_groups.add(group_key)
    save_filter_config()
    await matcher.finish("下载已关闭")


@on_command("开启下载解析", rule=to_me(), permission=SUPERUSER | ADMIN(), block=True).handle()
async def _(matcher: Matcher, session: Session = UniSession()):
    """开启媒体下载"""
    group_key = get_group_key(session)
    if group_key not in _FILTER_CONFIG.do_not_download_media_groups:
        await matcher.finish("下载已开启, 无需重复开启")
    _FILTER_CONFIG.do_not_download_media_groups.remove(group_key)
    save_filter_config()
    await matcher.finish("下载已开启")


@on_command("查看关闭解析", permission=SUPERUSER, block=True).handle()
async def _(matcher: Matcher):
    """查看关闭解析/下载的群列表（以 group_key 展示）"""
    lines: list[str] = []
    lines.append(f"disable_all={_FILTER_CONFIG.disable_all}")
    if _FILTER_CONFIG.enabled_groups:
        lines.append("enabled_groups=" + ", ".join(sorted(_FILTER_CONFIG.enabled_groups)))
    if _DISABLED_GROUPS_SET:
        lines.append("disabled_groups=" + ", ".join(sorted(_DISABLED_GROUPS_SET)))

    disabled_by_platform = {
        platform: sorted(item.disabled_groups)
        for platform, item in _FILTER_CONFIG.filter_dict.items()
        if item.disabled_groups
    }
    for platform, groups in sorted(disabled_by_platform.items()):
        lines.append(f"{platform} disabled=" + ", ".join(groups))

    if _FILTER_CONFIG.do_not_download_media_groups:
        lines.append("do_not_download=" + ", ".join(sorted(_FILTER_CONFIG.do_not_download_media_groups)))
    if _FILTER_CONFIG.bili_auto_download_when_disabled_groups:
        lines.append(
            "bili_auto_download_when_disabled="
            + ", ".join(sorted(_FILTER_CONFIG.bili_auto_download_when_disabled_groups))
        )

    await matcher.finish("\n".join(lines) if lines else "暂无配置")


@on_command("开启b站自动下载", rule=to_me(), permission=SUPERUSER | ADMIN(), block=True).handle()
async def _(matcher: Matcher, session: Session = UniSession()):
    """在关闭哔哩哔哩解析时仍允许自动下载"""
    group_key = get_group_key(session)
    if group_key in _FILTER_CONFIG.bili_auto_download_when_disabled_groups:
        await matcher.finish("b站自动下载已开启, 无需重复开启")
    _FILTER_CONFIG.bili_auto_download_when_disabled_groups.add(group_key)
    save_filter_config()
    await matcher.finish("b站自动下载已开启（关闭b站解析时仍可自动下载）")


@on_command("关闭b站自动下载", rule=to_me(), permission=SUPERUSER | ADMIN(), block=True).handle()
async def _(matcher: Matcher, session: Session = UniSession()):
    """关闭在哔哩哔哩解析关闭时的自动下载"""
    group_key = get_group_key(session)
    if group_key not in _FILTER_CONFIG.bili_auto_download_when_disabled_groups:
        await matcher.finish("b站自动下载已关闭, 无需重复关闭")
    _FILTER_CONFIG.bili_auto_download_when_disabled_groups.remove(group_key)
    save_filter_config()
    await matcher.finish("b站自动下载已关闭")
