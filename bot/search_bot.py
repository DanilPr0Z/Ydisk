import requests
import os
import html
import asyncio
import aiohttp
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
from dotenv import load_dotenv

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

        self.bot = Bot(token=self.token)
        self.storage = MemoryStorage()
        self.dp = Dispatcher(storage=self.storage)
        self.router = Router()
        self.dp.include_router(self.router)

        # Создаем aiohttp сессию для асинхронных запросов
        self.session = None

        # Регистрируем обработчики ТОЛЬКО для приватных чатов
        self.router.message.register(self.start, Command("start"), F.chat.type == ChatType.PRIVATE)
        self.router.message.register(self.search_command, Command("search"), F.chat.type == ChatType.PRIVATE)
        self.router.message.register(self.help_command, Command("help"), F.chat.type == ChatType.PRIVATE)

        # Обработчик Reply-кнопок ДО обычных сообщений
        self.router.message.register(self.handle_reply_buttons, F.chat.type == ChatType.PRIVATE)

        # Обработчик обычных сообщений (поиск) - должен быть ПОСЛЕДНИМ
        self.router.message.register(self.handle_search_query, F.chat.type == ChatType.PRIVATE)

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
            await message.answer(
                "❌ <b>Доступ запрещен</b>\n\n"
                "Этот бот доступен только для участников разрешенных групп.\n"
                "Пожалуйста, вступите в одну из групп чтобы использовать бота.",
                parse_mode=ParseMode.HTML,
                reply_markup=ReplyKeyboardRemove()
            )
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

        await message.answer(
            welcome_text,
            parse_mode=ParseMode.HTML,
            reply_markup=self.get_main_menu_keyboard()
        )

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

        await message.answer(
            help_text,
            parse_mode=ParseMode.HTML,
            reply_markup=self.get_help_keyboard()
        )

    async def handle_reply_buttons(self, message: types.Message):
        """Обработчик Reply-кнопок"""
        if not await self.check_access(message):
            return

        text = message.text

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

            await message.answer(
                search_help_text,
                parse_mode=ParseMode.HTML,
                reply_markup=self.get_search_keyboard()
            )
            return

        await self.perform_search(message, query, state)

    async def handle_search_query(self, message: types.Message, state: FSMContext):
        """Обработчик обычных сообщений для поиска"""
        if not await self.check_access(message):
            return

        query = message.text.strip()

        # Пропускаем команды и кнопки меню (они уже обработаны выше)
        if (query.startswith('/') or
                query in ["🔍 Начать поиск", "🏠 Главное меню", "❓ Помощь", "ℹ️ О боте"]):
            return

        # Если это обычный текст - выполняем поиск
        await self.perform_search(message, query, state)

    def split_message(self, text, max_length=4000):
        """Разбивает длинное сообщение на части"""
        if len(text) <= max_length:
            return [text]

        parts = []
        while text:
            if len(text) <= max_length:
                parts.append(text)
                break

            split_pos = text.rfind('\n', 0, max_length)
            if split_pos == -1:
                split_pos = text.rfind(' ', 0, max_length)
            if split_pos == -1:
                split_pos = max_length

            parts.append(text[:split_pos])
            text = text[split_pos:].lstrip()

        return parts

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

    async def send_results_page(self, chat_id, all_results, query, state, page=0, previous_messages=None):
        """Отправляет одну страницу результатов (10 файлов)"""
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

        if page == 0:
            header_text = f"✅ Найдено <b>{total_files}</b> файлов по запросу '<b>{html.escape(query)}</b>':\n\n"
            header_msg = await self.bot.send_message(
                chat_id=chat_id,
                text=header_text,
                parse_mode=ParseMode.HTML
            )
            current_messages.append(header_msg.message_id)
        else:
            header_text = f"📄 <b>Страница {page + 1}</b> | Найдено <b>{total_files}</b> файлов\n"
            header_msg = await self.bot.send_message(
                chat_id=chat_id,
                text=header_text,
                parse_mode=ParseMode.HTML
            )
            current_messages.append(header_msg.message_id)

        send_tasks = []
        for i, result in enumerate(page_results, start=start_idx + 1):
            name = html.escape(result['name'])
            path = html.escape(result['path'])
            size = html.escape(result['size_formatted'])
            modified = html.escape(result['modified'][:10])

            file_text = f"""
📄 <b>{name}</b>

📁 <i>Путь:</i> {path}
            """

            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(
                text="📋 Получить ссылки на файл",
                callback_data=f"file_{i - 1}"
            ))

            task = asyncio.create_task(
                self.bot.send_message(
                    chat_id=chat_id,
                    text=file_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=builder.as_markup(),
                    disable_web_page_preview=True
                )
            )
            send_tasks.append((task, i))

        for task, file_num in send_tasks:
            try:
                file_msg = await task
                current_messages.append(file_msg.message_id)
            except Exception:
                pass

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

        nav_msg = await self.bot.send_message(
            chat_id=chat_id,
            text=nav_text,
            parse_mode=ParseMode.HTML,
            reply_markup=nav_builder.as_markup() if nav_builder.buttons else None
        )
        current_messages.append(nav_msg.message_id)

        # После показа результатов возвращаем меню поиска
        await self.bot.send_message(
            chat_id=chat_id,
            text="💡 <b>Что дальше?</b>\n\nВведите новый запрос для поиска или используйте кнопки меню:",
            parse_mode=ParseMode.HTML,
            reply_markup=self.get_search_keyboard()
        )

        await state.update_data(current_messages=current_messages)
        return current_messages

    async def perform_search(self, message: types.Message, query: str, state: FSMContext):
        """Выполняет поиск через API асинхронно"""
        try:
            search_message = await message.answer(
                f"🔍 Ищу: <b>{html.escape(query)}</b>...",
                parse_mode=ParseMode.HTML
            )

            session = await self.get_session()

            try:
                async with session.get(f"{self.api_url}?q={query}") as response:
                    if response.status != 200:
                        await search_message.edit_text("❌ Ошибка при поиске. Попробуйте позже.")
                        return

                    data = await response.json()

            except asyncio.TimeoutError:
                await search_message.edit_text("⏰ Таймаут при поиске. Сервер долго не отвечает.")
                return
            except Exception as e:
                await search_message.edit_text("❌ Ошибка подключения к серверу.")
                print(f"API error: {e}")
                return

            if data['results_count'] == 0:
                await search_message.edit_text(
                    f"❌ По запросу '<b>{html.escape(query)}</b>' ничего не найдено",
                    parse_mode=ParseMode.HTML
                )
                return

            await search_message.delete()

            await self.send_results_page(
                chat_id=message.chat.id,
                all_results=data['results'],
                query=query,
                state=state,
                page=0
            )

        except Exception as e:
            print(f"Search error: {e}")
            await message.answer("❌ Произошла ошибка при поиске.")

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
            print(f"Callback error: {e}")
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
            print(f"More callback error: {e}")
            await callback_query.answer("❌ Ошибка при загрузке файлов")

    async def get_session(self):
        """Создает aiohttp сессию при необходимости"""
        if self.session is None:
            timeout = aiohttp.ClientTimeout(total=60)
            self.session = aiohttp.ClientSession(timeout=timeout)
        return self.session

    async def close_session(self):
        """Закрывает aiohttp сессию"""
        if self.session:
            await self.session.close()
            self.session = None

    async def run(self):
        """Запускает бота с улучшенной обработкой ошибок"""
        print("🤖 Бот запускается...")

        # Устанавливаем команды меню
        await self.setup_bot_commands()

        # Закрываем возможные старые сессии
        await self.close_session()

        try:
            # Проверяем подключение к Telegram API
            me = await self.bot.get_me()
            print(f"✅ Бот @{me.username} успешно подключен")

            # Запускаем polling с обработкой конфликтов
            await self.dp.start_polling(
                self.bot,
                allowed_updates=["message", "callback_query"],
                skip_updates=True
            )
        except Exception as e:
            print(f"❌ Ошибка запуска бота: {e}")
        finally:
            await self.close_session()