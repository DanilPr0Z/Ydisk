#!/usr/bin/env python
import os
import sys
import asyncio
import subprocess
import time
import signal
from dotenv import load_dotenv

load_dotenv()


class SystemManager:
    def __init__(self):
        self.django_process = None
        self.bot_process = None

    def start_django(self):
        """Запускает Django через subprocess"""
        print("🚀 Запуск Django сервера...")

        self.django_process = subprocess.Popen([
            sys.executable, 'manage.py', 'runserver', '0.0.0.0:8000'
        ])

        return self.django_process

    def start_bot(self):
        """Запускает бота через subprocess"""
        print("🤖 Запуск Telegram бота...")

        self.bot_process = subprocess.Popen([
            sys.executable, 'run_bot.py'
        ])

        return self.bot_process

    def stop_process(self, process, name):
        """Останавливает процесс"""
        if process and process.poll() is None:
            print(f"⏹️ Остановка {name}...")
            process.terminate()
            try:
                process.wait(timeout=10)
                print(f"✅ {name} остановлен")
            except subprocess.TimeoutExpired:
                print(f"❌ {name} не остановился, принудительное завершение...")
                process.kill()
                process.wait()

    def cleanup(self):
        """Очистка всех процессов"""
        self.stop_process(self.bot_process, "бота")
        self.stop_process(self.django_process, "Django сервера")

    def wait_for_exit(self):
        """Ожидание завершения с обработкой Ctrl+C"""
        try:
            # Ждем завершения любого из процессов
            while True:
                if self.django_process and self.django_process.poll() is not None:
                    print("❌ Django сервер завершился")
                    break
                if self.bot_process and self.bot_process.poll() is not None:
                    print("❌ Бот завершился")
                    break
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n👋 Остановка системы по запросу пользователя")

    def run_system(self):
        """Запускает всю систему"""
        print("=" * 50)
        print("🌟 Запуск системы Cascate Cloud")
        print("=" * 50)

        # Запускаем Django
        self.start_django()
        print("⏳ Ожидание 5 секунд для запуска Django...")
        time.sleep(5)

        # Проверяем, что Django запустился
        if self.django_process.poll() is not None:
            print("❌ Django сервер не запустился")
            return False

        print("✅ Django сервер запущен")

        # Запускаем бота
        self.start_bot()
        print("✅ Бот запущен")

        print("\n📍 Система запущена:")
        print("   🌐 Django: http://localhost:8000")
        print("   🤖 Бот: работает")
        print("\n⏹️  Нажмите Ctrl+C для остановки")

        # Ждем завершения
        self.wait_for_exit()
        return True


def main():
    manager = SystemManager()

    try:
        manager.run_system()
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
    finally:
        manager.cleanup()


if __name__ == '__main__':
    main()