# search_bot.py - ОПРОЩЕННАЯ ВЕРСИЯ ДЛЯ ПРОВЕРКИ ДОСТУПА ПО ГРУППАМ
import os
import html
import asyncio
import aiohttp
import logging
from aiogram import Bot, Dispatcher, types, Router
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ParseMode
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()


class SearchStates(StatesGroup):
    waiting_for_search = State()


class SearchBot:
    def __init__(self):
        self.token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.api_url = os.getenv('SITE_API_URL', 'http://localhost:8000/api/search/')

        # Получаем ID разрешенных групп из .env
        allowed_groups = os.getenv('ALLOWED_GROUP_IDS', '')
        self.allowed_group_ids = [int(group_id.strip()) for group_id in allowed_groups.split(',') if group_id.strip()]

        if not self.token:
            raise ValueError("TELEGRAM_BOT_TOKEN не установлен")

        # Создаем бота
        self.bot = Bot(token=self.token)
        self.storage = MemoryStorage()
        self.dp = Dispatcher(storage=self.storage)
        self.router = Router()

        # ДОБАВЛЕНИЕ МИДДЛВАРИ ДЛЯ ПРОВЕРКИ ДОСТУПА
        self.router.message.middleware(self.access_middleware)
        self.router.callback_query.middleware(self.access_middleware)

        self.dp.include_router(self.router)

        # Регистрируем обработчики
        self.register_handlers()

    async def access_middleware(self, handler, event: types.Update, data: dict):
        """Миддлварь для проверки доступа"""
        user_id = None
        if event.message:
            user_id = event.message.from_user.id
        elif event.callback_query:
            user_id = event.callback_query.from_user.id

        if user_id:
            has_access = await self.check_group_membership(user_id)
            if not has_access:
                if event.message:
                    await event.message.answer(
                        "❌ Доступ запрещен. Вы не состоите в разрешённой группе.",
                        parse_mode=ParseMode.HTML
                    )
                elif event.callback_query:
                    await event.callback_query.answer("❌ Доступ запрещен", show_alert=True)
                return

        return await handler(event, data)

    async def check_group_membership(self, user_id: int) -> bool:
        """Проверяет, состоит ли пользователь в разрешённых группах"""
        # Упрощена проверка на основе group_ids
        for group_id in self.allowed_group_ids:
            try:
                # Мы проверяем только наличие в группе
                member = await self.bot.get_chat_member(group_id, user_id)
                if member.status in ['member', 'administrator', 'creator']:
                    return True
            except Exception as e:
                logger.debug(f"Ошибка проверки группы {group_id} для пользователя {user_id}: {e}")
        return False

    def register_handlers(self):
        """Регистрируем обработчики команд и сообщений"""
        self.router.message.register(self.start_handler, Command("start"))
        self.router.message.register(self.help_handler, Command("help"))

    async def start_handler(self, message: types.Message):
        """Обработчик команды /start"""
        await message.answer(
            "Привет! Используй команды, чтобы начать работу.",
            reply_markup=self.get_main_keyboard()
        )

    async def help_handler(self, message: types.Message):
        """Обработчик команды /help"""
        await message.answer(
            "Это бот для поиска файлов. Команды:\n/start - Начать\n/help - Помощь",
            reply_markup=self.get_main_keyboard()
        )

    def get_main_keyboard(self):
        """Создает главную клавиатуру"""
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="🔍 Начать поиск")],
                [KeyboardButton(text="❓ Помощь")]
            ],
            resize_keyboard=True,
        )
        return keyboard

    async def run(self):
        """Запускает бота"""
        await self.dp.start_polling(self.bot)


async def main():
    bot = SearchBot()
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
