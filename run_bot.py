from bot.search_bot import SearchBot
import os
import asyncio
from dotenv import load_dotenv

load_dotenv()


async def main():
    """Запускает только бота"""
    print("🤖 Запуск только Telegram бота...")

    # Проверяем наличие токена
    if not os.getenv('TELEGRAM_BOT_TOKEN'):
        print("❌ TELEGRAM_BOT_TOKEN не найден в .env файле")
        return

    try:
        bot = SearchBot()
        await bot.run()
    except KeyboardInterrupt:
        print("\n🤖 Бот остановлен")
    except Exception as e:
        print(f"❌ Ошибка бота: {e}")


if __name__ == '__main__':
    asyncio.run(main())