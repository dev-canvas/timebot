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

active_timers = {}

# Инициализация БД
def init_db():
    conn = sqlite3.connect('tasks.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            task_number TEXT,
            duration INTEGER,  -- в секундах
            date TEXT,
            time_start TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def get_main_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⏰ Начать")],
            [KeyboardButton(text="⏹️ Стоп")],
            [KeyboardButton(text="📊 Отчет за день")]
        ],
        resize_keyboard=True
    )
    return keyboard

@dp.message(Command("start"))
async def start_handler(message: types.Message):
    await message.answer(
        "🕐 Секундомер для задач готов!\n"
        "⏰ Начать / ⏹️ Стоп / 📊 Отчет за день",
        reply_markup=get_main_keyboard()
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
        "date": date.today().isoformat()
    }
    
    await state.clear()
    await message.answer(
        f"✅ Запущен таймер для **{task_number}**\n⏳ Время идет...",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
    )

@dp.message(F.text == "⏹️ Стоп")
async def stop_timer(message: types.Message):
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
    
    # Сохраняем в БД
    conn = sqlite3.connect('tasks.db')
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO tasks (user_id, task_number, duration, date, time_start) VALUES (?, ?, ?, ?, ?)",
        (user_id, task_number, int(elapsed), date_str, datetime.now().strftime("%H:%M"))
    )
    conn.commit()
    conn.close()
    
    await message.answer(
        f"⏹️ **{task_number}** завершена!\n"
        f"⏱️ Время: **{time_str}**\n"
        f"📅 {date_str}",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
    )

@dp.message(F.text == "📊 Отчет за день")
async def daily_report(message: types.Message):
    user_id = message.from_user.id
    today = date.today().isoformat()
    
    conn = sqlite3.connect('tasks.db')
    cursor = conn.cursor()
    cursor.execute(
        "SELECT task_number, duration, time_start FROM tasks WHERE user_id = ? AND date = ? ORDER BY time_start",
        (user_id, today)
    )
    tasks = cursor.fetchall()
    conn.close()
    
    if not tasks:
        await message.answer("📊 За сегодня задач еще нет.")
        return
    
    total_seconds = sum(task[1] for task in tasks)
    total_hours, remainder = divmod(total_seconds, 3600)
    total_minutes, total_seconds = divmod(remainder, 60)
    total_str = f"{total_hours:02d}:{total_minutes:02d}:{total_seconds:02d}"
    
    report_text = f"📊 **Отчет за {today}**\n\n"
    report_text += f"**Всего времени: {total_str}**\n\n"
    
    for task_num, duration, start_time in tasks:
        hours, remainder = divmod(duration, 3600)
        minutes, seconds = divmod(remainder, 60)
        task_time = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        report_text += f"• {task_num}: {task_time} ({start_time})\n"
    
    await message.answer(report_text, reply_markup=get_main_keyboard(), parse_mode="Markdown")

@dp.message()
async def ignore_messages(message: types.Message):
    user_id = message.from_user.id
    if user_id in active_timers:
        await message.answer("⏳ Таймер работает! Нажми '⏹️ Стоп' для завершения.")
    else:
        await message.answer("Используй кнопки ⏰ Начать / ⏹️ Стоп / 📊 Отчет за день", 
                           reply_markup=get_main_keyboard())

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
