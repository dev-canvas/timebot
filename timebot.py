    if not task_id:
        await state.clear()
        await message.answer(
            "Не нашёл последнюю задачу. Используй кнопки ⏰ Начать / ⏹️ Стоп / 📊 Отчет за день.",
            reply_markup=get_main_keyboard(),
        )
        return

    if message.text == "❌ Нет":
        # просто выходим без описания
        await state.clear()
        await message.answer(
            "Окей, описание не добавлено.",
            reply_markup=get_main_keyboard(),
        )
        return

    # если "✅ Да" — просим ввести текст
    await message.answer(
        "Пришлите текстовое описание, что делали по этой задаче.",
        reply_markup=get_main_keyboard(),
    )
    # сохраняем id задачи и ждём текст
    await state.update_data(last_task_id=task_id)
    await state.set_state(TaskTimer.waiting_description)


@dp.message(TaskTimer.waiting_description)
async def save_description(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    data = await state.get_data()
    task_id = data.get("last_task_id")

    if not task_id:
        await state.clear()
        await message.answer(
            "Не нашёл последнюю задачу. Используй кнопки ⏰ Начать / ⏹️ Стоп / 📊 Отчет за день.",
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


@dp.message(F.text == "📊 Отчет за день")
async def daily_report(message: types.Message):
    user_id = message.from_user.id
    today = date.today().isoformat()

    conn = sqlite3.connect("tasks.db")
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT task_number, duration, time_start, description
        FROM tasks
        WHERE user_id = ? AND date = ?
        ORDER BY time_start
        """,
        (user_id, today),
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

    report_text = f"📊 *Отчет за {today}*\n\n"
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


@dp.message()
async def ignore_messages(message: types.Message):
    user_id = message.from_user.id
    if user_id in active_timers:
        await message.answer("⏳ Таймер работает! Нажми '⏹️ Стоп' для завершения.")
    else:
        await message.answer(
            "Используй кнопки ⏰ Начать / ⏹️ Стоп / 📊 Отчет за день",
            reply_markup=get_main_keyboard(),
        )


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
