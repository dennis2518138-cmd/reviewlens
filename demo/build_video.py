"""把录屏 + 旁白 + 字幕合成演示视频。

流程：
 1) 读 demo_timing.json，用 (墙钟总时长 - 视频时长) 求录屏起点偏移；
 2) 剪掉"拒绝→重写"之间的 1.6s 空白窗（14.2~15.8 视频时间），后续时间整体前移；
 3) 每句旁白按所属阶段窗口自动适配语速（+8% 起，最多 +30%），保证不压到下一段；
 4) 生成 ASS 字幕（微软雅黑，底部半透明底条）；
 5) ffmpeg：trim+concat → ass 烧字幕 → 旁白按 adelay 定位混音 → H.264/AAC。

用法：D:/python/python.exe demo/build_video.py
产出：C:/Users/Lenovo/Downloads/reviewlens_demo.mp4
"""
import json
import subprocess
from pathlib import Path

DEMO = Path("D:/projects/reviewlens/demo")
RAW = DEMO / "raw"
VO = DEMO / "vo"
OUT_MP4 = Path("C:/Users/Lenovo/Downloads/reviewlens_demo.mp4")
PY = "D:/python/python.exe"
VOICE = "zh-CN-YunyangNeural"   # News / Professional —— 播音腔，社区里最"讲解感"的中文音色
CUT_FROM, CUT_TO = 14.2, 15.8   # 视频时间轴上的空白窗


