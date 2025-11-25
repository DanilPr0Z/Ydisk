import requests
import os
import html
import asyncio
import aiohttp
import time
import logging
from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.enums import ParseMode, ChatType
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramRetryAfter, TelegramNetworkError
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
        self.dp.include_router(self.router)

        # Создаем aiohttp сессию для асинхронных запросов
        self.session = None

        # Кэш для частых запросов
        self.search_cache = {}
        self.cache_timeout = 300  # 5 минут

        # Кэш проверок доступа
        self.access_cache = {}
        self.access_cache_timeout = 600  # 10 минут

        # Ограничитель скорости отправки сообщений
        self.rate_limit_delay = 0.05

        # Регистрируем обработчики
        self.register_handlers()

    def register_handlers(self):
        """Регистрирует все обработчики"""
        # Обработчики команд
        self.router.message.register(self.start_handler, Command("start"))
        self.router.message.register(self.search_handler, Command("search"))
        self.router.message.register(self.help_handler, Command("help"))

        # Обработчик Reply-кнопок
        self.router.message.register(
            self.reply_buttons_handler,
            F.text.in_(["🔍 Начать поиск", "🏠 Главное меню", "❓ Помощь", "ℹ️ О боте"])
        )

        # Обработчик обычных сообщений для поиска
        self.router.message.register(
            self.text_handler,
            F.text & ~F.text.startswith('/')
        )

        # Callback обработчики
        self.router.callback_query.register(self.file_callback_handler, F.data.startswith('file_'))
        self.router.callback_query.register(self.more_callback_handler, F.data.startswith('more_'))

    async def check_access(self, user_id: int) -> bool:
        """Быстрая проверка доступа пользователя с кэшированием"""
        # Если группы не указаны, доступ разрешен всем
        if not self.allowed_group_ids:
            return True

        # Проверяем кэш
        cache_key = f"access_{user_id}"
        current_time = time.time()

        if cache_key in self.access_cache:
            cache_data = self.access_cache[cache_key]
            if current_time - cache_data['timestamp'] < self.access_cache_timeout:
                return cache_data['has_access']

        # Проверяем доступ
        has_access = False
        for group_id in self.allowed_group_ids:
            try:
                # Быстрая проверка без детальной информации
                member = await self.bot.get_chat_member(chat_id=group_id, user_id=user_id)
                if member.status in ['member', 'administrator', 'creator']:
                    has_access = True
                    break
            except Exception as e:
                logger.debug(f"Ошибка проверки доступа для {user_id} в группе {group_id}: {e}")
                continue

        # Сохраняем в кэш
        self.access_cache[cache_key] = {
            'has_access': has_access,
            'timestamp': current_time
        }

        return has_access

    async def check_access_decorator(self, handler, event, data):
        """Декоратор для проверки доступа"""
        if isinstance(event, (types.Message, types.CallbackQuery)):
            user_id = event.from_user.id

            # Проверяем доступ
            has_access = await self.check_access(user_id)

            if not has_access:
                if isinstance(event, types.Message):
                    await self.send_access_denied(event)
                elif isinstance(event, types.CallbackQuery):
                    await event.answer("❌ Доступ запрещен", show_alert=True)
                return

        return await handler(event, data)

    async def start_handler(self, message: types.Message):
        """Обработчик команды /start"""
        logger.info(f"🔹 /start от пользователя {message.from_user.id}")

        # Проверяем доступ
        has_access = await self.check_access(message.from_user.id)
        if not has_access:
            await self.send_access_denied(message)
            return

        welcome_text = """
🔍 <b>Бот для поиска файлов в Cascate Cloud</b>

<b>Доступные команды:</b>
/search &lt;запрос&gt; - поиск файлов
&lt;текст&gt; - быстрый поиск по тексту

<b>Примеры:</b>
<code>/search Распашные двери</code>
<code>Распашные двери ALTA</code>
<code>инструкция установки</code>

💡 <i>Бот работает только в личных сообщениях</i>
        """

        try:
            await message.answer(
                welcome_text,
                parse_mode=ParseMode.HTML,
                reply_markup=self.get_main_keyboard()
            )
        except Exception as e:
            logger.error(f"Ошибка при отправке стартового сообщения: {e}")
            await message.answer("❌ Произошла ошибка. Попробуйте еще раз.")

    async def search_handler(self, message: types.Message, state: FSMContext):
        """Обработчик команды /search"""
        logger.info(f"🔹 /search от пользователя {message.from_user.id}")

        # Проверяем доступ
        has_access = await self.check_access(message.from_user.id)
        if not has_access:
            await self.send_access_denied(message)
            return

        query = message.text.replace('/search', '').strip()

        if not query:
            search_help_text = """
🔍 <b>Поиск файлов</b>

Используйте команду:
<code>/search запрос</code>

<b>Пример:</b>
<code>/search двери ALTA PRO</code>

Или просто введите запрос без команды.
            """

            try:
                await message.answer(
                    search_help_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=self.get_search_keyboard()
                )
            except Exception as e:
                logger.error(f"Ошибка при отправке подсказки поиска: {e}")
            return

        await self.perform_search(message, query, state)

    async def help_handler(self, message: types.Message):
        """Обработчик команды /help"""
        logger.info(f"🔹 /help от пользователя {message.from_user.id}")

        # Проверяем доступ
        has_access = await self.check_access(message.from_user.id)
        if not has_access:
            await self.send_access_denied(message)
            return

        help_text = """
<b>📖 Помощь по использованию бота</b>

<b>Основные команды:</b>
• <code>/start</code> - запустить бота
• <code>/search &lt;запрос&gt;</code> - поиск файлов
• <code>/help</code> - эта справка

<b>Быстрый поиск:</b>
Просто отправьте любой текст - бот выполнит поиск автоматически.

<b>Примеры запросов:</b>
<code>двери ALTA PRO</code>
<code>инструкция по установке</code>
<code>чертежи фасадов</code>

<b>Навигация:</b>
• Используйте кнопки "Показать еще" для просмотра всех результатов
• Нажмите на номер файла для получения ссылок
        """

        try:
            await message.answer(
                help_text,
                parse_mode=ParseMode.HTML,
                reply_markup=self.get_help_keyboard()
            )
        except Exception as e:
            logger.error(f"Ошибка при отправке справки: {e}")

    async def reply_buttons_handler(self, message: types.Message):
        """Обработчик Reply-кнопок"""
        logger.info(f"🔹 Кнопка '{message.text}' от {message.from_user.id}")

        # Проверяем доступ
        has_access = await self.check_access(message.from_user.id)
        if not has_access:
            await self.send_access_denied(message)
            return

        text = message.text

        try:
            if text == "🔍 Начать поиск":
                search_help_text = """
🔍 <b>Режим поиска</b>

Введите поисковый запрос для поиска файлов:

<b>Примеры:</b>
<code>двери ALTA PRO</code>
<code>инструкция по установке</code>
<code>чертежи фасадов</code>

💡 <i>Просто введите запрос и нажмите отправить</i>
                """

                await message.answer(
                    search_help_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=self.get_search_keyboard()
                )

            elif text == "🏠 Главное меню":
                await self.start_handler(message)

            elif text == "❓ Помощь":
                await self.help_handler(message)

            elif text == "ℹ️ О боте":
                about_text = """
🤖 <b>Cascate Cloud Search Bot</b>

<b>О боте:</b>
Этот бот помогает искать файлы в облачном хранилище Cascate Cloud.

<b>Возможности:</b>
• 🔍 Быстрый поиск по названиям файлов
• 📁 Просмотр структуры каталогов  
• 🌐 Прямые ссылки на Яндекс.Диск
• 📥 Возможность скачивания файлов

💡 <i>Для начала работы нажмите "Начать поиск"</i>
                """

                await message.answer(
                    about_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=self.get_main_keyboard()
                )
        except Exception as e:
            logger.error(f"Ошибка при обработке кнопки: {e}")

    async def text_handler(self, message: types.Message, state: FSMContext):
        """Обработчик обычных сообщений для поиска"""
        logger.info(f"🔹 Сообщение от {message.from_user.id}: '{message.text}'")

        # Проверяем доступ
        has_access = await self.check_access(message.from_user.id)
        if not has_access:
            await self.send_access_denied(message)
            return

        query = message.text.strip()

        if not query:
            return

        try:
            await self.bot.send_chat_action(message.chat.id, "typing")
        except Exception as e:
            logger.error(f"Ошибка отправки действия: {e}")

        await self.perform_search(message, query, state)

    async def send_access_denied(self, message: types.Message):
        """Отправляет сообщение о запрете доступа"""
        try:
            await message.answer(
                "❌ <b>Доступ запрещен</b>\n\n"
                "Этот бот доступен только для участников разрешенных групп.\n"
                "Пожалуйста, вступите в одну из групп чтобы использовать бота.",
                parse_mode=ParseMode.HTML,
                reply_markup=ReplyKeyboardRemove()
            )
        except Exception as e:
            logger.error(f"Ошибка при отправке сообщения о доступе: {e}")

    def get_main_keyboard(self):
        """Создает главную клавиатуру"""
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="🔍 Начать поиск")],
                [KeyboardButton(text="❓ Помощь"), KeyboardButton(text="ℹ️ О боте")]
            ],
            resize_keyboard=True,
            input_field_placeholder="Выберите действие..."
        )
        return keyboard

    def get_search_keyboard(self):
        """Создает клавиатуру для поиска"""
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="🏠 Главное меню")],
                [KeyboardButton(text="❓ Помощь")]
            ],
            resize_keyboard=True,
            input_field_placeholder="Введите запрос..."
        )
        return keyboard

    def get_help_keyboard(self):
        """Создает клавиатуру для помощи"""
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="🔍 Начать поиск")],
                [KeyboardButton(text="🏠 Главное меню")]
            ],
            resize_keyboard=True
        )
        return keyboard

    async def setup_commands(self):
        """Устанавливает команды бота"""
        commands = [
            types.BotCommand(command="/start", description="Запустить бота"),
            types.BotCommand(command="/search", description="Поиск файлов"),
            types.BotCommand(command="/help", description="Помощь"),
        ]
        await self.bot.set_my_commands(commands)

    async def send_message_safe(self, chat_id, text, **kwargs):
        """Безопасная отправка сообщения"""
        try:
            await asyncio.sleep(self.rate_limit_delay)
            return await self.bot.send_message(chat_id=chat_id, text=text, **kwargs)
        except TelegramRetryAfter as e:
            logger.warning(f"⚠️ Rate limit, ждем {e.retry_after}s")
            await asyncio.sleep(e.retry_after)
            return await self.send_message_safe(chat_id, text, **kwargs)
        except Exception as e:
            logger.error(f"❌ Ошибка отправки: {e}")
            return None

    async def perform_search(self, message: types.Message, query: str, state: FSMContext):
        """Выполняет поиск"""
        start_time = time.time()
        progress_msg = None

        try:
            logger.info(f"🔍 Поиск: '{query}'")

            progress_msg = await self.send_message_safe(
                chat_id=message.chat.id,
                text=f"🔍 Ищу: <b>{html.escape(query)}</b>...",
                parse_mode=ParseMode.HTML
            )

            # Здесь будет реальный поиск через API
            data = await self.search_files_api(query)

            execution_time = time.time() - start_time
            logger.info(f"✅ Поиск выполнен за {execution_time:.2f}с, найдено: {data.get('results_count', 0)}")

            if data.get('results_count', 0) == 0:
                if progress_msg:
                    await progress_msg.edit_text(
                        f"❌ По запросу '<b>{html.escape(query)}</b>' ничего не найдено\n\n"
                        f"💡 <i>Попробуйте уточнить запрос</i>",
                        parse_mode=ParseMode.HTML
                    )
                return

            if progress_msg:
                await progress_msg.delete()

            await self.send_results(
                chat_id=message.chat.id,
                results=data['results'],
                query=query,
                state=state
            )

        except Exception as e:
            logger.error(f"❌ Ошибка при поиске: {e}")
            if progress_msg:
                await progress_msg.edit_text(
                    "❌ <b>Произошла ошибка при поиске</b>\n\nПопробуйте позже",
                    parse_mode=ParseMode.HTML
                )

    async def search_files_api(self, query: str):
        """Поиск файлов через API"""
        # Временная заглушка для теста
        return {
            'results_count': 2,
            'results': [
                {
                    'name': 'Тестовый файл 1.pdf',
                    'path': '/документы/тест',
                    'public_link': 'https://yandex.ru',
                    'download_link': 'https://yandex.ru/download'
                },
                {
                    'name': 'Тестовый файл 2.docx',
                    'path': '/документы/тест',
                    'public_link': 'https://yandex.ru',
                    'download_link': 'https://yandex.ru/download'
                }
            ]
        }

    async def send_results(self, chat_id, results, query, state, page=0):
        """Отправляет результаты поиска"""
        try:
            page_size = 5
            start_idx = page * page_size
            end_idx = start_idx + page_size
            page_results = results[start_idx:end_idx]

            total_files = len(results)
            total_pages = (total_files + page_size - 1) // page_size

            await state.update_data(
                last_results=results,
                current_page=page,
                current_query=query
            )

            # Заголовок
            header_text = f"✅ Найдено <b>{total_files}</b> файлов по запросу '<b>{html.escape(query)}</b>':\n\n"
            await self.send_message_safe(chat_id, header_text, parse_mode=ParseMode.HTML)

            # Файлы
            for i, result in enumerate(page_results, start=start_idx + 1):
                name = html.escape(result['name'])
                path = html.escape(result['path'])

                file_text = f"📄 <b>{name}</b>\n📁 <i>Путь:</i> {path}"

                builder = InlineKeyboardBuilder()
                builder.row(InlineKeyboardButton(
                    text="📋 Получить ссылки",
                    callback_data=f"file_{i - 1}"
                ))

                await self.send_message_safe(
                    chat_id=chat_id,
                    text=file_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=builder.as_markup(),
                    disable_web_page_preview=True
                )

            # Навигация
            if total_pages > 1:
                nav_text = f"📄 Страница {page + 1} из {total_pages}"
                nav_builder = InlineKeyboardBuilder()

                if page > 0:
                    nav_builder.row(InlineKeyboardButton(
                        text="⬅️ Назад",
                        callback_data=f"more_{page - 1}"
                    ))

                if end_idx < total_files:
                    if page > 0:
                        nav_builder.add(InlineKeyboardButton(
                            text="➡️ Вперед",
                            callback_data=f"more_{page + 1}"
                        ))
                    else:
                        nav_builder.row(InlineKeyboardButton(
                            text="➡️ Вперед",
                            callback_data=f"more_{page + 1}"
                        ))

                await self.send_message_safe(
                    chat_id=chat_id,
                    text=nav_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=nav_builder.as_markup()
                )

        except Exception as e:
            logger.error(f"Ошибка при отправке результатов: {e}")

    async def file_callback_handler(self, callback_query: types.CallbackQuery, state: FSMContext):
        """Обработчик кнопок файлов"""
        try:
            # Проверяем доступ
            has_access = await self.check_access(callback_query.from_user.id)
            if not has_access:
                await callback_query.answer("❌ Доступ запрещен", show_alert=True)
                return

            file_index = int(callback_query.data.split('_')[1])
            user_data = await state.get_data()
            results = user_data.get('last_results', [])

            if file_index < len(results):
                file_info = results[file_index]
                name = html.escape(file_info['name'])
                path = html.escape(file_info['path'])

                file_text = f"📄 <b>{name}</b>\n\n📁 <b>Путь:</b> {path}"

                builder = InlineKeyboardBuilder()
                if file_info.get('public_link'):
                    builder.row(InlineKeyboardButton(
                        text="🌐 Открыть в Яндекс.Диске",
                        url=file_info['public_link']
                    ))
                if file_info.get('download_link'):
                    builder.row(InlineKeyboardButton(
                        text="📥 Скачать файл",
                        url=file_info['download_link']
                    ))

                await callback_query.message.edit_text(
                    file_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=builder.as_markup(),
                    disable_web_page_preview=True
                )

            await callback_query.answer()
        except Exception as e:
            logger.error(f"Ошибка callback: {e}")
            await callback_query.answer("❌ Ошибка")

    async def more_callback_handler(self, callback_query: types.CallbackQuery, state: FSMContext):
        """Обработчик пагинации"""
        try:
            # Проверяем доступ
            has_access = await self.check_access(callback_query.from_user.id)
            if not has_access:
                await callback_query.answer("❌ Доступ запрещен", show_alert=True)
                return

            page = int(callback_query.data.split('_')[1])
            user_data = await state.get_data()
            results = user_data.get('last_results', [])
            query = user_data.get('current_query', '')

            await callback_query.answer("⏳ Загружаем...")
            await callback_query.message.delete()

            await self.send_results(
                chat_id=callback_query.message.chat.id,
                results=results,
                query=query,
                state=state,
                page=page
            )

        except Exception as e:
            logger.error(f"Ошибка пагинации: {e}")
            await callback_query.answer("❌ Ошибка")

    async def get_session(self):
        """Создает aiohttp сессию"""
        if self.session is None:
            self.session = aiohttp.ClientSession()
        return self.session

    async def close_session(self):
        """Закрывает сессию"""
        if self.session:
            await self.session.close()

    async def run(self):
        """Запускает бота"""
        logger.info("🤖 Запуск бота...")

        await self.setup_commands()

        try:
            me = await self.bot.get_me()
            logger.info(f"✅ Бот @{me.username} запущен")

            await self.dp.start_polling(self.bot, skip_updates=True)
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
        finally:
            await self.close_session()

