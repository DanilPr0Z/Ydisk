from bot.search_bot import SearchBot
import os
import asyncio
from dotenv import load_dotenv

load_dotenv()

async def main():
    # Проверяем наличие токена
    if not os.getenv('TELEGRAM_BOT_TOKEN'):
        print("❌ TELEGRAM_BOT_TOKEN не найден в .env файле")
        exit(1)

    bot = SearchBot()
    await bot.run()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🤖 Бот остановлен")
