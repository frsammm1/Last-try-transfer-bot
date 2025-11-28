import os
import asyncio
import logging
import time
import math
import re
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

# --- TURBO CLIENT SETUP ---
# Connection ke liye retry badha diye aur flood sleep kam kar diya
user_client = TelegramClient(
    StringSession(STRING_SESSION), 
    API_ID, 
    API_HASH,
    connection_retries=None,
    flood_sleep_threshold=20
)
bot_client = TelegramClient('bot_session', API_ID, API_HASH)

# --- GLOBAL STATE ---
# Isme hum user ka data save karenge jab wo range bhejega
pending_requests = {} 
current_task = None
is_running = False
status_message = None
last_update_time = 0

# --- WEB SERVER ---
async def handle(request):
    return web.Response(text="Bot is Running (Interactive Mode)! 🤖")

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
    if now - last_update_time < 5: return 
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
            f"⚡️ **High Speed Transfer**\n"
            f"📂 `{file_name}`\n"
            f"{task_type}: {progress_bar}\n"
            f"🚀 Speed: `{human_readable_size(speed)}/s`\n"
            f"💾 Size: `{human_readable_size(current)} / {human_readable_size(total)}`"
        )
    except: pass

# --- LINK PARSER ---
def extract_id_from_link(link):
    # Link se Message ID nikalna (Channel ID ignore karte hain kyunki wo pehle step me mil gaya)
    regex = r"(\d+)$"
    match = re.search(regex, link)
    if match:
        return int(match.group(1))
    return None

# --- TRANSFER LOGIC ---
async def transfer_process(event, source_id, dest_id, start_msg, end_msg):
    global is_running, status_message
    
    status_message = await event.respond(
        f"🚀 **Mission Started!**\n"
        f"From ID: `{start_msg}`\n"
        f"To ID: `{end_msg}`\n"
        f"Mode: **Sequential Turbo**"
    )
    
    total_processed = 0
    
    try:
        # Loop through Range
        async for message in user_client.iter_messages(
            source_id, 
            min_id=start_msg-1, 
            max_id=end_msg+1, 
            reverse=True
        ):
            if not is_running:
                await status_message.edit("🛑 **Stopped by User!**")
                break

            if getattr(message, 'action', None):
                continue

            try:
                # --- METADATA ---
                file_name = "Text Message"
                attributes = []
                if message.media:
                    if hasattr(message.media, 'document'):
                        attributes = message.media.document.attributes
                        for attr in attributes:
                            if hasattr(attr, 'file_name'):
                                file_name = attr.file_name
                    elif hasattr(message.media, 'photo'):
                        file_name = "Photo.jpg"

                # --- PHASE 1: DOWNLOAD ---
                await status_message.edit(f"⬇️ **Downloading:** `{file_name}`")
                
                thumb = await user_client.download_media(message, thumb=-1) if message.media else None
                buffer = None
                
                if message.media:
                    start_time = time.time()
                    async def dl_callback(curr, tot):
                        await progress_callback(curr, tot, start_time, file_name, "⬇️ Downloading")
                    
                    try:
                        # Chunk Size Boosted to 128KB (Telethon Default is lower)
                        buffer = await user_client.download_media(message, file=bytes, progress_callback=dl_callback)
                    except Exception as e:
                        logger.error(f"Download Error: {e}")
                        continue

                # --- PHASE 2: UPLOAD ---
                await status_message.edit(f"⬆️ **Uploading:** `{file_name}`")

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
                
                # Cleanup RAM & Disk immediately
                if thumb and os.path.exists(thumb): os.remove(thumb)
                if buffer: del buffer
                
                total_processed += 1

            except FloodWaitError as e:
                logger.warning(f"FloodWait: {e.seconds}s")
                await asyncio.sleep(e.seconds)
            except Exception as e:
                logger.error(f"Error on {message.id}: {e}")
                continue

        if is_running:
            await status_message.edit(f"✅ **Job Done!**\nTotal: `{total_processed}` files moved.")

    except Exception as e:
        if status_message: await status_message.edit(f"❌ Error: {e}")
    finally:
        is_running = False

# --- COMMAND HANDLERS ---

@bot_client.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    await event.respond(
        "👋 **Smart Clone Bot Ready!**\n\n"
        "**Step 1:** `/clone <Source_ID> <Dest_ID>`\n"
        "**Step 2:** Wait for Bot to ask for Range.\n"
        "**Step 3:** Send Range Link.\n\n"
        "To Stop: `/stop`"
    )

# Step 1: User sets Target
@bot_client.on(events.NewMessage(pattern='/clone'))
async def clone_init(event):
    global is_running
    if is_running: return await event.respond("⚠️ Task already running! Use `/stop`.")

    try:
        args = event.text.split()
        source_id = int(args[1])
        dest_id = int(args[2])

        # State Save kar rahe hain
        pending_requests[event.chat_id] = {
            'source': source_id,
            'dest': dest_id
        }
        
        await event.respond(
            "✅ **Target Set!**\n\n"
            "Ab **Range** bhejo is format me:\n"
            "`https://t.me/c/xxx/6-https://t.me/c/xxx/6465`\n\n"
            "_(Matlab start message link - end message link)_"
        )
    except:
        await event.respond("❌ Usage: `/clone -100xxx -100yyy`")

# Step 2: User sends Range (This listens to ALL messages)
@bot_client.on(events.NewMessage())
async def range_listener(event):
    global current_task, is_running
    
    # Check 1: Kya user ne pehle /clone command diya tha?
    if event.chat_id not in pending_requests:
        return # Ignore random messages
    
    # Check 2: Kya message me 't.me' aur '-' hai?
    text = event.text.strip()
    if "t.me" not in text or "-" not in text:
        return # Probably not a range link
    
    try:
        links = text.split("-")
        link1 = links[0].strip()
        link2 = links[1].strip()
        
        msg1_id = extract_id_from_link(link1)
        msg2_id = extract_id_from_link(link2)

        if not msg1_id or not msg2_id:
            return await event.respond("❌ Links me Message ID nahi mila!")
        
        # Ensure correct order
        if msg1_id > msg2_id:
            msg1_id, msg2_id = msg2_id, msg1_id

        # Get stored IDs
        data = pending_requests[event.chat_id]
        source_id = data['source']
        dest_id = data['dest']
        
        # Clean up state
        del pending_requests[event.chat_id]

        # Start Task
        is_running = True
        current_task = asyncio.create_task(
            transfer_process(event, source_id, dest_id, msg1_id, msg2_id)
        )
        
    except Exception as e:
        await event.respond(f"❌ Range Error: {e}")

@bot_client.on(events.NewMessage(pattern='/stop'))
async def stop_handler(event):
    global is_running
    is_running = False
    if current_task: current_task.cancel()
    # Clear any pending states
    if event.chat_id in pending_requests:
        del pending_requests[event.chat_id]
    await event.respond("🛑 Stopping...")

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    user_client.start()
    loop.create_task(start_web_server())
    bot_client.start(bot_token=BOT_TOKEN)
    bot_client.run_until_disconnected()


    
