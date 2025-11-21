#!/usr/bin/env python
import os
import sys
import asyncio
import subprocess
import time
from dotenv import load_dotenv

load_dotenv()


async def run_system():
    """Простой запуск системы"""
    print("=" * 50)
    print("🌟 Запуск системы Cascate Cloud")
    print("=" * 50)

    # Запускаем Django в фоновом процессе
    print("🚀 Запуск Django сервера...")
    django_process = subprocess.Popen([
        sys.executable, 'manage.py', 'runserver', '0.0.0.0:8000'
    ])

    # Ждем запуска Django
    print("⏳ Ожидание 5 секунд для запуска Django...")
    await asyncio.sleep(5)

    # Проверяем, что Django еще работает
    if django_process.poll() is not None:
        print("❌ Django сервер завершился с ошибкой")
        return

    print("✅ Django сервер запущен")

    # Запускаем бота
    print("🤖 Запуск Telegram бота...")
    try:
        from bot.search_bot import SearchBot
        bot = SearchBot()
        await bot.run()
    except KeyboardInterrupt:
        print("\n⏹️ Бот остановлен")
    except Exception as e:
        print(f"❌ Ошибка бота: {e}")
    finally:
        print("⏹️ Остановка Django сервера...")
        django_process.terminate()
        django_process.wait()


async def main():
    """Основная асинхронная функция"""
    await run_system()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Система остановлена")