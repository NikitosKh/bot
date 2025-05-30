import asyncio, logging, os, re, subprocess, tempfile, threading, sys
from datetime import timedelta
from pathlib import Path


import streamlit as st
import yt_dlp
from imageio_ffmpeg import get_ffmpeg_exe

try:                   
    
    from telegram import Update
    from telegram.ext import (
        ApplicationBuilder,
        CommandHandler,
        ContextTypes,
    )
    PTB_MODE = "async"
except ImportError:                 
    from telegram import Update
    from telegram.ext import (
        Updater,
        CommandHandler,
        CallbackContext,
    )
    PTB_MODE = "sync"

FFMPEG_BIN = get_ffmpeg_exe()
CLIP_RE = re.compile(
    r"^\s*(?P<url>https?://\S+)"
    r"\s+(?P<t1>\d+(?::\d{1,2}){0,2})"
    r"\s+(?P<t2>\d+(?::\d{1,2}){0,2})\s*$"
)


def hms_to_sec(ts: str) -> int:
    parts = [int(p) for p in ts.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, s = parts
    return h * 3600 + m * 60 + s


def clip_youtube(url: str, t1: str, t2: str, outfile: Path) -> None:
    info = yt_dlp.YoutubeDL(
        {"quiet": True, "skip_download": True,
         "format": "best[ext=mp4][vcodec!*=av01]/best[ext=mp4]/best"}
    ).extract_info(url, download=False)

    stream_url = info.get(
        "url",
        next(f["url"] for f in info["formats"]
             if f.get("ext") == "mp4" and f.get("acodec") != "none"),
    )

    s0, s1 = hms_to_sec(t1), hms_to_sec(t2)
    if s1 <= s0:
        raise ValueError("end timestamp must be later than start timestamp")

    subprocess.run(
        [FFMPEG_BIN, "-hide_banner", "-loglevel", "error",
         "-ss", str(timedelta(seconds=s0)), "-i", stream_url,
         "-t", str(s1 - s0), "-c", "copy", "-avoid_negative_ts", "make_zero",
         str(outfile)],
        check=True
    )


def _parse_or_reply(message_text, reply_fn):
    parts = message_text.split(maxsplit=3)[1:]  
    if len(parts) < 3:
        reply_fn("Usage:\n/clip <YouTube-URL> <start> <end>")
        return None
    m = CLIP_RE.match(" ".join(parts))
    if not m:
        reply_fn("Couldn’t parse that 🤔")
        return None
    return m["url"], m["t1"], m["t2"]


if PTB_MODE == "async":
    async def h_start(update: Update, _: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "Hi!  Send /clip <url> <start> <end>\n"
            "Example: /clip https://youtu.be/dQw4w9WgXcQ 0:30 1:00"
        )

    async def h_clip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not update.message:
            return
        parsed = _parse_or_reply(update.message.text, update.message.reply_text)
        if not parsed:
            return
        url, t1, t2 = parsed
        note = await update.message.reply_text("⏳ Clipping…")

        loop = asyncio.get_running_loop()
        try:
            with tempfile.TemporaryDirectory() as td:
                out = Path(td) / "clip.mp4"
                await loop.run_in_executor(None, clip_youtube, url, t1, t2, out)
                await update.message.reply_video(
                    video=out.open("rb"), supports_streaming=True,
                    caption=f"[{t1}–{t2}] of {url}")
        except Exception as e:
            logging.exception("clip error")
            await note.edit_text(f"⚠️ Failed: {e}")
        else:
            await note.delete()

else:
    def h_start(update: Update, _: CallbackContext):
        update.message.reply_text(
            "Hi!  Send /clip <url> <start> <end>\n"
            "Example: /clip https://youtu.be/dQw4w9WgXcQ 0:30 1:00"
        )

    def h_clip(update: Update, _: CallbackContext):
        if not update.message:
            return
        parsed = _parse_or_reply(update.message.text, update.message.reply_text)
        if not parsed:
            return
        url, t1, t2 = parsed
        note = update.message.reply_text("⏳ Clipping…")
        try:
            with tempfile.TemporaryDirectory() as td:
                out = Path(td) / "clip.mp4"
                clip_youtube(url, t1, t2, out)
                update.message.reply_video(
                    video=out.open("rb"), supports_streaming=True,
                    caption=f"[{t1}–{t2}] of {url}")
        except Exception as e:
            logging.exception("clip error")
            note.edit_text(f"Failed: {e}")
        else:
            note.delete()

def run_bot() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s:%(name)s:%(message)s")

    token = os.getenv("TG_BOT_TOKEN") or sys.exit("Set TG_BOT_TOKEN")

    if PTB_MODE == "async":           

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)      

        app = (ApplicationBuilder()
               .token(token)
               .concurrent_updates(True)
               .build())
        app.add_handler(CommandHandler("start", h_start))
        app.add_handler(CommandHandler("clip",  h_clip))

        logging.info("Bot (async) online.")
        app.run_polling(drop_pending_updates=True)   
        


st.set_page_config(page_title="YouTube Clip Bot", page_icon="🎬")

@st.cache_resource
def launch_once():
    t = threading.Thread(target=run_bot, daemon=True)
    t.start()
    return t

launch_once()

st.title("🎬 YouTube Clip Bot for Telegram")
st.success(f"Bot running in {PTB_MODE.upper()} mode.  Open Telegram and /clip!")

st.write("""
Example command:



/clip https://youtu.be/ESXOAJRdcwQ 0:30 1:15


and you'll get the trimmed MP4 back.
"""
)

st.caption("You may close this tab; Streamlit will keep the bot alive.")

