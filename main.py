import os
import asyncio
import random
from telethon import TelegramClient, events
from telethon.tl.types import InputPeerChannel, InputPeerChat, InputPeerUser
from telethon.errors import FloodWaitError, ChannelPrivateError
from datetime import datetime
from aiohttp import web
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Environment Variables
API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
STRING_SESSION = os.getenv('STRING_SESSION')
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID'))
PORT = int(os.getenv('PORT', 8080))

# Global Variables
active_transfers = {}
source_channel = None

# Initialize Clients
user_client = TelegramClient('user_session', API_ID, API_HASH)
bot_client = TelegramClient('bot_session', API_ID, API_HASH)


async def start_clients():
    """Start both User and Bot clients"""
    try:
        await user_client.start(string_session=STRING_SESSION)
        await bot_client.start(bot_token=BOT_TOKEN)
        logger.info("✅ Dono clients successfully start ho gaye!")
        
        me = await user_client.get_me()
        logger.info(f"User Client: {me.first_name} (@{me.username})")
        
        bot_me = await bot_client.get_me()
        logger.info(f"Bot Client: {bot_me.first_name} (@{bot_me.username})")
        
    except Exception as e:
        logger.error(f"❌ Client start karne mein error: {e}")
        raise


async def get_random_delay():
    """Anti-ban ke liye random delay"""
    delay = random.uniform(1.5, 3.5)
    await asyncio.sleep(delay)


async def forward_message_zero_copy(message, destination_chat_id):
    """
    Zero-copy transfer using file_id and file_reference
    Yeh function local storage use nahi karta
    """
    try:
        if message.media:
            # Media ke saath message forward karo (zero-copy)
            await bot_client.send_message(
                destination_chat_id,
                message.text or "",
                file=message.media
            )
        else:
            # Text-only message
            await bot_client.send_message(
                destination_chat_id,
                message.text or ""
            )
        
        return True
    
    except FloodWaitError as e:
        logger.warning(f"⚠️ FloodWait: {e.seconds} seconds wait karna padega")
        await asyncio.sleep(e.seconds)
        return await forward_message_zero_copy(message, destination_chat_id)
    
    except Exception as e:
        logger.error(f"❌ Message forward mein error: {e}")
        return False


async def auto_transfer_channel(source_id, destination_id, start_from_id=0):
    """
    Sequential transfer with anti-ban measures
    """
    transfer_id = f"{source_id}_{destination_id}"
    active_transfers[transfer_id] = {
        'status': 'running',
        'transferred': 0,
        'failed': 0,
        'start_time': datetime.now()
    }
    
    try:
        logger.info(f"🚀 Transfer shuru: {source_id} -> {destination_id}")
        
        # Channel se messages fetch karo
        async for message in user_client.iter_messages(
            source_id, 
            min_id=start_from_id,
            reverse=True
        ):
            if active_transfers[transfer_id]['status'] == 'stopped':
                break
            
            # Message forward karo (zero-copy)
            success = await forward_message_zero_copy(message, destination_id)
            
            if success:
                active_transfers[transfer_id]['transferred'] += 1
                logger.info(f"✅ Message {message.id} transferred successfully")
            else:
                active_transfers[transfer_id]['failed'] += 1
            
            # Anti-ban delay
            await get_random_delay()
        
        active_transfers[transfer_id]['status'] = 'completed'
        logger.info(f"🎉 Transfer complete! Total: {active_transfers[transfer_id]['transferred']}")
        
    except ChannelPrivateError:
        logger.error("❌ Channel private hai ya access nahi hai")
        active_transfers[transfer_id]['status'] = 'error'
    
    except Exception as e:
        logger.error(f"❌ Transfer mein error: {e}")
        active_transfers[transfer_id]['status'] = 'error'


# Bot Commands
@bot_client.on(events.NewMessage(pattern='/start'))
async def start_command(event):
    """Start command handler"""
    if event.sender_id != ADMIN_ID:
        await event.respond("❌ Unauthorized access!")
        return
    
    await event.respond(
        "🤖 **Telegram Auto-Forwarder Bot**\n\n"
        "**Available Commands:**\n"
        "/setsource <channel_id> - Source channel set karo\n"
        "/transfer <destination_id> - Transfer shuru karo\n"
        "/status - Current transfer status dekho\n"
        "/stop - Running transfer band karo"
    )


@bot_client.on(events.NewMessage(pattern='/setsource'))
async def set_source_command(event):
    """Set source channel"""
    global source_channel
    
    if event.sender_id != ADMIN_ID:
        return
    
    try:
        channel_id = int(event.text.split()[1])
        
        # Verify channel access
        await user_client.get_entity(channel_id)
        source_channel = channel_id
        
        await event.respond(f"✅ Source channel set: `{channel_id}`")
    
    except IndexError:
        await event.respond("❌ Usage: /setsource <channel_id>")
    except ValueError:
        await event.respond("❌ Invalid channel ID")
    except Exception as e:
        await event.respond(f"❌ Error: {e}")


@bot_client.on(events.NewMessage(pattern='/transfer'))
async def transfer_command(event):
    """Start transfer"""
    if event.sender_id != ADMIN_ID:
        return
    
    if not source_channel:
        await event.respond("❌ Pehle source channel set karo: /setsource <id>")
        return
    
    try:
        destination_id = int(event.text.split()[1])
        
        await event.respond(f"🚀 Transfer shuru ho raha hai...\n"
                          f"Source: `{source_channel}`\n"
                          f"Destination: `{destination_id}`")
        
        # Background task start karo
        asyncio.create_task(auto_transfer_channel(source_channel, destination_id))
    
    except IndexError:
        await event.respond("❌ Usage: /transfer <destination_id>")
    except ValueError:
        await event.respond("❌ Invalid destination ID")


@bot_client.on(events.NewMessage(pattern='/status'))
async def status_command(event):
    """Check transfer status"""
    if event.sender_id != ADMIN_ID:
        return
    
    if not active_transfers:
        await event.respond("📊 Koi active transfer nahi hai")
        return
    
    status_text = "📊 **Transfer Status:**\n\n"
    for tid, data in active_transfers.items():
        status_text += (
            f"**ID:** `{tid}`\n"
            f"Status: {data['status']}\n"
            f"Transferred: {data['transferred']}\n"
            f"Failed: {data['failed']}\n"
            f"Duration: {(datetime.now() - data['start_time']).seconds}s\n\n"
        )
    
    await event.respond(status_text)


@bot_client.on(events.NewMessage(pattern='/stop'))
async def stop_command(event):
    """Stop active transfers"""
    if event.sender_id != ADMIN_ID:
        return
    
    for tid in active_transfers:
        active_transfers[tid]['status'] = 'stopped'
    
    await event.respond("⏹️ Saare transfers stop kar diye gaye")


# Health Check Server
async def health_check(request):
    """Health check endpoint for Render"""
    return web.Response(text="OK", status=200)


async def start_web_server():
    """Start web server for health checks"""
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"🌐 Health check server running on port {PORT}")


async def main():
    """Main function"""
    try:
        # Start clients
        await start_clients()
        
        # Start health check server
        await start_web_server()
        
        logger.info("✅ Bot successfully start ho gaya!")
        logger.info("🔄 Bot running... Press Ctrl+C to stop")
        
        # Keep running
        await asyncio.Event().wait()
    
    except KeyboardInterrupt:
        logger.info("🛑 Bot band ho raha hai...")
    
    finally:
        await user_client.disconnect()
        await bot_client.disconnect()


if __name__ == '__main__':
    asyncio.run(main())
