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
        self.allowed_group_ids = [group_id.strip() for group_id in allowed_groups.split(',') if group_id.strip()]

        if not self.token:
            raise ValueError("TELEGRAM_BOT_TOKEN не установлен")

        # Создаем бота БЕЗ DefaultBotProperties (для совместимости)
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

        # Ограничитель скорости отправки сообщений
        self.rate_limit_delay = 0.1  # 0.1 секунд между сообщениями

        # Регистрируем обработчики в ПРАВИЛЬНОМ порядке
        self.router.message.register(self.start, Command("start"), F.chat.type == ChatType.PRIVATE)
        self.router.message.register(self.search_command, Command("search"), F.chat.type == ChatType.PRIVATE)
        self.router.message.register(self.help_command, Command("help"), F.chat.type == ChatType.PRIVATE)

        # Обработчик Reply-кнопок с более строгим фильтром
        self.router.message.register(
            self.handle_reply_buttons,
            F.chat.type == ChatType.PRIVATE,
            F.text.in_(["🔍 Начать поиск", "🏠 Главное меню", "❓ Помощь", "ℹ️ О боте"])
        )

        # Обработчик обычных сообщений для поиска - ДОЛЖЕН БЫТЬ ПОСЛЕДНИМ
        self.router.message.register(
            self.handle_search_query,
            F.chat.type == ChatType.PRIVATE,
            F.text
        )

        # Callback обработчики
        self.router.callback_query.register(self.button_callback, F.data.startswith('file_'))
        self.router.callback_query.register(self.more_callback, F.data.startswith('more_'))

    def get_main_menu_keyboard(self):
        """Создает Reply-клавиатуру для главного меню"""
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="🔍 Начать поиск")],
                [KeyboardButton(text="❓ Помощь"), KeyboardButton(text="ℹ️ О боте")]
            ],
            resize_keyboard=True,
            input_field_placeholder="Выберите действие или введите запрос..."
        )
        return keyboard

    def get_search_keyboard(self):
        """Создает Reply-клавиатуру для поиска"""
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="🏠 Главное меню")],
                [KeyboardButton(text="❓ Помощь")]
            ],
            resize_keyboard=True,
            input_field_placeholder="Введите поисковый запрос..."
        )
        return keyboard

    def get_help_keyboard(self):
        """Создает Reply-клавиатуру для помощи"""
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="🔍 Начать поиск")],
                [KeyboardButton(text="🏠 Главное меню")]
            ],
            resize_keyboard=True,
            input_field_placeholder="Выберите действие..."
        )
        return keyboard

    async def setup_bot_commands(self):
        """Устанавливает команды бота в меню"""
        commands = [
            types.BotCommand(command="/start", description="Запустить бота"),
            types.BotCommand(command="/search", description="Поиск файлов"),
            types.BotCommand(command="/help", description="Помощь"),
        ]
        await self.bot.set_my_commands(commands)

    async def is_user_member_of_any_group(self, user_id: int) -> bool:
        """Проверяет, является ли пользователь участником любой из разрешенных групп"""
        if not self.allowed_group_ids:
            return True  # Если группы не указаны, доступ разрешен всем

        for group_id in self.allowed_group_ids:
            try:
                member = await self.bot.get_chat_member(chat_id=group_id, user_id=user_id)
                if member.status in ['member', 'administrator', 'creator']:
                    return True
            except Exception:
                continue

        return False

    async def check_access(self, message: types.Message) -> bool:
        """Проверяет доступ и отправляет сообщение если доступ запрещен"""
        if not await self.is_user_member_of_any_group(message.from_user.id):
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
            return False
        return True

    async def start(self, message: types.Message):
        """Обработчик команды /start (только в ЛС)"""
        if not await self.check_access(message):
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
                reply_markup=self.get_main_menu_keyboard()
            )
        except Exception as e:
            logger.error(f"Ошибка при отправке стартового сообщения: {e}")

    async def help_command(self, message: types.Message):
        """Обработчик команды /help"""
        if not await self.check_access(message):
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

    async def handle_reply_buttons(self, message: types.Message):
        """Обработчик Reply-кнопок"""
        if not await self.check_access(message):
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
                welcome_text = """
🏠 <b>Главное меню</b>

Выберите действие:
                """

                await message.answer(
                    welcome_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=self.get_main_menu_keyboard()
                )

            elif text == "❓ Помощь":
                await self.help_command(message)

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
                    reply_markup=self.get_main_menu_keyboard()
                )
        except Exception as e:
            logger.error(f"Ошибка при обработке reply-кнопки: {e}")

    async def search_command(self, message: types.Message, state: FSMContext):
        """Обработчик команды /search (только в ЛС)"""
        if not await self.check_access(message):
            return

        query = message.text.replace('/search', '').strip()

        if not query:
            # Показываем подсказку по поиску
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

    async def handle_search_query(self, message: types.Message, state: FSMContext):
        """Обработчик обычных сообщений для поиска"""
        if not await self.check_access(message):
            return

        query = message.text.strip()

        logger.info(f"🔍 Получен поисковый запрос: '{query}'")

        # Отправляем действие "печатает" чтобы избежать таймаута
        try:
            await self.bot.send_chat_action(message.chat.id, "typing")
        except Exception as e:
            logger.error(f"Ошибка отправки действия: {e}")

        # Выполняем поиск
        await self.perform_search(message, query, state)

    async def send_single_message(self, chat_id, text, **kwargs):
        """Отправляет одно сообщение с обработкой ошибок и задержкой"""
        try:
            await asyncio.sleep(self.rate_limit_delay)  # Задержка 0.1 секунды между сообщениями
            return await self.bot.send_message(chat_id=chat_id, text=text, **kwargs)
        except TelegramRetryAfter as e:
            logger.warning(f"⚠️ Rate limit, waiting {e.retry_after}s")
            await asyncio.sleep(e.retry_after)
            return await self.send_single_message(chat_id, text, **kwargs)
        except Exception as e:
            logger.error(f"❌ Ошибка отправки сообщения: {e}")
            return None

    async def send_results_page(self, chat_id, all_results, query, state, page=0, previous_messages=None):
        """Отправляет одну страницу результатов (10 файлов) с ограничением скорости"""
        try:
            page_size = 10
            start_idx = page * page_size
            end_idx = start_idx + page_size
            page_results = all_results[start_idx:end_idx]

            total_files = len(all_results)
            total_pages = (total_files + page_size - 1) // page_size

            await state.update_data(
                last_results=all_results,
                current_page=page,
                current_query=query
            )

            if previous_messages:
                asyncio.create_task(
                    self.delete_messages_batch(chat_id, previous_messages)
                )

            current_messages = []

            # Отправляем заголовок
            if page == 0:
                header_text = f"✅ Найдено <b>{total_files}</b> файлов по запросу '<b>{html.escape(query)}</b>':\n\n"
            else:
                header_text = f"📄 <b>Страница {page + 1}</b> | Найдено <b>{total_files}</b> файлов\n"

            header_msg = await self.send_single_message(
                chat_id=chat_id,
                text=header_text,
                parse_mode=ParseMode.HTML
            )
            if header_msg:
                current_messages.append(header_msg.message_id)

            # Отправляем файлы по одному с задержкой 0.1 секунды
            for i, result in enumerate(page_results, start=start_idx + 1):
                name = html.escape(result['name'])
                path = html.escape(result['path'])

                file_text = f"""
📄 <b>{name}</b>

📁 <i>Путь:</i> {path}
                """

                builder = InlineKeyboardBuilder()
                builder.row(InlineKeyboardButton(
                    text="📋 Получить ссылки на файл",
                    callback_data=f"file_{i - 1}"
                ))

                file_msg = await self.send_single_message(
                    chat_id=chat_id,
                    text=file_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=builder.as_markup(),
                    disable_web_page_preview=True
                )

                if file_msg:
                    current_messages.append(file_msg.message_id)
                else:
                    logger.warning(f"⚠️ Не удалось отправить файл {i}")

            # Отправляем навигацию
            nav_text = f"⚡ <b>Страница {page + 1} из {total_pages}</b> | <i>Файлы {start_idx + 1}-{min(end_idx, total_files)} из {total_files}</i>"

            nav_builder = InlineKeyboardBuilder()

            if end_idx < total_files:
                nav_builder.row(InlineKeyboardButton(
                    text="➡️ Показать еще",
                    callback_data=f"more_{page + 1}"
                ))

            if page > 0:
                nav_builder.row(InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=f"more_{page - 1}"
                ))

            nav_msg = await self.send_single_message(
                chat_id=chat_id,
                text=nav_text,
                parse_mode=ParseMode.HTML,
                reply_markup=nav_builder.as_markup() if nav_builder.buttons else None
            )
            if nav_msg:
                current_messages.append(nav_msg.message_id)

            # После показа результатов возвращаем меню поиска
            menu_msg = await self.send_single_message(
                chat_id=chat_id,
                text="💡 <b>Что дальше?</b>\n\nВведите новый запрос для поиска или используйте кнопки меню:",
                parse_mode=ParseMode.HTML,
                reply_markup=self.get_search_keyboard()
            )
            if menu_msg:
                current_messages.append(menu_msg.message_id)

            await state.update_data(current_messages=current_messages)
            return current_messages

        except Exception as e:
            logger.error(f"Ошибка при отправке страницы результатов: {e}")
            return []

    async def delete_messages_batch(self, chat_id, message_ids):
        """Быстрое удаление сообщений пачками"""
        if not message_ids:
            return

        delete_tasks = []
        for msg_id in message_ids:
            try:
                task = asyncio.create_task(
                    self.bot.delete_message(chat_id=chat_id, message_id=msg_id)
                )
                delete_tasks.append(task)
            except Exception:
                continue

        if delete_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*delete_tasks, return_exceptions=True),
                    timeout=5.0
                )
            except asyncio.TimeoutError:
                pass
            except Exception:
                pass

    async def execute_search_with_timeout(self, query: str, timeout: int = 55):
        """Выполнение поиска с ограничением по времени"""
        try:
            # Проверяем кэш
            cache_key = query.lower().strip()
            if cache_key in self.search_cache:
                cache_data = self.search_cache[cache_key]
                if time.time() - cache_data['timestamp'] < self.cache_timeout:
                    logger.info(f"📦 Используем кэш для запроса: {query}")
                    return cache_data['results']

            # Выполняем поиск с таймаутом
            return await asyncio.wait_for(self.search_files_api(query), timeout=timeout)

        except asyncio.TimeoutError:
            # Очищаем кэш при таймауте
            if cache_key in self.search_cache:
                del self.search_cache[cache_key]
            raise

    async def search_files_api(self, query: str):
        """Поиск файлов через API"""
        # Проверяем кэш еще раз (на случай конкурентных запросов)
        cache_key = query.lower().strip()
        if cache_key in self.search_cache:
            cache_data = self.search_cache[cache_key]
            if time.time() - cache_data['timestamp'] < self.cache_timeout:
                return cache_data['results']

        session = await self.get_session()
        search_url = f"{self.api_url}?q={query}"
        logger.info(f"🌐 Отправляем запрос к API: {search_url}")

        try:
            async with session.get(search_url) as response:
                logger.info(f"🌐 Получен ответ: {response.status}")

                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"❌ Ошибка API: {response.status} - {error_text}")
                    return {'results_count': 0, 'results': []}

                data = await response.json()
                logger.info(f"📊 Получено результатов: {data.get('results_count', 0)}")

                # Сохраняем в кэш
                self.search_cache[cache_key] = {
                    'results': data,
                    'timestamp': time.time()
                }

                return data

        except asyncio.TimeoutError:
            logger.error(f"⏰ Таймаут HTTP запроса для: {query}")
            return {'results_count': 0, 'results': []}
        except Exception as e:
            logger.error(f"❌ Ошибка при поиске файлов: {e}")
            return {'results_count': 0, 'results': []}

    async def perform_search(self, message: types.Message, query: str, state: FSMContext):
        """Выполняет поиск через API асинхронно с обработкой ошибок"""
        start_time = time.time()
        progress_msg = None

        try:
            logger.info(f"🔍 Начинаем поиск: '{query}'")

            # Отправляем сообщение о начале поиска
            progress_msg = await self.send_single_message(
                chat_id=message.chat.id,
                text=f"🔍 Ищу: <b>{html.escape(query)}</b>...",
                parse_mode=ParseMode.HTML
            )

            # Выполняем поиск с таймаутом
            data = await self.execute_search_with_timeout(query, timeout=55)

            execution_time = time.time() - start_time
            logger.info(f"✅ Поиск '{query}' выполнен за {execution_time:.2f}с, найдено: {data.get('results_count', 0)}")

            if data.get('results_count', 0) == 0:
                if progress_msg:
                    await progress_msg.edit_text(
                        f"❌ По запросу '<b>{html.escape(query)}</b>' ничего не найдено\n\n"
                        f"💡 <i>Попробуйте:</i>\n"
                        f"• Уточнить запрос\n"
                        f"• Использовать другие ключевые слова\n"
                        f"• Проверить орфографию",
                        parse_mode=ParseMode.HTML
                    )
                return

            if progress_msg:
                await progress_msg.delete()

            await self.send_results_page(
                chat_id=message.chat.id,
                all_results=data['results'],
                query=query,
                state=state,
                page=0
            )

        except asyncio.TimeoutError:
            logger.error(f"⏰ Таймаут при поиске: '{query}'")
            if progress_msg:
                await progress_msg.edit_text(
                    f"⏰ <b>Поиск занял слишком много времени</b>\n\n"
                    f"Запрос: <b>{html.escape(query)}</b>\n\n"
                    f"💡 <i>Попробуйте:</i>\n"
                    f"• Упростить запрос\n"
                    f"• Разбить на несколько слов\n"
                    f"• Повторить позже",
                    parse_mode=ParseMode.HTML
                )

        except TelegramRetryAfter as e:
            logger.warning(f"⚠️ Telegram RetryAfter: {e.retry_after}")
            await asyncio.sleep(e.retry_after)
            # Повторяем поиск после ожидания
            await self.perform_search(message, query, state)

        except TelegramNetworkError as e:
            logger.error(f"❌ Сетевая ошибка Telegram: {e}")
            if progress_msg:
                await progress_msg.edit_text(
                    f"❌ <b>Сетевая ошибка</b>\n\n"
                    f"Не удалось отправить результаты поиска.\n"
                    f"Попробуйте повторить запрос через минуту.",
                    parse_mode=ParseMode.HTML
                )

        except Exception as e:
            logger.error(f"❌ Неожиданная ошибка при поиске '{query}': {e}")
            if progress_msg:
                await progress_msg.edit_text(
                    f"❌ <b>Произошла ошибка при поиске</b>\n\n"
                    f"Запрос: <b>{html.escape(query)}</b>\n\n"
                    f"💡 <i>Попробуйте другой запрос или повторите позже</i>",
                    parse_mode=ParseMode.HTML
                )

    async def button_callback(self, callback_query: types.CallbackQuery, state: FSMContext):
        """Обработчик нажатий на кнопки файлов"""
        if not await self.is_user_member_of_any_group(callback_query.from_user.id):
            await callback_query.answer("❌ Доступ запрещен. Вступите в одну из разрешенных групп.", show_alert=True)
            return

        try:
            file_index = int(callback_query.data.split('_')[1])

            user_data = await state.get_data()
            results = user_data.get('last_results', [])

            if file_index < len(results):
                file_info = results[file_index]

                name = html.escape(file_info['name'])
                path = html.escape(file_info['path'])

                file_text = f"""
📄 <b>{name}</b>

📁 <b>Путь:</b> {path}
                """

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
            logger.error(f"Callback error: {e}")
            await callback_query.answer("❌ Ошибка при получении информации о файле")

    async def more_callback(self, callback_query: types.CallbackQuery, state: FSMContext):
        """Обработчик кнопки навигации"""
        if not await self.is_user_member_of_any_group(callback_query.from_user.id):
            await callback_query.answer("❌ Доступ запрещен. Вступите в одну из разрешенных групп.", show_alert=True)
            return

        try:
            page = int(callback_query.data.split('_')[1])

            user_data = await state.get_data()
            results = user_data.get('last_results', [])
            query = user_data.get('current_query', '')
            previous_messages = user_data.get('current_messages', [])

            if not results:
                await callback_query.answer("❌ Результаты поиска устарели")
                return

            await callback_query.answer("⏳ Загружаем...")

            await self.send_results_page(
                chat_id=callback_query.message.chat.id,
                all_results=results,
                query=query,
                state=state,
                page=page,
                previous_messages=previous_messages
            )

        except Exception as e:
            logger.error(f"More callback error: {e}")
            await callback_query.answer("❌ Ошибка при загрузке файлов")

    async def get_session(self):
        """Создает aiohttp сессию при необходимости"""
        if self.session is None:
            timeout = aiohttp.ClientTimeout(total=30)  # Таймаут для HTTP запросов
            self.session = aiohttp.ClientSession(timeout=timeout)
        return self.session

    async def close_session(self):
        """Закрывает aiohttp сессию"""
        if self.session:
            await self.session.close()
            self.session = None

    async def run(self):
        """Запускает бота с улучшенной обработкой ошибок"""
        logger.info("🤖 Бот запускается...")

        # Устанавливаем команды меню
        await self.setup_bot_commands()

        # Закрываем возможные старые сессии
        await self.close_session()

        try:
            # Проверяем подключение к Telegram API
            me = await self.bot.get_me()
            logger.info(f"✅ Бот @{me.username} успешно подключен")

            # Запускаем polling с обработкой конфликтов
            await self.dp.start_polling(
                self.bot,
                allowed_updates=["message", "callback_query"],
                skip_updates=True
            )
        except Exception as e:
            logger.error(f"❌ Ошибка запуска бота: {e}")
        finally:
            await self.close_session()

