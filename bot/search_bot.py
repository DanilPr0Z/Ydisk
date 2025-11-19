
import requests
import os
import html
import asyncio
import aiohttp
from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ParseMode
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

        if not self.token:
            raise ValueError("TELEGRAM_BOT_TOKEN не установлен")

        self.bot = Bot(token=self.token)
        self.storage = MemoryStorage()
        self.dp = Dispatcher(storage=self.storage)
        self.router = Router()
        self.dp.include_router(self.router)

        # Создаем aiohttp сессию для асинхронных запросов
        self.session = None

        # Регистрируем обработчики
        self.router.message.register(self.start, Command("start"))
        self.router.message.register(self.search_command, Command("search"))
        self.router.message.register(self.handle_message, F.text)
        self.router.callback_query.register(self.button_callback, F.data.startswith('file_'))
        self.router.callback_query.register(self.more_callback, F.data.startswith('more_'))

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

    async def start(self, message: types.Message):
        """Обработчик команды /start"""
        welcome_text = """
🔍 <b>Бот для поиска файлов в Cascate Cloud</b>

<b>Доступные команды:</b>
/search &lt;запрос&gt; - поиск файлов
&lt;текст&gt; - быстрый поиск по тексту

<b>Примеры:</b>
<code>/search Распашные двери</code>
<code>Распашные двери ALTA</code>
<code>инструкция установки</code>
        """
        await message.answer(welcome_text, parse_mode=ParseMode.HTML)

    async def search_command(self, message: types.Message, state: FSMContext):
        """Обработчик команды /search"""
        query = message.text.replace('/search', '').strip()

        if not query:
            await message.answer("❌ Укажите поисковый запрос после команды\nПример: <code>/search инструкция</code>",
                                 parse_mode=ParseMode.HTML)
            return

        await self.perform_search(message, query, state)

    async def handle_message(self, message: types.Message, state: FSMContext):
        """Обработчик обычных сообщений (быстрый поиск)"""
        query = message.text.strip()

        if query.startswith('/'):
            return

        await self.perform_search(message, query, state)

    async def delete_messages_batch(self, chat_id, message_ids):
        """Быстрое удаление сообщений пачками"""
        if not message_ids:
            return

        # Создаем задачи для асинхронного удаления
        delete_tasks = []
        for msg_id in message_ids:
            try:
                # Создаем задачу удаления, но не ждем ее выполнения сразу
                task = asyncio.create_task(
                    self.bot.delete_message(chat_id=chat_id, message_id=msg_id)
                )
                delete_tasks.append(task)
            except Exception as e:
                print(f"Error creating delete task for message {msg_id}: {e}")
                continue

        # Ждем завершения всех задач удаления с таймаутом
        if delete_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*delete_tasks, return_exceptions=True),
                    timeout=5.0
                )
            except asyncio.TimeoutError:
                print("Timeout while deleting messages batch")
            except Exception as e:
                print(f"Error in batch delete: {e}")

    async def send_results_page(self, chat_id, all_results, query, state, page=0, previous_messages=None):
        """Отправляет одну страницу результатов (10 файлов)"""
        page_size = 10
        start_idx = page * page_size
        end_idx = start_idx + page_size
        page_results = all_results[start_idx:end_idx]

        total_files = len(all_results)
        total_pages = (total_files + page_size - 1) // page_size

        # Сохраняем все результаты и текущую страницу в состояние
        await state.update_data(
            last_results=all_results,
            current_page=page,
            current_query=query
        )

        # Если есть предыдущие сообщения - удаляем их асинхронно
        if previous_messages:
            # Запускаем удаление в фоне, не ждем завершения
            asyncio.create_task(
                self.delete_messages_batch(chat_id, previous_messages)
            )

        current_messages = []

        # Отправляем заголовок для первой страницы
        if page == 0:
            header_text = f"✅ Найдено <b>{total_files}</b> файлов по запросу '<b>{html.escape(query)}</b>':\n\n"
            header_msg = await self.bot.send_message(
                chat_id=chat_id,
                text=header_text,
                parse_mode=ParseMode.HTML
            )
            current_messages.append(header_msg.message_id)
        else:
            # Для последующих страниц показываем заголовок с номером страницы
            header_text = f"📄 <b>Страница {page + 1}</b> | Найдено <b>{total_files}</b> файлов\n"
            header_msg = await self.bot.send_message(
                chat_id=chat_id,
                text=header_text,
                parse_mode=ParseMode.HTML
            )
            current_messages.append(header_msg.message_id)

        # Быстро отправляем все файлы на странице
        send_tasks = []
        for i, result in enumerate(page_results, start=start_idx + 1):
            # Формируем текст для файла
            name = html.escape(result['name'])
            path = html.escape(result['path'])
            size = html.escape(result['size_formatted'])
            modified = html.escape(result['modified'][:10])

            file_text = f"""
📄 <b>{name}</b>

📁 <i>Путь:</i> {path}
            """

            # Создаем кнопку для этого файла
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(
                text="📋 Получить ссылки на файл",
                callback_data=f"file_{i - 1}"  # Индекс в массиве результатов
            ))

            # Создаем задачу отправки сообщения
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

        # Ждем завершения отправки всех сообщений и собираем их ID
        for task, file_num in send_tasks:
            try:
                file_msg = await task
                current_messages.append(file_msg.message_id)
            except Exception as e:
                print(f"Error sending file message {file_num}: {e}")

        # Добавляем кнопку навигации
        nav_text = f"⚡ <b>Страница {page + 1} из {total_pages}</b> | <i>Файлы {start_idx + 1}-{min(end_idx, total_files)} из {total_files}</i>"

        nav_builder = InlineKeyboardBuilder()

        # Если есть еще файлы, добавляем кнопку "Показать еще"
        if end_idx < total_files:
            nav_builder.row(InlineKeyboardButton(
                text="➡️ Показать еще",
                callback_data=f"more_{page + 1}"
            ))

        # Всегда добавляем кнопку "В начало" кроме первой страницы
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

        # Сохраняем ID текущих сообщений для возможности удаления
        await state.update_data(current_messages=current_messages)

        return current_messages

    async def send_long_message(self, chat_id, text, reply_markup=None, disable_web_page_preview=True):
        """Отправляет длинное сообщение частями"""
        parts = self.split_message(text)

        for i, part in enumerate(parts):
            is_last_part = (i == len(parts) - 1)
            current_markup = reply_markup if is_last_part else None

            try:
                await self.bot.send_message(
                    chat_id=chat_id,
                    text=part,
                    parse_mode=ParseMode.HTML,
                    reply_markup=current_markup,
                    disable_web_page_preview=disable_web_page_preview
                )
            except Exception as e:
                print(f"Error sending message part {i}: {e}")

    async def perform_search(self, message: types.Message, query: str, state: FSMContext):
        """Выполняет поиск через API асинхронно"""
        try:
            # Показываем что бот работает
            search_message = await message.answer(f"🔍 Ищу: <b>{html.escape(query)}</b>...",
                                                  parse_mode=ParseMode.HTML)

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
                print(f"API connection error: {e}")
                return

            if data['results_count'] == 0:
                await search_message.edit_text(f"❌ По запросу '<b>{html.escape(query)}</b>' ничего не найдено",
                                               parse_mode=ParseMode.HTML)
                return

            # Удаляем сообщение "Ищу..."
            await search_message.delete()

            # Отправляем первую страницу результатов
            await self.send_results_page(
                chat_id=message.chat.id,
                all_results=data['results'],
                query=query,
                state=state,
                page=0
            )

        except Exception as e:
            await message.answer("❌ Произошла ошибка при поиске.")
            print(f"Bot error: {e}")

    async def button_callback(self, callback_query: types.CallbackQuery, state: FSMContext):
        """Обработчик нажатий на кнопки файлов"""
        try:
            file_index = int(callback_query.data.split('_')[1])

            user_data = await state.get_data()
            results = user_data.get('last_results', [])

            if file_index < len(results):
                file_info = results[file_index]

                name = html.escape(file_info['name'])
                path = html.escape(file_info['path'])
                size = html.escape(file_info['size_formatted'])
                modified = html.escape(file_info['modified'][:10])
                media_type = html.escape(file_info.get('media_type', 'Неизвестно'))

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

                # Редактируем исходное сообщение с файлом, добавляя ссылки
                await callback_query.message.edit_text(
                    file_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=builder.as_markup(),
                    disable_web_page_preview=True
                )

            await callback_query.answer()

        except Exception as e:
            await callback_query.answer("❌ Ошибка при получении информации о файле")
            print(f"Callback error: {e}")

    async def more_callback(self, callback_query: types.CallbackQuery, state: FSMContext):
        """Обработчик кнопки навигации"""
        try:
            page = int(callback_query.data.split('_')[1])

            user_data = await state.get_data()
            results = user_data.get('last_results', [])
            query = user_data.get('current_query', '')
            previous_messages = user_data.get('current_messages', [])

            if not results:
                await callback_query.answer("❌ Результаты поиска устарели")
                return

            # Показываем что загружаем следующую страницу
            await callback_query.answer("⏳ Загружаем...")

            # Отправляем следующую страницу, удаляя предыдущие сообщения
            await self.send_results_page(
                chat_id=callback_query.message.chat.id,
                all_results=results,
                query=query,
                state=state,
                page=page,
                previous_messages=previous_messages
            )

        except Exception as e:
            await callback_query.answer("❌ Ошибка при загрузке файлов")
            print(f"More callback error: {e}")

    async def run(self):
        """Запускает бота"""
        print("🤖 Бот запущен...")
        try:
            await self.dp.start_polling(self.bot)
        finally:
            # Закрываем сессию при остановке бота
            await self.close_session()
