"""ReviewLens 演示录屏：用 Playwright 录真实浏览器（不碰用户桌面）。

要点：
- record_video 录 WebM；无头浏览器录不到鼠标指针，所以注入一个合成指针 + 点击波纹
  （onetake 的硬规则："因必须可见"，否则页面像自己动的）。
- 每个阶段记录 time.monotonic() 偏移，写 demo_timing.json，供旁白/字幕精确对齐。
- 全程真实 paced：打字有 delay、每步之间有停顿，避免录出来像快放。
"""
import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8800"
OUT = Path("D:/projects/reviewlens/demo")
VIDEO_DIR = OUT / "raw"
QUESTION = "物流方面用户在抱怨什么？"
NOTE = "请写明结论依据的是哪几条评论，并把两个口径的冲突说清楚"

CURSOR_JS = """
() => {
  if (window.__curReady) return;
  window.__curReady = true;
  let C = null, D = null;
  const mk = (tag, css) => { const d = document.createElement(tag); Object.assign(d.style, css); return d; };
  const ensure = () => {
    if (C || !document.body) return !!C;
    C = mk('div', {position:'fixed', left:'0', top:'0', width:'22px', height:'22px',
      borderRadius:'50%', background:'rgba(255,255,255,.85)', border:'2px solid #1f2937',
      boxShadow:'0 2px 10px rgba(0,0,0,.35)', pointerEvents:'none', zIndex:'999999',
      transform:'translate(-50%,-50%)'});
    D = mk('div', {position:'fixed', left:'0', top:'0', width:'6px', height:'6px', borderRadius:'50%',
      background:'#1f2937', pointerEvents:'none', zIndex:'1000000', transform:'translate(-50%,-50%)'});
    document.body.appendChild(C); document.body.appendChild(D);
    return true;
  };
  window.__cur = {x: 80, y: 80};
  window.__move = (x, y) => {
    window.__cur.x = x; window.__cur.y = y;
    if (!ensure()) return;
    C.style.left = x + 'px'; C.style.top = y + 'px';
    D.style.left = x + 'px'; D.style.top = y + 'px';
  };
  window.__ripple = (x, y) => {
    if (!ensure()) return;
    const r = mk('div', {position:'fixed', left:x+'px', top:y+'px', width:'10px', height:'10px',
      borderRadius:'50%', border:'2px solid #2563eb', pointerEvents:'none', zIndex:'999998',
      transform:'translate(-50%,-50%)', opacity:'1'});
    document.body.appendChild(r);
    try {
      r.animate([{width:'10px',height:'10px',opacity:1},{width:'70px',height:'70px',opacity:0}],
               {duration:520, easing:'ease-out'}).onfinish = () => r.remove();
    } catch (e) { setTimeout(() => r.remove(), 520); }
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', ensure);
  } else { ensure(); }
}
"""


