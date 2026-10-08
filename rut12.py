import os
import sqlite3
import json
import re
import shutil
import time
import threading
import schedule
import telebot
from telebot import types
from dotenv import load_dotenv

# Загружаем критические данные из скрытого файла secur.env
load_dotenv("secur.env")

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID_RAW = os.getenv("ADMIN_ID")
ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW else None
BOT_USERNAME = os.getenv("BOT_USERNAME", "turservis_bot")

# Проверка на наличие обязательных данных
if not TOKEN:
    raise ValueError("❌ Ошибка: Не найден BOT_TOKEN в файле secur.env!")

# Путь к базе данных
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'tourism.db')
BACKUP_DIR = os.path.join(BASE_DIR, 'backups')
MAX_FILE_SIZE = 500 * 1024  # 500 КБ

PHOTO_INSTRUCTION = (
    "\n\n💡 **Как сделать фото нужного размера (до 500 КБ):**\n"
    "1️⃣ **Скриншот:** Сделайте скриншот нужного фото на телефоне и отправьте его.\n"
    "2️⃣ **Сжатие Telegram:** Отправляйте как обычную картинку (НЕ как файл/документ).\n"
    "3️⃣ **Избранное:** Перешлите фото себе в «Избранное» (Saved Messages) и сохраните оттуда."
)

UI_TEXTS = {
    "found": "🎉 Найдено объектов:",
    "not_found": "🔍 По вашему запросу ничего не найдено.",
    "show_contacts": "Показать контакты 📱",
    "open_map": "🗺 Открыть на карте",
    "type_guide": "🚩 **Гид:**",
    "type_hotel": "🏨 **Отель:**",
    "type_transport": "🚗 **Транспорт:**",
    "type_restaurant": "🍲 **Ресторан:**",
    "type_masterclass": "🎨 **Мастер-класс:**",
    "lbl_lang": "🗣 **Языки:**",
    "lbl_desc": "📝 **Описание:**",
    "lbl_price": "💰 **Цена / Средний чек:**",
    "lbl_menu": "🍲 **Питание:**",
    "lbl_loc": "🗺 **Локация:**",
    "lbl_prog": "📝 **Программа:**",
    "lbl_dur": "⏱ **Длительность:**",
    "lbl_capacity": "👥 **Вместимость:**"
}

# --- СИСТЕМА БЭКАПОВ ---
def create_database_backup():
    """Создает резервную копию базы данных и отправляет её администратору."""
    if not os.path.exists(DB_PATH):
        print("⚠️ База данных не найдена для бэкапа.")
        return
    
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)
        
    timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    backup_filename = f"tourism_backup_{timestamp}.db"
    backup_path = os.path.join(BACKUP_DIR, backup_filename)
    
    try:
        shutil.copy2(DB_PATH, backup_path)
        print(f"✅ Успешный бэкап создан: {backup_filename}")
        
        if ADMIN_ID:
            with open(backup_path, 'rb') as f:
                bot.send_document(
                    ADMIN_ID, 
                    f, 
                    caption=f"🛡 **Автоматический бэкап БД**\nДата: {timestamp}", 
                    parse_mode="Markdown"
                )
    except Exception as e:
        print(f"❌ Ошибка создания бэкапа: {e}")

def schedule_backup_worker():
    """Фоновый поток для выполнения бэкапов по расписанию (каждый день в 03:00)."""
    schedule.every().day.at("03:00").do(create_database_backup)
    while True:
        schedule.run_pending()
        time.sleep(60)

