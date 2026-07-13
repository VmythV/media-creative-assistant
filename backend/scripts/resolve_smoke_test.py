"""DaVinci Resolve Studio 脚本 API 冒烟测试。

前置条件：DaVinci Resolve 正在运行。
验证：连接 Resolve → 创建项目 → 创建时间线 → 汇报版本信息。

用法（在 backend/ 目录）：
    uv run python scripts/resolve_smoke_test.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402

SMOKE_PROJECT = "mca-smoke-test"


def main() -> int:
    modules_path = str(Path(settings.resolve_script_api) / "Modules")
    if modules_path not in sys.path:
        sys.path.append(modules_path)

    try:
        import DaVinciResolveScript as dvr
    except ImportError as e:
        print(f"[FAIL] 无法导入 DaVinciResolveScript: {e}")
        print(f"       检查路径: {modules_path}")
        return 1

    resolve = dvr.scriptapp("Resolve")
    if resolve is None:
        print("[FAIL] 无法连接 Resolve。请确认 DaVinci Resolve 正在运行，")
        print("       且 Preferences > System > General 中 External scripting 已设为 Local。")
        return 1

    print(f"[OK] 已连接: {resolve.GetProductName()} {resolve.GetVersionString()}")

    pm = resolve.GetProjectManager()
    project = pm.CreateProject(SMOKE_PROJECT) or pm.LoadProject(SMOKE_PROJECT)
    if project is None:
        print("[FAIL] 无法创建/加载项目")
        return 1
    print(f"[OK] 项目就绪: {project.GetName()}")

    mp = project.GetMediaPool()
    timeline = mp.CreateEmptyTimeline("mca-smoke-timeline")
    if timeline is None:
        # 可能已存在同名时间线
        for i in range(1, int(project.GetTimelineCount()) + 1):
            t = project.GetTimelineByIndex(i)
            if t and t.GetName() == "mca-smoke-timeline":
                timeline = t
                break
    if timeline is None:
        print("[FAIL] 无法创建时间线")
        return 1
    print(f"[OK] 时间线就绪: {timeline.GetName()}")
    print(f"[OK] 冒烟测试通过 (Python {sys.version.split()[0]})")
    print(f"     可在 Resolve 中删除测试项目 '{SMOKE_PROJECT}'。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
