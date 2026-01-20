import asyncio
import time
import json
from dataclasses import dataclass
from typing import Dict, Set
import os
from dotenv import load_dotenv
import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# Завантажуємо конфігурацію з .env файлу
load_dotenv()

# ----------------- Telegram -----------------
telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
admin_user_id = os.getenv("ADMIN_USER_ID", "").strip()

# Проверка всех конфигов
if not telegram_bot_token:
    print("❌ ОШИБКА: TELEGRAM_BOT_TOKEN не установлен!")
    print("📝 Создайте файл .env и добавьте TELEGRAM_BOT_TOKEN")
    exit(1)

if admin_user_id and admin_user_id.strip():
    try:
        admin_user_id = int(admin_user_id)
    except ValueError:
        print("⚠️ ADMIN_USER_ID має бути числом, ігнорується")
        admin_user_id = None
else:
    admin_user_id = None
# ----------------- Обязательная подписка -----------------
REQUIRED_CHANNEL = "@mexcsofts"  # Канал на который нужно подписаться
REQUIRED_CHANNEL_ID = -1003419272973  # ID канала (без @)

# ----------------- Splash state -----------------
STOCKS_SPLASH_THRESHOLD = 1
CASUAL_SPLASH_THRESHOLD = 5
FAIRPRICE_CHANGE_THRESHOLD = 3
FAIRPRICE_STEP_THRESHOLD = 1
HOLDVOL_SPLASH_THRESHOLD = 10
SYMBOLS_TO_IGNORE = []
isTrackingSTOCKS = True
splash_state = {}
fairprice_state = {}
holdvol_state = {}
bot_users: Set[int] = set()  # Храним ID пользователей которые писали боту
user_subscriptions: Dict[int, Set[str]] = {}  # Храним подписки пользователей {user_id: {symbols}}
user_thresholds: Dict[int, float] = {}  # Храним персональные пороги splash {user_id: threshold_percent}

# Файл для сохранения состояния
STATE_FILE = "bot_state.json"

async def check_subscription(bot: Bot, user_id: int) -> bool:
    """Проверяет подписку пользователя на обязательный канал"""
    # Админ всегда имеет доступ
    if admin_user_id and user_id == admin_user_id:
        return True
    
    try:
        member = await bot.get_chat_member(chat_id=REQUIRED_CHANNEL_ID, user_id=user_id)
        # Проверяем статус: member, administrator, creator
        is_subscribed = member.status in ["member", "administrator", "creator"]
        print(f"[SUBSCRIPTION] User {user_id} subscription check: {is_subscribed} (status: {member.status})")
        return is_subscribed
    except Exception as e:
        print(f"[SUBSCRIPTION] Ошибка проверки подписки для {user_id}: {e}")
        # Если ошибка доступа к каналу - пропускаем проверку (бот не админ канала)
        if "chat not found" in str(e).lower() or "forbidden" in str(e).lower():
            print(f"[SUBSCRIPTION] Бот не имеет доступа к каналу, пропускаем проверку")
            return True
        return False

