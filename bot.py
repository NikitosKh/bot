import logging, os, re, subprocess, tempfile, threading
from datetime import timedelta
from pathlib import Path


import streamlit as st
import yt_dlp
from imageio_ffmpeg import get_ffmpeg_exe
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext

FFMPEG_BIN = get_ffmpeg_exe()
CLIP_RE = re.compile(
    r"^\s*(?P<url>https?://\S+)\s+"
    r"(?P<t1>\d+(?::\d{1,2}){0,2})\s+"
    r"(?P<t2>\d+(?::\d{1,2}){0,2})\s*$"
)


def hms_to_sec(ts: str) -> int:
    p = [int(x) for x in ts.split(":")]
    while len(p) < 3: p.insert(0, 0)
    h, m, s = p
    return h * 3600 + m * 60 + s


def clip_youtube(url: str, t1: str, t2: str, out: Path) -> None:
    ydl = yt_dlp.YoutubeDL({
        "quiet": True,
        "skip_download": True,
        "format": "best[ext=mp4][vcodec!*=av01]/best[ext=mp4]/best",
    })
    info = ydl.extract_info(url, download=False)
    stream_url = info.get(
        "url",
        next(f["url"] for f in info["formats"]
             if f.get("ext") == "mp4" and f.get("acodec") != "none")
    )

    s0, s1 = hms_to_sec(t1), hms_to_sec(t2)
    if s1 <= s0: raise ValueError("end ≤ start")
    dur = s1 - s0

    cmd = [FFMPEG_BIN, "-hide_banner", "-loglevel", "error",
           "-ss", str(timedelta(seconds=s0)), "-i", stream_url,
           "-t", str(dur), "-c", "copy", "-avoid_negative_ts", "make_zero",
           str(out)]
    subprocess.run(cmd, check=True)


def h_start(upd: Update, _: CallbackContext) -> None:
    upd.message.reply_text(
        "Hi! Send /clip <url> <start> <end> and I'll return the snippet.\n"
        "Example:\n/clip https://youtu.be/dQw4w9WgXcQ 0:30 1:00"
    )

def h_clip(upd: Update, _: CallbackContext) -> None:
    if not upd.message or not upd.message.text: return
    args = upd.message.text.split(maxsplit=3)[1:]
    if len(args) < 3:
        upd.message.reply_text(
            "Usage:\n/clip <YouTube-URL> <start> <end>\n"
            "Example: /clip https://youtu.be/abc123 0:30 1:15"
        ); return
    m = CLIP_RE.match(" ".join(args))
    if not m:
        upd.message.reply_text("Couldn’t parse that 🤔"); return
    url, t1, t2 = m["url"], m["t1"], m["t2"]
    note = upd.message.reply_text("⏳ Clipping…")

    try:
        with tempfile.TemporaryDirectory() as td:
            clip_path = Path(td)/"clip.mp4"
            clip_youtube(url, t1, t2, clip_path)
            upd.message.reply_video(
                video=clip_path.open("rb"), supports_streaming=True,
                caption=f"[{t1}–{t2}] of {url}")
    except Exception as e:
        logging.exception("clip error")
        note.edit_text(f"⚠️ Failed: {e}")
    else:
        note.delete()


def run_bot() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s:%(name)s:%(message)s")
    token = os.getenv("TG_BOT_TOKEN")
    if not token:
        raise RuntimeError("Set TG_BOT_TOKEN env var")

    updater = Updater(token)               
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", h_start))
    dp.add_handler(CommandHandler("clip",  h_clip))

    logging.info("Bot thread online.")
    updater.start_polling(drop_pending_updates=True)
    updater.idle(stop_signals=())  


st.set_page_config(page_title="YouTube Clip Bot", page_icon="🎬")

@st.cache_resource
def launch_bot_once() -> threading.Thread:
    t = threading.Thread(target=run_bot, daemon=True)
    t.start()
    return t

launch_bot_once()                 

st.title("🎬 YouTube Clip Bot for Telegram")
st.success("Bot is running in the background.  Open Telegram and talk to it!")

st.write(
    """
Send:

/clip https://youtu.be/dQw4w9WgXcQ 0:30 1:00
and the bot will reply with the trimmed MP4 clip.
"""
)

st.caption(
    "You may close this tab; Streamlit will keep the bot alive on the server."
)


