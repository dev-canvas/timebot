import asyncio
import time
import sqlite3
from datetime import datetime, date, timedelta, timezone
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
import os
import csv
from io import StringIO, BytesIO
from pathlib import Path

# ================== НАСТРОЙКИ БАЗЫ ДАННЫХ ==================
# Путь к папке data
DATA_DIR = Path("/data")
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Путь к базе данных
DB_PATH = DATA_DIR / "tasks.db"

# ================== НАСТРОЙКИ ==================
# АДМИН ID - ИСПРАВЛЕНО: конвертируем в int
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
API_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# ЮKassa / провайдер платежей
PROVIDER_TOKEN = os.getenv("PROVIDER_TOKEN")

# заполни в env
PREMIUM_PRICE = 99  # руб/мес
PREMIUM_TITLE = "Премиум навсегда"
PREMIUM_DESCRIPTION = "Доступ к экспорту в CSV и дополнительным функциям"

bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ================== ЧАСОВЫЕ ПОЯСА ==================
class SimpleTimezone:
    TIMEZONES = {
        "Europe/Moscow": 3,
        "Asia/Tbilisi": 4,
        "Europe/Samara": 4,
        "Asia/Yekaterinburg": 5,
        "Europe/London": 0,
        "Asia/Bangkok": 7,
    }

    def __init__(self, name: str):
        self.name = name
        self.offset_hours = self.TIMEZONES.get(name, 3)

    @staticmethod
    def is_valid(tz_name: str) -> bool:
        return tz_name in SimpleTimezone.TIMEZONES

    def get_current_time(self) -> datetime:
        utc_now = datetime.now(timezone.utc)
        local_tz = timezone(timedelta(hours=self.offset_hours))
        return utc_now.astimezone(local_tz)


MOSCOW_TZ = SimpleTimezone("Europe/Moscow")

# ================== STATE ==================
class TaskTimer(StatesGroup):
    waiting_task_number = State()
    waiting_description_choice = State()
    waiting_description_text = State()
    waiting_report_date = State()
    choosing_calendar_month = State()
    choosing_task_for_report = State()
    waiting_reports_menu = State()
    waiting_timezone_choice = State()
    waiting_custom_timezone = State()
    waiting_broadcast_message = State()
    waiting_broadcast_photo = State()
    waiting_msg_to_all_message = State()


# активные таймеры {user_id: {...}}
active_timers = {}

# ================== БАЗА ДАННЫХ ==================
def init_db():
    """Ваша функция создания базы с расширенными таблицами"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()

    # Таблица пользователей (расширенная)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            joined_date TEXT,
            is_admin INTEGER DEFAULT 0,
            is_premium INTEGER DEFAULT 0
        )
    ''')

    # Таблица задач
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            task_number TEXT,
            duration INTEGER,
            date TEXT,
            time_start TEXT,
            description TEXT
        )
    ''')

    # Часовые пояса
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_timezones (
            user_id INTEGER PRIMARY KEY,
            timezone TEXT
        )
    ''')

    conn.commit()
    conn.close()
    print(f"База данных готова: {DB_PATH}")


init_db()


def log_user(user_id: int, username: str, first_name: str):
    """Логирует нового пользователя в таблицу users"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT OR IGNORE INTO users (user_id, username, first_name, joined_date, is_admin, is_premium)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            username or "unknown",
            first_name or "User",
            date.today().isoformat(),
            1 if user_id == ADMIN_ID else 0,
            0,
        ),
    )
    conn.commit()
    conn.close()


def get_statistics():
    """Получает общую статистику по всему боту"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(DISTINCT user_id) FROM tasks")
    total_users = cursor.fetchone()[0]

    seven_days_ago = (date.today() - timedelta(days=7)).isoformat()
    cursor.execute(
        "SELECT COUNT(DISTINCT user_id) FROM tasks WHERE date >= ?",
        (seven_days_ago,),
    )
    active_users = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM tasks")
    total_tasks = cursor.fetchone()[0]

    cursor.execute("SELECT SUM(duration) FROM tasks")
    total_seconds = cursor.fetchone()[0] or 0
    total_hours = total_seconds / 3600
    avg_hours = total_hours / total_users if total_users > 0 else 0

    conn.close()

    return {
        "total_users": total_users,
        "active_users": active_users,
        "total_tasks": total_tasks,
        "total_hours": round(total_hours, 1),
        "avg_hours": round(avg_hours, 1),
    }


def get_user_stats(user_id: int):
    """Получает статистику конкретного пользователя"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()

    cursor.execute(
        "SELECT username, first_name, joined_date FROM users WHERE user_id = ?",
        (user_id,),
    )
    user_info = cursor.fetchone()

    cursor.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ?", (user_id,))
    task_count = cursor.fetchone()[0]

    cursor.execute("SELECT SUM(duration) FROM tasks WHERE user_id = ?", (user_id,))
    total_seconds = cursor.fetchone()[0] or 0
    total_hours = total_seconds / 3600

    avg_time = total_seconds / task_count if task_count > 0 else 0
    avg_minutes = avg_time / 60

    cursor.execute("SELECT MAX(date) FROM tasks WHERE user_id = ?", (user_id,))
    last_activity = cursor.fetchone()[0] or "нет активности"

    conn.close()

    return {
        "username": user_info[0] if user_info else "unknown",
        "first_name": user_info[1] if user_info else "User",
        "joined_date": user_info[2] if user_info else "unknown",
        "task_count": task_count,
        "total_hours": round(total_hours, 1),
        "avg_minutes": round(avg_minutes, 1),
        "last_activity": last_activity,
    }


