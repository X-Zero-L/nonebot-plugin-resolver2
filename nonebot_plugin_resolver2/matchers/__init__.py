from nonebot import require
from nonebot.matcher import Matcher

require("nonebot_plugin_alconna")
from .acfun import acfun
from .bilibili import bilibili
from .douyin import douyin
from .kuaishou import kuaishou
from .kugou import kugou
from .magnet import magnet
from .ncm import ncm
from .tiktok import tiktok
from .twitter import twitter
from .weibo import weibo
from .xiaohongshu import xiaohongshu
from .ytb import ytb

resolvers: dict[str, type[Matcher]] = {
    "bilibili": bilibili,
    "acfun": acfun,
    "douyin": douyin,
    "kuaishou": kuaishou,
    "ytb": ytb,
    "kugou": kugou,
    "magnet": magnet,
    "ncm": ncm,
    "twitter": twitter,
    "tiktok": tiktok,
    "weibo": weibo,
    "xiaohongshu": xiaohongshu,
}
