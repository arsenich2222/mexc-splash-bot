"""
Cloudflare Workers версія MEXC Splash Bot
Використовує webhook замість polling
"""

from js import Response, fetch, JSON
import json
import asyncio

# Глобальні змінні стану
bot_users = set()
user_subscriptions = {}
user_thresholds = {}
available_contracts = {}

TELEGRAM_BOT_TOKEN = "8271876259:AAG2eUfTwZ5wS89toJVfVfMOZx7ZdGzB9jM"
ADMIN_USER_ID = 1049032098
CASUAL_SPLASH_THRESHOLD = 5.0

async def send_telegram_message(chat_id, text, parse_mode="HTML"):
    """Відправка повідомлення через Telegram API"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True
    }
    
    response = await fetch(url, {
        "method": "POST",
        "headers": {"Content-Type": "application/json"},
        "body": JSON.stringify(data)
    })
    return await response.json()

async def handle_start(chat_id, username):
    """Обробка команди /start"""
    bot_users.add(chat_id)
    
    text = (
        f"👋 Привет, {username}!\n\n"
        f"🤖 Это бот для мониторинга фьючерсов MEXC.\n"
        f"📊 Он автоматически отслеживает:\n"
        f"  • Price Splash (резкие изменения цены)\n"
        f"  • Отклонения Fair Price\n"
        f"  • Изменения Open Interest\n\n"
        f"📝 <b>Доступные команды:</b>\n"
        f"  /search BTC - найти доступные монеты\n"
        f"  /subscribe SYMBOL - подписаться на монету\n"
        f"  /unsubscribe SYMBOL - отписаться от монеты\n"
        f"  /clear - удалить все подписки\n"
        f"  /my - посмотреть свои подписки\n\n"
        f"  /setthreshold ПРОЦЕНТ - установить свой порог\n"
        f"  /mythreshold - посмотреть свой порог\n\n"
        f"✅ Используйте /search для поиска монет!"
    )
    
    await send_telegram_message(chat_id, text)

async def handle_webhook(request):
    """Обробка вхідних webhook запитів від Telegram"""
    try:
        update = await request.json()
        
        if "message" not in update:
            return Response.new("ok")
        
        message = update["message"]
        chat_id = message["chat"]["id"]
        text = message.get("text", "")
        username = message["from"].get("username", message["from"].get("first_name", "User"))
        
        # Обробка команд
        if text.startswith("/start"):
            await handle_start(chat_id, username)
        elif text.startswith("/my"):
            subs = user_subscriptions.get(chat_id, set())
            if subs:
                sub_list = "\n".join([f"  • <code>{s}</code>" for s in sorted(subs)])
                await send_telegram_message(
                    chat_id,
                    f"📊 <b>Ваши подписки</b>\n\nВсего монет: <b>{len(subs)}</b>\n\n{sub_list}"
                )
            else:
                await send_telegram_message(
                    chat_id,
                    "📭 У вас пока нет подписок на монеты.\n\nИспользуйте: <code>/subscribe SYMBOL</code>"
                )
        
        return Response.new("ok")
        
    except Exception as e:
        print(f"Error handling webhook: {e}")
        return Response.new("error", {"status": 500})

async def on_fetch(request):
    """Головний обробник запитів Cloudflare Worker"""
    url = request.url
    
    if url.endswith("/webhook"):
        return await handle_webhook(request)
    
    return Response.new("MEXC Splash Bot is running on Cloudflare Workers!")

# Експорт для Cloudflare Workers
exports = {"fetch": on_fetch}
