from datetime import datetime
import json
import os
import re
import telebot

# === 1. ОСНОВНЫЕ НАСТРОЙКИ ===
BOT_TOKEN = "8883804990:AAGgqdjsDe3IDqO37IttDFutwrl49AYv_W8"
MY_CHAT_ID = 869516218  # Укажите ваш Telegram ID из @userinfobot

# Имя файла, в котором сохраняются подписки
DB_FILE = "subscriptions.json"


# === 2. ФУНКЦИИ РАБОТЫ С БАЗОЙ ДАННЫХ (JSON) ===
def load_subscriptions():
    """Загружает базу подписок из файла.

    Если файла нет, создаёт новый.
    """
    if not os.path.exists(DB_FILE):
        default_data = {
            str(MY_CHAT_ID): "2030-12-31"  # Вы как админ по умолчанию
        }
        save_subscriptions(default_data)
        return default_data

    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Ошибка чтения файла базы: {e}")
        return {str(MY_CHAT_ID): "2030-12-31"}


def save_subscriptions(data):
    """Сохраняет словарь подписок в файл JSON."""
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"Ошибка сохранения файла базы: {e}")


# Загружаем базу при старте программы
USERS_ACCESS = load_subscriptions()

bot = telebot.TeleBot(BOT_TOKEN)


# === 3. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===
def dms_to_dd(degrees, minutes, seconds, direction):
    dd = float(degrees) + (float(minutes) / 60.0) + (float(seconds) / 3600.0)
    if str(direction).upper() in ["S", "W"]:
        dd *= -1
    return dd


def check_user_access(user_id):
    user_str = str(user_id)
    if user_str not in USERS_ACCESS:
        return False, "⛔ У вас нет доступа к боту."

    expire_str = USERS_ACCESS[user_str]
    expire_date = datetime.strptime(expire_str, "%Y-%m-%d").date()
    today = datetime.now().date()

    if today > expire_date:
        return (
            False,
            f"⌛ Срок подписки истек ({expire_str}). Пожалуйста, продлите доступ.",
        )

    return True, "OK"


# === 4. АДМИН-КОМАНДА: ПРОДЛЕНИЕ ПОДПИСКИ (/setsub ID YYYY-MM-DD) ===
@bot.message_handler(commands=["setsub"])
def set_subscription(message):
    if message.from_user.id != MY_CHAT_ID:
        return

    try:
        _, user_id_str, new_date = message.text.split()
        user_id = int(user_id_str)
        datetime.strptime(new_date, "%Y-%m-%d")

        # Сохраняем в память и сразу на диск
        USERS_ACCESS[str(user_id)] = new_date
        save_subscriptions(USERS_ACCESS)

        bot.reply_to(
            message,
            f"✅ Подписка для `{user_id}` продлена до *{new_date}* и сохранена!",
            parse_mode="Markdown",
        )

        # Пытаемся уведомить пользователя
        try:
            bot.send_message(
                user_id,
                f"🎉 Ваша подписка продлена до *{new_date}*!",
                parse_mode="Markdown",
            )
        except Exception:
            pass

    except Exception:
        bot.reply_to(
            message,
            "❌ **Ошибка!** Формат: `/setsub ID_ПОЛЬЗОВАТЕЛЯ ГГГГ-ММ-ДД`\nПример: `/setsub 987654321 2026-12-31`",
            parse_mode="Markdown",
        )


@bot.message_handler(commands=["start", "help"])
def send_welcome(message):
    user_id = message.from_user.id
    is_allowed, msg = check_user_access(user_id)

    if not is_allowed:
        bot.reply_to(message, msg)
        return

    expire_date = USERS_ACCESS[str(user_id)]
    welcome_text = (
        f"👋 Привет, {message.from_user.first_name}!\n"
        f"✅ Подписка активна до: *{expire_date}*\n\n"
        f"Отправьте координаты в формате:\n"
        f"`38 22 45.0343 N 66 17 37.1195 E`"
    )
    bot.reply_to(message, welcome_text, parse_mode="Markdown")


# === 5. ОБРАБОТКА КООРДИНАТ ===
@bot.message_handler(func=lambda message: True)
def handle_coordinates(message):
    user_id = message.from_user.id

    # Проверка доступа
    is_allowed, msg = check_user_access(user_id)
    if not is_allowed:
        bot.reply_to(message, msg)
        return

    text = message.text.strip()
    pattern = r"(\d+)\s+(\d+)\s+([\d.]+)\s*([NSEWnsew])"
    matches = re.findall(pattern, text)

    if len(matches) != 2:
        bot.reply_to(
            message,
            "❌ **Неверный формат!**\nПример:\n`38 22 45.0343 N 66 17 37.1195 E`",
            parse_mode="Markdown",
        )
        return

    lat_val, lon_val = None, None

    for deg, mn, sec, direction in matches:
        dd = dms_to_dd(deg, mn, sec, direction)
        direction = str(direction).upper()

        if direction in ["N", "S"]:
            lat_val = dd
        elif direction in ["E", "W"]:
            lon_val = dd

    if lat_val is None or lon_val is None:
        bot.reply_to(message, "❌ Проверьте направления (N/S и E/W).")
        return

    # Отправка карты пользователю
    bot.send_location(message.chat.id, latitude=lat_val, longitude=lon_val)
    bot.reply_to(
        message,
        f"📍 **Ваша локация:** `{lat_val:.6f}, {lon_val:.6f}`",
        parse_mode="Markdown",
    )

    # Дублирование карты вам
    if user_id != MY_CHAT_ID:
        sender_name = message.from_user.first_name or "Пользователь"
        username = (
            f"(@{message.from_user.username})"
            if message.from_user.username
            else ""
        )

        admin_info = (
            f"🔔 **Пользователь отправил координаты:**\n"
            f"• От: {sender_name} {username} [ID: `{user_id}`]"
        )

        bot.send_message(MY_CHAT_ID, admin_info, parse_mode="Markdown")
        bot.send_location(MY_CHAT_ID, latitude=lat_val, longitude=lon_val)


if __name__ == "__main__":
    print("Бот успешно запущен!")
    bot.infinity_polling()
