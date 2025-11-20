import requests
import os
import html
import asyncio
import aiohttp
from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
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
        self.router.message.register(self.handle_message, F.text, F.chat.type == ChatType.PRIVATE)

        # Callback обработчики
        self.router.callback_query.register(self.button_callback, F.data.startswith('file_'))
        self.router.callback_query.register(self.more_callback, F.data.startswith('more_'))

    async def is_user_member_of_any_group(self, user_id: int) -> bool:
        """Проверяет, является ли пользователь участником любой из разрешенных групп"""
        if not self.allowed_group_ids:
            # Если группы не указаны, доступ разрешен всем
            return True

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
            groups_info = "\n".join([f"• <code>{group_id}</code>" for group_id in self.allowed_group_ids])

            access_denied_text = "❌ <b>Доступ запрещен</b>\n\n"

            if self.allowed_group_ids:
                access_denied_text += (
                    "Этот бот доступен только для участников разрешенных групп.\n"
                    "Пожалуйста, вступите в одну из групп чтобы использовать бота."
                )
            else:
                access_denied_text += "Настройки доступа не настроены. Обратитесь к администратору."

            await message.answer(access_denied_text, parse_mode=ParseMode.HTML)
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
        await message.answer(welcome_text, parse_mode=ParseMode.HTML)

    async def search_command(self, message: types.Message, state: FSMContext):
        """Обработчик команды /search (только в ЛС)"""
        if not await self.check_access(message):
            return

        query = message.text.replace('/search', '').strip()

        if not query:
            await message.answer("❌ Укажите поисковый запрос после команды\nПример: <code>/search инструкция</code>",
                                 parse_mode=ParseMode.HTML)
            return

        await self.perform_search(message, query, state)

    async def handle_message(self, message: types.Message, state: FSMContext):
        """Обработчик обычных сообщений (только в ЛС)"""
        if not await self.check_access(message):
            return

        query = message.text.strip()

        if query.startswith('/'):
            return

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

            # Ищем место для разбивки (последний перенос строки перед лимитом)
            split_pos = text.rfind('\n', 0, max_length)
            if split_pos == -1:
                # Если нет переносов, разбиваем по границе слова
                split_pos = text.rfind(' ', 0, max_length)
            if split_pos == -1:
                # Если нет пробелов, просто обрезаем
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

    async def send_results_in_parts(self, chat_id, all_results, query, state):
        """Отправляет все результаты частями с кнопками (оригинальный метод из первого файла)"""
        total_files = len(all_results)

        # Сохраняем ВСЕ результаты в состояние
        await state.update_data(last_results=all_results)

        # Создаем кнопки для ВСЕХ результатов
        builder = InlineKeyboardBuilder()

        for i, result in enumerate(all_results):
            display_name = result['name']

            # Обрезаем длинные названия, но оставляем читаемыми
            if len(display_name) > 35:
                if '.' in display_name:
                    name_part, ext = display_name.rsplit('.', 1)
                    display_name = name_part[:32] + '...' + '.' + ext
                else:
                    display_name = display_name[:35] + '...'

            button_text = f"{i + 1}. {display_name}"

            builder.row(InlineKeyboardButton(
                text=button_text,
                callback_data=f"file_{i}"
            ))

        # Разбиваем результаты на батчи по 10 файлов для лучшей читаемости
        batch_size = 10
        total_batches = (total_files + batch_size - 1) // batch_size

        for batch_num in range(total_batches):
            start_idx = batch_num * batch_size
            end_idx = min((batch_num + 1) * batch_size, total_files)
            batch_results = all_results[start_idx:end_idx]

            # Формируем текст для батча
            if batch_num == 0:
                # Первое сообщение с заголовком
                batch_text = f"✅ Найдено <b>{total_files}</b> файлов по запросу '<b>{html.escape(query)}</b>':\n\n"
            else:
                # Последующие сообщения
                batch_text = f"📄 Файлы {start_idx + 1}-{end_idx} из {total_files}:\n\n"

            # Добавляем файлы батча
            for i, result in enumerate(batch_results, start=start_idx + 1):
                name = html.escape(result['name'])
                path = html.escape(result['path'])
                size = html.escape(result['size_formatted'])
                modified = html.escape(result['modified'][:10])

                batch_text += f"<b>{i}. {name}</b>\n"
                batch_text += f"📁 <i>Путь:</i> {path}\n"
                batch_text += f"📦 <i>Размер:</i> {size}\n"
                batch_text += f"📅 <i>Изменен:</i> {modified}\n\n"

            # Определяем, нужно ли добавлять кнопки
            is_last_batch = (batch_num == total_batches - 1)

            if is_last_batch:
                # В последнем сообщении все кнопки
                current_markup = builder.as_markup()
            else:
                # В промежуточных сообщениях НЕТ кнопок
                current_markup = None

            # Отправляем батч
            await self.send_long_message(
                chat_id=chat_id,
                text=batch_text,
                reply_markup=current_markup,
                disable_web_page_preview=True
            )

            # Небольшая пауза между сообщениями чтобы не спамить
            if not is_last_batch:
                await asyncio.sleep(0.3)

    async def send_results_page(self, chat_id, all_results, query, state, page=0, previous_messages=None):
        """Отправляет одну страницу результатов (10 файлов) - метод из второго файла"""
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

        await state.update_data(current_messages=current_messages)
        return current_messages

    async def send_long_message(self, chat_id, text, reply_markup=None, disable_web_page_preview=True):
        """Отправляет длинное сообщение частями"""
        parts = self.split_message(text)

        for i, part in enumerate(parts):
            is_last_part = (i == len(parts) - 1)
            current_markup = reply_markup if is_last_part else None  # Клавиатура только в последней части

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
                # Пытаемся отправить без разметки если есть ошибка
                try:
                    await self.bot.send_message(
                        chat_id=chat_id,
                        text=part[:4000],
                        reply_markup=current_markup,
                        disable_web_page_preview=disable_web_page_preview
                    )
                except Exception as e2:
                    print(f"Error sending plain text part: {e2}")

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

            # Используем новый метод с пагинацией из второго файла
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
            await callback_query.answer("❌ Ошибка при загрузке файлов")
            print(f"More callback error: {e}")

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
        """Запускает бота"""
        print("🤖 Бот запущен...")
        try:
            await self.dp.start_polling(self.bot)
        finally:
            # Закрываем сессию при остановке бота
            await self.close_session()