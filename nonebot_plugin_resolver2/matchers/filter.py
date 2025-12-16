import json
from typing import Literal

from nonebot import logger, on_command
from nonebot.adapters.onebot.v11 import (
    GROUP_ADMIN,
    GROUP_OWNER,
    Bot,
    GroupMessageEvent,
    MessageEvent,
    PrivateMessageEvent,
)
from nonebot.matcher import Matcher
from nonebot.permission import SUPERUSER
from nonebot.rule import to_me
from pydantic import BaseModel, Field

from ..config import store
from ..constant import DISABLED_GROUPS, FILTER_CONFIG_FILE


# 支持分不同解析，如bilibili、douyin、ytb等
class FilterItem(BaseModel):
    disabled_groups: list[int] = Field(default_factory=list)


source_enum = Literal["bilibili", "douyin", "ytb"]
source_alias: dict[source_enum, list[str]] = {
    "bilibili": ["bilibili", "b23", "b站", "B站"],
    "douyin": ["douyin", "抖音", "v.douyin"],
    "ytb": ["ytb", "油管", "youtube", "youtu.be"],
}


class FilterConfig(BaseModel):
    filter_dict: dict[source_enum, FilterItem] = Field(default_factory=dict)
    do_not_download_media_groups: list[int] = Field(default_factory=list)
    bili_auto_download_when_disabled_groups: list[int] = Field(default_factory=list)


def load_or_initialize_set() -> set[int]:
    """加载或初始化关闭解析的名单"""
    data_file = store.get_plugin_data_file(DISABLED_GROUPS)
    # 判断是否存在
    if not data_file.exists():
        data_file.write_text(json.dumps([]))
    return set(json.loads(data_file.read_text()))


def load_filter_config() -> FilterConfig:
    data_file = store.get_plugin_data_file(FILTER_CONFIG_FILE)
    if not data_file.exists():
        data_file.write_text(json.dumps({}))
    return FilterConfig.model_validate_json(data_file.read_text())


def save_disabled_groups():
    """保存关闭解析的名单"""
    data_file = store.get_plugin_data_file(DISABLED_GROUPS)
    data_file.write_text(json.dumps(list(disabled_group_set)))
    filter_config_file = store.get_plugin_data_file(FILTER_CONFIG_FILE)
    filter_config_file.write_text(json.dumps(filter_config.model_dump()))


# 内存中关闭解析的名单，第一次先进行初始化
disabled_group_set: set[int] = load_or_initialize_set()
filter_config: FilterConfig = load_filter_config()


# Rule
def is_not_in_disabled_groups(event: MessageEvent) -> bool:
    return True if not isinstance(event, GroupMessageEvent) else event.group_id not in disabled_group_set


def is_not_in_disabled_groups_by_source(event: MessageEvent, source: source_enum) -> bool:
    return (
        True
        if not isinstance(event, GroupMessageEvent)
        else event.group_id not in filter_config.filter_dict.get(source, FilterItem()).disabled_groups
    )


def is_not_in_disabled_groups_by_source_alias(event: MessageEvent, source_alias: str) -> bool:
    for source, aliases in source_alias.items():
        if source_alias in aliases:
            return is_not_in_disabled_groups_by_source(event, source)
    return True


def is_not_in_disabled_groups_by_bilibili(event: MessageEvent) -> bool:
    return is_not_in_disabled_groups_by_source(event, "bilibili")


def is_not_in_disabled_groups_by_douyin(event: MessageEvent) -> bool:
    return is_not_in_disabled_groups_by_source(event, "douyin")


def is_not_in_disabled_groups_by_ytb(event: MessageEvent) -> bool:
    return is_not_in_disabled_groups_by_source(event, "ytb")


def is_in_bili_auto_download_when_disabled_groups(event: MessageEvent) -> bool:
    return (
        True
        if not isinstance(event, GroupMessageEvent)
        else event.group_id in filter_config.bili_auto_download_when_disabled_groups
    )


def is_not_in_do_not_download_media_groups(event: MessageEvent) -> bool:
    return (
        True
        if not isinstance(event, GroupMessageEvent)
        else event.group_id not in filter_config.do_not_download_media_groups
    )


@on_command("开启所有解析", permission=SUPERUSER, block=True).handle()
async def _(matcher: Matcher, bot: Bot, event: PrivateMessageEvent):
    """开启所有解析"""
    disabled_group_set.clear()
    save_disabled_groups()
    await matcher.finish("所有解析已开启")


@on_command("关闭所有解析", permission=SUPERUSER, block=True).handle()
async def _(matcher: Matcher, bot: Bot, event: PrivateMessageEvent):
    """关闭所有解析"""
    gid_list: list[int] = [g["group_id"] for g in await bot.get_group_list()]
    disabled_group_set.update(gid_list)
    save_disabled_groups()
    await matcher.finish("所有解析已关闭")


