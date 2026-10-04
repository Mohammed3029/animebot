import os
import asyncio
import re
import math
import time
import random
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode, ChatMemberStatus
from pyrogram.errors import UserNotParticipant
from aiohttp import web

from database import save_anime_post, add_resolution, get_anime_post, get_force_subs, remove_force_sub, add_force_sub

# --- Environment Variables ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "")
OWNER_ID = int(os.environ.get("OWNER_ID", 0))
STICKER_ID = os.environ.get("STICKER_ID", "CAACAgUAAxkBAAIRlmrCC6ZTNUrmxhub-cIF84bgPZepAAJ1HgACVYJpVeG3f8_krb7yPQQ")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "Aero_Anime_bot").replace("@", "")
FORCE_SUB_IMAGE = os.environ.get("FORCE_SUB_IMAGE", "https://telegra.ph/file/d44b82e8b38a45b8078f9-6d3683b575f0209f33.jpg")

if str(CHANNEL_ID).replace("-", "").isdigit():
    CHANNEL_ID = int(CHANNEL_ID)

PORT = int(os.environ.get("PORT", 8080))

app = Client("anime_uploader_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

DOWNLOAD_DIR = "./downloads"
THUMB_DIR = "./thumbs"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(THUMB_DIR, exist_ok=True)

active_anime_session = {}

def is_owner(_, __, message):
    return message.from_user and message.from_user.id == OWNER_ID

owner_filter = filters.create(is_owner)

# --- Web Server ---

async def handle_health_check(request):
    return web.Response(text="Anime Uploader Bot is running!")

async def start_web_server():
    server = web.Application()
    server.router.add_get("/", handle_health_check)
    runner = web.AppRunner(server)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

# --- Helpers & Progress Formatting ---

def format_bytes(size):
    if not size:
        return "0 B"
    power = 2**10
    n = 0
    power_labels = {0: 'B', 1: 'KB', 2: 'MB', 3: 'GB'}
    while size > power and n < 3:
        size /= power
        n += 1
    return f"{size:.2f} {power_labels[n]}"

def format_time(seconds):
    if not seconds or math.isnan(seconds) or seconds < 0:
        return "0s"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"

def create_hexagonal_bar(current, total):
    if total == 0:
        return "⬡⬡⬡⬡⬡⬡⬡⬡⬡⬡ 0%"
    percentage = (current / total) * 100
    filled = int(percentage // 10)
    filled = min(max(filled, 0), 10)
    bar = "⬢" * filled + "⬡" * (10 - filled)
    return f"{bar} {int(percentage)}%"

def build_initial_download_text(meta_tag, file_title, file_id):
    return (
        "<blockquote>"
        f"<b>›› Anime Name :</b> <i>[{meta_tag}] {file_title} ({file_id})</i>\n\n"
        f"<b>›› Status :</b> <i>downloading</i>\n\n"
        f"<b>›› Powered by @Aero_Unity</b>"
        "</blockquote>"
    )

def build_encoding_progress_text(filename, status_label, current, total, speed, elapsed, eta, encoded_count, task_id):
    bar = create_hexagonal_bar(current, total)
    curr_str = format_bytes(current)
    tot_str = format_bytes(total)
    speed_str = f"{format_bytes(speed)}/s"
    
    return (
        "<blockquote>"
        f"<b>›› Anime Name :</b> <i>{filename}</i>\n\n"
        f"<b>›› Status :</b> <i>{status_label}</i>\n"
        f"<code>{bar}</code>\n\n"
        f"<b>›› Speed :</b> <i>{speed_str}</i>\n"
        f"<b>›› Size :</b> <i>{curr_str}</i> out of <i>{tot_str}</i>\n"
        f"<b>›› Time Took :</b> <i>{format_time(elapsed)}</i>\n"
        f"<b>›› Time Left :</b> <i>{format_time(eta)}</i>\n\n"
        f"<b>›› Files (s) Encoded :</b> <i>{encoded_count}/4</i>\n"
        f"<b>›› Task ID :</b> <i>{task_id}</i>\n\n"
        f"<b>○ Powered by @Aero_Unity</b>"
        "</blockquote>"
    )

def build_quality_keyboard(anime_id, resolutions_dict):
    small_caps_map = {
        "480p": "480ᴘ",
        "720p": "720ᴘ",
        "1080p": "1080ᴘ",
        "HDRip": "ʜᴅʀɪᴘ",
        "4K": "4ᴋ"
    }
    
    qualities_order = ["480p", "720p", "1080p", "HDRip", "4K"]
    buttons = []
    row = []

    for q in qualities_order:
        if q in resolutions_dict:
            text = small_caps_map.get(q, q)
            url = f"https://t.me/{BOT_USERNAME}?start=dl_{anime_id}_{q}"
            row.append(InlineKeyboardButton(text=text, url=url))
            
            if len(row) == 2:
                buttons.append(row)
                row = []
            
    if row:
        buttons.append(row)
        
    return InlineKeyboardMarkup(buttons) if buttons else None

def build_file_delivery_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(text="• ᴜᴘᴅᴀᴛᴇs •", url="https://t.me/Aero_Unity")]
    ])

