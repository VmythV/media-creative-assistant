"""工具组加载：import 即注册到全局 registry。"""

_loaded = False


def load_all_tools() -> None:
    global _loaded
    if _loaded:
        return
    from app.tools import audio, editor, ir_tools, media, shots, vision  # noqa: F401

    _loaded = True
