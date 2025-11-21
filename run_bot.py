import os
import asyncio
import sys
import psutil
from dotenv import load_dotenv

load_dotenv()


def is_bot_already_running():
    """Проверяет, не запущен ли уже бот"""
    current_pid = os.getpid()
    current_script = os.path.basename(__file__)

    for process in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            # Пропускаем текущий процесс
            if process.info['pid'] == current_pid:
                continue

            # Ищем процессы Python с нашим ботом
            cmdline = process.info.get('cmdline', [])
            if (cmdline and
                    'python' in ''.join(cmdline).lower() and
                    any('search_bot' in str(arg) for arg in cmdline) and
                    any('run_bot' in str(arg) for arg in cmdline or any('run_all' in str(arg) for arg in cmdline))):
                print(f"❌ Бот уже запущен в процессе {process.info['pid']}")
                return True

        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return False


async def main():
    """Запускает только бота с проверкой на дублирование"""
    print("🤖 Проверка запуска Telegram бота...")

    # Проверяем, не запущен ли уже бот
    if is_bot_already_running():
        print("❌ Бот уже запущен. Остановите предыдущую версию перед запуском.")
        sys.exit(1)

    # Проверяем наличие токена
    if not os.getenv('TELEGRAM_BOT_TOKEN'):
        print("❌ TELEGRAM_BOT_TOKEN не найден в .env файле")
        sys.exit(1)

    try:
        from bot.search_bot import SearchBot
        bot = SearchBot()
        print("✅ Бот инициализирован, запускаем...")
        await bot.run()
    except ImportError as e:
        print(f"❌ Ошибка импорта: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Ошибка запуска бота: {e}")
        sys.exit(1)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🤖 Бот остановлен пользователем")
    except Exception as e:
        print(f"❌ Неожиданная ошибка: {e}")