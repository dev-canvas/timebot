import asyncio
import time
import sqlite3
from datetime import datetime, date

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

import os

API_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)


class TaskTimer(StatesGroup):
    waiting_task_number = State()
    waiting_description_choice = State()
    waiting_description_text = State()
    waiting_report_date = State()


# активные таймеры {user_id: {...}}
active_timers = {}


# Инициализация БД
def init_db():
    conn = sqlite3.connect("tasks.db")
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            task_number TEXT,
            duration INTEGER,  -- в секундах
            date TEXT,
            time_start TEXT,
            description TEXT
        )
        """
    )
    conn.commit()
    conn.close()


init_db()


def get_main_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⏰ Начать")],
            [KeyboardButton(text="⏹️ Стоп")],
            [KeyboardButton(text="📊 Отчет за сегодня")],
            [KeyboardButton(text="📆 Отчет по дате")],
        ],
        resize_keyboard=True,
    )
    return keyboard


@dp.message(Command("start"))
async def start_handler(message: types.Message):
    await message.answer(
        "🕐 Секундомер для задач готов!\n"
        "⏰ Начать / ⏹️ Стоп / 📊 Отчет за сегодня / 📆 Отчет по дате",
        reply_markup=get_main_keyboard(),
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

    # сохраняем задачу в БД без описания (description = NULL)
    conn = sqlite3.connect("tasks.db")
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO tasks (user_id, task_number, duration, date, time_start, description)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (user_id, task_number, int(elapsed), date_str, datetime.now().strftime("%H:%M"), None),
    )
    task_id = cursor.lastrowid
    conn.commit()
    conn.close()

    # запоминаем id завершённой задачи
    await state.update_data(last_task_id=task_id)

    await message.answer(
        f"⏹️ *{task_number}* завершена!\n"
        f"⏱️ Время: *{time_str}*\n"
        f"📅 {date_str}",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard(),
    )

    # спрашиваем про описание
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Да"), KeyboardButton(text="❌ Нет")],
        ],
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
            "Не нашёл последнюю задачу. Используй кнопки ⏰ Начать / ⏹️ Стоп / 📊 Отчет за сегодня / 📆 Отчет по дате.",
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

    # если пришло что-то другое — повторно спрашиваем
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
            "Не нашёл последнюю задачу. Используй кнопки ⏰ Начать / ⏹️ Стоп / 📊 Отчет за сегодня / 📆 Отчет по дате.",
            reply_markup=get_main_keyboard(),
        )
        return

    description = message.text.strip()

    conn = sqlite3.connect("tasks.db")
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

    conn = sqlite3.connect("tasks.db")
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
        await message.answer(f"📊 За {date_str} задач нет.")
        return

    total_seconds = sum(task[1] for task in tasks)
    total_hours, remainder = divmod(total_seconds, 3600)
    total_minutes, total_seconds = divmod(remainder, 60)
    total_str = f"{total_hours:02d}:{total_minutes:02d}:{total_seconds:02d}"

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


@dp.message(F.text == "📊 Отчет за сегодня")
async def daily_report_today(message: types.Message):
    user_id = message.from_user.id
    await send_report_for_date(user_id, date.today(), message)


@dp.message(F.text == "📆 Отчет по дате")
async def ask_report_date(message: types.Message, state: FSMContext):
    await state.set_state(TaskTimer.waiting_report_date)
    await message.answer(
        "Введите дату для отчета в формате ГГГГ-ММ-ДД (например, 2025-12-12).",
        reply_markup=get_main_keyboard(),
    )


@dp.message(TaskTimer.waiting_report_date)
async def report_for_custom_date(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    text = message.text.strip()

    try:
        year, month, day = map(int, text.split("-"))
        report_date = date(year, month, day)
    except Exception:
        await message.answer(
            "Не получилось разобрать дату. Введите, пожалуйста, в формате ГГГГ-ММ-ДД, например: 2025-12-12.",
            reply_markup=get_main_keyboard(),
        )
        return

    await state.clear()
    await send_report_for_date(user_id, report_date, message)


@dp.message()
async def ignore_messages(message: types.Message):
    user_id = message.from_user.id
    if user_id in active_timers:
        await message.answer("⏳ Таймер работает! Нажми '⏹️ Стоп' для завершения.")
    else:
        await message.answer(
            "Используй кнопки ⏰ Начать / ⏹️ Стоп / 📊 Отчет за сегодня / 📆 Отчет по дате",
            reply_markup=get_main_keyboard(),
        )


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
