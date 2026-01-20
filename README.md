# MEXC Splash Alert Bot

Telegram бот для моніторингу фьючерсів MEXC з алертами про різкі зміни цін.

## Деплой на Railway.app

### Крок 1: Встановлення Railway CLI
```bash
npm install -g @railway/cli
```

### Крок 2: Логін
```bash
railway login
```

### Крок 3: Ініціалізація проекту
```bash
cd c:\splashbot
railway init
```

### Крок 4: Додати змінні оточення
```bash
railway variables set TELEGRAM_BOT_TOKEN=8271876259:AAG2eUfTwZ5wS89toJVfVfMOZx7ZdGzB9jM
railway variables set ADMIN_USER_ID=1049032098
```

### Крок 5: Деплой
```bash
railway up
```

### Крок 6: Перевірка логів
```bash
railway logs
```

## Альтернатива: Деплой через GitHub

1. Створіть репозиторій на GitHub
2. Запушіть код:
```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/splashbot.git
git push -u origin main
```
3. Зайдіть на https://railway.app
4. New Project → Deploy from GitHub → Виберіть репозиторій
5. Додайте змінні оточення в Settings → Variables:
   - `TELEGRAM_BOT_TOKEN`: 8271876259:AAG2eUfTwZ5wS89toJVfVfMOZx7ZdGzB9jM
   - `ADMIN_USER_ID`: 1049032098

## Команди бота

- `/start` - Привітання та інструкції
- `/search BTC` - Пошук монет
- `/subscribe BTC` - Підписатись на монету
- `/unsubscribe BTC` - Відписатись
- `/clear` - Видалити всі підписки
- `/my` - Мої підписки
- `/setthreshold 2.5` - Встановити поріг алертів (%)
- `/mythreshold` - Подивитись поточний поріг

## Адмін команди

- `/users` - Список користувачів
- `/user USER_ID` - Інфо про користувача
