import os
import asyncio
import logging
import time
import math
import re
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError, MessageNotModifiedError
from telethon.tl.types import DocumentAttributeFilename, DocumentAttributeVideo, DocumentAttributeAudio
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

# --- CLIENT SETUP ---
# Connection Optimization
user_client = TelegramClient(
    StringSession(STRING_SESSION), 
    API_ID, 
    API_HASH, 
    connection_retries=None,
    flood_sleep_threshold=60
)
bot_client = TelegramClient(
    'bot_session', 
    API_ID, 
    API_HASH, 
    connection_retries=None,
    flood_sleep_threshold=60
)

# --- GLOBAL STATE ---
pending_requests = {} 
current_task = None
is_running = False
status_message = None
last_update_time = 0
animation_frame = 0

# --- WEB SERVER ---
async def handle(request):
    return web.Response(text="Bot is Running (Max Speed)! 🟢")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"Web server started on port {PORT}")

# --- HELPER FUNCTIONS ---
def human_readable_size(size):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024.0: return f"{size:.2f}{unit}"
        size /= 1024.0
    return f"{size:.2f}TB"

def time_formatter(seconds):
    if seconds is None: return "..."
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0: return f"{hours}h {minutes}m {seconds}s"
    if minutes > 0: return f"{minutes}m {seconds}s"
    return f"{seconds}s"

async def progress_callback(current, total, start_time, file_name):
    global last_update_time, status_message, animation_frame
    now = time.time()
    
    # Update every 8 seconds (Focus on Speed)
    if now - last_update_time < 8: return 
    last_update_time = now
    
    percentage = current * 100 / total if total > 0 else 0
    time_diff = now - start_time
    speed = current / time_diff if time_diff > 0 else 0
    eta = (total - current) / speed if speed > 0 else 0
        
    frames = ["🟢", "🟡", "🟠", "🔴"]
    icon = frames[animation_frame % len(frames)]
    animation_frame += 1
    
    filled = math.floor(percentage / 10)
    bar = "▇" * filled + "░" * (10 - filled)
    
    try:
        await status_message.edit(
            f"{icon} **MAX SPEED MODE**\n"
            f"📂 `{file_name}`\n\n"
            f"**{bar} {round(percentage, 1)}%**\n\n"
            f"🚀 **Speed:** `{human_readable_size(speed)}/s`\n"
            f"⏳ **ETA:** `{time_formatter(eta)}`\n"
            f"💾 **Data:** `{human_readable_size(current)}`"
        )
    except MessageNotModifiedError: pass
    except Exception: pass

# --- CUSTOM STREAM CLASS (8MB CHUNKS) ---
class UserClientStream:
    def __init__(self, client, location, file_size, file_name, start_time):
        self.client = client
        self.location = location
        self.file_size = file_size
        self.file_name = file_name
        self.start_time = start_time
        self.current_bytes = 0
        
        # 8MB Chunks = Less API Calls = More Speed
        self.generator = client.iter_download(location, chunk_size=8192*1024)
        self.buffer = b""
        self._finished = False

    def __len__(self):
        return self.file_size

    async def read(self, size=-1):
        # Always serve at least 8MB if possible
        if size == -1: size = 8192 * 1024
        
        while len(self.buffer) < size and not self._finished:
            try:
                chunk = await self.generator.__anext__()
                self.buffer += chunk
                self.current_bytes += len(chunk)
                await progress_callback(self.current_bytes, self.file_size, self.start_time, self.file_name)
            except StopAsyncIteration:
                self._finished = True
            except Exception as e:
                logger.error(f"Stream Read Error: {e}")
                self._finished = True
                break

        data = self.buffer[:size]
        self.buffer = self.buffer[size:]
        return data

    @property
    def name(self): return self.file_name

# --- ATTRIBUTE CLEANER ---
def clean_attributes(original_attributes, file_name):
    new_attributes = []
    new_attributes.append(DocumentAttributeFilename(file_name=file_name))
    
    for attr in original_attributes:
        if isinstance(attr, DocumentAttributeVideo):
            new_attributes.append(DocumentAttributeVideo(
                duration=attr.duration,
                w=attr.w,
                h=attr.h,
                round_message=attr.round_message,
                supports_streaming=True
            ))
        elif isinstance(attr, DocumentAttributeAudio):
            new_attributes.append(DocumentAttributeAudio(
                duration=attr.duration,
                voice=attr.voice,
                title=attr.title,
                performer=attr.performer
            ))
    return new_attributes

# --- LINK PARSER ---
def extract_id_from_link(link):
    regex = r"(\d+)$"
    match = re.search(regex, link)
    if match: return int(match.group(1))
    return None