def sh(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def dur(path: Path) -> float:
    r = sh(["ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1", str(path)])
    return float(r.stdout.strip())


# 旁白：文本 + 所属窗口的墙钟阶段区间（clock 秒）
LINES = [
    ("评论分析，先审再发。", "page_ready", "type_question"),
    ("先问一句。", "type_question", "click_send"),
    ("检索真实评论，拆任务、算统计、跨口径对比。", "click_send", "review_panel_visible"),
    ("报告写完了不直接发布，先停下来等人工审批。", "review_panel_visible", "click_reject"),
    ("点拒绝，它重写一版。", "click_reject", "round2_rewritten"),
    ("重写会标明依据哪几条评论。", "round2_rewritten", "click_approve"),
    ("通过才发布，单号和字数都留痕。", "click_approve", "end"),
]


def main() -> None:
    import os
    os.chdir(DEMO)  # ass=subs.ass 走相对路径，必须先切到 demo/
    timing = json.loads((DEMO / "demo_timing.json").read_text(encoding="utf-8"))
    video = Path(timing["video"])
    vdur = dur(video)
    offset = timing["total"] - vdur                     # 墙钟 → 视频时间
    print(f"视频 {vdur:.2f}s  墙钟 {timing['total']:.2f}s  起点偏移 {offset:.2f}s")

    ph = {p["phase"]: p["t"] for p in timing["phases"]}

    def to_video(clock_t: float) -> float:
        t = clock_t - offset
        if t <= CUT_FROM:
            return t
        if t >= CUT_TO:
            return t - (CUT_TO - CUT_FROM)
        return CUT_FROM

    vo_total = vdur - (CUT_TO - CUT_FROM)
    print(f"剪掉空白 {CUT_TO-CUT_FROM:.1f}s 后，成片时长应为 {vo_total:.2f}s")

    # ---- 1) 逐句合成，自动适配窗口 ----
    clips = []
    for i, (text, a, b) in enumerate(LINES, 1):
        win_start = max(0.05, to_video(ph[a]))
        win_end = to_video(ph[b])
        window = max(0.8, win_end - win_start - 0.12)
        best = None
        for rate in (0, 4, 8, 12, 16):
            out = VO / f"line{i:02d}_{rate}.mp3"
            r = sh([PY, "-m", "edge_tts", "--voice", VOICE, f"--rate=+{rate}%",
                    "--text", text, "--write-media", str(out)])
            if r.returncode != 0:
                print(f"   ❗line{i} tts 失败: {r.stderr[-200:]}")
                continue
            d = dur(out)
            best = (out, d, rate)
            if d <= window:
                break
        if best is None:
            raise SystemExit(f"line{i} 合成失败")
        out, d, rate = best
        clips.append({"i": i, "text": text, "file": out, "dur": d, "rate": rate,
                      "start": round(win_start, 3), "window": round(window, 3),
                      "fits": d <= window})
        flag = "✅" if d <= window else "⚠️超出窗口"
        print(f"   line{i} rate=+{rate}% {d:.2f}s / 窗口{window:.2f}s {flag}  「{text}」")

    # ---- 2) ASS 字幕 ----
    def ts(t: float) -> str:
        h = int(t // 3600); m = int(t % 3600 // 60); s = t % 60
        return f"{h}:{m:02d}:{s:05.2f}"

    ass = ["[Script Info]", "ScriptType: v4.00+", "PlayResX: 1600", "PlayResY: 900",
           "WrapStyle: 0", "", "[V4+ Styles]",
           "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
           "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
           "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
           # 描边式：无底板，白字 + 深色描边 + 轻微阴影（比灰底条干净，不压界面）
           "Style: Main,Microsoft YaHei,44,&H00FFFFFF,&H00FFFFFF,&H00141414,&H00141414,"
           "0,0,0,0,100,100,0.6,0,1,3.2,0.9,2,90,90,72,1",
           "", "[Events]",
           "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    for c in clips:
        end = min(c["start"] + c["dur"] + 0.35, vo_total - 0.05)
        ass.append(f"Dialogue: 0,{ts(c['start'])},{ts(end)},Main,,0,0,0,,{c['text']}")
    (DEMO / "subs.ass").write_text("\n".join(ass) + "\n", encoding="utf-8")
    print(f"字幕 -> subs.ass（{len(clips)} 条）")

    # ---- 3) ffmpeg：剪+拼+烧字幕+混旁白 ----
    inputs = ["-i", str(video)]
    for c in clips:
        inputs += ["-i", str(c["file"])]
    parts = [
        f"[0:v]trim=0:{CUT_FROM},setpts=PTS-STARTPTS[v1]",
        f"[0:v]trim={CUT_TO},setpts=PTS-STARTPTS[v2]",
        "[v1][v2]concat=n=2:v=1:a=0[vcat]",
        "[vcat]ass=subs.ass[vout]",
        f"anullsrc=r=44100:cl=stereo,atrim=0:{vo_total}[abase]",
    ]
    amix_in = "[abase]"
    for n, c in enumerate(clips, start=1):
        ms = int(c["start"] * 1000)
        parts.append(f"[{n}:a]aresample=44100,adelay={ms}|{ms},volume=1.0[a{n}]")
        amix_in += f"[a{n}]"
    # 旁白响度整形：先压峰值（语音制峰比高，直接拉大会削波），再抬 6dB，最后限幅到 -1.5 dBTP
    parts.append(f"{amix_in}amix=inputs={len(clips)+1}:normalize=0,"
                 f"acompressor=threshold=0.05:ratio=3:attack=8:release=180,"
                 f"volume=10dB,alimiter=limit=0.80,atrim=0:{vo_total}[aout]")
    cmd = (["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(parts),
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
            "-r", "25", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(OUT_MP4)])
    r = sh(cmd)
    if r.returncode != 0:
        print("❗ffmpeg 失败：")
        print(r.stderr[-3000:])
        raise SystemExit(1)

    print(f"\n成片 -> {OUT_MP4}")
    print(f"   时长 {dur(OUT_MP4):.2f}s  大小 {OUT_MP4.stat().st_size/1048576:.2f} MB")
    (DEMO / "build_report.json").write_text(json.dumps(
        {"output": str(OUT_MP4), "duration": round(dur(OUT_MP4), 3),
         "clips": [{k: (str(v) if isinstance(v, Path) else v) for k, v in c.items()} for c in clips],
         "cut": [CUT_FROM, CUT_TO], "offset": round(offset, 3)}, ensure_ascii=False, indent=2),
        encoding="utf-8")


if __name__ == "__main__":
    main()
