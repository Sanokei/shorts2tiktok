"""Record the app-side half of the TikTok review demo.

Drives the real app in a real browser against the real APIs and saves the
footage to review/app-demo.mp4. Nothing here is staged: the channel listing is
a live YouTube Data API call and the account name in the header comes back from
user.info.basic.

The TikTok consent screen and the finished post are not in here and cannot be.
Those are your session, so film them yourself and splice them on. Run this with
the app already running on 127.0.0.1:8712.
"""
import pathlib
import subprocess
import sys
import time

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "review"
APP_URL = "http://127.0.0.1:8712/"

CAPTION_CSS = """
#demo-caption {
  position: fixed; left: 0; right: 0; bottom: 0; z-index: 9999;
  background: rgba(10,11,13,.94); border-top: 1px solid #2a2e36;
  color: #e8e6e3; font: 15px/1.4 system-ui, sans-serif;
  padding: 14px 22px; letter-spacing: -0.01em;
}
#demo-caption b { color: #25f4ee; font-weight: 600; }
"""


def caption(page, text, hold=3.0):
    page.evaluate(
        """([text, css]) => {
            let el = document.getElementById('demo-caption');
            if (!el) {
                const style = document.createElement('style');
                style.textContent = css;
                document.head.appendChild(style);
                el = document.createElement('div');
                el.id = 'demo-caption';
                document.body.appendChild(el);
            }
            el.innerHTML = text;
        }""",
        [text, CAPTION_CSS],
    )
    time.sleep(hold)


def main():
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            record_video_dir=str(OUT / "raw"),
            record_video_size={"width": 1280, "height": 800},
        )
        page = context.new_page()
        page.goto(APP_URL, wait_until="networkidle")
        time.sleep(1.5)

        caption(page,
                "<b>Shorts Porter</b> runs on the creator's own machine. "
                "No server, no other users.", 4)

        caption(page,
                "<b>Login Kit, user.info.basic</b>: the header names the TikTok "
                "account this install is authorized for, read back from the API.", 4)
        page.hover("#tt-pill")
        time.sleep(2)

        caption(page,
                "The creator's own YouTube key and channel. Credentials stay in a "
                "file on this machine.", 4)

        caption(page,
                "<b>Finding the creator's own Shorts</b> through the YouTube Data API.", 3)
        page.click("#scan")
        page.wait_for_selector("table tbody tr", timeout=120000)
        time.sleep(3)

        caption(page,
                "Every Short on the channel, with the ones already sent to TikTok "
                "marked so nothing is posted twice.", 4.5)
        page.mouse.wheel(0, 500)
        time.sleep(2.5)
        page.mouse.wheel(0, 500)
        time.sleep(2.5)
        page.mouse.wheel(0, -1000)
        time.sleep(1.5)

        caption(page,
                "The creator picks which videos to move. Nothing is uploaded "
                "without this explicit choice.", 4)
        boxes = page.locator("table tbody input[type=checkbox]")
        for i in range(min(6, boxes.count())):
            boxes.nth(i).uncheck()
            time.sleep(0.25)
        for i in range(3):
            boxes.nth(i).check()
            time.sleep(0.5)
        time.sleep(2)

        caption(page,
                "<b>Content Posting API, video.upload</b>: the default. The video "
                "goes to the creator's own TikTok inbox and they finish the post "
                "inside TikTok.", 5)
        page.select_option("#mode", "inbox")
        time.sleep(2.5)

        caption(page,
                "<b>video.publish</b>: the optional mode, for publishing without "
                "leaving the desktop. The audience comes from creator_info and the "
                "creator chooses it here.", 5)
        page.select_option("#mode", "direct")
        time.sleep(3)

        caption(page,
                "The caption is built from the creator's own YouTube title and tags, "
                "and they can edit the template.", 4)
        page.mouse.wheel(0, 400)
        time.sleep(3)

        caption(page,
                "Each video can also be saved locally, since it is the creator's "
                "own footage.", 4)

        caption(page, "", 0.5)
        context.close()
        browser.close()

    raw = sorted((OUT / "raw").glob("*.webm"))
    if not raw:
        sys.exit("no video was recorded")
    source = raw[-1]
    target = OUT / "app-demo.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(source),
        "-c:v", "libx264", "-preset", "slow", "-crf", "26",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-vf", "scale=1280:-2", str(target),
    ], check=True)
    print("wrote %s (%.1f MB)" % (target, target.stat().st_size / 1e6))


if __name__ == "__main__":
    main()
