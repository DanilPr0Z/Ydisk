#!/usr/bin/env python
import os
import sys
import asyncio
import subprocess
import time
import schedule
import threading
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()


class SystemManager:
    def __init__(self):
        self.django_process = None
        self.bot_task = None
        self.scheduler_thread = None
        self.running = True

    async def update_database(self):
        """Запускает обновление базы данных с оптимальными параметрами"""
        print(f"🕒 [{datetime.now().strftime('%H:%M:%S')}] Запуск автоматического обновления БД...")

        try:
            # Используем вашу оптимальную команду
            update_process = subprocess.Popen([
                sys.executable, 'manage.py', 'update_file_index',
                '--workers=32', '--batch-size=200'
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            # Читаем вывод в реальном времени
            while True:
                output = update_process.stdout.readline()
                if output == '' and update_process.poll() is not None:
                    break
                if output:
                    print(f"📦 [{datetime.now().strftime('%H:%M:%S')}] {output.strip()}")

            # Получаем результат
            return_code = update_process.poll()
            if return_code == 0:
                print(f"✅ [{datetime.now().strftime('%H:%M:%S')}] База данных успешно обновлена!")
            else:
                error = update_process.stderr.read()
                print(f"❌ [{datetime.now().strftime('%H:%M:%S')}] Ошибка обновления БД: {error}")

        except Exception as e:
            print(f"❌ [{datetime.now().strftime('%H:%M:%S')}] Ошибка при запуске обновления БД: {e}")

    def schedule_daily_update(self):
        """Настраивает ежедневное обновление в 3:00 ночи"""
        schedule.every().day.at("03:00").do(lambda: asyncio.create_task(self.update_database()))

        print("⏰ Планировщик запущен - ежедневное обновление БД в 03:00")
        print("⚡ Параметры обновления: --workers=32 --batch-size=200")

        while self.running:
            schedule.run_pending()
            time.sleep(60)  # Проверяем каждую минуту

    def start_scheduler(self):
        """Запускает планировщик в отдельном потоке"""
        self.scheduler_thread = threading.Thread(target=self.schedule_daily_update, daemon=True)
        self.scheduler_thread.start()

    async def run_django(self):
        """Запускает Django сервер"""
        print("🚀 Запуск Django сервера...")

        self.django_process = subprocess.Popen([
            sys.executable, 'manage.py', 'runserver', '0.0.0.0:8000'
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        # Читаем вывод Django в реальном времени
        async def read_django_output():
            while self.django_process and self.django_process.poll() is not None:
                try:
                    output = self.django_process.stdout.readline()
                    if output:
                        print(f"🌐 Django: {output.strip()}")
                    await asyncio.sleep(0.1)
                except Exception:
                    break

        asyncio.create_task(read_django_output())
        return self.django_process

    async def run_bot(self):
        """Запускает Telegram бота"""
        print("🤖 Запуск Telegram бота...")

        try:
            from bot.search_bot import SearchBot
            bot = SearchBot()
            await bot.run()
        except Exception as e:
            print(f"❌ Ошибка бота: {e}")
            return False
        return True

    async def run_system(self):
        """Запускает всю систему"""
        print("=" * 50)
        print("🌟 Запуск системы Cascate Cloud")
        print("=" * 50)
        print("⚡ Оптимальные параметры обновления БД:")
        print("   • --workers=32")
        print("   • --batch-size=200")
        print("=" * 50)

        # Запускаем планировщик обновлений БД
        self.start_scheduler()

        # Запускаем Django
        await self.run_django()

        # Ждем запуска Django
        print("⏳ Ожидание 5 секунд для запуска Django...")
        await asyncio.sleep(5)

        # Проверяем, что Django запустился
        if self.django_process.poll() is not None:
            print("❌ Django сервер не запустился")
            return

        print("✅ Django сервер запущен")

        # Запускаем бота
        try:
            await self.run_bot()
        except KeyboardInterrupt:
            print("\n⏹️ Бот остановлен")
        except Exception as e:
            print(f"❌ Ошибка бота: {e}")
        finally:
            self.cleanup()

    def cleanup(self):
        """Очистка ресурсов"""
        print("🧹 Очистка ресурсов...")
        self.running = False

        if self.django_process and self.django_process.poll() is None:
            print("⏹️ Остановка Django сервера...")
            self.django_process.terminate()
            try:
                self.django_process.wait(timeout=10)
                print("✅ Django сервер остановлен")
            except subprocess.TimeoutExpired:
                print("❌ Принудительное завершение Django...")
                self.django_process.kill()


async def main():
    manager = SystemManager()

    try:
        await manager.run_system()
    except KeyboardInterrupt:
        print("\n👋 Система остановлена по запросу пользователя")
    except Exception as e:
        print(f"❌ Критическая ошибка системы: {e}")
    finally:
        manager.cleanup()


if __name__ == '__main__':
    # Проверяем наличие schedule в зависимостях
    try:
        import schedule
    except ImportError:
        print("❌ Библиотека 'schedule' не установлена")
        print("📦 Установите: pip install schedule")
        sys.exit(1)

    asyncio.run(main())