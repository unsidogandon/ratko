# Ratko Userbot

Ratko является поддерживаемым Telegram-юзерботом на базе актуальной ветки
[`coddrago/Heroku:beta`](https://github.com/coddrago/Heroku/tree/beta).
Пакет Python `heroku` и внутренние пространства имён базы данных сохранены для
совместимости с существующими модулями, сессиями и резервными копиями.

[English documentation](README.md)

## Безопасность

Сторонние модули выполняют Python-код с теми же системными и Telegram-правами,
что и Ratko. Устанавливайте модули только от доверенных разработчиков. Команды
`.terminal` и `.eval` намеренно дают полный доступ владельца, поэтому их нельзя
разрешать другим пользователям.

## Требования

- Python 3.10 или новее
- Git и FFmpeg
- Telegram `API_ID` и `API_HASH` с <https://my.telegram.org/apps>

## Установка

Ubuntu и Debian:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv
git clone --branch main https://github.com/unsidogandon/ratko.git
cd ratko
./install.sh
```

Ручная установка на других системах:

```bash
git clone --branch main https://github.com/unsidogandon/ratko.git
cd ratko
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m heroku
```

При запуске от `root` добавьте `--root`. Имя пакета `heroku` сохранено ради
совместимости модулей.

## Docker

Требуется Docker Compose v2:

```bash
git clone --branch main https://github.com/unsidogandon/ratko.git
cd ratko
./docker.sh
```

Код приложения находится в неизменяемом образе в `/app`. Сессии, конфигурация и
загруженные модули хранятся в томе `worker` в `/data`.

## Обновление

Для обычной установки используйте команду `.update` в Telegram. Обновление
применяется только как Git fast-forward: локальные изменения отслеживаемых файлов
и расходящиеся коммиты не удаляются.

В Docker встроенное обновление намеренно отключено. Для обновления исходников,
пересборки образа и перезапуска контейнера выполните:

```bash
./docker.sh
```

Пользователям старой версии Ratko рекомендуется создать бэкап через `.backupall`
и один раз выполнить старую команду `.update -f` после публикации нового `main`.
При первом запуске Ratko перенесёт корневые `ratko-*.session` и
`heroku-*.session` в каталог `sessions/` и продолжит читать оба формата.

## Проверка

Безопасные команды, которые не подключаются к Telegram:

```bash
python -m compileall -q heroku
bash -n install.sh docker.sh banner.sh
git diff --check
```

## Лицензия

Ratko распространяется по лицензии [GNU AGPLv3](LICENSE) и основан на Heroku и
Hikka. Их copyright-уведомления и внутренние имена совместимости сохранены.
Спасибо Codrago, Hikari и разработчикам Telethon/herokutl.
