
import requests
import os
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

        # Регистрируем обработчики
        self.router.message.register(self.start, Command("start"))
        self.router.message.register(self.search_command, Command("search"))
        self.router.message.register(self.handle_message, F.text)
        self.router.callback_query.register(self.button_callback, F.data.startswith('file_'))

    async def start(self, message: types.Message):
        """Обработчик команды /start"""
        welcome_text = """
🔍 *Бот для поиска файлов в Cascate Cloud*

*Доступные команды:*
/search <запрос> - поиск файлов
<текст> - быстрый поиск по тексту

*Примеры:*
`/search Распашные двери`
`Распашные двери ALTA`
`инструкция установки`
        """
        await message.answer(welcome_text, parse_mode=ParseMode.MARKDOWN)

    async def search_command(self, message: types.Message, state: FSMContext):
        """Обработчик команды /search"""
        query = message.text.replace('/search', '').strip()

        if not query:
            await message.answer("❌ Укажите поисковый запрос после команды\nПример: `/search инструкция`",
                                 parse_mode=ParseMode.MARKDOWN)
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
        """Выполняет поиск через API"""
        try:
            # Показываем что бот работает
            await message.answer(f"🔍 Ищу: *{query}*...", parse_mode=ParseMode.MARKDOWN)

            # Вызываем API
            response = requests.get(f"{self.api_url}?q={query}", timeout=30)

            if response.status_code != 200:
                await message.answer("❌ Ошибка при поиске. Попробуйте позже.")
                return

            data = response.json()

            if data['results_count'] == 0:
                await message.answer(f"❌ По запросу '*{query}*' ничего не найдено",
                                     parse_mode=ParseMode.MARKDOWN)
                return

            # Сохраняем результаты в состояние
            await state.update_data(last_results=data['results'])

            # Отправляем результаты
            results_text = f"✅ Найдено *{data['results_count']}* файлов по запросу '*{query}*':\n\n"

            for i, result in enumerate(data['results'][:10]):  # Ограничиваем 10 результатами
                results_text += f"*{i + 1}. {result['name']}*\n"
                results_text += f"📁 Путь: {result['path']}\n"
                results_text += f"📦 Размер: {result['size_formatted']}\n"
                results_text += f"📅 Изменен: {result['modified'][:10]}\n\n"

            # Создаем кнопки для результатов
            builder = InlineKeyboardBuilder()
            for i, result in enumerate(data['results'][:5]):  # Максимум 5 кнопок
                builder.add(InlineKeyboardButton(
                    text=f"📎 {result['name'][:30]}...",
                    callback_data=f"file_{i}"
                ))
            builder.adjust(1)  # По одной кнопке в строке

            await message.answer(
                results_text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=builder.as_markup(),
                disable_web_page_preview=True
            )

        except requests.exceptions.Timeout:
            await message.answer("⏰ Таймаут при поиске. Попробуйте позже.")
        except Exception as e:
            await message.answer("❌ Произошла ошибка при поиске.")
            print(f"Bot error: {e}")

    async def button_callback(self, callback_query: types.CallbackQuery, state: FSMContext):
        """Обработчик нажатий на кнопки"""
        try:
            file_index = int(callback_query.data.split('_')[1])

            # Получаем сохраненные результаты
            user_data = await state.get_data()
            results = user_data.get('last_results', [])

            if file_index < len(results):
                file_info = results[file_index]

                file_text = f"""
*📄 {file_info['name']}*

*📁 Путь:* {file_info['path']}
*📦 Размер:* {file_info['size_formatted']}
*📅 Изменен:* {file_info['modified'][:10]}
*🔗 Тип:* {file_info['media_type']}
                """

                # Создаем кнопки для действий с файлом
                builder = InlineKeyboardBuilder()
                if file_info.get('public_link'):
                    builder.add(InlineKeyboardButton(
                        text="🌐 Открыть в Яндекс.Диске",
                        url=file_info['public_link']
                    ))
                if file_info.get('download_link'):
                    builder.add(InlineKeyboardButton(
                        text="📥 Скачать файл",
                        url=file_info['download_link']
                    ))
                builder.adjust(1)  # По одной кнопке в строке

                await callback_query.message.edit_text(
                    file_text,
                    parse_mode=ParseMode.MARKDOWN,
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
        await self.dp.start_polling(self.bot)

