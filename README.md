# SMENA

Telegram-магазин номеров api.cc: баланс в USD, админка, TON, Crypto Pay и xRocket.

## Bothost: минимум настроек

Выберите Dockerfile из репозитория. Он запускает `python main.py`: программа создаёт/обновляет базу и запускает бота, админку и проверку платежей в одном контейнере.

В `.env.example` остались только:

- `BOT_TOKEN` — Bothost обычно передаёт его автоматически.
- `APICC_API_KEY` — ключ поставщика номеров.
- `CRYPTOPAY_TOKEN` — Crypto Pay и курс TON/USD.
- `XROCKET_TOKEN` — ключ Legacy API xRocket.
- `TONCENTER_API_KEY` — проверка прямых переводов TON.
- `TON_WALLET` — адрес кошелька.

Ненужные способы оплаты оставьте без ключей. Переменные базы, URL API, портов, пароля PostgreSQL и курса добавлять не нужно. Mainnet-адреса API заданы в коде. Без ключа api.cc включается деморежим.

SQLite находится в `/app/data/smena.db`. Включены WAL, `synchronous=FULL`, внешний ключевой контроль и атомарное изменение баланса с журналом. Папка создаётся автоматически, миграции применяются до запуска служб. Старая `smena.db` из корня переносится через SQLite backup API, включая WAL; исходник сохраняется. Рабочая база в постоянной папке не перезаписывается.

Первый пароль админки создаётся автоматически и сохраняется между перезапусками. Посмотреть в терминале хостинга:

```sh
cat /app/data/admin-access.txt
```

Откройте HTTPS-домен, выданный хостингом. Веб-сервер слушает `0.0.0.0`, порт берёт из уже существующего `PORT` платформы либо использует 8000. В админке: «Каталог и цены» → «Валюта поставщика» → фактическая валюта кабинета api.cc → импорт → настройка цен → включение товаров. Изменение курса отключает товары до проверки новых цен.

## Номера и платежи

- Новая аренда и повторное использование: только «Більше 60 днів», `expiry=5` (в OpenAPI — 60–80 дней). Наличие проверяется с тем же фильтром перед покупкой и при включении товара.
- Продление существующего номера: стандартные месячные периоды API, без обещания ровно 60 суток.
- SMS доступны по персональной ссылке; бот уведомляет о новых сообщениях по счётчику. Для текста SMS в боте нужен формат ответа `/api/record`.
- Автоотмены аренды нет в API. Неопределённые покупки и продления разбираются в админке с аудитом.
- Crypto Pay: счёт в USD, приём TON/USDT. xRocket Legacy: счёт в USDT с учётом 1:1 к USD. Прямой TON: курс Crypto Pay, уникальный комментарий и проверка TON Center.
- Повторное подтверждение не создаёт повторное списание или зачисление. После таймаута платная покупка не повторяется автоматически.

## Локальный запуск

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
# Если .env ещё нет — скопируйте .env.example и заполните ключи.
.venv/Scripts/python.exe main.py
```

На Windows данные находятся в `data/` проекта. Без `BOT_TOKEN` запускаются только админка и worker. Адрес: http://127.0.0.1:8000. Пароль — `data/admin-access.txt`; ранее заданный вручную пароль сохраняется. Проверка хранилища без Telegram: `python main.py --check`.

## Обычный VPS

[Инструкция](deploy/INSTALL.md). Теперь используется один сервис `app` и постоянный SQLite-том. Явно заданный старый `DATABASE_URL` для внешнего PostgreSQL поддерживается, но для Bothost не нужен. PostgreSQL автоматически в SQLite не конвертируется.

Для резервной копии работающей базы используйте SQLite backup API, а не копирование одного `.db` при активном WAL. Сохраняйте и файлы доступа к админке. Не запускайте несколько копий одного Telegram-бота одновременно.

## Проверки

```powershell
.venv/Scripts/python.exe -m pip install -e '.[dev]'
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check app deploy tests migrations main.py
npm ci --ignore-scripts
npm test
.venv/Scripts/python.exe -m app.cli check-ledger
```

[Отчёт об исправлениях](docs/REVIEW.md). Реальная покупка api.cc, платежи и запуск на вашем Bothost ещё требуют проверки с вашими аккаунтами. Основной проверенный путь хранения — SQLite.

Источники: [хранилище Bothost](https://bothost.ru/docs/database-storage), [точка входа](https://bothost.ru/docs/main-file-entrypoint), [Crypto Pay](https://help.send.tg/en/articles/10279948-crypto-pay-api), [xRocket](https://pay.xrocket.tg/api/), [TON Center](https://docs.ton.org/api/v3/blockchain-data/get-transactions). Схема api.cc — `docs/apicc.openapi.json`.
