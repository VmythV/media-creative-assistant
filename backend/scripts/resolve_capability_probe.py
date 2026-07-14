"""第二轮：A) 干净时间线上验证音频 recordFrame；B) 转场对象属性 + FCPXML1.9 回读。"""

import sys
import time
from pathlib import Path

sys.path.append("/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules")
import DaVinciResolveScript as dvr

SCRATCH = Path(__file__).parent
MUSIC = "/Users/may/program/media-creative-assistant/data/music/ambient_demo.wav"

resolve = dvr.scriptapp("Resolve")
pm = resolve.GetProjectManager()
project = pm.CreateProject(f"api-verify2-{time.strftime('%m%d-%H%M%S')}")
project.SetSetting("timelineFrameRate", "25")
mp = project.GetMediaPool()

print("=== A（复验）：纯音频 recordFrame 入轨 ===")
audio_item = mp.ImportMedia([MUSIC])[0]
tl = mp.CreateEmptyTimeline("audio-only-test")
project.SetCurrentTimeline(tl)
start = tl.GetStartFrame()
print(f"起始帧 {start}")

# 音频放 A1 轨、时间线 4 秒处，取素材前 3 秒
r = mp.AppendToTimeline([{"mediaPoolItem": audio_item, "startFrame": 0, "endFrame": 74,
                          "mediaType": 2, "trackIndex": 1, "recordFrame": start + 100}])
print(f"append 返回: {r}")
for t in range(1, (tl.GetTrackCount("audio") or 0) + 1):
    for it in tl.GetItemListInTrack("audio", t) or []:
        print(f"  A{t}: {it.GetName()} start={it.GetStart()} (期望 {start + 100}) dur={it.GetDuration()} (期望 75)")

# 两段音频同轨不同位置（模拟多段配乐/音效对位）
r2 = mp.AppendToTimeline([{"mediaPoolItem": audio_item, "startFrame": 0, "endFrame": 24,
                           "mediaType": 2, "trackIndex": 1, "recordFrame": start + 250}])
items = tl.GetItemListInTrack("audio", 1) or []
print(f"第二段后 A1 轨 {len(items)} 项: " + "; ".join(f"start={i.GetStart()} dur={i.GetDuration()}" for i in items))

print("\n=== B（复验）：转场对象属性 + FCPXML 1.9 回读 ===")
# 上一轮项目里已导入的 transition-import-test 时间线还在另一个项目里；重新导入一次
fcpxml_src = SCRATCH / "transition_test.fcpxml"
tl2 = mp.ImportTimelineFromFile(str(fcpxml_src), {"timelineName": "transition-roundtrip"})
print(f"导入: {tl2.GetName() if tl2 else None}")
project.SetCurrentTimeline(tl2)
for it in tl2.GetItemListInTrack("video", 1) or []:
    props = it.GetProperty() or {}
    print(f"  V1: name={it.GetName()!r} start={it.GetStart()} dur={it.GetDuration()} "
          f"type_hint={'transition' if it.GetMediaPoolItem() is None else 'clip'}")

out19 = SCRATCH / "roundtrip19.fcpxml"
ok = tl2.Export(str(out19), resolve.EXPORT_FCPXML_1_9)
if ok and out19.is_file():
    content = out19.read_text(encoding="utf-8")
    idx = content.find("<transition")
    print(f"FCPXML1.9 导出 ok，<transition> {'保留 ✅' if idx >= 0 else '丢失 ❌'}")
    if idx >= 0:
        print("  片段: " + content[idx:idx + 120].replace("\n", " "))
else:
    print(f"Export 1.9 失败（ok={ok}, is_file={out19.is_file()}）")

# 顺带验证 EDL 导入路径的转场（C 附加实验）：EDL 叠化
print("\n=== C（附加）：EDL 叠化导入 ===")
SEG1 = "/Users/may/program/media-creative-assistant/data/output/plan_5/segments/seg_001.mp4"
SEG2 = "/Users/may/program/media-creative-assistant/data/output/plan_5/segments/seg_002.mp4"
edl = """TITLE: edl-dissolve-test
FCM: NON-DROP FRAME

001  seg_001  V     C        00:00:00:00 00:00:03:00 01:00:00:00 01:00:03:00
002  seg_001  V     C        00:00:03:00 00:00:03:00 01:00:03:00 01:00:03:00
002  seg_002  V     D    025 00:00:01:00 00:00:04:00 01:00:03:00 01:00:06:00
"""
edl_path = SCRATCH / "dissolve_test.edl"
edl_path.write_text(edl, encoding="utf-8")
# EDL 导入需要素材已在媒体池
mp.ImportMedia([SEG1, SEG2])
tl3 = mp.ImportTimelineFromFile(str(edl_path), {"timelineName": "edl-dissolve-test"})
if tl3:
    project.SetCurrentTimeline(tl3)
    for it in tl3.GetItemListInTrack("video", 1) or []:
        print(f"  V1: name={it.GetName()!r} start={it.GetStart()} dur={it.GetDuration()}")
else:
    print("EDL 导入失败")

pm.SaveProject()
print("\ndone")
