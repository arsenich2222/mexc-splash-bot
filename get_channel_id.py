import asyncio
from aiogram import Bot
import os
from dotenv import load_dotenv

load_dotenv()
token = os.getenv("TELEGRAM_BOT_TOKEN")

async def get_channel_info():
    bot = Bot(token=token)
    
    # Спроби різних варіантів
    channels = [
        "@mexcsofts",
        "-1002509308697",
        "mexcsofts"
    ]
    
    for channel in channels:
        try:
            chat = await bot.get_chat(channel)
            print(f"\n✅ Знайдено канал: {channel}")
            print(f"ID: {chat.id}")
            print(f"Title: {chat.title}")
            print(f"Username: {chat.username}")
            print(f"Type: {chat.type}")
        except Exception as e:
            print(f"\n❌ Помилка для {channel}: {e}")
    
    await bot.session.close()

if __name__ == "__main__":
    asyncio.run(get_channel_info())