def start_backup_scheduler():
    t = threading.Thread(target=schedule_backup_worker, daemon=True)
    t.start()
    print("🛡 Система автоматических бэкапов запущена.")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS objects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            type TEXT,
            city TEXT,
            data_json TEXT
        )
    ''')
    conn.commit()
    conn.close()

def save_object_to_db(user_id, obj_type, city, data):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO objects (user_id, type, city, data_json) VALUES (?, ?, ?, ?)",
        (user_id, obj_type, city, json.dumps(data, ensure_ascii=False))
    )
    conn.commit()
    conn.close()

def update_object_in_db(db_id, new_data):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE objects SET city = ?, type = ?, data_json = ? WHERE id = ?",
        (new_data.get('city'), new_data.get('type'), json.dumps(new_data, ensure_ascii=False), db_id)
    )
    conn.commit()
    conn.close()

def delete_object_from_db(db_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM objects WHERE id = ?", (db_id,))
    conn.commit()
    conn.close()

def get_all_objects_from_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, user_id, type, city, data_json FROM objects")
    rows = cursor.fetchall()
    conn.close()
    
    objects_list = []
    for row in rows:
        obj_data = json.loads(row[4])
        obj_data['db_id'] = row[0]
        obj_data['user_id'] = row[1]
        obj_data['type'] = row[2]
        obj_data['city'] = row[3]
        objects_list.append(obj_data)
    return objects_list

init_db()
bot = telebot.TeleBot(TOKEN)
user_temp = {}

# --- КЛАВИАТУРЫ ---
def get_role_keyboard(user_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    markup.row("🧳 Я турист / I am a tourist")
    markup.row("💼 Предоставляю услуги / I provide services")
    markup.row("🗂 База поставщиков", "🔍 Поиск по ключевым словам")
    if user_id == ADMIN_ID:
        markup.add(types.KeyboardButton("👑 Панель Администратора"))
    return markup

def get_tourist_category_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("🚩 Гиды / Guides", "🏨 Отели / Hotels")
    markup.row("🚗 Транспорт / Transport", "🍲 Рестораны / Restaurants")
    markup.row("🎨 Мастер-классы / Masterclasses")
    markup.row("🔍 Поиск по ключевым словам", "🏙 Выбрать другой город / Change city")
    markup.row("🏠 В главное меню / Main Menu")
    return markup

def get_price_filter_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.row("20 - 50 $", "50 - 75 $")
    markup.row("75 - 100 $", "+ 100 $")
    markup.row("🌐 Все цены / All prices")
    markup.row("🏠 В главное меню / Main Menu")
    return markup

def get_provider_keyboard(user_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("🚩 Зарегистрироваться как Гид\nRegister as Guide")
    markup.row("🏨 Зарегистрировать Гостиницу\nRegister Hotel")
    markup.row("🚗 Зарегистрировать Транспорт\nRegister Transport")
    markup.row("🍲 Зарегистрировать Ресторан\nRegister Restaurant")
    markup.row("🎨 Зарегистрировать Мастер-класс\nRegister Masterclass")
    markup.row("🏠 В главное меню / Main Menu")
    return markup

def get_city_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("Ташкент / Tashkent", "Самарканд / Samarkand")
    markup.row("Бухара / Bukhara", "Хива / Khiva")
    markup.row("Андижан / Andijan", "Термез / Termez")
    markup.row("Весь Узбекистан / All Uzbekistan")
    markup.row("🏠 В главное меню / Main Menu")
    return markup

def get_languages_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("🇬🇧 English", "🇷🇺 Русский", "🇩🇪 Deutsch")
    markup.row("🇫🇷 Français", "🇪🇸 Español", "🇮🇹 Italiano")
    markup.row("🇯🇵 日本語", "🇰🇷 한국어", "🇨🇳 中文")
    markup.row("🇸🇦 العربية", "🇹🇷 Türkçe")
    markup.row("🌐 Все языки / All languages")
    markup.row("🏠 В главное меню / Main Menu")
    return markup

def get_cancel_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("🏠 В главное меню / Main Menu")
    return markup

# --- ГЛОБАЛЬНЫЙ ПЕРЕХВАТЧИК КНОПКИ ВОЗВРАТА ---
@bot.message_handler(func=lambda message: message.text in ["🏠 В главное меню", "🏠 В главное меню / Main Menu"])
def back_to_main_global(message):
    user_id = message.from_user.id
    if user_id in user_temp:
        del user_temp[user_id]
    bot.send_message(
        message.chat.id,
        "Вы вернулись в главное меню / You returned to the main menu:",
        reply_markup=get_role_keyboard(user_id)
    )

# --- КОМАНДА /START ---
@bot.message_handler(commands=['start', 'reset', 'admin'])
def send_welcome(message):
    user_id = message.from_user.id
    if user_id in user_temp:
        del user_temp[user_id]
    
    if message.text == '/admin' and user_id == ADMIN_ID:
        admin_panel_main(message)
        return

    welcome_text = "👋 **Добро пожаловать в Tourism Bot!**\n\nВыберите нужный раздел:"
    bot.send_message(message.chat.id, welcome_text, reply_markup=get_role_keyboard(user_id), parse_mode="Markdown")

# ==========================================
# ПОИСК ПО КЛЮЧЕВЫМ СЛОВАМ
# ==========================================
@bot.message_handler(func=lambda message: message.text == "🔍 Поиск по ключевым словам")
def keyword_search_start(message):
    user_id = message.from_user.id
    user_temp[user_id] = {'role': 'search'}
    msg = bot.send_message(
        message.chat.id,
        "🔍 **Поиск по ключевым словам**\n\nВведите ключевое слово для поиска (например: *название отеля, блюдо, имя гида или услугу*):",
        reply_markup=get_cancel_keyboard(),
        parse_mode="Markdown"
    )
    bot.register_next_step_handler(msg, process_keyword_search)

def process_keyword_search(message):
    if message.text and "В главное меню" in message.text:
        return
    query = message.text.strip().lower()
    user_id = message.from_user.id
    
    all_objects = get_all_objects_from_db()
    filtered = []
    
    for obj in all_objects:
        searchable_text = " ".join([
            str(obj.get('name', '')),
            str(obj.get('title', '')),
            str(obj.get('model', '')),
            str(obj.get('description', '')),
            str(obj.get('cuisine', '')),
            str(obj.get('menu', '')),
            str(obj.get('city', '')),
            str(obj.get('location', '')),
            str(obj.get('language', ''))
        ]).lower()
        
        if query in searchable_text:
            filtered.append(obj)
            
    if not filtered:
        bot.send_message(
            message.chat.id,
            f"🔍 По запросу **«{message.text}»** ничего не найдено.",
            parse_mode="Markdown",
            reply_markup=get_role_keyboard(user_id)
        )
        return
        
    bot.send_message(message.chat.id, f"🎉 Найдено объектов по запросу «{message.text}»: **{len(filtered)}**", parse_mode="Markdown")
    
    is_admin = (user_id == ADMIN_ID)
    for item in filtered:
        if is_admin:
            send_admin_item_card(message.chat.id, item)
        else:
            send_item_card_to_user(message.chat.id, item)
            
    bot.send_message(
        message.chat.id,
        "Вы можете выполнить новый поиск или вернуться в главное меню:",
        reply_markup=get_role_keyboard(user_id)
    )

def send_admin_item_card(chat_id, item):
    db_id = item.get('db_id')
    otype = item.get('type')
    item_city = item.get('city')
    title = item.get('name') or item.get('title') or item.get('model') or f"Объект #{db_id}"
    price = item.get('price') or item.get('avg_check') or '-'
    desc = item.get('description') or item.get('cuisine') or item.get('menu') or '-'
    phone = item.get('phone') or '-'
    loc = item.get('location') or '-'
    
    card_text = (
        f"🆔 **ID:** `{db_id}` | 🏷 **Тип:** {otype.capitalize()} | 🏙 **Город:** {item_city}\n"
        f"👤 **Название/Модель:** {title}\n"
        f"💰 **Цена:** {price}\n"
        f"📞 **Телефон:** `{phone}`\n"
        f"🗺 **Адрес:** {loc}\n"
        f"📝 **Описание:** {desc}"
    )
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🗑 Удалить", callback_data=f"adm_del_{db_id}"),
        types.InlineKeyboardButton("✏️ Редактировать карточку", callback_data=f"adm_edit_{db_id}")
    )
    bot.send_message(chat_id, card_text, parse_mode="Markdown", reply_markup=markup)

# ==========================================
# ПАНЕЛЬ АДМИНИСТРАТОРА
# ==========================================
@bot.message_handler(func=lambda message: message.text == "👑 Панель Администратора" and message.from_user.id == ADMIN_ID)
def admin_panel_main(message):
    user_id = message.from_user.id
    user_temp[user_id] = {'role': 'admin'}
    bot.send_message(
        message.chat.id,
        "👑 **Панель Администратора**\n\n📍 Выберите город для управления объектами:",
        reply_markup=get_city_keyboard(),
        parse_mode="Markdown"
    )

@bot.message_handler(func=lambda message: user_temp.get(message.from_user.id, {}).get('role') == 'admin' and message.text in [
    "Ташкент / Tashkent", "Самарканд / Samarkand", "Бухара / Bukhara", 
    "Хива / Khiva", "Андижан / Andijan", "Термез / Termez", "Весь Узбекистан / All Uzbekistan"
])
def admin_city_selected(message):
    user_id = message.from_user.id
    city_text = message.text
    city = city_text.split(" / ")[0] if " / " in city_text else city_text
    user_temp[user_id]['admin_city'] = city
    
    bot.send_message(
        message.chat.id,
        f"🏙 Выбран город: **{city}**\n\nВыберите категорию объектов для управления:",
        reply_markup=get_tourist_category_keyboard(),
        parse_mode="Markdown"
    )

@bot.message_handler(func=lambda message: user_temp.get(message.from_user.id, {}).get('role') == 'admin' and message.text in [
    "🚩 Гиды / Guides", "🏨 Отели / Hotels", "🚗 Транспорт / Transport", "🍲 Рестораны / Restaurants", "🎨 Мастер-классы / Masterclasses"
])
def admin_category_selected(message):
    user_id = message.from_user.id
    session = user_temp.get(user_id, {})
    city = session.get('admin_city', 'Весь Узбекистан')

    cat_type_map = {
        "🚩 Гиды / Guides": "guide",
        "🏨 Отели / Hotels": "hotel",
        "🚗 Транспорт / Transport": "transport",
        "🍲 Рестораны / Restaurants": "restaurant",
        "🎨 Мастер-классы / Masterclasses": "masterclass"
    }
    target_type = cat_type_map.get(message.text)

    all_objects = get_all_objects_from_db()
    filtered = []
    for obj in all_objects:
        if obj.get('type') != target_type:
            continue
        obj_city = obj.get('city', '')
        if "Весь Узбекистан" not in city and obj_city != city:
            continue
        filtered.append(obj)

    if not filtered:
        bot.send_message(
            message.chat.id,
            f"📭 В городе **{city}** по данной категории объектов нет.",
            parse_mode="Markdown",
            reply_markup=get_tourist_category_keyboard()
        )
        return

    bot.send_message(message.chat.id, f"📋 Найдено объектов ({city}): **{len(filtered)}**", parse_mode="Markdown")
    for item in filtered:
        send_admin_item_card(message.chat.id, item)

    bot.send_message(message.chat.id, "Выберите другую категорию или измените город:", reply_markup=get_tourist_category_keyboard())

@bot.callback_query_handler(func=lambda call: call.data.startswith("adm_del_") and call.from_user.id == ADMIN_ID)
def admin_delete_object(call):
    db_id = call.data.replace("adm_del_", "")
    delete_object_from_db(db_id)
    bot.answer_callback_query(call.id, "✅ Объект успешно удален!")
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"❌ *Объект (ID: {db_id}) удален администратором.*",
        parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("adm_edit_") and call.from_user.id == ADMIN_ID)
def admin_edit_object_menu(call):
    db_id = call.data.replace("adm_edit_", "")
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📝 Название/Модель", callback_data=f"editfield_{db_id}_name"),
        types.InlineKeyboardButton("💰 Цена", callback_data=f"editfield_{db_id}_price"),
        types.InlineKeyboardButton("📞 Телефон", callback_data=f"editfield_{db_id}_phone"),
        types.InlineKeyboardButton("🗺 Адрес / Локация", callback_data=f"editfield_{db_id}_location"),
        types.InlineKeyboardButton("📝 Описание / Программа", callback_data=f"editfield_{db_id}_desc"),
        types.InlineKeyboardButton("🔗 Ссылка на карту", callback_data=f"editfield_{db_id}_map")
    )
    bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
    bot.answer_callback_query(call.id, "Выберите поле для изменения:")

@bot.callback_query_handler(func=lambda call: call.data.startswith("editfield_") and call.from_user.id == ADMIN_ID)
def admin_edit_specific_field(call):
    _, db_id, field_key = call.data.split("_")
    user_id = call.from_user.id
    user_temp[user_id] = {'admin_editing_id': db_id, 'admin_editing_field': field_key}
    
    field_names = {
        "name": "название / модель",
        "price": "цену / стоимость",
        "phone": "номер телефона",
        "location": "текстовый адрес",
        "desc": "описание / программу",
        "map": "ссылку на карту"
    }
    
    msg = bot.send_message(
        call.message.chat.id,
        f"✏️ Введите **новое значение ({field_names.get(field_key, field_key)})** для объекта (ID: {db_id}):",
        reply_markup=get_cancel_keyboard(),
        parse_mode="Markdown"
    )
    bot.register_next_step_handler(msg, admin_save_edited_field)
    bot.answer_callback_query(call.id)

def admin_save_edited_field(message):
    if message.text and "В главное меню" in message.text:
        return
    user_id = message.from_user.id
    if user_id not in user_temp or 'admin_editing_id' not in user_temp[user_id]:
        return
    
    db_id = user_temp[user_id]['admin_editing_id']
    field_key = user_temp[user_id]['admin_editing_field']
    new_value = message.text

    all_objs = get_all_objects_from_db()
    target = next((o for o in all_objs if str(o.get('db_id')) == str(db_id)), None)
    
    if target:
        if field_key == "name":
            if 'name' in target: target['name'] = new_value
            elif 'title' in target: target['title'] = new_value
            elif 'model' in target: target['model'] = new_value
        elif field_key == "price":
            target['price'] = new_value
            target['avg_check'] = new_value
        elif field_key == "phone":
            target['phone'] = new_value
        elif field_key == "location":
            target['location'] = new_value
        elif field_key == "desc":
            if 'description' in target: target['description'] = new_value
            elif 'cuisine' in target: target['cuisine'] = new_value
            elif 'menu' in target: target['menu'] = new_value
        elif field_key == "map":
            target['map_link'] = new_value

        update_object_in_db(db_id, target)
        bot.send_message(message.chat.id, f"✅ Данные объекта #{db_id} успешно обновлены!", parse_mode="Markdown", reply_markup=get_tourist_category_keyboard())
    else:
        bot.send_message(message.chat.id, "❌ Ошибка: объект не найден в базе.", reply_markup=get_tourist_category_keyboard())
    
    if user_id in user_temp:
        del user_temp[user_id]

# ==========================================
# ПАНЕЛЬ ТУРИСТА
# ==========================================
@bot.message_handler(func=lambda message: "Я турист" in message.text or message.text == "🏙 Выбрать другой город / Change city")
def tourist_main_menu(message):
    user_id = message.from_user.id
    user_temp[user_id] = {'role': 'tourist'}
    bot.send_message(
        message.chat.id,
        "🧳 **Раздел для туристов / For tourists**\n\n📍 Выберите город / Select city:",
        reply_markup=get_city_keyboard(),
        parse_mode="Markdown"
    )

@bot.message_handler(func=lambda message: user_temp.get(message.from_user.id, {}).get('role') == 'tourist' and message.text in [
    "Ташкент / Tashkent", "Самарканд / Samarkand", "Бухара / Bukhara", 
    "Хива / Khiva", "Андижан / Andijan", "Термез / Termez", "Весь Узбекистан / All Uzbekistan"
])
def tourist_city_selected(message):
    user_id = message.from_user.id
    city_text = message.text
    city = city_text.split(" / ")[0] if " / " in city_text else city_text
    user_temp[user_id]['tourist_city'] = city
    
    bot.send_message(
        message.chat.id,
        f"🏙 Выбран город: **{city}**\n\nВыберите категорию услуг:",
        reply_markup=get_tourist_category_keyboard(),
        parse_mode="Markdown"
    )

@bot.message_handler(func=lambda message: user_temp.get(message.from_user.id, {}).get('role') == 'tourist' and message.text in [
    "🚩 Гиды / Guides", "🏨 Отели / Hotels", "🚗 Транспорт / Transport", "🍲 Рестораны / Restaurants", "🎨 Мастер-классы / Masterclasses"
])
def tourist_category_selected(message):
    user_id = message.from_user.id
    session = user_temp.get(user_id, {})
    
    cat_type_map = {
        "🚩 Гиды / Guides": "guide",
        "🏨 Отели / Hotels": "hotel",
        "🚗 Транспорт / Transport": "transport",
        "🍲 Рестораны / Restaurants": "restaurant",
        "🎨 Мастер-классы / Masterclasses": "masterclass"
    }
    target_type = cat_type_map.get(message.text)
    session['tourist_cat_type'] = target_type

    if target_type == "guide":
        bot.send_message(
            message.chat.id,
            "🗣 Выберите язык проведения экскурсий / Choose guide language:",
            reply_markup=get_languages_keyboard(),
            parse_mode="Markdown"
        )
    elif target_type in ["hotel", "transport"]:
        bot.send_message(
            message.chat.id,
            "💰 Выберите ценовой диапазон / Select price range:",
            reply_markup=get_price_filter_keyboard(),
            parse_mode="Markdown"
        )
    else:
        execute_tourist_search(message.chat.id, user_id)

@bot.message_handler(func=lambda message: user_temp.get(message.from_user.id, {}).get('role') == 'tourist' and user_temp.get(message.from_user.id, {}).get('tourist_cat_type') == 'guide' and message.text in [
    "🇬🇧 English", "🇷🇺 Русский", "🇩🇪 Deutsch", "🇫🇷 Français", "🇪🇸 Español", "🇮🇹 Italiano",
    "🇯🇵 日本語", "🇰🇷 한국어", "🇨🇳 中文", "🇸🇦 العربية", "🇹🇷 Türkçe", "🌐 Все языки / All languages"
])
def tourist_language_selected(message):
    user_id = message.from_user.id
    user_temp[user_id]['tourist_language'] = message.text
    execute_tourist_search(message.chat.id, user_id)

@bot.message_handler(func=lambda message: user_temp.get(message.from_user.id, {}).get('role') == 'tourist' and user_temp.get(message.from_user.id, {}).get('tourist_cat_type') in ['hotel', 'transport'] and message.text in [
    "20 - 50 $", "50 - 75 $", "75 - 100 $", "+ 100 $", "🌐 Все цены / All prices"
])
def tourist_price_selected(message):
    user_id = message.from_user.id
    user_temp[user_id]['tourist_price'] = message.text
    execute_tourist_search(message.chat.id, user_id)

def match_price_range(price_str, target_range):
    if "Все цены" in target_range:
        return True
    numbers = [int(n) for n in re.findall(r'\d+', price_str)]
    if not numbers:
        return True
    avg_price = sum(numbers) / len(numbers)

    if target_range == "20 - 50 $":
        return 20 <= avg_price <= 50
    elif target_range == "50 - 75 $":
        return 50 < avg_price <= 75
    elif target_range == "75 - 100 $":
        return 75 < avg_price <= 100
    elif target_range == "+ 100 $":
        return avg_price > 100
    return True

def execute_tourist_search(chat_id, user_id):
    session = user_temp.get(user_id, {})
    city = session.get('tourist_city', 'Весь Узбекистан')
    target_type = session.get('tourist_cat_type')
    target_lang = session.get('tourist_language', '🌐 Все языки / All languages')
    target_price = session.get('tourist_price', '🌐 Все цены / All prices')

    all_objects = get_all_objects_from_db()
    filtered = []
    for obj in all_objects:
        if obj.get('type') != target_type:
            continue
        obj_city = obj.get('city', '')
        if "Весь Узбекистан" not in city and obj_city != city:
            continue
        if target_type == "guide" and "Все языки" not in target_lang:
            obj_lang = obj.get('card_language') or obj.get('language') or ''
            if target_lang not in obj_lang:
                continue
        if target_type in ["hotel", "transport"]:
            obj_price_text = str(obj.get('price') or obj.get('avg_check') or '')
            if not match_price_range(obj_price_text, target_price):
                continue
        filtered.append(obj)

    if not filtered:
        bot.send_message(
            chat_id,
            f"🔍 В городе **{city}** по вашему запросу ничего не найдено.",
            parse_mode="Markdown",
            reply_markup=get_tourist_category_keyboard()
        )
        return

    bot.send_message(chat_id, f"🎉 Найдено объектов: {len(filtered)}")
    for item in filtered:
        send_item_card_to_user(chat_id, item)

    bot.send_message(chat_id, "Выберите другую категорию или измените город:", reply_markup=get_tourist_category_keyboard())

def send_item_card_to_user(chat_id, item):
    desc = item.get('description', '-')
    languages = item.get('language', '-')
    price = item.get('price') or item.get('avg_check') or '-'
    card_lang = item.get('card_language', 'RU')
    map_link = item.get('map_link', '')
    location = item.get('location', '-')
    
    text = ""
    if item.get('type') == 'guide':
        text = f"{UI_TEXTS['type_guide']} [{card_lang}] ({item.get('city')})\n{UI_TEXTS['lbl_lang']} {languages}\n{UI_TEXTS['lbl_loc']} {location}\n{UI_TEXTS['lbl_desc']} {desc}\n{UI_TEXTS['lbl_price']} {price}"
    elif item.get('type') == 'hotel':
        text = f"{UI_TEXTS['type_hotel']} {item.get('name')} [{card_lang}] ({item.get('city')})\n{UI_TEXTS['lbl_menu']} {item.get('menu', '-')}\n{UI_TEXTS['lbl_loc']} {location}\n{UI_TEXTS['lbl_price']} {price}"
    elif item.get('type') == 'transport':
        text = f"{UI_TEXTS['type_transport']} {item.get('model')} [{card_lang}] ({UI_TEXTS['lbl_capacity']} {item.get('capacity', '-')})\n{UI_TEXTS['lbl_loc']} {location}\n{UI_TEXTS['lbl_desc']} {desc}\n{UI_TEXTS['lbl_price']} {price}"
    elif item.get('type') == 'restaurant':
        text = f"{UI_TEXTS['type_restaurant']} {item.get('name')} [{card_lang}] ({item.get('city')})\n{UI_TEXTS['lbl_desc']} {item.get('cuisine', '-')}\n{UI_TEXTS['lbl_loc']} {location}\n{UI_TEXTS['lbl_price']} {price}"
    elif item.get('type') == 'masterclass':
        text = f"{UI_TEXTS['type_masterclass']} {item.get('title', '')} [{card_lang}] ({item.get('city')})\n{UI_TEXTS['lbl_prog']} {desc}\n{UI_TEXTS['lbl_loc']} {location}\n{UI_TEXTS['lbl_dur']} {item.get('duration', '-')}\n{UI_TEXTS['lbl_price']} {price}"
        
    markup = types.InlineKeyboardMarkup(row_width=1)
    if map_link and map_link.startswith("http"):
        markup.add(types.InlineKeyboardButton(UI_TEXTS['open_map'], url=map_link))
    markup.add(types.InlineKeyboardButton(UI_TEXTS['show_contacts'], callback_data=f"show_contact_{item.get('db_id')}"))
    
    # 🛡 Защита от копирования и пересылки включена (protect_content=True)
    bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=markup, disable_web_page_preview=True, protect_content=True)
    
    for p_key in ['cert_photo', 'facade_photo', 'room_photo', 'transport_photo', 'restaurant_photo', 'mc_photo']:
        if item.get(p_key):
            try:
                # 🛡 Защита фото от скачивания/сохранения
                bot.send_photo(chat_id, item[p_key], protect_content=True)
            except Exception:
                pass

@bot.callback_query_handler(func=lambda call: call.data.startswith("show_contact_"))
def handle_contact_request(call):
    db_id = call.data.split("_")[-1]
    all_objects = get_all_objects_from_db()
    target_obj = next((o for o in all_objects if str(o.get('db_id')) == db_id), None)
    
    if not target_obj:
        bot.answer_callback_query(call.id, "Объект не найден.")
        return
        
    contacts_text = "📞 **Контакты / Contacts:**\n"
    if target_obj.get('phone'):
        contacts_text += f"Телефон / Phone: `{target_obj['phone']}`\n"
    if target_obj.get('tg_contact'):
        contacts_text += f"Telegram: {target_obj['tg_contact']}\n"
        
    # Защита контактов тоже под защитой контента
    bot.send_message(call.message.chat.id, contacts_text, parse_mode="Markdown", protect_content=True)
    bot.answer_callback_query(call.id, "Контакты отправлены / Contacts sent!")

# ==========================================
# БАЗА ПОСТАВЩИКОВ (КАТАЛОГ)
# ==========================================
@bot.message_handler(func=lambda message: message.text == "🗂 База поставщиков")
def suppliers_menu(message):
    markup = types.InlineKeyboardMarkup(row_width=1)
    categories = {
        "Гиды": "guide",
        "Транспорт": "transport",
        "Отели": "hotel",
        "Рестораны": "restaurant",
        "Мастер-классы": "masterclass"
    }
    for cat_name, cat_key in categories.items():
        markup.add(types.InlineKeyboardButton(f"📂 {cat_name}", callback_data=f"supcat_{cat_key}"))
    bot.send_message(
        message.chat.id,
        "🗂 **Каталог поставщиков**\n\nВыберите нужную категорию:",
        reply_markup=markup,
        parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("supcat_"))
def show_suppliers_by_category(call):
    try:
        cat_key = call.data.replace("supcat_", "", 1)
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("SELECT id, city, data_json FROM objects WHERE type = ? LIMIT 5", (cat_key,))
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            bot.answer_callback_query(call.id, text="В этой категории пока пусто.", show_alert=True)
            return

        bot.answer_callback_query(call.id)
        response = f"📂 **Категория: {cat_key.capitalize()}** (Последние карточки):\n\n"
        for r in rows:
            db_id, city, data_json = r[0], r[1], r[2]
            obj = json.loads(data_json)
            title = obj.get('name') or obj.get('title') or obj.get('model') or f"Объект #{db_id}"
            desc = obj.get('description') or obj.get('cuisine') or obj.get('menu') or '-'
            price = obj.get('price') or obj.get('avg_check') or '-'
            phone = obj.get('phone') or '-'
            tg = obj.get('tg_contact') or '-'
            loc = obj.get('location') or '-'
            map_link = obj.get('map_link') or ''
            
            response += f"👤 **{title}** ({city})\n"
            response += f"💰 Цена / Средний чек: {price}\n"
            response += f"📞 Контакт: `{phone}` | 💬 {tg}\n"
            response += f"🗺 Локация: {loc}"
            if map_link:
                response += f" ([ссылка на карту]({map_link}))"
            response += f"\n📝 {desc}\n" + "—"*25 + "\n\n"

        bot.send_message(
            call.message.chat.id,
            response,
            parse_mode="Markdown",
            disable_web_page_preview=True,
            protect_content=True
        )
    except Exception as e:
        print(f"Ошибка вывода поставщиков: {e}")

@bot.message_handler(func=lambda message: message.text in ["💼 Предоставляю услуги", "💼 Предоставляю услуги / I provide services"])
def provider_menu(message):
    bot.send_message(
        message.chat.id,
        "📋 Раздел для поставщиков услуг.\nВыберите категорию для регистрации вашего объекта:",
        reply_markup=get_provider_keyboard(message.from_user.id)
    )

# ==========================================
# РЕГИСТРАЦИЯ ПОСТАВЩИКОВ И ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================================
def finish_provider_registration(message):
    if message.text and "В главное меню" in message.text:
        return
    user_id = message.from_user.id
    tg_contact = f"@{message.from_user.username}" if message.from_user.username else "-"
    phone = message.contact.phone_number if message.contact else message.text
    
    if user_id in user_temp:
        user_temp[user_id]['phone'] = phone
        user_temp[user_id]['tg_contact'] = tg_contact
        info = user_temp[user_id]
        save_object_to_db(user_id, info['type'], info['city'], info)
        del user_temp[user_id]
        
    ref_link = f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("🌐 Создать карточку на другом языке / Create card in another language")
    markup.row("🏠 В главное меню / Main Menu")
    
    summary_text = (
        "🎉 **Регистрация успешно завершена!**\n"
        "Ваш объект внесен в базу данных и получил статус **`🛡 Verified Partner`**.\n\n"
        f"📱 **Ваша персональная цифровая визитка:**\n`{ref_link}`\n\n"
        "💡 Хотите добавить эту же услугу на другом языке, чтобы охватить больше туристов?"
    )
    bot.send_message(message.chat.id, summary_text, parse_mode="Markdown", reply_markup=markup)

@bot.message_handler(func=lambda message: "Создать карточку на другом языке" in message.text)
def create_another_language_card(message):
    msg = bot.send_message(
        message.chat.id,
        "Отлично! Давайте создадим дополнительную карточку. Выберите язык новой карточки:",
        reply_markup=get_languages_keyboard()
    )
    bot.register_next_step_handler(msg, provider_category_after_lang)

def provider_category_after_lang(message):
    if message.text and "В главное меню" in message.text:
        return
    user_temp[message.from_user.id] = {'card_language': message.text, 'user_id': message.from_user.id}
    bot.send_message(
        message.chat.id,
        "Выберите категорию услуги для нового языка:",
        reply_markup=get_provider_keyboard(message.from_user.id)
    )

def handle_photo_input(message, field_name, next_step_func, prompt_text):
    if message.text and "В главное меню" in message.text:
        return
    user_id = message.from_user.id
    if not message.photo:
        msg = bot.send_message(
            message.chat.id,
            "⚠️ Пожалуйста, отправьте именно **фотографию** (изображение):" + PHOTO_INSTRUCTION,
            parse_mode="Markdown",
            reply_markup=get_cancel_keyboard()
        )
        bot.register_next_step_handler(msg, lambda m: handle_photo_input(m, field_name, next_step_func, prompt_text))
        return
        
    photo = message.photo[-1]
    file_info = bot.get_file(photo.file_id)
    if file_info.file_size > MAX_FILE_SIZE:
        size_kb = round(file_info.file_size / 1024)
        msg = bot.send_message(
            message.chat.id,
            f"❌ **Файл слишком большой ({size_kb} КБ)!**\nМаксимальный размер фото — **500 КБ**." + PHOTO_INSTRUCTION,
            parse_mode="Markdown",
            reply_markup=get_cancel_keyboard()
        )
        bot.register_next_step_handler(msg, lambda m: handle_photo_input(m, field_name, next_step_func, prompt_text))
        return
        
    user_temp[user_id][field_name] = file_info.file_id
    if prompt_text:
        msg = bot.send_message(message.chat.id, prompt_text, parse_mode="Markdown", reply_markup=get_cancel_keyboard())
        bot.register_next_step_handler(msg, next_step_func)
    else:
        next_step_func(message)

def provider_phone_step(message):
    if message.text and "В главное меню" in message.text:
        return
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add(types.KeyboardButton("📱 Отправить номер телефона / Send phone number", request_contact=True))
    markup.add(types.KeyboardButton("🏠 В главное меню / Main Menu"))
    msg = bot.send_message(
        message.chat.id,
        "📞 Отправьте ваш **номер телефона** (нажмите кнопку или введите текстом) / Send your phone number:",
        reply_markup=markup
    )
    bot.register_next_step_handler(msg, finish_provider_registration)

# 1. ГИДЫ
@bot.message_handler(func=lambda message: "Зарегистрироваться как Гид" in message.text)
def start_guide_reg(message):
    user_id = message.from_user.id
    if user_id not in user_temp or 'card_language' not in user_temp[user_id]:
        user_temp[user_id] = {}
    user_temp[user_id].update({'type': 'guide', 'user_id': user_id, 'role': 'provider'})
    
    if 'card_language' not in user_temp[user_id]:
        msg = bot.send_message(message.chat.id, "🌐 На каком языке будет эта карточка / Card language?", reply_markup=get_languages_keyboard())
        bot.register_next_step_handler(msg, guide_card_lang_saved)
    else:
        msg = bot.send_message(message.chat.id, "📍 Выберите ваш **город / Select city**:", reply_markup=get_city_keyboard())
        bot.register_next_step_handler(msg, guide_city)

def guide_card_lang_saved(message):
    if message.text and "В главное меню" in message.text:
        return
    user_temp[message.from_user.id]['card_language'] = message.text
    msg = bot.send_message(message.chat.id, "📍 Выберите ваш **город / Select city**:", reply_markup=get_city_keyboard())
    bot.register_next_step_handler(msg, guide_city)

def guide_city(message):
    if message.text and "В главное меню" in message.text:
        return
    city_text = message.text
    user_temp[message.from_user.id]['city'] = city_text.split(" / ")[0] if " / " in city_text else city_text
    msg = bot.send_message(message.chat.id, "🗣 Укажите **языки** проведения экскурсий:", reply_markup=get_cancel_keyboard())
    bot.register_next_step_handler(msg, guide_lang)

def guide_lang(message):
    if message.text and "В главное меню" in message.text:
        return
    user_temp[message.from_user.id]['language'] = message.text
    msg = bot.send_message(message.chat.id, "🗺 Введите текстовый **адрес / место встречи** (например: *ул. Регистан, 15*):", reply_markup=get_cancel_keyboard())
    bot.register_next_step_handler(msg, guide_location_text)

def guide_location_text(message):
    if message.text and "В главное меню" in message.text:
        return
    user_temp[message.from_user.id]['location'] = message.text
    msg = bot.send_message(
        message.chat.id,
        "🔗 Отправьте **активную ссылку на карту**:\n*(Или отправьте символ `-`, если ссылки нет)*",
        reply_markup=get_cancel_keyboard(),
        parse_mode="Markdown"
    )
    bot.register_next_step_handler(msg, guide_map_link)

def guide_map_link(message):
    if message.text and "В главное меню" in message.text:
        return
    link = message.text.strip()
    user_temp[message.from_user.id]['map_link'] = link if link != "-" else ""
    msg = bot.send_message(message.chat.id, "📝 Напишите **описание услуг**:", reply_markup=get_cancel_keyboard())
    bot.register_next_step_handler(msg, guide_desc)

def guide_desc(message):
    if message.text and "В главное меню" in message.text:
        return
    user_temp[message.from_user.id]['description'] = message.text
    msg = bot.send_message(message.chat.id, "💰 Укажите **стоимость услуг / средний чек** (например: *50$ за экскурсию*):", reply_markup=get_cancel_keyboard())
    bot.register_next_step_handler(msg, guide_price)

def guide_price(message):
    if message.text and "В главное меню" in message.text:
        return
    user_temp[message.from_user.id]['price'] = message.text
    user_temp[message.from_user.id]['avg_check'] = message.text
    msg = bot.send_message(message.chat.id, "📜 Отправьте **фото сертификата** (до 500 КБ):" + PHOTO_INSTRUCTION, parse_mode="Markdown", reply_markup=get_cancel_keyboard())
    bot.register_next_step_handler(msg, lambda m: handle_photo_input(m, 'cert_photo', provider_phone_step, ""))

# 2. ГОСТИНИЦЫ
@bot.message_handler(func=lambda message: "Зарегистрировать Гостиницу" in message.text)
def start_hotel_reg(message):
    user_id = message.from_user.id
    if user_id not in user_temp or 'card_language' not in user_temp[user_id]:
        user_temp[user_id] = {}
    user_temp[user_id].update({'type': 'hotel', 'user_id': user_id, 'role': 'provider'})
    
    if 'card_language' not in user_temp[user_id]:
        msg = bot.send_message(message.chat.id, "🌐 На каком языке будет эта карточка / Card language?", reply_markup=get_languages_keyboard())
        bot.register_next_step_handler(msg, hotel_card_lang_saved)
    else:
        msg = bot.send_message(message.chat.id, "📍 Выберите ваш **город / Select city**:", reply_markup=get_city_keyboard())
        bot.register_next_step_handler(msg, hotel_city)

def hotel_card_lang_saved(message):
    if message.text and "В главное меню" in message.text:
        return
    user_temp[message.from_user.id]['card_language'] = message.text
    msg = bot.send_message(message.chat.id, "📍 Выберите ваш **город / Select city**:", reply_markup=get_city_keyboard())
    bot.register_next_step_handler(msg, hotel_city)

def hotel_city(message):
    if message.text and "В главное меню" in message.text:
        return
    city_text = message.text
    user_temp[message.from_user.id]['city'] = city_text.split(" / ")[0] if " / " in city_text else city_text
    msg = bot.send_message(message.chat.id, "🏨 Введите **название гостиницы**:", reply_markup=get_cancel_keyboard())
    bot.register_next_step_handler(msg, hotel_name)

def hotel_name(message):
    if message.text and "В главное меню" in message.text:
        return
    user_temp[message.from_user.id]['name'] = message.text
    msg = bot.send_message(message.chat.id, "🍲 Укажите тип **питания** (например: *завтрак включен*):", reply_markup=get_cancel_keyboard())
    bot.register_next_step_handler(msg, hotel_menu)

def hotel_menu(message):
    if message.text and "В главное меню" in message.text:
        return
    user_temp[message.from_user.id]['menu'] = message.text
    msg = bot.send_message(message.chat.id, "💰 Укажите **средний чек / стоимость за сутки** (например: *от 40$ за номер*):", reply_markup=get_cancel_keyboard())
    bot.register_next_step_handler(msg, hotel_price)

def hotel_price(message):
    if message.text and "В главное меню" in message.text:
        return
    user_temp[message.from_user.id]['price'] = message.text
    user_temp[message.from_user.id]['avg_check'] = message.text
    msg = bot.send_message(message.chat.id, "🗺 Введите текстовый **адрес** отеля (например: *ул. Регистан, 15*):", reply_markup=get_cancel_keyboard())
    bot.register_next_step_handler(msg, hotel_location_text)

def hotel_location_text(message):
    if message.text and "В главное меню" in message.text:
        return
    user_temp[message.from_user.id]['location'] = message.text
    msg = bot.send_message(
        message.chat.id,
        "🔗 Отправьте **активную ссылку на карту**:\n*(Или отправьте символ `-`, если ссылки нет)*",
        reply_markup=get_cancel_keyboard(),
        parse_mode="Markdown"
    )
    bot.register_next_step_handler(msg, hotel_map_link)

def hotel_map_link(message):
    if message.text and "В главное меню" in message.text:
        return
    link = message.text.strip()
    user_temp[message.from_user.id]['map_link'] = link if link != "-" else ""
    msg = bot.send_message(message.chat.id, "📸 Отправьте **фото фасада** отеля (до 500 КБ):" + PHOTO_INSTRUCTION, parse_mode="Markdown", reply_markup=get_cancel_keyboard())
    bot.register_next_step_handler(msg, lambda m: handle_photo_input(m, 'facade_photo', ask_hotel_room_photo, ""))

def ask_hotel_room_photo(message):
    if message.text and "В главное меню" in message.text:
        return
    msg = bot.send_message(
        message.chat.id,
        "📸 Отправьте **фото номера** (до 500 КБ):" + PHOTO_INSTRUCTION,
        parse_mode="Markdown",
        reply_markup=get_cancel_keyboard()
    )
    bot.register_next_step_handler(msg, lambda m: handle_photo_input(m, 'room_photo', provider_phone_step, ""))

# 3. ТРАНСПОРТ
@bot.message_handler(func=lambda message: "Зарегистрировать Транспорт" in message.text)
def start_transport_reg(message):
    user_id = message.from_user.id
    if user_id not in user_temp or 'card_language' not in user_temp[user_id]:
        user_temp[user_id] = {}
    user_temp[user_id].update({'type': 'transport', 'user_id': user_id, 'role': 'provider'})
    
    if 'card_language' not in user_temp[user_id]:
        msg = bot.send_message(message.chat.id, "🌐 На каком языке будет эта карточка / Card language?", reply_markup=get_languages_keyboard())
        bot.register_next_step_handler(msg, transport_card_lang_saved)
    else:
        msg = bot.send_message(message.chat.id, "📍 Выберите ваш **город / Select city**:", reply_markup=get_city_keyboard())
        bot.register_next_step_handler(msg, transport_city)

def transport_card_lang_saved(message):
    if message.text and "В главное меню" in message.text:
        return
    user_temp[message.from_user.id]['card_language'] = message.text
    msg = bot.send_message(message.chat.id, "📍 Выберите ваш **город / Select city**:", reply_markup=get_city_keyboard())
    bot.register_next_step_handler(msg, transport_city)

def transport_city(message):
    if message.text and "В главное меню" in message.text:
        return
    city_text = message.text
    user_temp[message.from_user.id]['city'] = city_text.split(" / ")[0] if " / " in city_text else city_text
    msg = bot.send_message(message.chat.id, "🚗 Укажите **модель авто и вместимость**:", reply_markup=get_cancel_keyboard())
    bot.register_next_step_handler(msg, transport_model)

def transport_model(message):
    if message.text and "В главное меню" in message.text:
        return
    user_temp[message.from_user.id]['model'] = message.text
    msg = bot.send_message(message.chat.id, "📝 Опишите **условия аренды / трансфера**:", reply_markup=get_cancel_keyboard())
    bot.register_next_step_handler(msg, transport_desc)

def transport_desc(message):
    if message.text and "В главное меню" in message.text:
        return
    user_temp[message.from_user.id]['description'] = message.text
    msg = bot.send_message(message.chat.id, "💰 Укажите **средний чек / стоимость** (например: *30$ в час или по договоренности*):", reply_markup=get_cancel_keyboard())
    bot.register_next_step_handler(msg, transport_price)

def transport_price(message):
    if message.text and "В главное меню" in message.text:
        return
    user_temp[message.from_user.id]['price'] = message.text
    user_temp[message.from_user.id]['avg_check'] = message.text
    msg = bot.send_message(message.chat.id, "🗺 Введите текстовый **адрес базирования / подачи**:", reply_markup=get_cancel_keyboard())
    bot.register_next_step_handler(msg, transport_location_text)

def transport_location_text(message):
    if message.text and "В главное меню" in message.text:
        return
    user_temp[message.from_user.id]['location'] = message.text
    msg = bot.send_message(
        message.chat.id,
        "🔗 Отправьте **активную ссылку на карту**:\n*(Или отправьте символ `-`, если ссылки нет)*",
        reply_markup=get_cancel_keyboard(),
        parse_mode="Markdown"
    )
    bot.register_next_step_handler(msg, transport_map_link)

def transport_map_link(message):
    if message.text and "В главное меню" in message.text:
        return
    link = message.text.strip()
    user_temp[message.from_user.id]['map_link'] = link if link != "-" else ""
    msg = bot.send_message(message.chat.id, "📸 Отправьте **фото автомобиля** (до 500 КБ):" + PHOTO_INSTRUCTION, parse_mode="Markdown", reply_markup=get_cancel_keyboard())
    bot.register_next_step_handler(msg, lambda m: handle_photo_input(m, 'transport_photo', provider_phone_step, ""))

# 4. РЕСТОРАНЫ
@bot.message_handler(func=lambda message: "Зарегистрировать Ресторан" in message.text)
def start_restaurant_reg(message):
    user_id = message.from_user.id
    if user_id not in user_temp or 'card_language' not in user_temp[user_id]:
        user_temp[user_id] = {}
    user_temp[user_id].update({'type': 'restaurant', 'user_id': user_id, 'role': 'provider'})
    
    if 'card_language' not in user_temp[user_id]:
        msg = bot.send_message(message.chat.id, "🌐 На каком языке будет эта карточка / Card language?", reply_markup=get_languages_keyboard())
        bot.register_next_step_handler(msg, restaurant_card_lang_saved)
    else:
        msg = bot.send_message(message.chat.id, "📍 Выберите ваш **город / Select city**:", reply_markup=get_city_keyboard())
        bot.register_next_step_handler(msg, restaurant_city)

def restaurant_card_lang_saved(message):
    if message.text and "В главное меню" in message.text:
        return
    user_temp[message.from_user.id]['card_language'] = message.text
    msg = bot.send_message(message.chat.id, "📍 Выберите ваш **город / Select city**:", reply_markup=get_city_keyboard())
    bot.register_next_step_handler(msg, restaurant_city)

def restaurant_city(message):
    if message.text and "В главное меню" in message.text:
        return
    city_text = message.text
    user_temp[message.from_user.id]['city'] = city_text.split(" / ")[0] if " / " in city_text else city_text
    msg = bot.send_message(message.chat.id, "🍲 Введите **название ресторана/кафе**:", reply_markup=get_cancel_keyboard())
    bot.register_next_step_handler(msg, restaurant_name)

def restaurant_name(message):
    if message.text and "В главное меню" in message.text:
        return
    user_temp[message.from_user.id]['name'] = message.text
    msg = bot.send_message(message.chat.id, "🍳 Укажите тип **кухни**:", reply_markup=get_cancel_keyboard())
    bot.register_next_step_handler(msg, restaurant_cuisine)

def restaurant_cuisine(message):
    if message.text and "В главное меню" in message.text:
        return
    user_temp[message.from_user.id]['cuisine'] = message.text
    msg = bot.send_message(message.chat.id, "💰 Укажите **средний чек** (например: *15-20$ *):", reply_markup=get_cancel_keyboard())
    bot.register_next_step_handler(msg, restaurant_price)

def restaurant_price(message):
    if message.text and "В главное меню" in message.text:
        return
    user_temp[message.from_user.id]['price'] = message.text
    user_temp[message.from_user.id]['avg_check'] = message.text
    msg = bot.send_message(message.chat.id, "🗺 Введите текстовый **адрес** ресторана:", reply_markup=get_cancel_keyboard())
    bot.register_next_step_handler(msg, restaurant_location_text)

def restaurant_location_text(message):
    if message.text and "В главное меню" in message.text:
        return
    user_temp[message.from_user.id]['location'] = message.text
    msg = bot.send_message(
        message.chat.id,
        "🔗 Отправьте **активную ссылку на карту**:\n*(Или отправьте символ `-`, если ссылки нет)*",
        reply_markup=get_cancel_keyboard(),
        parse_mode="Markdown"
    )
    bot.register_next_step_handler(msg, restaurant_map_link)

def restaurant_map_link(message):
    if message.text and "В главное меню" in message.text:
        return
    link = message.text.strip()
    user_temp[message.from_user.id]['map_link'] = link if link != "-" else ""
    msg = bot.send_message(message.chat.id, "📸 Отправьте **фото ресторана** (до 500 КБ):" + PHOTO_INSTRUCTION, parse_mode="Markdown", reply_markup=get_cancel_keyboard())
    bot.register_next_step_handler(msg, lambda m: handle_photo_input(m, 'restaurant_photo', provider_phone_step, ""))

# 5. МАСТЕР-КЛАССЫ
@bot.message_handler(func=lambda message: "Зарегистрировать Мастер-класс" in message.text)
def start_masterclass_reg(message):
    user_id = message.from_user.id
    if user_id not in user_temp or 'card_language' not in user_temp[user_id]:
        user_temp[user_id] = {}
    user_temp[user_id].update({'type': 'masterclass', 'user_id': user_id, 'role': 'provider'})
    
    if 'card_language' not in user_temp[user_id]:
        msg = bot.send_message(message.chat.id, "🌐 На каком языке будет эта карточка / Card language?", reply_markup=get_languages_keyboard())
        bot.register_next_step_handler(msg, mc_card_lang_saved)
    else:
        msg = bot.send_message(message.chat.id, "📍 Выберите ваш **город / Select city**:", reply_markup=get_city_keyboard())
        bot.register_next_step_handler(msg, mc_city)

def mc_card_lang_saved(message):
    if message.text and "В главное меню" in message.text:
        return
    user_temp[message.from_user.id]['card_language'] = message.text
    msg = bot.send_message(message.chat.id, "📍 Выберите ваш **город / Select city**:", reply_markup=get_city_keyboard())
    bot.register_next_step_handler(msg, mc_city)

def mc_city(message):
    if message.text and "В главное меню" in message.text:
        return
    city_text = message.text
    user_temp[message.from_user.id]['city'] = city_text.split(" / ")[0] if " / " in city_text else city_text
    msg = bot.send_message(message.chat.id, "🎨 Укажите **название мастер-класса**:", reply_markup=get_cancel_keyboard())
    bot.register_next_step_handler(msg, mc_title)

def mc_title(message):
    if message.text and "В главное меню" in message.text:
        return
    user_temp[message.from_user.id]['title'] = message.text
    msg = bot.send_message(message.chat.id, "📝 Опишите **программу**:", reply_markup=get_cancel_keyboard())
    bot.register_next_step_handler(msg, mc_desc)

def mc_desc(message):
    if message.text and "В главное меню" in message.text:
        return
    user_temp[message.from_user.id]['description'] = message.text
    msg = bot.send_message(message.chat.id, "⏱ Укажите **длительность**:", reply_markup=get_cancel_keyboard())
    bot.register_next_step_handler(msg, mc_duration)

def mc_duration(message):
    if message.text and "В главное меню" in message.text:
        return
    user_temp[message.from_user.id]['duration'] = message.text
    msg = bot.send_message(message.chat.id, "💰 Укажите **средний чек / стоимость** (например: *20$ *):", reply_markup=get_cancel_keyboard())
    bot.register_next_step_handler(msg, mc_price)

def mc_price(message):
    if message.text and "В главное меню" in message.text:
        return
    user_temp[message.from_user.id]['price'] = message.text
    user_temp[message.from_user.id]['avg_check'] = message.text
    msg = bot.send_message(message.chat.id, "🗺 Введите текстовый **адрес проведения** мастер-класса:", reply_markup=get_cancel_keyboard())
    bot.register_next_step_handler(msg, mc_location_text)

def mc_location_text(message):
    if message.text and "В главное меню" in message.text:
        return
    user_temp[message.from_user.id]['location'] = message.text
    msg = bot.send_message(
        message.chat.id,
        "🔗 Отправьте **активную ссылку на карту** (место проведения):\n*(Или отправьте символ `-`, если ссылки нет)*",
        reply_markup=get_cancel_keyboard(),
        parse_mode="Markdown"
    )
    bot.register_next_step_handler(msg, mc_map_link)

def mc_map_link(message):
    if message.text and "В главное меню" in message.text:
        return
    link = message.text.strip()
    user_temp[message.from_user.id]['map_link'] = link if link != "-" else ""
    msg = bot.send_message(message.chat.id, "📸 Отправьте **фото с мастер-класса** (до 500 КБ):" + PHOTO_INSTRUCTION, parse_mode="Markdown", reply_markup=get_cancel_keyboard())
    bot.register_next_step_handler(msg, lambda m: handle_photo_input(m, 'mc_photo', provider_phone_step, ""))

if __name__ == '__main__':
    start_backup_scheduler()  # Запуск автоматических бэкапов БД в фоне
    print("Бот запущен с защитой контента и системой бэкапов!")
    from flask import Flask, request
import telebot

# (Здесь идет ваша основная логика бота, инициализация token и т.д., как было раньше)

app = Flask(__name__)

# ЗАМЕНИТЕ 'ваш_логин' НА СВОЙ РЕАЛЬНЫЙ ЛОГИН PYTHONANYWHERE
WEBHOOK_HOST = "Bunlok.pythonanywhere.com"  
WEBHOOK_PATH = f"/webhook/{TOKEN}"  # Убедитесь, что здесь используется ваша переменная с токеном бота
WEBHOOK_URL = f"https://{WEBHOOK_HOST}{WEBHOOK_PATH}"

# Удаляем старый вебхук и ставим новый
bot.remove_webhook()
bot.set_webhook(url=WEBHOOK_URL)

@app.route(WEBHOOK_PATH, methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return '', 200
    else:
        return 'Forbidden', 403

@app.route('/')
def index():
    return "Bot is running via Webhook!", 200

if __name__ == '__main__':
    # Если вы здесь запускали бэкапы или что-то еще, оставьте это
    app.run(host="0.0.0.0", port=5000)