def format_bold_mono_caption(raw_text):
    if not raw_text or not raw_text.strip():
        return "<b>Anime Post</b>"
    
    lines = raw_text.split("\n")
    formatted_lines = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            formatted_lines.append("")
            continue

        if ":" in stripped:
            parts = stripped.split(":", 1)
            key_part = parts[0]
            val_part = parts[1].strip()
            formatted_lines.append(f"<b>{key_part}:</b> <code>{val_part}</code>")
        else:
            formatted_lines.append(f"<b>{stripped}</b>")

    return "\n".join(formatted_lines)

async def auto_delete_file_task(client, chat_id, file_msg_id, warn_msg_id, delay=300):
    await asyncio.sleep(delay)
    try:
        await client.delete_messages(chat_id=chat_id, message_ids=[file_msg_id, warn_msg_id])
    except Exception as e:
        print(f"Failed to auto-delete messages: {e}")

# --- Force Subscribe Logic ---

async def check_force_sub(client, user_id, start_param=""):
    try:
        channels = await get_force_subs()
    except Exception:
        channels = []

    if not channels:
        return True, None

    buttons = []

    for channel in channels:
        try:
            chat = await client.get_chat(channel)
        except Exception:
            try:
                await remove_force_sub(channel)
            except Exception:
                pass
            continue

        try:
            member = await client.get_chat_member(chat.id, user_id)
            if member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
                raise UserNotParticipant
        except UserNotParticipant:
            if chat.username:
                link = f"https://t.me/{chat.username}"
            else:
                try:
                    invite = await client.create_chat_invite_link(chat.id, member_limit=1)
                    link = invite.invite_link
                except Exception:
                    continue

            buttons.append([InlineKeyboardButton(chat.title, url=link)])
        except Exception:
            continue

    if buttons:
        cb_data = f"checksub_{start_param}" if start_param else "checksub"
        buttons.append([InlineKeyboardButton("• ᴛʀʏ ᴀɢᴀɪɴ •", callback_data=cb_data)])
        return False, InlineKeyboardMarkup(buttons)

    return True, None

