from contextvars import ContextVar

# Whether parsers should generate media download tasks (video/audio/image).
DOWNLOAD_MEDIA: ContextVar[bool] = ContextVar("psr_download_media", default=True)

# Special mode: allow bilibili auto-download even if platform is disabled for the group.
BILI_AUTO_DOWNLOAD_ONLY: ContextVar[bool] = ContextVar("psr_bili_auto_download_only", default=False)