def get_all_user_ids() -> list[int]:
    """Список всех user_id"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users ORDER BY user_id")
    users = cursor.fetchall()
    conn.close()
    return [user_id for (user_id,) in users]


def get_all_users():
    """Получает список всех пользователей с их статистикой"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users ORDER BY user_id")
    users = cursor.fetchall()
    conn.close()

    users_list = []
    for (user_id,) in users:
        stats = get_user_stats(user_id)
        users_list.append({
            "user_id": user_id,
            **stats,
        })

    return users_list


def get_non_premium_users():
    """Получает список пользователей БЕЗ премиум-статуса"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute(
        "SELECT user_id FROM users WHERE is_premium = 0 AND user_id != ?",
        (ADMIN_ID,),
    )
    users = cursor.fetchall()
    conn.close()
    return [user_id for (user_id,) in users]


def is_premium_or_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь админом или имеет премиум"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()

    if user_id == ADMIN_ID:
        conn.close()
        return True

    cursor.execute("SELECT is_premium FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()

    return bool(result and result[0] == 1)


def set_premium_status(user_id: int, status: int) -> bool:
    """Устанавливает премиум-статус пользователю (0/1)"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET is_premium = ? WHERE user_id = ?",
        (1 if status else 0, user_id),
    )
    conn.commit()
    updated = cursor.rowcount > 0
    conn.close()
    return updated


def generate_csv_report(user_id: int) -> BytesIO:
    """Генерирует CSV файл с отчетом по задачам"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT task_number, date, time_start, duration, description
        FROM tasks
        WHERE user_id = ?
        ORDER BY date DESC, time_start DESC
        """,
        (user_id,),
    )
    tasks = cursor.fetchall()
    conn.close()

    output = StringIO()
    writer = csv.writer(output, lineterminator="\n")

    headers = [
        "№ по порядку",
        "Дата задачи",
        "Наименование задачи",
        "Время начала",
        "Время окончания",
        "Всего затраченное время",
        "Содержание работ",
    ]
    writer.writerow(headers)

    if tasks:
        for idx, (task_number, task_date, time_start, duration, description) in enumerate(tasks, 1):
            # time_start в БД = фактическое время окончания
            end_time = datetime.strptime(time_start, "%H:%M")
            duration_td = timedelta(seconds=duration)
            start_time = end_time - duration_td

            hours, remainder = divmod(duration, 3600)
            minutes, seconds = divmod(remainder, 60)
            duration_str = f"{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d}"

            row = [
                idx,
                task_date,
                task_number,
                start_time.strftime("%H:%M"),  # реальное время начала
                end_time.strftime("%H:%M"),    # реальное время окончания
                duration_str,
                description or "",
            ]
            writer.writerow(row)

    csv_bytes = BytesIO(output.getvalue().encode("utf-8-sig"))
    return csv_bytes


