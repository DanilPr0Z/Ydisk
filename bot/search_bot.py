import requests
import json
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler
from dotenv import load_dotenv

load_dotenv()


class SearchBot:
    def __init__(self):
        self.token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.api_url = os.getenv('SITE_API_URL', 'http://localhost:8000/api/search/')
        self.application = Application.builder().token(self.token).build()

        # Регистрируем обработчики
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("search", self.search_command))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        self.application.add_handler(CallbackQueryHandler(self.button_callback))

    async def start(self, update: Update, context: CallbackContext):
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
        await update.message.reply_text(welcome_text, parse_mode='Markdown')

    async def search_command(self, update: Update, context: CallbackContext):
        """Обработчик команды /search"""
        if not context.args:
            await update.message.reply_text("❌ Укажите поисковый запрос после команды\nПример: `/search инструкция`",
                                            parse_mode='Markdown')
            return

        query = ' '.join(context.args)
        await self.perform_search(update, query)

    async def handle_message(self, update: Update, context: CallbackContext):
        """Обработчик обычных сообщений (быстрый поиск)"""
        query = update.message.text
        await self.perform_search(update, query)

    async def perform_search(self, update: Update, query: str):
        """Выполняет поиск через API"""
        try:
            # Показываем что бот работает
            await update.message.reply_text(f"🔍 Ищу: *{query}*...", parse_mode='Markdown')

            # Вызываем API
            response = requests.get(f"{self.api_url}?q={query}", timeout=30)

            if response.status_code != 200:
                await update.message.reply_text("❌ Ошибка при поиске. Попробуйте позже.")
                return

            data = response.json()

            if data['results_count'] == 0:
                await update.message.reply_text(f"❌ По запросу '*{query}*' ничего не найдено", parse_mode='Markdown')
                return

            # Отправляем результаты
            results_text = f"✅ Найдено *{data['results_count']}* файлов по запросу '*{query}*':\n\n"

            for i, result in enumerate(data['results'][:10]):  # Ограничиваем 10 результатами
                results_text += f"*{i + 1}. {result['name']}*\n"
                results_text += f"📁 Путь: {result['path']}\n"
                results_text += f"📦 Размер: {result['size_formatted']}\n"
                results_text += f"📅 Изменен: {result['modified'][:10]}\n\n"

            # Создаем кнопки для результатов
            keyboard = []
            for i, result in enumerate(data['results'][:5]):  # Максимум 5 кнопок
                keyboard.append([
                    InlineKeyboardButton(
                        f"📎 {result['name'][:30]}...",
                        callback_data=f"file_{i}"
                    )
                ])

            reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None

            await update.message.reply_text(
                results_text,
                parse_mode='Markdown',
                reply_markup=reply_markup,
                disable_web_page_preview=True
            )

            # Сохраняем результаты в контексте для callback


        except requests.exceptions.Timeout:
            await update.message.reply_text("⏰ Таймаут при поиске. Попробуйте позже.")
        except Exception as e:
            await update.message.reply_text("❌ Произошла ошибка при поиске.")
            print(f"Bot error: {e}")

    async def button_callback(self, update: Update, context: CallbackContext):
        """Обработчик нажатий на кнопки"""
        query = update.callback_query
        await query.answer()

        if query.data.startswith('file_'):
            file_index = int(query.data.split('_')[1])
            results = context.user_data.get('last_results', [])

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
                keyboard = []
                if file_info['public_link']:
                    keyboard.append([
                        InlineKeyboardButton("🌐 Открыть в Яндекс.Диске", url=file_info['public_link'])
                    ])
                if file_info['download_link']:
                    keyboard.append([
                        InlineKeyboardButton("📥 Скачать файл", url=file_info['download_link'])
                    ])

                reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None

                await query.edit_message_text(
                    file_text,
                    parse_mode='Markdown',
                    reply_markup=reply_markup,
                    disable_web_page_preview=True
                )

    def run(self):
        """Запускает бота"""
        print("🤖 Бот запущен...")
        self.application.run_polling()