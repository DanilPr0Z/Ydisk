
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

    async def get_session(self):
        """Создает aiohttp сессию при необходимости"""
        if self.session is None:
            timeout = aiohttp.ClientTimeout(total=120)  # 60 секунд таймаут
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

        # Игнорируем команды, которые уже обрабатываются отдельно
        if query.startswith('/'):
            return

        await self.perform_search(message, query, state)

    async def perform_search(self, message: types.Message, query: str, state: FSMContext):
        """Выполняет поиск через API асинхронно"""
        try:
            # Показываем что бот работает
            search_message = await message.answer(f"🔍 Ищу: <b>{html.escape(query)}</b>...",
                                                  parse_mode=ParseMode.HTML)

            # Используем aiohttp для асинхронного запроса
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

            # Сохраняем ВСЕ результаты в состояние
            await state.update_data(last_results=data['results'])

            # Формируем текст результатов с HTML разметкой
            results_text = f"✅ Найдено <b>{data['results_count']}</b> файлов по запросу '<b>{html.escape(query)}</b>':\n\n"

            # Показываем ВСЕ файлы в списке
            for i, result in enumerate(data['results']):
                name = html.escape(result['name'])
                path = html.escape(result['path'])
                size = html.escape(result['size_formatted'])
                modified = html.escape(result['modified'][:10])

                results_text += f"<b>{i + 1}. {name}</b>\n"
                results_text += f"📁 <i>Путь:</i> {path}\n"
                results_text += f"📦 <i>Размер:</i> {size}\n"
                results_text += f"📅 <i>Изменен:</i> {modified}\n\n"

            # Создаем кнопки для ВСЕХ результатов - ОДНА КНОПКА В СТРОКУ
            builder = InlineKeyboardBuilder()

            for i, result in enumerate(data['results']):
                display_name = result['name']

                # Обрезаем длинные названия, но оставляем читаемыми
                if len(display_name) > 35:
                    # Сохраняем расширение файла
                    if '.' in display_name:
                        name_part, ext = display_name.rsplit('.', 1)
                        display_name = name_part[:32] + '...' + '.' + ext
                    else:
                        display_name = display_name[:35] + '...'

                # Создаем кнопку с номером и названием файла - КАЖДАЯ КНОПКА В ОТДЕЛЬНОЙ СТРОКЕ
                button_text = f"{i + 1}. {display_name}"

                builder.row(InlineKeyboardButton(
                    text=button_text,
                    callback_data=f"file_{i}"
                ))

            # Редактируем исходное сообщение с результатами
            await search_message.edit_text(
                results_text,
                parse_mode=ParseMode.HTML,
                reply_markup=builder.as_markup(),
                disable_web_page_preview=True
            )

        except Exception as e:
            await message.answer("❌ Произошла ошибка при поиске.")
            print(f"Bot error: {e}")

    async def button_callback(self, callback_query: types.CallbackQuery, state: FSMContext):
        """Обработчик нажатий на кнопки"""
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
📦 <b>Размер:</b> {size}
📅 <b>Изменен:</b> {modified}
🔗 <b>Тип:</b> {media_type}
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
            await callback_query.answer("❌ Ошибка при получении информации о файле")
            print(f"Callback error: {e}")

    async def run(self):
        """Запускает бота"""
        print("🤖 Бот запущен...")
        try:
            await self.dp.start_polling(self.bot)
        finally:
            # Закрываем сессию при остановке бота
            await self.close_session()

