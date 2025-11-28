import os
import asyncio
import logging
import time
import math
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError
from aiohttp import web

# --- CONFIGURATION ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH")
STRING_SESSION = os.environ.get("STRING_SESSION") 
BOT_TOKEN = os.environ.get("BOT_TOKEN")           
PORT = int(os.environ.get("PORT", 8080))

# --- LOGGING ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- CLIENTS ---
user_client = TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH)
bot_client = TelegramClient('bot_session', API_ID, API_HASH)

# --- GLOBAL VARS ---
current_task = None
is_running = False
status_message = None
last_update_time = 0

# --- WEB SERVER ---
async def handle(request):
    return web.Response(text="Bot is Running (Stable Mode)! 🛡️")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"Web server started on port {PORT}")

# --- HELPER: PROGRESS BAR ---
def human_readable_size(size):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024.0: return f"{size:.2f}{unit}"
        size /= 1024.0
    return f"{size:.2f}TB"

async def progress_callback(current, total, start_time, file_name, task_type):
    global last_update_time, status_message
    now = time.time()
    if now - last_update_time < 5: return # Update every 5s to save API limits
    last_update_time = now
    
    percentage = current * 100 / total
    speed = current / (now - start_time) if now - start_time > 0 else 0
    
    progress_bar = "[{0}{1}] {2}%".format(
        ''.join(["⬢" for i in range(math.floor(percentage / 10))]),
        ''.join(["⬡" for i in range(10 - math.floor(percentage / 10))]),
        round(percentage, 1)
    )
    
    try:
        await status_message.edit(
            f"🛡️ **Stable Mode**\n"
            f"📂 `{file_name}`\n"
            f"{task_type}: {progress_bar}\n"
            f"🚀 Speed: `{human_readable_size(speed)}/s`\n"
            f"💾 Size: `{human_readable_size(current)} / {human_readable_size(total)}`"
        )
    except: pass

# --- TRANSFER LOGIC ---
async def transfer_process(event, source_id, dest_id):
    global is_running, status_message
    
    status_message = await event.respond(f"🛡️ **Initializing Stable Clone...**\nSource: `{source_id}`")
    total_processed = 0
    
    try:
        async for message in user_client.iter_messages(source_id, reverse=True):
            if not is_running:
                await status_message.edit("🛑 **Stopped by User!**")
                break

            if getattr(message, 'action', None):
                continue

            try:
                # --- METADATA EXTRACTION ---
                file_name = "Unknown"
                attributes = []
                if message.media:
                    if hasattr(message.media, 'document'):
                        attributes = message.media.document.attributes
                        for attr in attributes:
                            if hasattr(attr, 'file_name'):
                                file_name = attr.file_name

                # --- PHASE 1: DOWNLOAD ---
                await status_message.edit(f"⬇️ **Downloading:** `{file_name}`\nPlease Wait...")
                
                # Thumbnail
                thumb = await user_client.download_media(message, thumb=-1) if message.media else None
                
                # Main File Download (With Progress)
                buffer = None
                if message.media:
                    start_time = time.time()
                    async def dl_callback(curr, tot):
                        await progress_callback(curr, tot, start_time, file_name, "⬇️ Downloading")
                    
                    try:
                        buffer = await user_client.download_media(message, file=bytes, progress_callback=dl_callback)
                    except Exception as e:
                        logger.error(f"Download Failed: {e}")
                        continue

                # --- PHASE 2: UPLOAD ---
                await status_message.edit(f"⬆️ **Uploading:** `{file_name}`\nAlmost there...")

                if message.text and not message.media:
                    await bot_client.send_message(dest_id, message.text)
                
                elif buffer:
                    start_time = time.time()
                    async def ul_callback(curr, tot):
                        await progress_callback(curr, tot, start_time, file_name, "⬆️ Uploading")

                    await bot_client.send_file(
                        dest_id,
                        file=buffer,
                        caption=message.text or "",
                        attributes=attributes,
                        thumb=thumb,
                        supports_streaming=True,
                        progress_callback=ul_callback
                    )
                    
                # Cleanup
                if thumb and os.path.exists(thumb): os.remove(thumb)
                if buffer: del buffer # Free RAM immediately
                
                total_processed += 1
                await asyncio.sleep(1) # Cool down

            except FloodWaitError as e:
                logger.warning(f"FloodWait: {e.seconds}s")
                await asyncio.sleep(e.seconds)
            except Exception as e:
                logger.error(f"Error on {message.id}: {e}")
                continue

        if is_running:
            await status_message.edit(f"✅ **Clone Complete!**\nTotal: `{total_processed}` files.")

    except Exception as e:
        if status_message: await status_message.edit(f"❌ Error: {e}")
    finally:
        is_running = False

# --- COMMANDS ---
@bot_client.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    await event.respond("🛡️ **Stable Bot Ready!**\n`/clone <Source> <Dest>`\n`/stop`")

@bot_client.on(events.NewMessage(pattern='/clone'))
async def clone_handler(event):
    global current_task, is_running
    if is_running: return await event.respond("⚠️ Already running.")
    try:
        args = event.text.split()
        current_task = asyncio.create_task(transfer_process(event, int(args[1]), int(args[2])))
        is_running = True
    except: await event.respond("❌ Usage: `/clone -100xxx -100yyy`")

@bot_client.on(events.NewMessage(pattern='/stop'))
async def stop_handler(event):
    global is_running
    is_running = False
    if current_task: current_task.cancel()
    await event.respond("🛑 Stopping...")

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    user_client.start()
    loop.create_task(start_web_server())
    bot_client.start(bot_token=BOT_TOKEN)
    bot_client.run_until_disconnected()