async def send_file_to_user(client, chat_id, anime_id, quality):
    anime_data = await get_anime_post(anime_id)
    if not anime_data:
        await client.send_message(chat_id, "❌ <b>File or Post not found!</b>", parse_mode=ParseMode.HTML)
        return

    res_info = anime_data.get("resolutions", {}).get(quality)
    if not res_info:
        await client.send_message(chat_id, f"❌ <b>{quality} video is not available yet!</b>", parse_mode=ParseMode.HTML)
        return

    file_id = res_info.get("file_id") if isinstance(res_info, dict) else res_info
    file_type = res_info.get("type", "video") if isinstance(res_info, dict) else "video"
    custom_file_caption = res_info.get("caption") if isinstance(res_info, dict) else None

    status_msg = await client.send_message(chat_id, "<b>Wait a Sec...</b>", parse_mode=ParseMode.HTML)

    if custom_file_caption:
        caption_text = f"<b>{custom_file_caption}</b>"
    else:
        caption_text = f"<b>{anime_data.get('title', 'Anime')} - {quality}</b>\n\n<b>Powered by @Aero_Unity</b>"

    delivery_markup = build_file_delivery_keyboard()

    try:
        if file_type == "document":
            sent_file = await client.send_document(
                chat_id, 
                document=file_id, 
                caption=caption_text, 
                parse_mode=ParseMode.HTML,
                reply_markup=delivery_markup
            )
        else:
            sent_file = await client.send_video(
                chat_id, 
                video=file_id, 
                caption=caption_text, 
                parse_mode=ParseMode.HTML,
                reply_markup=delivery_markup
            )
        await status_msg.delete()

        warn_msg = await client.send_message(
            chat_id=chat_id,
            text="⚠ <b>Warning:</b> This file will be deleted in <b>5 minutes</b>! Please forward it to your <b>Saved Messages</b> or another chat to keep it.",
            parse_mode=ParseMode.HTML,
            reply_to_message_id=sent_file.id
        )

        asyncio.create_task(
            auto_delete_file_task(client, chat_id, sent_file.id, warn_msg.id, delay=300)
        )

    except Exception as e:
        await status_msg.edit_text(f"❌ <b>Error:</b> <code>{e}</code>", parse_mode=ParseMode.HTML)

# --- FFmpeg & Metadata ---

async def apply_custom_metadata(input_path, output_path, title, comment="Powered by @Aero_Unity"):
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-metadata", f"title={title}",
        "-metadata", f"artist={comment}",
        "-metadata", f"comment={comment}",
        "-c", "copy",
        output_path
    ]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    await proc.communicate()
    return output_path if os.path.exists(output_path) else input_path

async def get_video_height(file_path):
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=height",
        "-of", "default=noprint_wrappers=1:nokey=1",
        file_path
    ]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, _ = await proc.communicate()
    line = stdout.decode().strip()
    try:
        return int(line) if line.isdigit() else 0
    except Exception:
        return 0

def detect_quality_from_filename_and_height(filename, caption, height):
    search_text = f"{filename} {caption}".lower()

    if "hdrip" in search_text or "hdr-ip" in search_text:
        return "HDRip"
    elif "4k" in search_text or "2160p" in search_text or height >= 2160:
        return "4K"
    elif "1080p" in search_text or height >= 1080:
        return "1080p"
    elif "720p" in search_text or height >= 720:
        return "720p"
    elif "480p" in search_text or height >= 480:
        return "480p"

    return "480p"

# --- Bot Commands ---

