from nonebot import require
from nonebot.plugin import PluginMetadata

# Compatibility wrapper: keep the old entrypoint name, but load the upstream master plugin.
require("nonebot_plugin_parser")

__plugin_meta__ = PluginMetadata(
    name="nonebot-plugin-resolver2 (compat)",
    description="Compatibility wrapper for nonebot-plugin-parser",
    usage="Load nonebot-plugin-parser and use its commands/features.",
    type="library",
)