@on_command(
    "开启解析",
    rule=to_me(),
    permission=GROUP_ADMIN | GROUP_OWNER | SUPERUSER,
    block=True,
).handle()
async def _(matcher: Matcher, bot: Bot, event: GroupMessageEvent):
    """开启解析"""
    gid = event.group_id
    plain_text = event.message.extract_plain_text().strip().replace("开启解析", "").strip()
    target_source = disabled_group_set
    if plain_text in source_alias["bilibili"]:
        target_source = filter_config.filter_dict["bilibili"].disabled_groups
    elif plain_text in source_alias["douyin"]:
        target_source = filter_config.filter_dict["douyin"].disabled_groups
    elif plain_text in source_alias["ytb"]:
        target_source = filter_config.filter_dict["ytb"].disabled_groups
    if gid in target_source:
        target_source.remove(gid)
        save_disabled_groups()
        await matcher.finish("解析已开启")
    else:
        await matcher.finish("解析已开启，无需重复开启")


@on_command(
    "关闭解析",
    rule=to_me(),
    permission=GROUP_ADMIN | GROUP_OWNER | SUPERUSER,
    block=True,
).handle()
async def _(matcher: Matcher, bot: Bot, event: GroupMessageEvent):
    """关闭解析"""
    gid = event.group_id
    plain_text = event.message.extract_plain_text().strip().replace("关闭解析", "").strip()
    target_source = disabled_group_set
    if plain_text in source_alias["bilibili"]:
        if "bilibili" not in filter_config.filter_dict:
            filter_config.filter_dict["bilibili"] = FilterItem()
        target_source = filter_config.filter_dict["bilibili"].disabled_groups
    elif plain_text in source_alias["douyin"]:
        if "douyin" not in filter_config.filter_dict:
            filter_config.filter_dict["douyin"] = FilterItem()
        target_source = filter_config.filter_dict["douyin"].disabled_groups
    elif plain_text in source_alias["ytb"]:
        if "ytb" not in filter_config.filter_dict:
            filter_config.filter_dict["ytb"] = FilterItem()
        target_source = filter_config.filter_dict["ytb"].disabled_groups
    if gid not in target_source:
        if isinstance(target_source, list):
            target_source.append(gid)
        else:
            target_source.add(gid)
        save_disabled_groups()
        await matcher.finish("解析已关闭")
    else:
        await matcher.finish("解析已关闭，无需重复关闭")


@on_command(
    "关闭下载解析",
    rule=to_me(),
    permission=GROUP_ADMIN | GROUP_OWNER | SUPERUSER,
    block=True,
).handle()
async def _(matcher: Matcher, bot: Bot, event: GroupMessageEvent):
    """解析关闭下载"""
    logger.info(f"关闭下载解析: {event}")
    gid = event.group_id
    if gid in filter_config.do_not_download_media_groups:
        await matcher.finish("下载已关闭, 无需重复关闭")
    else:
        filter_config.do_not_download_media_groups.append(gid)
        save_disabled_groups()
        await matcher.finish("下载已关闭")


@on_command(
    "开启下载解析",
    rule=to_me(),
    permission=GROUP_ADMIN | GROUP_OWNER | SUPERUSER,
    block=True,
).handle()
async def _(matcher: Matcher, bot: Bot, event: GroupMessageEvent):
    """解析开启下载"""
    logger.info(f"开启下载解析: {event}")
    gid = event.group_id
    if gid not in filter_config.do_not_download_media_groups:
        await matcher.finish("下载已开启, 无需重复开启")
    else:
        filter_config.do_not_download_media_groups.remove(gid)
        save_disabled_groups()
        await matcher.finish("下载已开启")


@on_command("查看关闭解析", permission=SUPERUSER, block=True).handle()
async def _(matcher: Matcher, bot: Bot, event: MessageEvent):
    """查看关闭解析"""
    disable_groups = [
        str(item) + "--" + (await bot.get_group_info(group_id=item))["group_name"] for item in disabled_group_set
    ]
    disable_groups = "\n".join(disable_groups)
    if isinstance(event, GroupMessageEvent):
        await matcher.send("已经发送到私信了~")
    message = f"解析关闭的群聊如下：\n{disable_groups} \n🌟 温馨提示：如果想开关解析需要在群聊@我然后输入[开启/关闭解析], 另外还可以私信我发送[开启/关闭所有解析]"  # noqa: E501
    await bot.send_private_msg(user_id=event.user_id, message=message)


@on_command(
    "开启b站自动下载",
    rule=to_me(),
    permission=GROUP_ADMIN | GROUP_OWNER | SUPERUSER,
    block=True,
).handle()
async def _(matcher: Matcher, bot: Bot, event: GroupMessageEvent):
    """在b站解析关闭时开启自动下载"""
    gid = event.group_id
    if gid in filter_config.bili_auto_download_when_disabled_groups:
        await matcher.finish("b站自动下载已开启, 无需重复开启")
    else:
        filter_config.bili_auto_download_when_disabled_groups.append(gid)
        save_disabled_groups()
        await matcher.finish("b站自动下载已开启（即使关闭b站解析也会下载视频）")


@on_command(
    "关闭b站自动下载",
    rule=to_me(),
    permission=GROUP_ADMIN | GROUP_OWNER | SUPERUSER,
    block=True,
).handle()
async def _(matcher: Matcher, bot: Bot, event: GroupMessageEvent):
    """在b站解析关闭时关闭自动下载"""
    gid = event.group_id
    if gid not in filter_config.bili_auto_download_when_disabled_groups:
        await matcher.finish("b站自动下载已关闭, 无需重复关闭")
    else:
        filter_config.bili_auto_download_when_disabled_groups.remove(gid)
        save_disabled_groups()
        await matcher.finish("b站自动下载已关闭")
