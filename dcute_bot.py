import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage

BOT_TOKEN = "8668305902:AAFCNyqMdfisL-CaSvR1iVxloxHjeDdikeA"

logging.basicConfig(level=logging.INFO)
bot = Bot(BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer(
        "🌸 Добро пожаловать в D.Cute Beauty!\n\n"
        "Выберите действие:\n"
        "📅 Записаться — /book\n"
        "📋 Мои записи — /mybookings"
    )

@dp.message(Command("book"))
async def book(message: types.Message):
    await message.answer("📅 Запись к мастеру. Выберите услугу...")

@dp.message(Command("mybookings"))
async def mybookings(message: types.Message):
    await message.answer("📋 Ваши записи...")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