@app.on_message(filters.command("start") & filters.private)
async def start_cmd(client, message):
    start_param = message.command[1] if len(message.command) > 1 else ""

    ok, keyboard = await check_force_sub(client, message.from_user.id, start_param)
    if not ok:
        await message.reply_photo(
            photo=FORCE_SUB_IMAGE,
            caption=(
                "**›› ‼️ ʟᴏᴏᴋs ʟɪᴋᴇ ʏᴏᴜ ʜᴀᴠᴇɴ'ᴛ ᴊᴏɪɴᴇᴅ ᴛᴏ ᴏᴜʀ ᴄʜᴀɴɴᴇʟs ʏᴇᴛ, sᴜʙsᴄʀɪʙᴇ ɴᴏᴡ...**\n\n"
                "• ᴘʀᴇss **ᴛʀʏ ᴀɢᴀɪɴ**."
            ),
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if start_param.startswith("dl_"):
        parts = start_param.split("_")
        if len(parts) >= 4:
            anime_id = f"{parts[1]}_{parts[2]}"
            quality = parts[3]
            await send_file_to_user(client, message.chat.id, anime_id, quality)
            return

    if message.from_user.id == OWNER_ID:
        await message.reply_text("👋 <b>Anime Uploader Bot Active (Owner Mode)!</b>", parse_mode=ParseMode.HTML)
    else:
        await message.reply_text("👋 <b>Welcome to Aero Anime Bot!</b>", parse_mode=ParseMode.HTML)

# --- Admin Force Sub Commands ---

@app.on_message(filters.command("addfsub") & filters.private & owner_filter)
async def add_fsub_cmd(client, message):
    if len(message.command) < 2:
        await message.reply_text("<b>Usage:</b> <code>/addfsub -100123456789</code> or <code>/addfsub @channelusername</code>", parse_mode=ParseMode.HTML)
        return
    
    channel_input = message.command[1]
    
    try:
        chat = await client.get_chat(channel_input)
        channel_id = str(chat.id)
        channel_title = chat.title
    except Exception as e:
        await message.reply_text(f"❌ <b>Invalid channel or bot is not an admin there!</b>\n<code>{e}</code>", parse_mode=ParseMode.HTML)
        return

    await add_force_sub(channel_id)
    await message.reply_text(f"✅ <b>Added {channel_title} (<code>{channel_id}</code>) to Force Sub channels!</b>", parse_mode=ParseMode.HTML)

@app.on_message(filters.command("rmfsub") & filters.private & owner_filter)
async def rm_fsub_cmd(client, message):
    if len(message.command) < 2:
        await message.reply_text("<b>Usage:</b> <code>/rmfsub -100123456789</code> or <code>/rmfsub @channelusername</code>", parse_mode=ParseMode.HTML)
        return

    channel_input = message.command[1]
    
    try:
        if channel_input.startswith("@"):
            chat = await client.get_chat(channel_input)
            channel_id = str(chat.id)
        else:
            channel_id = str(channel_input)
    except Exception:
        channel_id = str(channel_input)

    await remove_force_sub(channel_id)
    await message.reply_text(f"✅ <b>Removed <code>{channel_id}</code> from Force Sub channels!</b>", parse_mode=ParseMode.HTML)

@app.on_message(filters.command("fsublist") & filters.private & owner_filter)
async def list_fsub_cmd(client, message):
    channels = await get_force_subs()
    if not channels:
        await message.reply_text("❌ <b>No Force Sub channels set!</b>", parse_mode=ParseMode.HTML)
        return

    text = "📢 <b>Force Sub Channels:</b>\n\n"
    for ch in channels:
        try:
            chat = await client.get_chat(ch)
            text += f"• <b>{chat.title}</b> (<code>{chat.id}</code>)\n"
        except Exception:
            text += f"• <code>{ch}</code>\n"

    await message.reply_text(text, parse_mode=ParseMode.HTML)

# --- Force Subscribe Callback Query Handler ---

@app.on_callback_query(filters.regex(r"^checksub"))
async def check_sub_callback(client, callback_query):
    user_id = callback_query.from_user.id
    data_parts = callback_query.data.split("_", 1)
    start_param = data_parts[1] if len(data_parts) > 1 else ""

    for text in ["Checking subscription... ⏳", "Checking database... 🔍", "Almost done... ⚡"]:
        await callback_query.answer(text, show_alert=False)
        await asyncio.sleep(0.4)

    ok, keyboard = await check_force_sub(client, user_id, start_param)

    if not ok:
        await callback_query.answer("❌ You still haven't joined all required channels!", show_alert=True)
        try:
            await callback_query.message.edit_reply_markup(reply_markup=keyboard)
        except Exception:
            pass
    else:
        await callback_query.answer("✅ Thank you for joining!", show_alert=False)
        try:
            await callback_query.message.delete()
        except Exception:
            pass

        if start_param.startswith("dl_"):
            parts = start_param.split("_")
            if len(parts) >= 4:
                anime_id = f"{parts[1]}_{parts[2]}"
                quality = parts[3]
                await send_file_to_user(client, callback_query.message.chat.id, anime_id, quality)
        else:
            await client.send_message(
                chat_id=callback_query.message.chat.id,
                text="👋 <b>Welcome to Aero Anime Bot! You can now download files.</b>",
                parse_mode=ParseMode.HTML
            )

# --- Photo Post Handler ---

@app.on_message(filters.photo & filters.private & owner_filter)
async def handle_photo_post(client, message):
    raw_caption = message.caption or ""
    lines = [line.strip() for line in raw_caption.split("\n") if line.strip()]

    anime_name = lines[0] if lines else "Anime Title"
    anime_id = f"anime_{message.id}"

    formatted_caption = format_bold_mono_caption(raw_caption)

    try:
        channel_post = await client.send_photo(
            chat_id=CHANNEL_ID,
            photo=message.photo.file_id,
            caption=formatted_caption,
            parse_mode=ParseMode.HTML,
            reply_markup=build_quality_keyboard(anime_id, {})
        )
        await save_anime_post(anime_id, anime_name, channel_post.id)
    except Exception as e:
        await message.reply_text(f"❌ <b>Failed to post photo:</b> <code>{e}</code>", parse_mode=ParseMode.HTML)
        return

    sticker_msg_id = None
    if STICKER_ID:
        try:
            sticker_msg = await client.send_sticker(chat_id=CHANNEL_ID, sticker=STICKER_ID)
            sticker_msg_id = sticker_msg.id
        except Exception as e:
            print(f"Sticker error: {e}")

    initial_text = build_initial_download_text(
        meta_tag="By @Aero_Unity",
        file_title=anime_name,
        file_id="6DFC3NF"
    )
    
    status_msg = await client.send_message(
        chat_id=CHANNEL_ID,
        text=initial_text,
        parse_mode=ParseMode.HTML
    )

    active_anime_session[message.from_user.id] = {
        "anime_id": anime_id,
        "anime_name": anime_name,
        "raw_caption": raw_caption,
        "channel_msg_id": channel_post.id,
        "sticker_msg_id": sticker_msg_id,
        "status_msg_id": status_msg.id,
        "encoded_count": 0,
        "queue_lock": asyncio.Lock()
    }

    await message.reply_text(
        f"✅ <b>Poster created!</b>\n🆔 <b>ID:</b> <code>{anime_id}</code>\nNow send files.",
        parse_mode=ParseMode.HTML
    )

# --- Video & Document Handler ---

@app.on_message((filters.video | filters.document) & filters.private & owner_filter)
async def handle_forwarded_video(client, message):
    file_obj = message.video or message.document
    if not file_obj:
        return

    is_doc = bool(message.document)
    file_type = "document" if is_doc else "video"

    user_session = active_anime_session.get(message.from_user.id)
    if not user_session:
        await message.reply_text("❌ <b>No active session!</b> Send poster first.", parse_mode=ParseMode.HTML)
        return

    async with user_session["queue_lock"]:
        anime_id = user_session["anime_id"]
        anime_name = user_session["anime_name"]
        status_msg_id = user_session["status_msg_id"]

        file_name = getattr(file_obj, "file_name", None) or f"video_{message.id}.mp4"
        task_id = random.randint(1000, 9999)
        bot_pm_status = await message.reply_text("📥 <b>Starting download...</b>", parse_mode=ParseMode.HTML)

        init_dl_text = build_initial_download_text(
            meta_tag="By @Aero_Unity",
            file_title=f"{file_name}",
            file_id=f"ID-{task_id}"
        )
        try:
            await client.edit_message_text(chat_id=CHANNEL_ID, message_id=status_msg_id, text=init_dl_text, parse_mode=ParseMode.HTML)
        except Exception:
            pass

        await asyncio.sleep(2)

        thumb_path = None
        if file_obj.thumbs:
            thumb_file_id = file_obj.thumbs[0].file_id
            thumb_path = os.path.join(THUMB_DIR, f"thumb_{message.id}.jpg")
            await client.download_media(thumb_file_id, file_name=thumb_path)

        raw_path = os.path.join(DOWNLOAD_DIR, f"raw_{file_name}")

        start_time = time.time()
        last_update_time = [0]

        async def progress_callback(current, total):
            now = time.time()
            if now - last_update_time[0] >= 3 or current == total:
                last_update_time[0] = now
                elapsed = now - start_time
                speed = current / elapsed if elapsed > 0 else 0
                eta = (total - current) / speed if speed > 0 else 0
                
                pct = int((current / total) * 100) if total > 0 else 0
                
                try:
                    await bot_pm_status.edit_text(f"📥 <b>Downloading...</b> ({pct}%)", parse_mode=ParseMode.HTML)
                except Exception:
                    pass

                try:
                    enc_cnt = user_session["encoded_count"]
                    ch_text = build_encoding_progress_text(
                        filename=file_name,
                        status_label="Encoding",
                        current=current,
                        total=total,
                        speed=speed,
                        elapsed=elapsed,
                        eta=eta,
                        encoded_count=enc_cnt,
                        task_id=task_id
                    )
                    await client.edit_message_text(chat_id=CHANNEL_ID, message_id=status_msg_id, text=ch_text, parse_mode=ParseMode.HTML)
                except Exception:
                    pass

        await client.download_media(message, file_name=raw_path, progress=progress_callback)

        if not os.path.exists(raw_path):
            await bot_pm_status.edit_text("❌ <b>Download failed.</b>", parse_mode=ParseMode.HTML)
            return

        forwarded_caption = message.caption or os.path.splitext(file_name)[0]
        meta_title = forwarded_caption
        meta_pub = "Powered by @Aero_Unity"

        await bot_pm_status.edit_text("🏷️ <b>Applying metadata...</b>", parse_mode=ParseMode.HTML)
        meta_path = os.path.join(DOWNLOAD_DIR, f"{file_name}")
        final_path = await apply_custom_metadata(raw_path, meta_path, title=meta_title, comment=meta_pub)

        if os.path.exists(raw_path) and raw_path != final_path:
            os.remove(raw_path)

        height = await get_video_height(final_path)
        detected_quality = detect_quality_from_filename_and_height(file_name, forwarded_caption, height)

        file_caption = f"<b>{forwarded_caption}</b>"

        if is_doc:
            sent_msg = await client.send_document(
                chat_id=OWNER_ID,
                document=final_path,
                thumb=thumb_path if thumb_path and os.path.exists(thumb_path) else None,
                caption=file_caption,
                parse_mode=ParseMode.HTML
            )
            stored_file_id = sent_msg.document.file_id
        else:
            sent_msg = await client.send_video(
                chat_id=OWNER_ID,
                video=final_path,
                thumb=thumb_path if thumb_path and os.path.exists(thumb_path) else None,
                caption=file_caption,
                parse_mode=ParseMode.HTML
            )
            stored_file_id = sent_msg.video.file_id

        file_data = {
            "file_id": stored_file_id, 
            "type": file_type,
            "caption": forwarded_caption
        }
        await add_resolution(anime_id, detected_quality, file_data)

        user_session["encoded_count"] += 1
        current_enc = user_session["encoded_count"]

        anime_data = await get_anime_post(anime_id)
        resolutions = anime_data.get("resolutions", {}) if anime_data else {}

        try:
            await client.edit_message_reply_markup(
                chat_id=CHANNEL_ID,
                message_id=user_session["channel_msg_id"],
                reply_markup=build_quality_keyboard(anime_id, resolutions)
            )
        except Exception as e:
            print(f"Error updating main post buttons: {e}")

        if os.path.exists(final_path):
            os.remove(final_path)
        if thumb_path and os.path.exists(thumb_path):
            os.remove(thumb_path)

        if current_enc >= 4:
            if user_session.get("sticker_msg_id"):
                try:
                    await client.delete_messages(chat_id=CHANNEL_ID, message_ids=user_session["sticker_msg_id"])
                except Exception:
                    pass
            if user_session.get("status_msg_id"):
                try:
                    await client.delete_messages(chat_id=CHANNEL_ID, message_ids=user_session["status_msg_id"])
                except Exception:
                    pass

        await bot_pm_status.edit_text(
            f"✅ <b>Added {detected_quality} {file_type}!</b>",
            parse_mode=ParseMode.HTML
        )

@app.on_message(filters.private & ~owner_filter)
async def reject_unauthorized(client, message):
    await message.reply_text("❌ <b>Access Denied!</b>", parse_mode=ParseMode.HTML)

async def main():
    await start_web_server()
    await app.start()
    print("Anime Uploader Bot started!")
    await asyncio.Event().wait()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())