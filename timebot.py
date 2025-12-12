import asyncio
import time
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

import os
API_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Состояния FSM
class TaskTimer(StatesGroup):
    waiting_task_number = State()

# Хранилище активных таймеров {user_id: start_time}
active_timers = {}

# Клавиатура с кнопками
def get_main_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⏰ Начать")],
            [KeyboardButton(text="⏹️ Стоп")]
        ],
        resize_keyboard=True
    )
    return keyboard

# Inline клавиатура для управления
def get_inline_keyboard():
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏰ Начать", callback_data="start_timer")],
            [InlineKeyboardButton(text="⏹️ Стоп", callback_data="stop_timer")]
        ]
    )
    return keyboard

@dp.message(Command("start"))
async def start_handler(message: types.Message):
    await message.answer(
        "🕐 Секундомер для задач готов!\n"
        "Нажми '⏰ Начать' для запуска таймера.",
        reply_markup=get_main_keyboard()
    )

@dp.message(F.text == "⏰ Начать")
async def start_timer(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    # Проверяем, не запущен ли уже таймер
    if user_id in active_timers:
        await message.answer("⏳ Таймер уже запущен! Сначала нажми '⏹️ Стоп'.")
        return
    
    await message.answer("📝 Введите номер задачи (например: 'Задача 1'):")
    await state.set_state(TaskTimer.waiting_task_number)

@dp.message(TaskTimer.waiting_task_number)
async def save_task_number(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    task_number = message.text.strip()
    
    # Сохраняем время начала
    active_timers[user_id] = {
        "start_time": time.time(),
        "task_number": task_number
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
    
    # Вычисляем прошедшее время
    elapsed = time.time() - start_time
    minutes, seconds = divmod(int(elapsed), 60)
    hours, minutes = divmod(minutes, 60)
    
    time_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    
    await message.answer(
        f"⏹️ **{task_number}** завершена!\n"
        f"⏱️ Время: **{time_str}**",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
    )

# Игнорируем все остальные сообщения во время работы таймера
@dp.message()
async def ignore_messages(message: types.Message):
    user_id = message.from_user.id
    if user_id in active_timers:
        await message.answer("⏳ Таймер работает! Нажми '⏹️ Стоп' для завершения.")
    else:
        await message.answer("Используй кнопки ⏰ Начать / ⏹️ Стоп", reply_markup=get_main_keyboard())

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())