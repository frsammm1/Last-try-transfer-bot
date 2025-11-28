import os
import asyncio
import logging
import random
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import MediaCaptionTooLongError
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

# --- WEB SERVER (Keep-Alive) ---
async def handle(request):
    return web.Response(text="Bot is Running on Docker! 🐳")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"Web server started on port {PORT}")

# --- TRANSFER LOGIC ---
async def transfer_process(event, source_id, dest_id):
    global is_running
    try:
        await event.respond(f"🚀 **Cloning Started!**\nSource: `{source_id}`\nDest: `{dest_id}`")
        
        async for message in user_client.iter_messages(source_id, reverse=True):
            if not is_running:
                await bot_client.send_message(event.chat_id, "🛑 **Stopped!**")
                break

            if getattr(message, 'action', None):
                continue

            try:
                await asyncio.sleep(random.uniform(2, 5))
                
                if message.text and not message.media:
                    await bot_client.send_message(dest_id, message.text)

                elif message.media:
                    caption = message.text or ""
                    try:
                        # Attempt 1: Direct Reference
                        await bot_client.send_file(dest_id, file=message.media, caption=caption)
                    except MediaCaptionTooLongError:
                        await bot_client.send_file(dest_id, file=message.media)
                        await bot_client.send_message(dest_id, caption)
                    except Exception:
                        # Attempt 2: Stream (No Disk)
                        buffer = await user_client.download_media(message, file=bytes)
                        await bot_client.send_file(dest_id, file=buffer, caption=caption)

            except Exception as e:
                logger.error(f"Error msg {message.id}: {e}")
                continue

        if is_running:
            await bot_client.send_message(event.chat_id, "✅ **Done!**")

    except Exception as e:
        await bot_client.send_message(event.chat_id, f"❌ Error: {e}")
    finally:
        is_running = False

# --- BOT COMMANDS ---
@bot_client.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    await event.respond("👋 **Docker Bot Ready!**\nUse: `/clone <Source_ID> <Dest_ID>`\nStop: `/stop`")

@bot_client.on(events.NewMessage(pattern='/clone'))
async def clone_handler(event):
    global current_task, is_running
    if is_running:
        return await event.respond("⚠️ Already running. Use `/stop` first.")
    
    try:
        args = event.text.split()
        source_id = int(args[1])
        dest_id = int(args[2])
        is_running = True
        current_task = asyncio.create_task(transfer_process(event, source_id, dest_id))
    except Exception:
        await event.respond("❌ Usage: `/clone -100xxxx -100yyyy`")

@bot_client.on(events.NewMessage(pattern='/stop'))
async def stop_handler(event):
    global is_running, current_task
    if not is_running:
        return await event.respond("😴 Nothing to stop.")
    is_running = False
    if current_task: current_task.cancel()
    await event.respond("🛑 Stopping...")

# --- MAIN ---
if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    user_client.start()
    loop.create_task(start_web_server())
    bot_client.start(bot_token=BOT_TOKEN)
    try:
        bot_client.run_until_disconnected()
    except KeyboardInterrupt:
        pass