def main() -> None:
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    for old in VIDEO_DIR.glob("*.webm"):
        old.unlink()
    phases: list[dict] = []
    t0 = time.monotonic()

    def mark(name: str) -> None:
        phases.append({"phase": name, "t": round(time.monotonic() - t0, 3)})
        print(f"   [{round(time.monotonic()-t0,2):>6}s] {name}")

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True,
                                    args=["--force-device-scale-factor=1", "--hide-scrollbars"])
        ctx = browser.new_context(viewport={"width": 1600, "height": 900},
                                  record_video_dir=str(VIDEO_DIR),
                                  record_video_size={"width": 1600, "height": 900})
        page = ctx.new_page()
        page.add_init_script(f"({CURSOR_JS})();")
        mark("navigate")
        page.goto(BASE, wait_until="load")
        page.wait_for_selector("#q", state="visible")
        # 指针只是视觉增强，失败不能让整轮录制白跑：容错 + 打印诊断
        try:
            page.wait_for_function("() => window.__curReady === true", timeout=8000)
            cur_ok = page.evaluate("() => typeof window.__move === 'function'")
            print(f"   cursor overlay ready: {cur_ok}")
        except Exception as e:
            print(f"   ⚠️ 指针覆盖层未就绪（继续录制）: {type(e).__name__}")
            page.evaluate("""() => {
              window.__move = () => {};
              window.__ripple = () => {};
            }""")
        page.mouse.move(0, 0)
        page.evaluate("() => window.__move(1200, 780)")
        mark("page_ready")
        page.wait_for_timeout(1600)

        # 1) 输入问题（逐字，带光标跟随）
        box = page.locator("#q").bounding_box()
        mark("hover_input")
        page.evaluate(f"() => window.__move({box['x']+box['width']*0.5}, {box['y']+box['height']*0.5})")
        page.wait_for_timeout(700)
        page.evaluate(f"() => window.__ripple({box['x']+box['width']*0.5}, {box['y']+box['height']*0.5})")
        page.click("#q")
        mark("type_question")
        page.type("#q", QUESTION, delay=95)
        page.wait_for_timeout(500)

        # 2) 点发送
        sb = page.locator("#send").bounding_box()
        mark("hover_send")
        page.evaluate(f"() => window.__move({sb['x']+sb['width']/2}, {sb['y']+sb['height']/2})")
        page.wait_for_timeout(650)
        page.evaluate(f"() => window.__ripple({sb['x']+sb['width']/2}, {sb['y']+sb['height']/2})")
        page.click("#send")
        mark("click_send")

        # 3) 等进度跑完、审批面板出现（真等待，不截断）
        page.wait_for_selector("#review", state="visible", timeout=120_000)
        # 面板在首屏下方：一出现就滚过去，否则观众看不到"待审批"这个关键动作
        page.locator("#review").scroll_into_view_if_needed()
        mark("review_panel_visible")
        page.wait_for_timeout(2600)

        # 4) 填拒绝意见
        nb = page.locator("#note").bounding_box()
        mark("hover_note")
        page.evaluate(f"() => window.__move({nb['x']+nb['width']/2}, {nb['y']+nb['height']/2})")
        page.wait_for_timeout(600)
        page.evaluate(f"() => window.__ripple({nb['x']+nb['width']/2}, {nb['y']+nb['height']/2})")
        page.click("#note")
        mark("type_note")
        page.type("#note", NOTE, delay=28)
        page.wait_for_timeout(500)

        # 5) 拒绝 → 等第二轮重写
        nbb = page.locator("#no").bounding_box()
        mark("hover_reject")
        page.evaluate(f"() => window.__move({nbb['x']+nbb['width']/2}, {nbb['y']+nbb['height']/2})")
        page.wait_for_timeout(700)
        page.evaluate(f"() => window.__ripple({nbb['x']+nbb['width']/2}, {nbb['y']+nbb['height']/2})")
        page.click("#no")
        mark("click_reject")
        # 拒绝后审批面板会隐藏、草稿重写，等它重新出现且真的是第 2 轮
        # （坑：第一轮文案是「第 1 / 2 轮」，含 "2"，所以不能只判 includes('2')）
        page.wait_for_function(
            "() => /第\\s*2\\s*\\//.test((document.querySelector('#round') || {}).textContent || '')",
            timeout=180_000)
        page.wait_for_selector("#review", state="visible", timeout=180_000)
        mark("round2_rewritten")
        page.wait_for_timeout(2600)

        # 6) 通过并发布
        page.locator("#ok").wait_for(state="visible", timeout=180_000)
        page.locator("#review").scroll_into_view_if_needed()
        ob = page.locator("#ok").bounding_box()
        mark("hover_approve")
        page.evaluate(f"() => window.__move({ob['x']+ob['width']/2}, {ob['y']+ob['height']/2})")
        page.wait_for_timeout(750)
        page.evaluate(f"() => window.__ripple({ob['x']+ob['width']/2}, {ob['y']+ob['height']/2})")
        page.click("#ok")
        mark("click_approve")
        page.wait_for_selector("#banner:not([hidden])", timeout=120_000)
        mark("published")
        page.wait_for_timeout(3000)
        page.evaluate("() => window.__move(1400, 860)")
        page.wait_for_timeout(1200)
        mark("end")

        video = page.video.path()
        ctx.close()
        browser.close()

    total = round(time.monotonic() - t0, 3)
    (OUT / "demo_timing.json").write_text(json.dumps(
        {"total": total, "phases": phases, "video": str(video)}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n视频: {video}")
    print(f"总时长: {total}s  阶段数: {len(phases)}")
    print("落盘 -> demo/demo_timing.json")


if __name__ == "__main__":
    main()