# --- TRANSFER PROCESS ---
async def transfer_process(event, source_id, dest_id, start_msg, end_msg):
    global is_running, status_message
    
    status_message = await event.respond(f"🟢 **Bot Connected!**\nSource: `{source_id}`")
    total_processed = 0
    
    try:
        async for message in user_client.iter_messages(source_id, min_id=start_msg-1, max_id=end_msg+1, reverse=True):
            if not is_running:
                await status_message.edit("🛑 **Stopped!**")
                break

            if getattr(message, 'action', None): continue

            try:
                # --- METADATA ---
                file_name = "Unknown"
                mime_type = "application/octet-stream"
                original_attributes = []
                
                if message.media:
                    if hasattr(message.media, 'document'):
                        original_attributes = list(message.media.document.attributes)
                        mime_type = message.media.document.mime_type
                        for attr in original_attributes:
                            if isinstance(attr, DocumentAttributeFilename): file_name = attr.file_name; break
                        if file_name == "Unknown":
                            ext = mime_type.split('/')[-1]
                            file_name = f"file_{message.id}.{ext}"
                    elif hasattr(message.media, 'photo'):
                        file_name = f"Image_{message.id}.jpg"
                        mime_type = "image/jpeg"

                await status_message.edit(f"🔍 **Found:** `{file_name}`")

                # --- TRANSFER ---
                if message.text and not message.media:
                    await bot_client.send_message(dest_id, message.text)
                
                elif message.media:
                    start_time = time.time()
                    sent_successfully = False
                    
                    # 1. DIRECT COPY
                    try:
                        await bot_client.send_file(dest_id, message.media, caption=message.text or "")
                        await status_message.edit(f"✅ **Fast Copy:** `{file_name}`")
                        sent_successfully = True
                    except Exception:
                        pass # Fail silently to Stream Mode

                    # 2. STREAM MODE (If Direct Failed)
                    if not sent_successfully:
                        file_size = 0
                        location = None
                        
                        if hasattr(message.media, 'document'):
                            file_size = message.media.document.size
                            location = message.media.document
                        elif hasattr(message.media, 'photo'):
                            file_size = 5*1024*1024 
                            location = message.media.photo

                        if location:
                            clean_attrs = clean_attributes(original_attributes, file_name)
                            
                            if file_size < 10*1024*1024:
                                buffer = await user_client.download_media(message, file=bytes)
                                await bot_client.send_file(
                                    dest_id, 
                                    buffer, 
                                    caption=message.text or "",
                                    attributes=clean_attrs,
                                    force_document=('video' not in mime_type and 'image' not in mime_type)
                                )
                            else:
                                stream = UserClientStream(user_client, location, file_size, file_name, start_time)
                                thumb = await user_client.download_media(message, thumb=-1)

                                await bot_client.send_file(
                                    dest_id,
                                    file=stream,
                                    caption=message.text or "",
                                    attributes=clean_attrs,
                                    thumb=thumb,
                                    supports_streaming=True,
                                    file_size=file_size,
                                    mime_type=mime_type
                                )
                                if thumb and os.path.exists(thumb): os.remove(thumb)
                        else:
                             await bot_client.send_file(dest_id, message.media)

                total_processed += 1
                
            except FloodWaitError as e:
                logger.warning(f"FloodWait: {e.seconds}s")
                await asyncio.sleep(e.seconds)
            except Exception as e:
                logger.error(f"Failed {message.id}: {e}")
                try: await status_message.edit(f"❌ **Failed:** `{file_name}`\n`{str(e)[:50]}`")
                except: pass
                continue

        if is_running:
            await status_message.edit(f"✅ **Job Done!**\nTotal: `{total_processed}`")

    except Exception as e:
        if status_message: await status_message.edit(f"❌ Critical Error: {e}")
    finally:
        is_running = False

# --- COMMANDS ---
@bot_client.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    await event.respond("🟢 **Max Speed Bot Ready!**\n`/clone Source Dest`")

@bot_client.on(events.NewMessage(pattern='/clone'))
async def clone_init(event):
    global is_running
    if is_running: return await event.respond("⚠️ Running...")
    try:
        args = event.text.split()
        pending_requests[event.chat_id] = {'source': int(args[1]), 'dest': int(args[2])}
        await event.respond("✅ **Set!** Send Range Link.")
    except: await event.respond("❌ Usage: `/clone -100xxx -100yyy`")

@bot_client.on(events.NewMessage())
async def range_listener(event):
    global current_task, is_running
    if event.chat_id not in pending_requests or "t.me" not in event.text: return
    try:
        links = event.text.strip().split("-")
        msg1, msg2 = extract_id_from_link(links[0]), extract_id_from_link(links[1])
        if msg1 > msg2: msg1, msg2 = msg2, msg1
        
        data = pending_requests.pop(event.chat_id)
        is_running = True
        current_task = asyncio.create_task(transfer_process(event, data['source'], data['dest'], msg1, msg2))
    except Exception as e: await event.respond(f"❌ Error: {e}")

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


