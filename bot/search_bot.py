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

        # Кэш пользователей с доступом
        self.allowed_users_cache = set()
        self.cache_loaded = False

        # Ограничитель скорости отправки сообщений
        self.rate_limit_delay = 0.1

        # Регистрируем обработчики
        self.register_handlers()

    async def load_all_members_fast(self):
        """Быстрая загрузка всех участников групп"""
        if not self.allowed_group_ids:
            logger.info("✅ Группы не указаны, доступ разрешен всем")
            self.cache_loaded = True
            return

        logger.info(f"🚀 Начинаю загрузку участников из {len(self.allowed_group_ids)} групп...")
        print("🔄 ЗАГРУЗКА КЭША: Начинаю сбор пользователей из групп...")

        total_members = 0
        start_time = time.time()

        for group_id in self.allowed_group_ids:
            try:
                logger.info(f"📦 Загружаю пользователей из группы {group_id}...")
                print(f"📦 Обрабатываю группу {group_id}...")

                # Загружаем ВСЕХ участников группы
                members_count = 0
                try:
                    async for member in self.bot.get_chat_members(group_id):
                        if member.user.id not in self.allowed_users_cache:
                            self.allowed_users_cache.add(member.user.id)
                            total_members += 1
                            members_count += 1

                        # Пауза чтобы не превысить лимиты API
                        if members_count % 50 == 0:
                            await asyncio.sleep(0.1)

                    logger.info(f"👥 Группа {group_id}: {members_count} участников")
                    print(f"✅ Группа {group_id}: добавлено {members_count} участников")

                except Exception as e:
                    logger.warning(f"⚠️ Не удалось загрузить всех участников группы {group_id}: {e}")
                    print(f"⚠️ Группа {group_id}: загружаем только администраторов")

                    # Fallback: загружаем только администраторов
                    admins = await self.bot.get_chat_administrators(group_id)
                    admin_count = len(admins)

                    for admin in admins:
                        if admin.user.id not in self.allowed_users_cache:
                            self.allowed_users_cache.add(admin.user.id)
                            total_members += 1

                    logger.info(f"👥 Группа {group_id}: {admin_count} администраторов (fallback)")
                    print(f"✅ Группа {group_id}: добавлено {admin_count} администраторов")

                # Небольшая задержка между группами
                await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"❌ Ошибка загрузки группы {group_id}: {e}")
                print(f"❌ Ошибка загрузки группы {group_id}")
                continue

        loading_time = time.time() - start_time
        logger.info(f"✅ Загрузка завершена за {loading_time:.2f}с. Всего пользователей в кэше: {total_members}")
        print(f"🎉 КЭШ ЗАГРУЖЕН УСПЕШНО!")
        print(f"⏱ Время загрузки: {loading_time:.2f} секунд")
        print(f"👥 Пользователей в кэше: {total_members}")
        print(f"📊 Групп обработано: {len(self.allowed_group_ids)}")
        print("🤖 Бот готов к работе!")

        self.cache_loaded = True

    async def check_access(self, user_id: int) -> bool:
        """Проверяет доступ пользователя"""
        # Если группы не указаны, доступ разрешен всем
        if not self.allowed_group_ids:
            return True

        # Если кэш еще не загружен, загружаем его
        if not self.cache_loaded:
            logger.info("🔄 Кэш не загружен, загружаем...")
            await self.load_all_members_fast()

        # Быстрая проверка в памяти
        if user_id in self.allowed_users_cache:
            return True

        # Если пользователя нет в кэше, проверяем индивидуально и добавляем
        logger.info(f"🔍 Пользователь {user_id} не в кэше, проверяем индивидуально...")
        has_access = await self.check_single_user(user_id)

        if has_access:
            self.allowed_users_cache.add(user_id)
            logger.info(f"➕ Добавлен в кэш: {user_id}")

        return has_access

    async def check_single_user(self, user_id: int) -> bool:
        """Проверяет одного пользователя через API"""
        for group_id in self.allowed_group_ids:
            try:
                member = await self.bot.get_chat_member(chat_id=group_id, user_id=user_id)
                if member.status in ['member', 'administrator', 'creator']:
                    return True
            except Exception as e:
                logger.warning(f"Ошибка проверки пользователя {user_id}: {e}")
                continue
        return False

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

    async def send_single_message(self, chat_id, text, **kwargs):
        """Отправляет одно сообщение с обработкой ошибок и задержкой"""
        try:
            await asyncio.sleep(self.rate_limit_delay)
            return await self.bot.send_message(chat_id=chat_id, text=text, **kwargs)
        except TelegramRetryAfter as e:
            logger.warning(f"⚠️ Rate limit, waiting {e.retry_after}s")
            await asyncio.sleep(e.retry_after)
            return await self.send_single_message(chat_id, text, **kwargs)
        except Exception as e:
            logger.error(f"❌ Ошибка отправки сообщения: {e}")
            return None

    async def perform_search(self, message: types.Message, query: str, state: FSMContext):
        """Выполняет поиск через API"""
        start_time = time.time()
        progress_msg = None

        try:
            logger.info(f"🔍 Начинаем поиск: '{query}'")

            progress_msg = await self.send_single_message(
                chat_id=message.chat.id,
                text=f"🔍 Ищу: <b>{html.escape(query)}</b>...",
                parse_mode=ParseMode.HTML
            )

            data = await self.execute_search_with_timeout(query, timeout=55)

            execution_time = time.time() - start_time
            logger.info(f"✅ Поиск '{query}' выполнен за {execution_time:.2f}с, найдено: {data.get('results_count', 0)}")

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
                    f"Попробуйте упростить запрос",
                    parse_mode=ParseMode.HTML
                )

        except TelegramRetryAfter as e:
            logger.warning(f"⚠️ Telegram RetryAfter: {e.retry_after}")
            await asyncio.sleep(e.retry_after)
            await self.perform_search(message, query, state)

        except Exception as e:
            logger.error(f"❌ Ошибка при поиске '{query}': {e}")
            if progress_msg:
                await progress_msg.edit_text(
                    f"❌ <b>Произошла ошибка при поиске</b>\n\n"
                    f"Попробуйте позже",
                    parse_mode=ParseMode.HTML
                )

    async def execute_search_with_timeout(self, query: str, timeout: int = 55):
        """Выполнение поиска с ограничением по времени"""
        try:
            cache_key = query.lower().strip()
            if cache_key in self.search_cache:
                cache_data = self.search_cache[cache_key]
                if time.time() - cache_data['timestamp'] < self.cache_timeout:
                    logger.info(f"📦 Используем кэш для запроса: {query}")
                    return cache_data['results']

            return await asyncio.wait_for(self.search_files_api(query), timeout=timeout)

        except asyncio.TimeoutError:
            if cache_key in self.search_cache:
                del self.search_cache[cache_key]
            raise

    async def search_files_api(self, query: str):
        """Поиск файлов через API"""
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
                    return {'results_count': 0, 'results': []}

                data = await response.json()
                logger.info(f"📊 Получено результатов: {data.get('results_count', 0)}")

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

    async def send_results_page(self, chat_id, all_results, query, state, page=0, previous_messages=None):
        """Отправляет одну страницу результатов"""
        try:
            page_size = 10  # ВЕРНУЛ 10 ФАЙЛОВ НА СТРАНИЦЕ
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
                await asyncio.sleep(0.3)
                await self.delete_messages_batch(chat_id, previous_messages)

            current_messages = []

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
                await asyncio.sleep(0.2)
                task = asyncio.create_task(
                    self.bot.delete_message(chat_id=chat_id, message_id=msg_id)
                )
                delete_tasks.append(task)
            except Exception as e:
                continue

        if delete_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*delete_tasks, return_exceptions=True),
                    timeout=15.0
                )
            except (asyncio.TimeoutError, Exception):
                pass

    async def file_callback_handler(self, callback_query: types.CallbackQuery, state: FSMContext):
        """Обработчик нажатий на кнопки файлов"""
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
            logger.error(f"Callback error: {e}")
            await callback_query.answer("❌ Ошибка при обработке запроса")

    async def more_callback_handler(self, callback_query: types.CallbackQuery, state: FSMContext):
        """Обработчик кнопки навигации"""
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
            previous_messages = user_data.get('current_messages', [])

            if not results:
                await callback_query.answer("❌ Результаты устарели")
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
            await callback_query.answer("❌ Ошибка")

    async def get_session(self):
        """Создает aiohttp сессию"""
        if self.session is None:
            timeout = aiohttp.ClientTimeout(total=30)
            self.session = aiohttp.ClientSession(timeout=timeout)
        return self.session

    async def close_session(self):
        """Закрывает aiohttp сессию"""
        if self.session:
            await self.session.close()
            self.session = None

    async def run(self):
        """Запускает бота"""
        logger.info("🤖 Запуск бота...")
        print("🚀 ЗАПУСК БОТА...")
        print("🔄 Начинаю загрузку кэша пользователей...")

        # Предварительно загружаем кэш при старте
        await self.load_all_members_fast()

        await self.setup_commands()

        try:
            me = await self.bot.get_me()
            logger.info(f"✅ Бот @{me.username} запущен")
            print(f"✅ Бот @{me.username} успешно запущен и готов к работе!")

            await self.dp.start_polling(
                self.bot,
                allowed_updates=["message", "callback_query"],
                skip_updates=True
            )
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
            print(f"❌ Ошибка запуска бота: {e}")
        finally:
            await self.close_session()