def get_user_timezone(user_id: int) -> SimpleTimezone:
    """Получает часовой пояс пользователя из БД"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute(
        "SELECT timezone FROM user_timezones WHERE user_id = ?",
        (user_id,),
    )
    result = cursor.fetchone()
    conn.close()

    if result and result[0]:
        try:
            return SimpleTimezone(result[0])
        except Exception:
            return MOSCOW_TZ
    return MOSCOW_TZ


def save_user_timezone(user_id: int, timezone_str: str) -> bool:
    """Сохраняет часовой пояс пользователя в БД"""
    if not SimpleTimezone.is_valid(timezone_str):
        return False

    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO user_timezones (user_id, timezone) VALUES (?, ?)",
        (user_id, timezone_str),
    )
    conn.commit()
    conn.close()
    return True


# ================== КЛАВИАТУРЫ ==================
def get_timezone_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🇷🇺 Москва (UTC+3)")],
            [KeyboardButton(text="🇬🇪 Батуми (UTC+4)")],
            [KeyboardButton(text="🇷🇺 Самара (UTC+4)")],
            [KeyboardButton(text="🇷🇺 Екатеринбург (UTC+5)")],
            [KeyboardButton(text="🇬🇧 Лондон (UTC+0)")],
            [KeyboardButton(text="🇹🇭 Бангкок (UTC+7)")],
            [KeyboardButton(text="Другой часовой пояс")],
            [KeyboardButton(text="Пропустить")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    return keyboard


def get_main_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Отчет за сегодня"), KeyboardButton(text="⏰ Начать")],
            [KeyboardButton(text="🔄 Другие отчеты"), KeyboardButton(text="⏹️ Стоп")],
        ],
        resize_keyboard=True,
    )
    return keyboard


def get_reports_submenu():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📆 Отчет по дате"), KeyboardButton(text="📋 Отчет по задаче")],
            [KeyboardButton(text="📥 Экспорт в CSV")],
            [KeyboardButton(text="🔙 Назад")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    return keyboard


def get_calendar_keyboard(year: int, month: int) -> InlineKeyboardMarkup:
    keyboard = []

    prev_year = year - 1 if month == 1 else year
    prev_month = 12 if month == 1 else month - 1
    next_year = year + 1 if month == 12 else year
    next_month = 1 if month == 12 else month + 1

    keyboard.append([
        InlineKeyboardButton(
            text="◀", callback_data=f"cal:{prev_year}:{prev_month:02d}"
        ),
        InlineKeyboardButton(
            text=f"{datetime(year, month, 1).strftime('%B %Y')}",
            callback_data="noop",
        ),
        InlineKeyboardButton(
            text="▶", callback_data=f"cal:{next_year}:{next_month:02d}"
        ),
    ])

    first_day = datetime(year, month, 1)
    if month < 12:
        last_day = datetime(year, month + 1, 1) - timedelta(days=1)
    else:
        last_day = datetime(year + 1, 1, 1) - timedelta(days=1)

    start_weekday = (first_day.weekday() + 1) % 7
    week = []

    for _ in range(start_weekday):
        week.append(InlineKeyboardButton(text=" ", callback_data="noop"))

    for day in range(1, last_day.day + 1):
        week.append(
            InlineKeyboardButton(
                text=str(day),
                callback_data=f"date:{year}:{month:02d}:{day:02d}",
            )
        )
        if len(week) == 7:
            keyboard.append(week)
            week = []

    if week:
        keyboard.append(week)

    keyboard.append(
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_calendar")]
    )

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_tasks_keyboard(user_id: int) -> InlineKeyboardMarkup | None:
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute(
        "SELECT DISTINCT task_number FROM tasks WHERE user_id = ? ORDER BY task_number",
        (user_id,),
    )
    tasks = cursor.fetchall()
    conn.close()

    if not tasks:
        return None

    keyboard = []
    row = []

    for idx, (task_num,) in enumerate(tasks):
        row.append(
            InlineKeyboardButton(text=task_num, callback_data=f"task:{task_num}")
        )
        if len(row) == 2 or idx == len(tasks) - 1:
            keyboard.append(row)
            row = []

    keyboard.append(
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_tasks")]
    )

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


# ================== ОБРАБОТЧИКИ КОМАНД ==================
@dp.message(Command("start"))
async def start_handler(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    log_user(user_id, username, first_name)

    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute(
        "SELECT timezone FROM user_timezones WHERE user_id = ?",
        (user_id,),
    )
    has_timezone = cursor.fetchone()
    conn.close()

    if not has_timezone:
        await state.set_state(TaskTimer.waiting_timezone_choice)
        await message.answer(
            "🌍 Добро пожаловать! Укажите ваш часовой пояс для правильного отображения времени:",
            reply_markup=get_timezone_keyboard(),
        )
    else:
        await message.answer(
            "🕐 Секундомер для задач готов!",
            reply_markup=get_main_keyboard(),
        )


@dp.message(Command("cancel"))
async def cancel_handler(message: types.Message, state: FSMContext):
    """Отмена текущего действия"""
    current_state = await state.get_state()
    if current_state is None:
        await message.answer(
            "Нечего отменять.",
            reply_markup=get_main_keyboard(),
        )
        return

    await state.clear()
    await message.answer(
        "✅ Действие отменено.",
        reply_markup=get_main_keyboard(),
    )


@dp.message(TaskTimer.waiting_timezone_choice)
async def handle_timezone_choice(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    text = message.text.strip()

    timezone_map = {
        "🇷🇺 Москва (UTC+3)": "Europe/Moscow",
        "🇬🇪 Батуми (UTC+4)": "Asia/Tbilisi",
        "🇷🇺 Самара (UTC+4)": "Europe/Samara",
        "🇷🇺 Екатеринбург (UTC+5)": "Asia/Yekaterinburg",
        "🇬🇧 Лондон (UTC+0)": "Europe/London",
        "🇹🇭 Бангкок (UTC+7)": "Asia/Bangkok",
        "Пропустить": "Europe/Moscow",
    }

    if text in timezone_map:
        save_user_timezone(user_id, timezone_map[text])
        if text == "Пропустить":
            await message.answer("⏭️ Установлен московский пояс (UTC+3)")
        else:
            await message.answer(f"✅ Часовой пояс установлен: {text}")

        await state.clear()
        await message.answer(
            "🕐 Секундомер для задач готов!",
            reply_markup=get_main_keyboard(),
        )
    elif text == "Другой часовой пояс":
        await state.set_state(TaskTimer.waiting_custom_timezone)
        await message.answer(
            "Введите часовой пояс из списка поддерживаемых:\n"
            "Europe/Moscow, Asia/Tbilisi, Europe/Samara, Asia/Yekaterinburg, Europe/London, Asia/Bangkok"
        )
    else:
        await message.answer(
            "Пожалуйста, выберите из предложенных вариантов или введите свой.",
            reply_markup=get_timezone_keyboard(),
        )


@dp.message(TaskTimer.waiting_custom_timezone)
async def handle_custom_timezone(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    timezone_str = message.text.strip()

    if save_user_timezone(user_id, timezone_str):
        await message.answer(f"✅ Часовой пояс установлен: {timezone_str}")
        await state.clear()
        await message.answer(
            "🕐 Секундомер для задач готов!",
            reply_markup=get_main_keyboard(),
        )
    else:
        await message.answer(
            f"❌ Часовой пояс '{timezone_str}' не найден.\n"
            "Поддерживаемые: Europe/Moscow, Asia/Tbilisi, Europe/Samara, "
            "Asia/Yekaterinburg, Europe/London, Asia/Bangkok"
        )


@dp.message(F.text == "⏰ Начать")
async def start_timer(message: types.Message, state: FSMContext):
    user_id = message.from_user.id

    if user_id in active_timers:
        await message.answer("⏳ Таймер уже запущен! Сначала нажми '⏹️ Стоп'.")
        return

    await message.answer("📝 Введите номер задачи (например: 'Задача 1'):")
    await state.set_state(TaskTimer.waiting_task_number)


@dp.message(TaskTimer.waiting_task_number)
async def save_task_number(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    task_number = message.text.strip()

    active_timers[user_id] = {
        "start_time": time.time(),
        "task_number": task_number,
        "date": date.today().isoformat(),
    }

    await state.clear()
    await message.answer(
        f"✅ Запущен таймер для *{task_number}*\n⏳ Время идет...",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown",
    )


@dp.message(F.text == "⏹️ Стоп")
async def stop_timer(message: types.Message, state: FSMContext):
    user_id = message.from_user.id

    if user_id not in active_timers:
        await message.answer("⏰ Таймер не запущен! Нажми '⏰ Начать'.")
        return

    timer_data = active_timers.pop(user_id)
    start_time = timer_data["start_time"]
    task_number = timer_data["task_number"]
    date_str = timer_data["date"]

    elapsed = time.time() - start_time
    minutes, seconds = divmod(int(elapsed), 60)
    hours, minutes = divmod(minutes, 60)
    time_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    user_tz = get_user_timezone(user_id)
    now_user = user_tz.get_current_time()
    time_start_str = now_user.strftime("%H:%M")

    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO tasks (user_id, task_number, duration, date, time_start, description)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (user_id, task_number, int(elapsed), date_str, time_start_str, None),
    )
    task_id = cursor.lastrowid
    conn.commit()
    conn.close()

    await state.update_data(last_task_id=task_id)
    await message.answer(
        f"⏹️ *{task_number}* завершена!\n"
        f"⏱️ Время: *{time_str}*\n"
        f"📅 {date_str}",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard(),
    )

    keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="✅ Да"), KeyboardButton(text="❌ Нет")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await message.answer("Добавить описание трудозатрат?", reply_markup=keyboard)
    await state.set_state(TaskTimer.waiting_description_choice)


@dp.message(TaskTimer.waiting_description_choice)
async def handle_description_choice(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    text = message.text.strip()
    data = await state.get_data()
    task_id = data.get("last_task_id")

    if not task_id:
        await state.clear()
        await message.answer(
            "Не нашёл последнюю задачу.",
            reply_markup=get_main_keyboard(),
        )
        return

    if text == "❌ Нет":
        await state.clear()
        await message.answer(
            "Окей, описание не добавлено.",
            reply_markup=get_main_keyboard(),
        )
        return

    if text == "✅ Да":
        await state.set_state(TaskTimer.waiting_description_text)
        await message.answer(
            "Пришлите текстовое описание, что делали по этой задаче.",
            reply_markup=get_main_keyboard(),
        )
        return

    await message.answer(
        "Выберите '✅ Да' или '❌ Нет'.",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="✅ Да"), KeyboardButton(text="❌ Нет")]],
            resize_keyboard=True,
            one_time_keyboard=True,
        ),
    )


@dp.message(TaskTimer.waiting_description_text)
async def save_description(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    data = await state.get_data()
    task_id = data.get("last_task_id")

    if not task_id:
        await state.clear()
        await message.answer(
            "Не нашёл последнюю задачу.",
            reply_markup=get_main_keyboard(),
        )
        return

    description = message.text.strip()

    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE tasks SET description = ? WHERE id = ? AND user_id = ?",
        (description, task_id, user_id),
    )
    conn.commit()
    conn.close()

    await state.clear()
    await message.answer(
        "Описание трудозатрат сохранено ✅",
        reply_markup=get_main_keyboard(),
    )


async def send_report_for_date(user_id: int, report_date: date, message: types.Message):
    date_str = report_date.isoformat()

    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT task_number, duration, time_start, description
        FROM tasks
        WHERE user_id = ? AND date = ?
        ORDER BY time_start
        """,
        (user_id, date_str),
    )
    tasks = cursor.fetchall()
    conn.close()

    if not tasks:
        await message.answer(
            f"📊 За {date_str} задач нет.",
            reply_markup=get_main_keyboard(),
        )
        return

    total_duration = sum(task[1] for task in tasks)
    total_hours, remainder = divmod(total_duration, 3600)
    total_minutes, total_secs = divmod(remainder, 60)
    total_str = f"{total_hours:02d}:{total_minutes:02d}:{total_secs:02d}"

    report_text = f"📊 *Отчет за {date_str}*\n\n"
    report_text += f"*Всего времени: {total_str}*\n\n"

    for task_num, duration, start_time, description in tasks:
        hours, remainder = divmod(duration, 3600)
        minutes, seconds = divmod(remainder, 60)
        task_time = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        report_text += f"• *{task_num}*: {task_time} ({start_time})\n"
        if description:
            report_text += f"  └ {description}\n"

    await message.answer(
        report_text,
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown",
    )


