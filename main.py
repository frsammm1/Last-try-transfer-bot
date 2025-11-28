import os
import asyncio
import logging
import time
import math
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError
from telethon.tl.types import DocumentAttributeVideo
from aiohttp import web

# --- CONFIGURATION ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH")
STRING_SESSION = os.environ.get("STRING_SESSION") 
BOT_TOKEN = os.environ.get("BOT_TOKEN")           
PORT = int(os.environ.get("PORT", 8080))
# RAM Management: 200MB se badi file hone par pipeline rook jayegi
LARGE_FILE_THRESHOLD = 200 * 1024 * 1024 

# --- LOGGING ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- CLIENTS ---
user_client = TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH)
bot_client = TelegramClient('bot_session', API_ID, API_HASH)

# --- GLOBAL VARS ---
queue = asyncio.Queue(maxsize=1) # Sirf 1 file advance me rakhega
is_running = False
status_message = None
last_update_time = 0

# --- WEB SERVER ---
async def handle(request):
    return web.Response(text="Bot is Running in High Speed Mode! ⚡️")

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

async def progress_callback(current, total, start_time, file_name, task_type="Uploading"):
    global last_update_time, status_message
    now = time.time()
    if now - last_update_time < 4: return # Update every 4s
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
            f"⚡️ **High Speed Mode**\n"
            f"📂 `{file_name}`\n"
            f"{task_type}: {progress_bar}\n"
            f"🚀 Speed: `{human_readable_size(speed)}/s`\n"
            f"💾 Size: `{human_readable_size(current)} / {human_readable_size(total)}`"
        )
    except: pass

# --- PRODUCER: DOWNLOADER TASK ---
async def producer_worker(source_id):
    global is_running, status_message
    
    async for message in user_client.iter_messages(source_id, reverse=True):
        if not is_running: break
        if getattr(message, 'action', None): continue

        # --- SMART LOGIC ---
        # Item ko queue me daalne ke liye pack karo
        item = {'msg': message, 'type': 'text', 'data': None, 'thumb': None, 'attrs': []}

        if message.media:
            file_size = 0
            if hasattr(message.media, 'document'):
                file_size = message.media.document.size
            elif hasattr(message.media, 'photo'):
                file_size = 5 * 1024 * 1024 # Approx 5MB for photo

            # Agar file bahut badi hai, to pehle queue khali hone ka wait karo (RAM Safety)
            if file_size > LARGE_FILE_THRESHOLD:
                await queue.join()

            try:
                # 1. Attributes & Name Nikalo
                file_name = "Unknown"
                if hasattr(message.media, 'document'):
                    item['attrs'] = message.media.document.attributes
                    for attr in item['attrs']:
                        if hasattr(attr, 'file_name'):
                            file_name = attr.file_name

                # 2. Download Thumbnail
                item['thumb'] = await user_client.download_media(message, thumb=-1)
                
                # 3. Download Main File to RAM
                # Note: Hum ye background me kar rahe hain jabki uploading chal rahi hai
                start = time.time()
                item['data'] = await user_client.download_media(message, file=bytes)
                item['type'] = 'media'
                item['name'] = file_name
                
            except Exception as e:
                logger.error(f"Download Fail: {e}")
                continue
        
        else:
             item['data'] = message.text

        # Queue me daalo (Agar queue full hai to ye wait karega = Pipelining)
        await queue.put(item)
    
    # End Signal
    await queue.put(None)

# --- CONSUMER: UPLOADER TASK ---
async def consumer_worker(dest_id, event):
    global is_running, status_message
    
    while is_running:
        item = await queue.get()
        if item is None: # End Signal
            break

        try:
            msg = item['msg']
            
            # --- UPLOAD PHASE ---
            if item['type'] == 'text':
                await bot_client.send_message(dest_id, item['data'])
            
            elif item['type'] == 'media':
                start_time = time.time()
                
                async def callback(curr, tot):
                    await progress_callback(curr, tot, start_time, item['name'])

                await bot_client.send_file(
                    dest_id,
                    file=item['data'],
                    caption=msg.text or "",
                    attributes=item['attrs'],
                    thumb=item['thumb'],
                    supports_streaming=True,
                    progress_callback=callback
                )
                
                # Cleanup Thumb
                if item['thumb'] and os.path.exists(item['thumb']):
                    os.remove(item['thumb'])

        except FloodWaitError as e:
            logger.warning(f"Sleeping {e.seconds}s for FloodWait")
            await asyncio.sleep(e.seconds)
        except Exception as e:
            logger.error(f"Upload Error: {e}")
        finally:
            queue.task_done()

    await status_message.edit("✅ **All Files Transferred!**")

# --- MAIN CONTROLLER ---
async def start_cloning(event, source_id, dest_id):
    global is_running, status_message
    status_message = await event.respond(f"⚡️ **High Speed Pipeline Started!**\nPreparing files...")
    
    is_running = True
    
    # Do task parallel chalayenge
    producer = asyncio.create_task(producer_worker(source_id))
    consumer = asyncio.create_task(consumer_worker(dest_id, event))
    
    await asyncio.gather(producer, consumer)
    is_running = False

# --- COMMANDS ---
@bot_client.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    await event.respond("⚡️ **Speed Bot Ready!**\nUse: `/clone <Source> <Dest>`\nStop: `/stop`")

@bot_client.on(events.NewMessage(pattern='/clone'))
async def clone_handler(event):
    global current_task, is_running
    if is_running: return await event.respond("⚠️ Already Running!")
    try:
        args = event.text.split()
        current_task = asyncio.create_task(start_cloning(event, int(args[1]), int(args[2])))
    except: await event.respond("❌ Usage: `/clone -100xxx -100yyy`")

@bot_client.on(events.NewMessage(pattern='/stop'))
async def stop_handler(event):
    global is_running
    is_running = False
    # Queue ko clear karo taaki tasks fans na jayein
    while not queue.empty():
        try: queue.get_nowait()
        except: pass
    await event.respond("🛑 Stopping Pipeline...")

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    user_client.start()
    loop.create_task(start_web_server())
    bot_client.start(bot_token=BOT_TOKEN)
    bot_client.run_until_disconnected()


