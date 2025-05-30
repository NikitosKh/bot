import asyncio
import logging
import os
import re
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import streamlit as st
import yt_dlp
from imageio_ffmpeg import get_ffmpeg_exe
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from telegram.error import TimedOut

FFMPEG_BIN = get_ffmpeg_exe()
EXECUTOR   = ThreadPoolExecutor(max_workers=os.cpu_count() or 2)
TIMEOUTS   = dict(
    read_timeout=120,
    write_timeout=120,
    connect_timeout=120,
    pool_timeout=120,
)
CLIP_RE = re.compile(
    r"^\s*(?P<url>https?://\S+)\s+"
    r"(?P<t1>\d+(?::\d{1,2}){0,2})\s+"
    r"(?P<t2>\d+(?::\d{1,2}){0,2})\s*$"
)

def _hms_to_sec(ts: str) -> int:
    parts = [int(x) for x in ts.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, s = parts
    return h * 3600 + m * 60 + s


async def _clip(url: str, t1: str, t2: str, outfile: Path) -> None:
    """Download *url*, cut [t1,t2) into *outfile* with ffmpeg."""
    loop = asyncio.get_running_loop()

    info = await loop.run_in_executor(
        EXECUTOR,
        lambda: yt_dlp.YoutubeDL(
            {"quiet": True,
             "skip_download": True,
             "format": "best[ext=mp4][vcodec!*=av01]/best[ext=mp4]/best"}
        ).extract_info(url, download=False)
    )
    stream_url = info.get(
        "url",
        next(
            f["url"]
            for f in info["formats"]
            if f.get("ext") == "mp4" and f.get("acodec") != "none"
        ),
    )

    s0, s1 = _hms_to_sec(t1), _hms_to_sec(t2)
    if s1 <= s0:
        raise ValueError("end timestamp must be later than start timestamp")
    duration = s1 - s0

    cmd = [
        FFMPEG_BIN,
        "-hide_banner", "-loglevel", "error",
        "-ss", str(timedelta(seconds=s0)),
        "-i", stream_url,
        "-t", str(duration),
        "-c", "copy",
        "-avoid_negative_ts", "make_zero",
        str(outfile),
    ]
    await loop.run_in_executor(EXECUTOR, lambda: subprocess.run(cmd, check=True))


async def clip_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/clip <url> <start> <end>"""
    if not ctx.args or len(ctx.args) < 3:
        await update.message.reply_text(
            "Usage:\n/clip <YouTube-URL> <start> <end>\n"
            "Example: /clip https://youtu.be/ESXOAJRdcwQ 0:30 1:15"
        )
        return

    m = CLIP_RE.match(" ".join(ctx.args))
    if not m:
        await update.message.reply_text("Couldn’t parse that 🤔")
        return

    url, t1, t2 = m["url"], m["t1"], m["t2"]
    note = await update.message.reply_text("⏳ Clipping…")
    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id,
                                   action=ChatAction.UPLOAD_VIDEO)

    try:
        with tempfile.TemporaryDirectory() as td:
            clip_path = Path(td) / "clip.mp4"
            await _clip(url, t1, t2, clip_path)

            try:
                await update.message.reply_video(
                    video=clip_path.open("rb"),
                    supports_streaming=True,
                    caption=f"[{t1}–{t2}] of {url}",
                    **TIMEOUTS,
                )
            except TimedOut:
                logging.warning("Telegram upload still running (TimedOut)")
    except Exception as e:
        logging.exception("clip error")
        await note.edit_text(f"⚠️ Failed: {e}")
    else:
        await note.delete()


async def start_command(update: Update, _):
    await update.message.reply_text(
        "Hi! Send /clip <url> <start> <end> and I’ll return your snippet. Example: /clip https://www.youtube.com/watch?v=lNGFAI7R0PE 00:00:30 00:01:15"
    )


def bot_thread():
    asyncio.set_event_loop(asyncio.new_event_loop())
    token = os.environ.get("TG_BOT_TOKEN")
    if not token:
        raise RuntimeError("Set TG_BOT_TOKEN environment variable")

    app = (
        ApplicationBuilder()
        .token(token)
        .concurrent_updates(True)
        .read_timeout(120).write_timeout(120)
        .connect_timeout(120).pool_timeout(120)
        .build()
    )
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("clip",  clip_command))

    logging.info("Telegram bot started (background thread)")
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=["message"],
        stop_signals=None,     
        close_loop=False,         
    )




st.set_page_config(page_title="YouTube Clip Bot", page_icon="🎬")
st.title("🎬 YouTube Clip Bot for Telegram")

st.write(
    """
Talk to your Telegram bot and send a command like:
/clip https://youtu.be/ESXOAJRdcwQ 0:30 1:15

The bot will reply with the trimmed MP4.
"""
)


if "bot_started" not in st.session_state:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s:%(name)s:%(message)s",
    )
    threading.Thread(target=bot_thread, daemon=True).start()
    st.session_state.bot_started = True
    st.success("Background Telegram bot started")

st.caption(
    "This page just keeps the bot alive. "
    "You may close the tab—Streamlit will continue running."
)