async def send_report_for_task(user_id: int, task_number: str, message: types.Message):
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT date, duration, time_start, description
        FROM tasks
        WHERE user_id = ? AND task_number = ?
        ORDER BY date, time_start
        """,
        (user_id, task_number),
    )
    tasks = cursor.fetchall()
    conn.close()

    if not tasks:
        await message.answer(
            f"📋 Нет данных для задачи *{task_number}*.",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard(),
        )
        return

    tasks_by_date: dict[str, list[tuple[int, str, str | None]]] = {}
    total_duration = 0

    for task_date, duration, time_start, description in tasks:
        tasks_by_date.setdefault(task_date, []).append(
            (duration, time_start, description)
        )
        total_duration += duration

    total_hours, remainder = divmod(total_duration, 3600)
    total_minutes, total_secs = divmod(remainder, 60)
    total_str = f"{total_hours:02d}:{total_minutes:02d}:{total_secs:02d}"

    report_text = f"📋 *Отчет по задаче: {task_number}*\n\n"
    report_text += f"*Всего времени: {total_str}*\n"
    report_text += f"*Записей: {len(tasks)}*\n\n"

    for task_date in sorted(tasks_by_date.keys()):
        day_entries = tasks_by_date[task_date]
        day_duration = sum(entry[0] for entry in day_entries)
        day_hours, remainder = divmod(day_duration, 3600)
        day_minutes, day_secs = divmod(remainder, 60)
        day_str = f"{day_hours:02d}:{day_minutes:02d}:{day_secs:02d}"

        report_text += f"📅 *{task_date}* ({day_str})\n"

        for duration, time_start, description in day_entries:
            hours, remainder = divmod(duration, 3600)
            minutes, seconds = divmod(remainder, 60)
            entry_time = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            report_text += f"  • {time_start}: {entry_time}\n"
            if description:
                report_text += f"    └ {description}\n"

        report_text += "\n"

    await message.answer(
        report_text,
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown",
    )


@dp.message(F.text == "📊 Отчет за сегодня")
async def daily_report_today(message: types.Message):
    user_id = message.from_user.id
    await send_report_for_date(user_id, date.today(), message)


@dp.message(F.text == "🔄 Другие отчеты")
async def show_reports_submenu(message: types.Message, state: FSMContext):
    await state.set_state(TaskTimer.waiting_reports_menu)
    await message.answer(
        "Выберите тип отчета:",
        reply_markup=get_reports_submenu(),
    )


@dp.message(TaskTimer.waiting_reports_menu, F.text == "📆 Отчет по дате")
async def ask_report_date(message: types.Message, state: FSMContext):
    today = date.today()
    await state.set_state(TaskTimer.choosing_calendar_month)
    await message.answer(
        "📅 Выберите дату для отчета:",
        reply_markup=get_calendar_keyboard(today.year, today.month),
    )


@dp.message(TaskTimer.waiting_reports_menu, F.text == "📋 Отчет по задаче")
async def ask_report_task(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    tasks_kb = get_tasks_keyboard(user_id)

    if not tasks_kb:
        await state.clear()
        await message.answer(
            "📋 У вас нет записей о задачах.",
            reply_markup=get_main_keyboard(),
        )
        return

    await state.set_state(TaskTimer.choosing_task_for_report)
    await message.answer(
        "📋 Выберите задачу для отчета:",
        reply_markup=tasks_kb,
    )


@dp.message(TaskTimer.waiting_reports_menu, F.text == "📥 Экспорт в CSV")
async def export_to_csv(message: types.Message, state: FSMContext):
    """Экспортирует данные в CSV"""
    user_id = message.from_user.id
    await state.clear()

    if not is_premium_or_admin(user_id):
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="💎 Купить премиум (99 ₽)", callback_data="buy_premium"
                    )
                ]
            ]
        )
        await message.answer(
            "❌ Доступ к экспорту в CSV доступен только премиум-пользователям.\n\n"
            "Оформите премиум за 99 ₽, чтобы выгружать свои задачи в CSV. | Чтобы вернуться в главное меню отправьте мне любой символ.",
            reply_markup=kb,
        )
        return

    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ?", (user_id,))
    task_count = cursor.fetchone()[0]
    conn.close()

    if task_count == 0:
        await message.answer(
            "❌ У вас нет записей о задачах для экспорта.",
            reply_markup=get_main_keyboard(),
        )
        return

    try:
        csv_file = generate_csv_report(user_id)
        csv_file.seek(0)

        await message.answer_document(
            document=types.BufferedInputFile(
                file=csv_file.getvalue(),
                filename=f"tasks_report_{date.today().isoformat()}.csv",
            ),
            caption=f"📊 Отчет по вашим задачам\n📋 Всего записей: {task_count}",
        )
        await message.answer(
            "✅ Готово!",
            reply_markup=get_main_keyboard(),
        )
    except Exception as e:
        print(f"CSV Export Error: {e}")
        await message.answer(
            f"❌ Ошибка создания файла: {str(e)}",
            reply_markup=get_main_keyboard(),
        )


@dp.message(TaskTimer.waiting_reports_menu, F.text == "🔙 Назад")
async def back_to_main_menu(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Вернулись в главное меню",
        reply_markup=get_main_keyboard(),
    )


# ================== ПЛАТЕЖИ ЗА ПРЕМИУМ ==================
@dp.callback_query(F.data == "buy_premium")
async def buy_premium_callback(callback: types.CallbackQuery):
    await callback.answer()
    payload = f"premium_{callback.from_user.id}"

    await callback.message.answer_invoice(
        title=PREMIUM_TITLE,
        description=PREMIUM_DESCRIPTION,
        payload=payload,
        provider_token=PROVIDER_TOKEN,
        currency="RUB",
        prices=[
            types.LabeledPrice(label=PREMIUM_TITLE, amount=PREMIUM_PRICE * 100),
        ],
        start_parameter="premium-sub",
    )


@dp.pre_checkout_query()
async def pre_checkout(pre_checkout_q: types.PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_q.id, ok=True)


@dp.message(F.successful_payment)
async def successful_payment(message: types.Message):
    user_id = message.from_user.id

    if not message.successful_payment:
        return

    payload = message.successful_payment.invoice_payload
    if not payload.startswith("premium_"):
        return

    set_premium_status(user_id, 1)
    await message.answer(
        "✅ Премиум активирован!\nТеперь вам доступен экспорт задач в CSV.",
        reply_markup=get_main_keyboard(),
    )


# ================== АДМИН-КОМАНДЫ ==================
@dp.message(Command("msg_to_all"))
async def start_msg_to_all(message: types.Message, state: FSMContext):
    """Начало рассылки всем пользователям"""
    if message.from_user.id != ADMIN_ID:
        await message.answer(
            "❌ Доступ запрещен. Эта команда только для администратора.",
            reply_markup=get_main_keyboard(),
        )
        return

    await message.answer(
        "📢 Отправьте сообщение для рассылки всем пользователям:\n"
        "- Текстовое сообщение\n"
        "- Или фото с текстом (caption)\n\n"
        "Для отмены используйте команду /cancel до начала отправки.",
        reply_markup=get_main_keyboard(),
    )
    await state.set_state(TaskTimer.waiting_msg_to_all_message)


@dp.message(Command("stats"))
async def admin_stats(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer(
            "❌ Доступ запрещен. Эта команда только для администратора.",
            reply_markup=get_main_keyboard(),
        )
        return

    stats = get_statistics()
    report = (
        "📊 СТАТИСТИКА БОТА\n\n"
        f"👥 Всего пользователей: {stats['total_users']}\n"
        f"👤 Активных за 7 дней: {stats['active_users']}\n"
        f"📋 Всего задач: {stats['total_tasks']}\n"
        f"⏱️ Всего часов: {stats['total_hours']}\n"
        f"💰 Среднее/пользователя: {stats['avg_hours']} часов\n"
    )
    await message.answer(report, reply_markup=get_main_keyboard())


@dp.message(Command("user_list"))
async def admin_user_list(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer(
            "❌ Доступ запрещен. Эта команда только для администратора.",
            reply_markup=get_main_keyboard(),
        )
        return

    users = get_all_users()

    if not users:
        await message.answer(
            "📋 Пока нет пользователей.",
            reply_markup=get_main_keyboard(),
        )
        return

    report = f"📋 ВСЕ ПОЛЬЗОВАТЕЛИ ({len(users)})\n\n"
    for idx, user in enumerate(users, 1):
        report += (
            f"{idx}️⃣ @{user['username']} | {user['first_name']} | "
            f"Присоединился: {user['joined_date']} | {user['total_hours']} часов\n"
        )

    await message.answer(report, reply_markup=get_main_keyboard())


@dp.message(Command("user"))
async def admin_user_info(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer(
            "❌ Доступ запрещен. Эта команда только для администратора.",
            reply_markup=get_main_keyboard(),
        )
        return

    try:
        user_id = int(message.text.split()[1])
    except (IndexError, ValueError):
        await message.answer(
            "❌ Используй: /user <user_id>",
            reply_markup=get_main_keyboard(),
        )
        return

    stats = get_user_stats(user_id)
    report = (
        "👤 ИНФОРМАЦИЯ О ПОЛЬЗОВАТЕЛЕ\n\n"
        f"Username: @{stats['username']}\n"
        f"Имя: {stats['first_name']}\n"
        f"ID: {user_id}\n"
        f"Присоединился: {stats['joined_date']}\n"
        f"Всего задач: {stats['task_count']}\n"
        f"Всего часов: {stats['total_hours']}\n"
        f"Среднее время/задача: {stats['avg_minutes']} минут\n"
        f"Последняя активность: {stats['last_activity']}\n"
    )
    await message.answer(report, reply_markup=get_main_keyboard())


@dp.message(Command("admin_help"))
async def admin_help(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer(
            "❌ Доступ запрещен. Эта команда только для администратора.",
            reply_markup=get_main_keyboard(),
        )
        return

    help_text = (
        "🔧 АДМИН КОМАНДЫ\n\n"
        "/stats - Общая статистика бота\n"
        "/user_list - Список всех пользователей\n"
        "/user <user_id> - Информация о пользователе\n"
        "/premium <user_id> <0|1> - Включить/выключить премиум у пользователя\n"
        "/broadcast - Рассылка сообщения (только пользователям без премиум)\n"
        "/msg_to_all - Рассылка всем пользователям\n"
        "/admin_help - Эта справка\n"
    )
    await message.answer(help_text, reply_markup=get_main_keyboard())


@dp.message(Command("broadcast"))
async def start_broadcast(message: types.Message, state: FSMContext):
    """Начало рассылки с возможностью отправки фото"""
    if message.from_user.id != ADMIN_ID:
        await message.answer(
            "❌ Доступ запрещен. Эта команда только для администратора.",
            reply_markup=get_main_keyboard(),
        )
        return

    await message.answer(
        "📢 Отправьте сообщение для рассылки:\n"
        "- Текстовое сообщение\n"
        "- Или фото с текстом (caption)\n\n"
        "Используйте /cancel для отмены."
    )
    await state.set_state(TaskTimer.waiting_broadcast_message)


@dp.message(TaskTimer.waiting_broadcast_message, F.photo)
async def send_broadcast_with_photo(message: types.Message, state: FSMContext):
    """Рассылка с фото"""
    if message.from_user.id != ADMIN_ID:
        return

    photo_id = message.photo[-1].file_id
    caption = message.caption or ""

    non_premium_users = get_non_premium_users()

    if not non_premium_users:
        await state.clear()
        await message.answer(
            "❌ Нет пользователей без премиум-статуса для рассылки.",
            reply_markup=get_main_keyboard(),
        )
        return

    await message.answer(
        f"📤 Начинаю рассылку фото {len(non_premium_users)} пользователям без премиума...",
        reply_markup=get_main_keyboard(),
    )

    success_count = 0
    error_count = 0

    for user_id in non_premium_users:
        try:
            await bot.send_photo(
                chat_id=user_id,
                photo=photo_id,
                caption=caption,
                parse_mode="Markdown" if caption else None,
            )
            success_count += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            error_count += 1
            print(f"Ошибка отправки пользователю {user_id}: {e}")

    await state.clear()
    await message.answer(
        f"✅ Рассылка завершена!\n"
        f"✅ Успешно: {success_count}\n"
        f"❌ Ошибок: {error_count}",
        reply_markup=get_main_keyboard(),
    )


@dp.message(TaskTimer.waiting_msg_to_all_message, F.text)
async def msg_to_all_text(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    broadcast_text = message.text.strip()
    user_ids = get_all_user_ids()

    if not user_ids:
        await state.clear()
        await message.answer(
            "❌ Нет пользователей для рассылки.",
            reply_markup=get_main_keyboard(),
        )
        return

    await message.answer(
        f"📤 Начинаю рассылку всем {len(user_ids)} пользователям...",
        reply_markup=get_main_keyboard(),
    )

    success_count = 0
    error_count = 0

    for user_id in user_ids:
        try:
            await bot.send_message(user_id, broadcast_text, parse_mode="Markdown")
            success_count += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            error_count += 1
            print(f"Ошибка отправки пользователю {user_id}: {e}")

    await state.clear()
    await message.answer(
        f"✅ Рассылка завершена!\n"
        f"✅ Успешно: {success_count}\n"
        f"❌ Ошибок: {error_count}",
        reply_markup=get_main_keyboard(),
    )


@dp.message(TaskTimer.waiting_msg_to_all_message, F.photo)
async def msg_to_all_photo(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    photo_id = message.photo[-1].file_id
    caption = message.caption or ""
    user_ids = get_all_user_ids()

    if not user_ids:
        await state.clear()
        await message.answer(
            "❌ Нет пользователей для рассылки.",
            reply_markup=get_main_keyboard(),
        )
        return

    await message.answer(
        f"📤 Начинаю рассылку фото {len(user_ids)} пользователям...",
        reply_markup=get_main_keyboard(),
    )

    success_count = 0
    error_count = 0

    for user_id in user_ids:
        try:
            await bot.send_photo(
                chat_id=user_id,
                photo=photo_id,
                caption=caption,
                parse_mode="Markdown" if caption else None,
            )
            success_count += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            error_count += 1
            print(f"Ошибка отправки пользователю {user_id}: {e}")

    await state.clear()
    await message.answer(
        f"✅ Рассылка завершена!\n"
        f"✅ Успешно: {success_count}\n"
        f"❌ Ошибок: {error_count}",
        reply_markup=get_main_keyboard(),
    )


@dp.message(TaskTimer.waiting_broadcast_message, F.text)
async def send_broadcast_text(message: types.Message, state: FSMContext):
    """Рассылка только текста (БЕЗ премиум пользователей)"""
    if message.from_user.id != ADMIN_ID:
        return

    broadcast_text = message.text.strip()
    non_premium_users = get_non_premium_users()

    if not non_premium_users:
        await state.clear()
        await message.answer(
            "❌ Нет пользователей без премиум-статуса для рассылки.",
            reply_markup=get_main_keyboard(),
        )
        return

    await message.answer(
        f"📤 Начинаю рассылку {len(non_premium_users)} пользователям без премиума...",
        reply_markup=get_main_keyboard(),
    )

    success_count = 0
    error_count = 0

    for user_id in non_premium_users:
        try:
            await bot.send_message(user_id, broadcast_text, parse_mode="Markdown")
            success_count += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            error_count += 1
            print(f"Ошибка отправки пользователю {user_id}: {e}")

    await state.clear()
    await message.answer(
        f"✅ Рассылка завершена!\n"
        f"✅ Успешно: {success_count}\n"
        f"❌ Ошибок: {error_count}",
        reply_markup=get_main_keyboard(),
    )


@dp.message(Command("premium"))
async def admin_set_premium(message: types.Message):
    """Команда: /premium <user_id> <0|1>"""
    if message.from_user.id != ADMIN_ID:
        await message.answer(
            "❌ Доступ запрещен. Эта команда только для администратора.",
            reply_markup=get_main_keyboard(),
        )
        return

    try:
        parts = message.text.split()
        if len(parts) != 3:
            await message.answer(
                "Использование: /premium <user_id> <0|1>",
                reply_markup=get_main_keyboard(),
            )
            return

        user_id = int(parts[1])
        status = int(parts[2])

        if status not in (0, 1):
            await message.answer(
                "Статус должен быть 0 или 1",
                reply_markup=get_main_keyboard(),
            )
            return

        if set_premium_status(user_id, status):
            await message.answer(
                f"✅ Пользователь {user_id}: премиум = {status}",
                reply_markup=get_main_keyboard(),
            )
        else:
            await message.answer(
                "❌ Пользователь не найден.",
                reply_markup=get_main_keyboard(),
            )
    except ValueError:
        await message.answer(
            "❌ Неверный формат. Пример: /premium 123456 1",
            reply_markup=get_main_keyboard(),
        )


# ================== CALLBACK QUERIES ==================
@dp.callback_query(F.data.startswith("cal:"))
async def handle_calendar_navigation(callback: types.CallbackQuery, state: FSMContext):
    try:
        parts = callback.data.split(":")
        if len(parts) < 3:
            await callback.answer("Ошибка навигации", show_alert=False)
            return

        year, month = int(parts[1]), int(parts[2])
        await callback.message.edit_reply_markup(
            reply_markup=get_calendar_keyboard(year, month)
        )
        await callback.answer()
    except (ValueError, IndexError):
        await callback.answer("Ошибка при обработке даты", show_alert=False)


@dp.callback_query(F.data.startswith("date:"))
async def handle_date_selection(callback: types.CallbackQuery, state: FSMContext):
    try:
        parts = callback.data.split(":")
        if len(parts) < 4:
            await callback.answer("Ошибка выбора даты", show_alert=False)
            return

        year, month, day = int(parts[1]), int(parts[2]), int(parts[3])
        selected_date = date(year, month, day)
        user_id = callback.from_user.id

        await callback.message.delete()
        await state.clear()
        await send_report_for_date(user_id, selected_date, callback.message)
    except (ValueError, IndexError):
        await callback.answer("Ошибка при обработке даты", show_alert=False)


@dp.callback_query(F.data.startswith("task:"))
async def handle_task_selection(callback: types.CallbackQuery, state: FSMContext):
    try:
        parts = callback.data.split(":", 1)
        if len(parts) < 2:
            await callback.answer("Ошибка выбора задачи", show_alert=False)
            return

        task_number = parts[1]
        user_id = callback.from_user.id

        await callback.message.delete()
        await state.clear()
        await send_report_for_task(user_id, task_number, callback.message)
    except (ValueError, IndexError):
        await callback.answer("Ошибка при обработке задачи", show_alert=False)


@dp.callback_query(F.data == "cancel_calendar")
async def cancel_calendar(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.delete()
    await state.clear()
    await callback.message.answer(
        "❌ Выбор даты отменен.",
        reply_markup=get_main_keyboard(),
    )


@dp.callback_query(F.data == "cancel_tasks")
async def cancel_tasks(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.delete()
    await state.clear()
    await callback.message.answer(
        "❌ Выбор задачи отменен.",
        reply_markup=get_main_keyboard(),
    )


@dp.callback_query(F.data == "noop")
async def noop_callback(callback: types.CallbackQuery):
    await callback.answer()


# ================== ЗАПУСК БОТА ==================
async def main():
    print("🤖 Бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())