async def send_subscription_required(message: types.Message):
    """Отправляет сообщение о необходимости подписки с кнопками"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="📢 Подписаться на канал",
                url=f"https://t.me/{REQUIRED_CHANNEL.replace('@', '')}"
            )
        ],
        [
            InlineKeyboardButton(
                text="✅ Проверить подписку",
                callback_data="check_subscription"
            )
        ]
    ])
    
    await message.answer(
        f"🔒 <b>Для использования бота необходимо подписаться на канал!</b>\n\n"
        f"📢 Канал: {REQUIRED_CHANNEL}\n\n"
        f"После подписки нажмите кнопку \"Проверить подписку\"",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

def save_state():
    """Сохраняем состояние бота в файл"""
    state = {
        "bot_users": list(bot_users),
        "user_subscriptions": {str(k): list(v) for k, v in user_subscriptions.items()},
        "user_thresholds": {str(k): v for k, v in user_thresholds.items()}
    }
    try:
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        print(f"[STATE] Сохранено: {len(bot_users)} пользователей, {sum(len(v) for v in user_subscriptions.values())} подписок")
    except Exception as e:
        print(f"[STATE] Ошибка сохранения: {e}")

def load_state():
    """Загружаем состояние бота из файла"""
    global bot_users, user_subscriptions, user_thresholds
    
    if not os.path.exists(STATE_FILE):
        print("[STATE] Файл состояния не найден, начинаем с чистого листа")
        return
    
    try:
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            state = json.load(f)
        
        bot_users = set(state.get("bot_users", []))
        user_subscriptions = {int(k): set(v) for k, v in state.get("user_subscriptions", {}).items()}
        user_thresholds = {int(k): float(v) for k, v in state.get("user_thresholds", {}).items()}
        
        print(f"[STATE] Загружено: {len(bot_users)} пользователей, {sum(len(v) for v in user_subscriptions.values())} подписок")
    except Exception as e:
        print(f"[STATE] Ошибка загрузки: {e}")

# ----------------- Data models -----------------
@dataclass
class TickerContractDetail:
    symbol: str
    isStock: bool
    limitMaxVol: float
    contractSize: float
    quoteCoin: str
    baseCoin: str
    maxVol: float

@dataclass
class TickerMarketData:
    tickerContract: TickerContractDetail
    lastPrice: float
    fairPrice: float
    indexPrice: float
    fundingRate: float
    openInterest: float
    volume24h: float

# Кеш доступних контрактів MEXC (оголошуємо після класу)
available_contracts: Dict[str, TickerContractDetail] = {}

# Helper function для нормалізації тікера
def normalize_symbol(input_symbol: str) -> tuple[str | None, list[str]]:
    """
    Нормалізує введений символ, додаючи _USDT якщо потрібно.
    Повертає (символ, список_можливих_варіантів)
    """
    symbol = input_symbol.upper().strip()
    
    # Якщо вже є в контрактах - повертаємо як є
    if symbol in available_contracts:
        return symbol, [symbol]
    
    # Якщо немає "_", додаємо "_USDT"
    if "_" not in symbol:
        usdt_symbol = f"{symbol}_USDT"
        if usdt_symbol in available_contracts:
            return usdt_symbol, [usdt_symbol]
        
        # Шукаємо всі можливі варіанти
        possible = [s for s in available_contracts.keys() if s.startswith(f"{symbol}_")]
        if possible:
            return None, possible
    
    return None, []


# ----------------- Async Telegram -----------------
async def send_telegram_message(session, chat_id, text):
    url = f"https://api.telegram.org/bot{telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        async with session.post(url, json=payload) as r:
            await r.text()
    except Exception as e:
        print("Telegram error:", e)

# ----------------- Bot Commands -----------------
async def handle_start(message: types.Message, bot: Bot):
    """Обработка команды /start"""
    user_id = message.from_user.id
    bot_users.add(user_id)
    
    # Проверка подписки на канал
    if not await check_subscription(bot, user_id):
        await send_subscription_required(message)
        return
    
    username = message.from_user.username or message.from_user.first_name
    await message.answer(
        f"👋 Привет, {username}!\n\n"
        f"🤖 Это бот для мониторинга сплешей и дампов MEXC .\n"
        f"📝 <b>Доступные команды:</b>\n"
        f"  /search BTC - найти доступные монеты\n"
        f"  /subscribe SYMBOL - подписаться на монету\n"
        f"  /unsubscribe SYMBOL - отписаться от монеты\n"
        f"  /clear - удалить все подписки\n"
        f"  /my - посмотреть свои подписки\n\n"
        f"  /setthreshold ПРОЦЕНТ - установить свой порог\n"
        f"  /mythreshold - посмотреть свой порог\n\n"
        f"✅ Используйте /search для поиска монет!",
        parse_mode="HTML"
    )
    save_state()
    print(f"[BOT] Новый пользователь: {username} (ID: {user_id})")

async def handle_users(message: types.Message, page: int = 0):
    """Обработка команды /users - только для админа с пагинацией"""
    user_id = message.from_user.id
    
    # Проверка админа
    if admin_user_id and user_id != admin_user_id:
        await message.answer("❌ У вас нет доступа к этой команде.")
        return
    
    await send_users_page(message, page)

async def send_users_page(target: types.Message | types.CallbackQuery, page: int = 0):
    """Отправка страницы со списком пользователей"""
    USERS_PER_PAGE = 10
    
    total_users = len(bot_users)
    sorted_users = sorted(bot_users)
    
    # Вычисляем границы страницы
    start_idx = page * USERS_PER_PAGE
    end_idx = min(start_idx + USERS_PER_PAGE, total_users)
    page_users = sorted_users[start_idx:end_idx]
    
    # Формируем список пользователей
    if total_users > 0:
        user_list = "\n".join([f"  {start_idx + i + 1}. User ID: <code>{uid}</code>" 
                               for i, uid in enumerate(page_users)])
    else:
        user_list = "<i>Пока нет пользователей</i>"
    
    # Текст сообщения
    total_pages = (total_users + USERS_PER_PAGE - 1) // USERS_PER_PAGE
    current_page = page + 1
    
    response = (
        f"👥 <b>Статистика пользователей</b>\n\n"
        f"Всего пользователей: <b>{total_users}</b>\n"
        f"Страница: <b>{current_page}</b> из <b>{total_pages}</b>\n\n"
        f"<b>Список:</b>\n{user_list}"
    )
    
    # Создаем кнопки пагинации
    keyboard = []
    buttons = []
    
    # Кнопка "Назад"
    if page > 0:
        buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"users_page:{page-1}"))
    
    # Кнопка "Вперед"
    if end_idx < total_users:
        buttons.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"users_page:{page+1}"))
    
    if buttons:
        keyboard.append(buttons)
    
    markup = InlineKeyboardMarkup(inline_keyboard=keyboard) if keyboard else None
    
    # Отправляем или редактируем сообщение
    if isinstance(target, types.Message):
        await target.answer(response, parse_mode="HTML", reply_markup=markup)
    else:  # CallbackQuery
        await target.message.edit_text(response, parse_mode="HTML", reply_markup=markup)
        await target.answer()

async def handle_users_pagination(callback: types.CallbackQuery):
    """Обработка нажатий на кнопки пагинации"""
    # Проверка админа
    if admin_user_id and callback.from_user.id != admin_user_id:
        await callback.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    # Извлекаем номер страницы
    page = int(callback.data.split(":")[1])
    await send_users_page(callback, page)

async def handle_check_subscription(callback: types.CallbackQuery, bot: Bot):
    """Обработка нажатия кнопки проверки подписки"""
    user_id = callback.from_user.id
    
    # Проверяем подписку
    is_subscribed = await check_subscription(bot, user_id)
    
    if is_subscribed:
        await callback.message.edit_text(
            f"✅ <b>Отлично!</b>\n\n"
            f"Вы подписаны на канал {REQUIRED_CHANNEL}\n\n"
            f"Теперь вам доступны все функции бота!\n"
            f"Используйте /start для начала работы.",
            parse_mode="HTML"
        )
        await callback.answer("✅ Подписка подтверждена!")
    else:
        await callback.answer(
            f"❌ Вы еще не подписаны на канал {REQUIRED_CHANNEL}\n\n"
            f"Подпишитесь и попробуйте снова!",
            show_alert=True
        )

async def handle_subscribe(message: types.Message, bot: Bot):
    """Обработка команды /subscribe SYMBOL - подписка на монету"""
    user_id = message.from_user.id
    bot_users.add(user_id)
    
    # Проверка подписки на канал
    if not await check_subscription(bot, user_id):
        await send_subscription_required(message)
        return
    
    # Извлекаем символ из команды
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "❌ Укажите символ монеты!\n\n"
            "Пример: <code>/subscribe BTC</code> или <code>/subscribe BTC_USDT</code>\n\n"
            "Используйте /search BTC для поиска доступных монет",
            parse_mode="HTML"
        )
        return
    
    input_symbol = args[1].strip()
    symbol, possible = normalize_symbol(input_symbol)
    
    # Проверяем существует ли такой тикер в MEXC
    if symbol is None:
        if possible:
            # Показываем возможные варианты
            similar_list = "\n".join([f"  • <code>{s}</code>" for s in possible[:10]])
            await message.answer(
                f"❓ Найдено несколько вариантов для <b>{input_symbol}</b>:\n\n"
                f"{similar_list}\n\n"
                f"Используйте полное название, например:\n"
                f"<code>/subscribe {possible[0]}</code>",
                parse_mode="HTML"
            )
        else:
            await message.answer(
                f"❌ Тикер <b>{input_symbol}</b> не найден на MEXC\n\n"
                f"Используйте /search для поиска доступных монет",
                parse_mode="HTML"
            )
        return
    
    # Инициализируем подписки пользователя если их нет
    if user_id not in user_subscriptions:
        user_subscriptions[user_id] = set()
    
    # Проверяем не подписан ли уже
    if symbol in user_subscriptions[user_id]:
        await message.answer(f"ℹ️ Вы уже подписаны на <b>{symbol}</b>", parse_mode="HTML")
        return
    
    # Добавляем подписку
    user_subscriptions[user_id].add(symbol)
    save_state()
    contract = available_contracts[symbol]
    await message.answer(
        f"✅ Вы подписались на <b>{symbol}</b>\n"
        f"Монета: ${contract.baseCoin}\n\n"
        f"Теперь вы будете получать алерты по этой монете.",
        parse_mode="HTML"
    )
    print(f"[BOT] User {user_id} subscribed to {symbol}")

async def handle_unsubscribe(message: types.Message, bot: Bot):
    """Обработка команды /unsubscribe SYMBOL - отписка от монеты"""
    user_id = message.from_user.id
    
    # Проверка подписки на канал
    if not await check_subscription(bot, user_id):
        await send_subscription_required(message)
        return
    
    # Извлекаем символ из команды
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "❌ Укажите символ монеты!\n\n"
            "Пример: <code>/unsubscribe BTC</code>",
            parse_mode="HTML"
        )
        return
    
    input_symbol = args[1].strip()
    symbol, _ = normalize_symbol(input_symbol)
    
    if symbol is None:
        symbol = input_symbol.upper()
    
    # Проверяем есть ли подписки
    if user_id not in user_subscriptions or symbol not in user_subscriptions[user_id]:
        await message.answer(f"ℹ️ Вы не подписаны на <b>{symbol}</b>", parse_mode="HTML")
        return
    
    # Удаляем подписку
    user_subscriptions[user_id].remove(symbol)
    save_state()
    await message.answer(
        f"✅ Вы отписались от <b>{symbol}</b>",
        parse_mode="HTML"
    )
    print(f"[BOT] User {user_id} unsubscribed from {symbol}")

async def handle_clear_subscriptions(message: types.Message, bot: Bot):
    """Обработка команды /clear - удалить все подписки"""
    user_id = message.from_user.id
    
    # Проверка подписки на канал
    if not await check_subscription(bot, user_id):
        await send_subscription_required(message)
        return
    
    # Проверяем есть ли подписки
    if user_id not in user_subscriptions or not user_subscriptions[user_id]:
        await message.answer("ℹ️ У вас нет активных подписок.")
        return
    
    count = len(user_subscriptions[user_id])
    user_subscriptions[user_id].clear()
    save_state()
    
    await message.answer(
        f"✅ Все подписки удалены!\n\n"
        f"Было удалено: <b>{count}</b> монет(ы)",
        parse_mode="HTML"
    )
    print(f"[BOT] User {user_id} cleared all subscriptions ({count} coins)")

async def handle_my_subscriptions(message: types.Message, bot: Bot):
    """Обработка команды /my - показать свои подписки"""
    user_id = message.from_user.id
    bot_users.add(user_id)
    
    # Проверка подписки на канал
    if not await check_subscription(bot, user_id):
        await send_subscription_required(message)
        return
    
    # Проверяем есть ли подписки
    if user_id not in user_subscriptions or not user_subscriptions[user_id]:
        await message.answer(
            "📭 У вас пока нет подписок на монеты.\n\n"
            "Используйте команду:\n"
            "<code>/subscribe SYMBOL</code>\n\n"
            "Например: <code>/subscribe BTC_USDT</code>",
            parse_mode="HTML"
        )
        return
    
    # Формируем список подписок
    subscriptions = sorted(user_subscriptions[user_id])
    sub_list = "\n".join([f"  • <code>{symbol}</code>" for symbol in subscriptions])
    
    await message.answer(
        f"📊 <b>Ваши подписки</b>\n\n"
        f"Всего монет: <b>{len(subscriptions)}</b>\n\n"
        f"{sub_list}\n\n"
        f"Для отписки используйте:\n"
        f"<code>/unsubscribe SYMBOL</code>",
        parse_mode="HTML"
    )

async def handle_set_threshold(message: types.Message, bot: Bot):
    """Обработка команды /setthreshold ПРОЦЕНТ - установить персональный порог splash"""
    user_id = message.from_user.id
    
    # Проверка подписки на канал
    if not await check_subscription(bot, user_id):
        await send_subscription_required(message)
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "❌ Укажите порог в процентах!\n\n"
            "Пример: <code>/setthreshold 4.5</code>",
            parse_mode="HTML"
        )
        return
    try:
        threshold = float(args[1].replace(",", ".").strip())
        if threshold <= 0 or threshold > 100:
            raise ValueError
    except ValueError:
        await message.answer("❌ Неверный формат порога. Введите число от 0 до 100.")
        return
    user_thresholds[user_id] = threshold
    save_state()
    await message.answer(
        f"✅ Ваш персональный порог splash установлен: <b>{threshold}%</b>\n\n"
        f"Теперь алерты будут приходить только при изменении цены на {threshold}% и более.",
        parse_mode="HTML"
    )

async def handle_my_threshold(message: types.Message):
    """Обработка команды /mythreshold - показать персональный порог splash"""
    user_id = message.from_user.id
    threshold = user_thresholds.get(user_id)
    if threshold is not None:
        await message.answer(
            f"🔔 Ваш персональный порог splash: <b>{threshold}%</b>",
            parse_mode="HTML"
        )
    else:
        await message.answer(
            f"🔔 У вас не установлен персональный порог splash.\n"
            f"По умолчанию: <b>{CASUAL_SPLASH_THRESHOLD}%</b>\n\n"
            f"Установить свой: <code>/setthreshold 4.5</code>",
            parse_mode="HTML"
        )

async def handle_search(message: types.Message):
    """Обработка команды /search TERM - поиск доступных монет"""
    user_id = message.from_user.id
    bot_users.add(user_id)
    
    # Извлекаем поисковый запрос
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        # Показываем топ монет
        top_symbols = list(available_contracts.keys())[:20]
        symbols_list = "\n".join([f"  • <code>{s}</code>" for s in top_symbols])
        await message.answer(
            f"🔍 <b>Топ 20 монет на MEXC:</b>\n\n{symbols_list}\n\n"
            f"Для поиска используйте:\n<code>/search BTC</code>",
            parse_mode="HTML"
        )
        return
    
    search_term = args[1].upper().strip()
    
    # Ищем монеты
    matches = [s for s in available_contracts.keys() if search_term in s]
    
    if not matches:
        await message.answer(
            f"❌ Монеты с <b>{search_term}</b> не найдены\n\n"
            f"Попробуйте другой запрос",
            parse_mode="HTML"
        )
        return
    
    # Показываем первые 20 результатов
    results = matches[:20]
    symbols_list = "\n".join([f"  • <code>{s}</code>" for s in results])
    
    more_text = f"\n\n... и еще {len(matches) - 20} монет" if len(matches) > 20 else ""
    
    await message.answer(
        f"🔍 <b>Найдено монет:</b> {len(matches)}\n\n"
        f"{symbols_list}{more_text}\n\n"
        f"Для подписки: <code>/subscribe SYMBOL</code>",
        parse_mode="HTML"
    )


async def handle_watch(message: types.Message):
    """Команда для перегляду поточного статусу монети"""
    user_id = message.from_user.id
    bot_users.add(user_id)
    
    # Извлекаем символ из команды
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "❌ Укажите символ монеты!\n\n"
            "Пример: <code>/watch SENT</code> или <code>/watch SENT_USDT</code>",
            parse_mode="HTML"
        )
        return
    
    input_symbol = args[1].strip()
    symbol, possible = normalize_symbol(input_symbol)
    
    # Проверяем существует ли такой тикер
    if symbol is None:
        if possible:
            similar_list = "\n".join([f"  • <code>{s}</code>" for s in possible[:5]])
            await message.answer(
                f"❓ Найдено несколько вариантов:\n\n{similar_list}\n\n"
                f"Используйте полное название",
                parse_mode="HTML"
            )
        else:
            await message.answer(
                f"❌ Тикер <b>{input_symbol}</b> не найден\n\n"
                f"Используйте /search для поиска",
                parse_mode="HTML"
            )
        return
    
    # Получаем текущее состояние
    state = splash_state.get(symbol)
    subscribed = symbol in user_subscriptions.get(user_id, set())
    user_threshold = user_thresholds.get(user_id, CASUAL_SPLASH_THRESHOLD)
    
    if state:
        current_price = state.get('max', 0)  # используем последнюю известную цену
        max_price = state['max']
        min_price = state['min']
        last_direction = state.get('last_direction', 'none')
        
        # Считаем текущие изменения
        drop_from_max = ((current_price - max_price) / max_price * 100) if max_price > 0 else 0
        pump_from_min = ((current_price - min_price) / min_price * 100) if min_price > 0 else 0
        
        status_msg = (
            f"📊 <b>Статус {symbol}</b>\n\n"
            f"💰 Max: {max_price:.8f}\n"
            f"💰 Min: {min_price:.8f}\n"
            f"📈 От мин: {pump_from_min:+.2f}%\n"
            f"📉 От макс: {drop_from_max:+.2f}%\n\n"
            f"🔄 Последнее направление: {last_direction}\n"
            f"🎯 Ваш порог: {user_threshold}%\n"
            f"{'✅ Подписаны' if subscribed else '❌ Не подписаны'}\n\n"
            f"⚠️ Алерт будет отправлен при изменении ≥{user_threshold}%"
        )
    else:
        status_msg = (
            f"📊 <b>Статус {symbol}</b>\n\n"
            f"⏳ Монета еще не отслеживается\n"
            f"Данные появятся после первого обновления\n\n"
            f"🎯 Ваш порог: {user_threshold}%\n"
            f"{'✅ Подписаны' if subscribed else '❌ Не подписаны'}"
        )
    
    await message.answer(status_msg, parse_mode="HTML")

async def handle_user_info(message: types.Message):
    """Обработка команды /user ID - показать инфо о пользователе (только для админа)"""
    user_id = message.from_user.id
    
    # Проверка админа
    if admin_user_id and user_id != admin_user_id:
        await message.answer("❌ У вас нет доступа к этой команде.")
        return
    
    # Извлекаем ID из команды
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "❌ Укажите ID пользователя!\n\n"
            "Пример: <code>/user 123456789</code>",
            parse_mode="HTML"
        )
        return
    
    try:
        target_user_id = int(args[1].strip())
    except ValueError:
        await message.answer("❌ Неверный формат ID. Используйте числовой ID.")
        return
    
    # Проверяем существует ли пользователь
    if target_user_id not in bot_users:
        await message.answer(
            f"❌ Пользователь с ID <code>{target_user_id}</code> не найден\n\n"
            f"Пользователь должен хотя бы раз написать боту /start",
            parse_mode="HTML"
        )
        return
    
    # Получаем подписки пользователя
    subscriptions = user_subscriptions.get(target_user_id, set())
    custom_threshold = user_thresholds.get(target_user_id)
    
    if not subscriptions:
        sub_list = "<i>Нет подписок</i>"
    else:
        sorted_subs = sorted(subscriptions)
        sub_list = "\n".join([f"  • <code>{symbol}</code>" for symbol in sorted_subs])
    
    threshold_text = f"Персональный: <b>{custom_threshold}%</b>" if custom_threshold else f"По умолчанию: {CASUAL_SPLASH_THRESHOLD}%"
    
    response = (
        f"👤 <b>Информация о пользователе</b>\n\n"
        f"User ID: <code>{target_user_id}</code>\n"
        f"Подписок: <b>{len(subscriptions)}</b>\n"
        f"Порог splash: {threshold_text}\n\n"
        f"<b>Отслеживаемые монеты:</b>\n{sub_list}"
    )
    
    await message.answer(response, parse_mode="HTML")

async def handle_all_tracked(message: types.Message):
    """Обработка команды /all_tracked - показать все отслеживаемые монеты (только для админа)"""
    user_id = message.from_user.id
    
    # Проверка админа
    if admin_user_id and user_id != admin_user_id:
        await message.answer("❌ У вас нет доступа к этой команде.")
        return
    
    # Собираем все уникальные монеты
    all_tracked = set()
    for subscribed_symbols in user_subscriptions.values():
        all_tracked.update(subscribed_symbols)
    
    if not all_tracked:
        await message.answer(
            "📭 Никто не отслеживает никакие монеты.",
            parse_mode="HTML"
        )
        return
    
    # Сортируем и форматируем список
    sorted_coins = sorted(all_tracked)
    coins_list = "\n".join([f"  • <code>{symbol}</code>" for symbol in sorted_coins])
    
    # Считаем сколько пользователей отслеживают каждую монету
    coin_user_count = {}
    for subscribed_symbols in user_subscriptions.values():
        for symbol in subscribed_symbols:
            coin_user_count[symbol] = coin_user_count.get(symbol, 0) + 1
    
    # Формируем статистику
    detailed_list = "\n".join([f"  • <code>{symbol}</code> — {coin_user_count[symbol]} пользователь(ей)" for symbol in sorted_coins])
    
    response = (
        f"📊 <b>Все отслеживаемые монеты</b>\n\n"
        f"Всего уникальных монет: <b>{len(all_tracked)}</b>\n\n"
        f"<b>Статистика:</b>\n{detailed_list}"
    )
    
    await message.answer(response, parse_mode="HTML")

async def bot_polling(bot: Bot, dp: Dispatcher):
    """Запуск polling для обработки команд"""
    print("[BOT] Запущен обработчик команд...")
    await dp.start_polling(bot)

# ----------------- FairPrice -----------------
async def send_fairprice_message(session, bot: Bot, md, change):
    """Отправка алерта Fair Price всем подписанным пользователям"""
    symbol = md.tickerContract.symbol
    
    limit_usd = md.tickerContract.maxVol * md.tickerContract.contractSize * md.lastPrice
    emoji = "🟢" if change > 0 else "🔴"
    side = "long" if change > 0 else "short"
    link = f"https://www.mexc.com/ru-RU/futures/{symbol}?lang=ru-RU"

    msg = (
        f"{emoji} <a href='{link}'>${md.tickerContract.baseCoin}</a> Fair Price {change:.2f}%\n"
        f"LastPrice: {md.lastPrice}\n"
        f"FairPrice: {md.fairPrice}\n\n"
        f'Side: {side}\n'
        f"Limit: ~${limit_usd:,.2f}"
    )
    
    # Отправляем всем пользователям, подписанным на этот символ
    sent_count = 0
    for user_id, subscribed_symbols in user_subscriptions.items():
        if symbol in subscribed_symbols:
            try:
                await bot.send_message(chat_id=user_id, text=msg, parse_mode="HTML", disable_web_page_preview=True)
                sent_count += 1
            except Exception as e:
                print(f"[BOT] Failed to send fairprice alert to user {user_id}: {e}")
    
    if sent_count > 0:
        print(f"[ALERT] Fair Price {symbol}: {change:.2f}% → sent to {sent_count} user(s)")

async def send_splash_message(session, bot: Bot, direction, change, splash_state_entry: dict, current_price, market_data_entry: TickerMarketData):
    """Отправка алерта Price Splash всем подписанным пользователям"""
    symbol = market_data_entry.tickerContract.symbol
    now = time.time()
    max_price = splash_state_entry['max']
    min_price = splash_state_entry['min']
    
    duration = (now - splash_state_entry["min_ts"]) / 60 if direction == "up" else (now - splash_state_entry["max_ts"]) / 60

    ticker_base_coin = market_data_entry.tickerContract.baseCoin
    emoji = "🟢" if direction == "up" else "🔴"
    sign = "+" if change > 0 else ""
    link = f"https://www.mexc.com/ru-RU/futures/{symbol}?lang=ru-RU"

    limit_usd = market_data_entry.tickerContract.maxVol * market_data_entry.tickerContract.contractSize * current_price

    message = (
        f"{emoji} <a href='{link}'>${ticker_base_coin}</a> | {sign}{change:.2f}%\n"
        f"LastPrice: {current_price}\n"
        f"FairPrice: {market_data_entry.fairPrice}\n\n"
        f"Limit: ~${limit_usd:,.2f}\n\n"
        f"⏱️ {duration:.1f} min\n"
    )
    
    # Отправляем всем пользователям, подписанным на этот символ
    sent_count = 0
    for user_id, subscribed_symbols in user_subscriptions.items():
        if symbol in subscribed_symbols:
            try:
                await bot.send_message(chat_id=user_id, text=message, parse_mode="HTML", disable_web_page_preview=True)
                sent_count += 1
            except Exception as e:
                print(f"[BOT] Failed to send splash alert to user {user_id}: {e}")
    
    if sent_count > 0:
        print(f"[ALERT] Price Splash {symbol}: {sign}{change:.2f}% → sent to {sent_count} user(s)")


async def send_holdvol_splash(session, bot: Bot, md_entry: TickerMarketData, direction, change_percent, state_entry):
    """Отправка алерта Open Interest всем подписанным пользователям"""
    symbol = md_entry.tickerContract.symbol
    emoji = "🟢" if direction == "up" else "🔴"
    link = f"https://www.mexc.com/ru-RU/futures/{symbol}?lang=ru-RU"

    # старое и новое значение OI
    old_oi = state_entry["last_alert_holdvol"]
    new_oi = md_entry.openInterest

    # в миллионах
    old_oi_m = old_oi / 1_000_000
    new_oi_m = new_oi / 1_000_000

    # стоимость LP * OI
    old_usd = old_oi * md_entry.lastPrice
    new_usd = new_oi * md_entry.lastPrice

    msg = (
        f"{emoji} <a href='{link}'>${md_entry.tickerContract.baseCoin}</a> OI — {change_percent:+.2f}%\n\n"
        f"{old_oi_m:.2f}m —> {new_oi_m:.2f}m\n"
        f"${old_usd:,.2f} —> ${new_usd:,.2f}"
    )

    # обновляем last_alert_holdvol в переданном state_entry
    state_entry["last_alert_holdvol"] = new_oi
    
    # Отправляем всем пользователям, подписанным на этот символ
    for user_id, subscribed_symbols in user_subscriptions.items():
        if symbol in subscribed_symbols:
            try:
                await bot.send_message(chat_id=user_id, text=msg, parse_mode="HTML", disable_web_page_preview=True)
            except Exception as e:
                print(f"[BOT] Failed to send OI alert to user {user_id}: {e}")

# ----------------- Price splash -----------------
async def check_price(md_entry: TickerMarketData, session, bot: Bot = None):
    price = md_entry.lastPrice
    symbol = md_entry.tickerContract.symbol
    if symbol in SYMBOLS_TO_IGNORE or price == 0:
        return
    
    is_stock = md_entry.tickerContract.isStock
    if is_stock:
        return

    now = time.time()

    if symbol not in splash_state:
        splash_state[symbol] = {"max": price, "max_ts": now, "min": price, "min_ts": now, "last_direction": None}
        # Логування для відстежуваних монет
        if any(symbol in subs for subs in user_subscriptions.values()):
            print(f"[WATCH] {symbol} initialized at {price}")
        return

    s = splash_state[symbol]
    
    # Оновлюємо поточну ціну в стейті для команди /watch
    s["current"] = price
    
    if price > s["max"]:
        s["max"] = price
        s["max_ts"] = now
    if price < s["min"]:
        s["min"] = price
        s["min_ts"] = now

    drop = (price - s["max"]) / s["max"] * 100
    pump = (price - s["min"]) / s["min"] * 100
    
    # Детальне логування для відстежуваних монет
    is_watched = any(symbol in subs for subs in user_subscriptions.values())
    if is_watched and (abs(drop) > 0.05 or abs(pump) > 0.05):  # логуємо навіть малі зміни
        print(f"[WATCH] {symbol}: price={price:.8f}, pump={pump:+.2f}%, drop={drop:+.2f}%, direction={s['last_direction']}")
    
    # Перевіряємо drop - чи є хтось підписаний і чи відповідає їх порогу
    if s["last_direction"] != "down":
        for user_id, subscribed_symbols in user_subscriptions.items():
            if symbol not in subscribed_symbols:
                continue
            user_threshold = user_thresholds.get(user_id, CASUAL_SPLASH_THRESHOLD)
            if abs(drop) >= user_threshold:
                print(f"[TRIGGER] {symbol} drop {drop:.2f}% ≥ user {user_id} threshold {user_threshold}%")
                await send_splash_message(session, bot, "down", drop, s, price, md_entry)
                s["last_direction"] = "down"
                s["min"] = price
                s["min_ts"] = now
                break
    
    # Перевіряємо pump - чи є хтось підписаний і чи відповідає їх порогу
    if s["last_direction"] != "up":
        for user_id, subscribed_symbols in user_subscriptions.items():
            if symbol not in subscribed_symbols:
                continue
            user_threshold = user_thresholds.get(user_id, CASUAL_SPLASH_THRESHOLD)
            if pump >= user_threshold:
                print(f"[TRIGGER] {symbol} pump {pump:.2f}% ≥ user {user_id} threshold {user_threshold}%")
                await send_splash_message(session, bot, "up", pump, s, price, md_entry)
                s["last_direction"] = "up"
                s["max"] = price
                s["max_ts"] = now
                break

async def check_fairprice(md_entry: TickerMarketData, session, bot: Bot = None):
    symbol = md_entry.tickerContract.symbol
    if not md_entry.fairPrice or not md_entry.lastPrice:
        return

    change = (md_entry.fairPrice - md_entry.lastPrice) / md_entry.fairPrice * 100
    abs_change = abs(change)
    side = "above" if md_entry.fairPrice > md_entry.lastPrice else "below"
    state = fairprice_state.get(symbol)

    if abs_change < FAIRPRICE_CHANGE_THRESHOLD:
        fairprice_state.pop(symbol, None)
        return

    if state is None or state["side"] != side:
        if bot:
            await send_fairprice_message(session, bot, md_entry, change)
        fairprice_state[symbol] = {"last_alert_change": change, "side": side}
        return

    if abs(change - state["last_alert_change"]) >= FAIRPRICE_STEP_THRESHOLD:
        if bot:
            await send_fairprice_message(session, bot, md_entry, change)
        state["last_alert_change"] = change

async def check_holdvol_splash(md_entry: TickerMarketData, session, bot: Bot = None):
    symbol = md_entry.tickerContract.symbol
    if symbol in SYMBOLS_TO_IGNORE or md_entry.openInterest == 0:
        return

    current_oi = md_entry.openInterest

    # если первый раз — инициализируем
    if symbol not in holdvol_state:
        holdvol_state[symbol] = {
            "max": current_oi,
            "max_ts": time.time(),
            "min": current_oi,
            "min_ts": time.time(),
            "last_direction": None,
            "last_alert_holdvol": current_oi,  # новое поле
        }
        return

    state = holdvol_state[symbol]
    now = time.time()

    # обновляем макс и мин
    if current_oi > state["max"]:
        state["max"] = current_oi
        state["max_ts"] = now
    if current_oi < state["min"]:
        state["min"] = current_oi
        state["min_ts"] = now

    # считаем изменение относительно макс/мин
    drop = (current_oi - state["max"]) / state["max"] * 100
    pump = (current_oi - state["min"]) / state["min"] * 100

    # сплеш вниз
    if drop <= -HOLDVOL_SPLASH_THRESHOLD and state["last_direction"] != "down":
        if bot:
            await send_holdvol_splash(session, bot, md_entry, "down", drop, state)
        state["last_direction"] = "down"
        state["min"] = current_oi
        state["min_ts"] = now

    # сплеш вверх
    if pump >= HOLDVOL_SPLASH_THRESHOLD and state["last_direction"] != "up":
        if bot:
            await send_holdvol_splash(session, bot, md_entry, "up", pump, state)
        state["last_direction"] = "up"
        state["max"] = current_oi
        state["max_ts"] = now
# ----------------- MEXC API -----------------
async def get_mexc_tickers_contract_detail(session) -> Dict[str, TickerContractDetail]:
    async with session.get("https://contract.mexc.com/api/v1/contract/detail") as r:
        data = (await r.json())["data"]

    contracts = {}
    for c in data:
        is_stock = any("stock" in x.lower() for x in c.get("conceptPlate", []))
        contracts[c["symbol"]] = TickerContractDetail(
            symbol=c["symbol"],
            isStock=is_stock,
            limitMaxVol=float(c["limitMaxVol"]),
            contractSize=float(c["contractSize"]),
            quoteCoin=c["quoteCoinName"],
            baseCoin=c["baseCoinName"],
            maxVol=float(c["maxVol"]),
        )
    return contracts

async def get_mexc_tickers_market_data(session, contracts):
    async with session.get("https://contract.mexc.com/api/v1/contract/ticker") as r:
        data = (await r.json())["data"]

    market = {}
    for t in data:
        c = contracts.get(t["symbol"])
        if not c:
            continue
        # Пропускаємо якщо немає fairPrice
        if "fairPrice" not in t or not t["fairPrice"]:
            continue
        market[t["symbol"]] = TickerMarketData(
            tickerContract=c,
            lastPrice=float(t["lastPrice"]),
            fairPrice=float(t["fairPrice"]),
            indexPrice=float(t["indexPrice"]),
            fundingRate=float(t["fundingRate"]),
            openInterest=float(t["holdVol"]),
            volume24h=float(t["volume24"]),
        )
    return market


# ----------------- Main -----------------
async def monitoring_loop(bot: Bot):
    """Основній цикл моніторингу MEXC"""
    global available_contracts
    
    timeout = aiohttp.ClientTimeout(total=5)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        contracts = await get_mexc_tickers_contract_detail(session)
        available_contracts = contracts  # Оновлюємо глобальний кеш

        last_contracts_update = time.time()
        CONTRACTS_REFRESH_INTERVAL = 60  # обновляем раз в 60 секунд

        while True:
            now = time.time()
            
            # обновляем contracts раз в минуту
            if now - last_contracts_update >= CONTRACTS_REFRESH_INTERVAL:
                try:
                    contracts = await get_mexc_tickers_contract_detail(session)
                    available_contracts = contracts  # Оновлюємо глобальний кеш
                    last_contracts_update = now
                    print(f"[{time.strftime('%H:%M:%S')}] Contracts updated ({len(contracts)} tickers)")
                except Exception as e:
                    print("Error updating contracts:", e)
            try:
                market_data = await get_mexc_tickers_market_data(session, contracts)
            except Exception as e:
                print("Error updating market data:", e)
                await asyncio.sleep(1)
                continue
            try:
                # price splash & fairprice & holdvol alerts
                for symbol, md_entry in market_data.items():
                    await check_price(md_entry, session, bot)
                    await check_fairprice(md_entry, session, bot)
                    # await check_holdvol_splash(md_entry, session, bot)
                    await asyncio.sleep(0.1)
            except Exception as e:
                print("Error parsing market data:", e)
                await asyncio.sleep(1)

async def main():
    """Запуск бота: мониторинг + обработка команд"""
    # Загружаем сохраненное состояние
    load_state()
    
    # Инициализация aiogram бота
    bot = Bot(token=telegram_bot_token)
    dp = Dispatcher()
    
    # Регистрация команд
    dp.message.register(handle_start, Command(commands=["start"]))
    dp.message.register(handle_search, Command(commands=["search", "find"]))
    dp.message.register(handle_watch, Command(commands=["watch", "status"]))
    dp.message.register(handle_users, Command(commands=["users"]))
    dp.message.register(handle_user_info, Command(commands=["user"]))
    dp.message.register(handle_all_tracked, Command(commands=["tracked"]))
    dp.message.register(handle_subscribe, Command(commands=["subscribe", "sub"]))
    dp.message.register(handle_unsubscribe, Command(commands=["unsubscribe", "unsub"]))
    dp.message.register(handle_clear_subscriptions, Command(commands=["clear", "clearall"]))
    dp.message.register(handle_my_subscriptions, Command(commands=["my", "mysubs"]))
    dp.message.register(handle_set_threshold, Command(commands=["setthreshold", "threshold"]))
    dp.message.register(handle_my_threshold, Command(commands=["mythreshold", "mythres"]))
    
    # Регистрация callback handler для пагинации и проверки подписки
    dp.callback_query.register(handle_users_pagination, F.data.startswith("users_page:"))
    dp.callback_query.register(handle_check_subscription, F.data == "check_subscription")
    
    print("[BOT] Starting MEXC Splash Alert Bot...")
    print("[BOT] Monitoring: ENABLED")
    if admin_user_id:
        print(f"[BOT] Admin ID: {admin_user_id}")
    print("[BOT] User commands: /start, /search, /subscribe, /unsubscribe, /clear, /my, /setthreshold, /mythreshold, /tracked")
    print("[BOT] Admin commands: /users, /user\n")
    
    # Запускаем оба таска параллельно
    await asyncio.gather(
        monitoring_loop(bot),
        bot_polling(bot, dp),
    )

import asyncio
import sys

if __name__ == "__main__":
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⏹️ Бот остановлен пользователем")
