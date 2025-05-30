#!/usr/bin/env python3
import logging, os, re, subprocess, tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

from imageio_ffmpeg import get_ffmpeg_exe
import yt_dlp

from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from telegram.error import TimedOut
from telegram import Update
from telegram.constants import ChatAction

FFMPEG_BIN = get_ffmpeg_exe()
EXECUTOR = ThreadPoolExecutor(max_workers=os.cpu_count() or 2)
TIMEOUTS = dict(              
    read_timeout=120, write_timeout=120,
    connect_timeout=120, pool_timeout=120,
)
CLIP_RE = re.compile(
    r"^\s*(?P<url>https?://\S+)\s+(?P<t1>\d+(?::\d{1,2}){0,2})\s+"
    r"(?P<t2>\d+(?::\d{1,2}){0,2})\s*$"
)

def _hms_to_s(ts: str) -> int:
    p = [int(x) for x in ts.split(":")]
    while len(p) < 3: p.insert(0, 0)
    h, m, s = p
    return h*3600 + m*60 + s


async def _clip(url: str, t1: str, t2: str, out: Path) -> None:
    loop = asyncio.get_running_loop()
    info = await loop.run_in_executor(
        EXECUTOR,
        lambda: yt_dlp.YoutubeDL(
            {"quiet": True, "skip_download": True,
             "format": "best[ext=mp4][vcodec!*=av01]/best[ext=mp4]/best"}
        ).extract_info(url, download=False)
    )
    stream_url = info.get(
        "url",
        next(f["url"] for f in info["formats"]
             if f.get("ext") == "mp4" and f.get("acodec") != "none"),
    )
    s0, s1 = _hms_to_s(t1), _hms_to_s(t2)
    if s1 <= s0: raise ValueError("end ≤ start")
    dur = s1 - s0
    cmd = [FFMPEG_BIN, "-hide_banner", "-loglevel", "error",
           "-ss", str(timedelta(seconds=s0)), "-i", stream_url,
           "-t", str(dur), "-c", "copy", "-avoid_negative_ts", "make_zero",
           str(out)]
    await loop.run_in_executor(EXECUTOR, lambda: subprocess.run(cmd, check=True))


async def clip_cmd(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ctx.args or len(ctx.args) < 3:
        await upd.message.reply_text(
            "Usage:\n/clip <YouTube-URL> <start> <end>\n"
            "Example: /clip https://youtu.be/ESXOAJRdcwQ 0:30 1:15"
        ); return
    m = CLIP_RE.match(" ".join(ctx.args))
    if not m:
        await upd.message.reply_text("Can't parse that 🤔"); return
    url, t1, t2 = m["url"], m["t1"], m["t2"]

    note = await upd.message.reply_text("⏳ Clipping…")
    await ctx.bot.send_chat_action(chat_id=upd.effective_chat.id,
                                   action=ChatAction.UPLOAD_VIDEO)
    try:
        with tempfile.TemporaryDirectory() as td:
            clip = Path(td)/"clip.mp4"
            await _clip(url, t1, t2, clip)
            try:
                await upd.message.reply_video(
                    video=clip.open("rb"), supports_streaming=True,
                    caption=f"[{t1}–{t2}] of {url}", **TIMEOUTS)
            except TimedOut:
                logging.warning("Upload still in progress, Telegram timed out")
    except Exception as e:
        logging.exception("clip error")
        await note.edit_text(f"⚠️ Failed: {e}")
    else:
        await note.delete()


async def start(upd: Update, _):
    await upd.message.reply_text(
        "Hi! Send /clip <url> <start> <end> and I’ll hand back the snippet. Example format: /clip https://www.youtube.com/watch?v=lNGFAI7R0PE 00:00:30 00:01:15")


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s:%(name)s:%(message)s")
    app = (ApplicationBuilder()
           .token(os.environ["TG_BOT_TOKEN"])
           .concurrent_updates(True)
           .read_timeout(120).write_timeout(120)
           .connect_timeout(120).pool_timeout(120)
           .build())

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clip", clip_cmd))

    logging.info("Bot online — Ctrl-C to stop")
    app.run_polling(drop_pending_updates=True, allowed_updates=["message"])


if __name__ == "__main__":
    import asyncio, sys
    if sys.platform == "win32":       
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    main()
