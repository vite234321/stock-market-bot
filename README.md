Stock Market Bot
Бот для автоматизированной торговли на фондовом рынке с использованием API Tinkoff Invest. Этот проект позволяет отслеживать акции, анализировать рынок с помощью технических индикаторов (RSI, MACD, Bollinger Bands) и совершать сделки на основе предсказаний машинного обучения.
Описание

Язык: Python 3.11
Фреймворк: FastAPI, Aiogram
Библиотеки: Tinkoff Invest API, SQLAlchemy, Scikit-learn, NumPy
Хостинг: Heroku
Функционал:
Автоматическая торговля акциями.
Подписка на свечи в реальном времени.
Анализ рынка с использованием ML-моделей.
Ежедневные отчёты о прибылях.



Установка

Клонируйте репозиторий:
git clone https://github.com/your-username/stock-market-bot.git
cd stock-market-bot


Создайте виртуальное окружение:
python -m venv venv
source venv/bin/activate  # На Linux/Mac
venv\Scripts\activate     # На Windows


Установите зависимости:
pip install -r requirements.txt


Настройте переменные окружения:Создайте файл .env и добавьте следующие переменные:
BOT_TOKEN=ваш_токен_бота_из_Telegram
ADMIN_ID=ваш_идентификатор_администратора


Инициализируйте базу данных:Убедитесь, что у вас настроен PostgreSQL, и выполните миграции (если применимо).


Запуск

Запустите приложение локально:
uvicorn app.api:app --reload


Развёртывание на Heroku:

Установите Heroku CLI.
Выполните:heroku login
heroku create stock-market-bot
git push heroku main
heroku ps:scale web=1





Использование

Настройка:

Установите токен Tinkoff Invest API через команду /set_token <токен> в Telegram.
Добавьте акции для торговли с помощью /add_stock <тикер> (например, /add_stock SBER).


Команды бота:

/start — Начать работу с ботом.
/help — Показать список команд.
/enable_autotrading — Включить автоторговлю.
/disable_autotrading — Выключить автоторговлю.
/status — Проверить статус бота.
/daily_report — Получить дневной отчёт о прибылях.


Пример работы:

Бот анализирует свечи, использует индикаторы и ML для принятия решений о покупке/продаже.
Отправляет уведомления в Telegram о сделках и статусе.



Требования

Python 3.11+
Доступ к Tinkoff Invest API (токен).
Установленные зависимости из requirements.txt.
Настроенный PostgreSQL для базы данных.

Устранение неполадок

Ошибка ModuleNotFoundError: Убедитесь, что requirements.txt обновлён, и выполните pip install -r requirements.txt.
Сбой деплоя на Heroku: Проверьте логи с помощью heroku logs --tail и убедитесь, что все зависимости установлены.
Проблемы с API: Проверьте валидность токена Tinkoff Invest.


Авторы

Скорик Тимофей Алексеевич тг@ogbloof и остальные

Контакты
@ogbloof по предложениям ко мне в лс
