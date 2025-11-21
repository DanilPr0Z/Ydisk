#!/usr/bin/env python
import os
import sys
import argparse
import asyncio


def main():
    parser = argparse.ArgumentParser(description='Cascate Cloud Management System')
    parser.add_argument('command', choices=['all', 'web', 'bot', 'migrate', 'collectstatic'],
                        help='Команда запуска: all - всё, web - только сайт, bot - только бот')

    args = parser.parse_args()

    if args.command == 'all':
        # Запускаем всё через новый менеджер
        from run_all import main as run_all_main
        asyncio.run(run_all_main())

    elif args.command == 'web':
        # Запускаем только веб-сайт
        print("🚀 Запуск только Django сервера...")
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'yadisk_explorer.settings')
        from django.core.management import execute_from_command_line
        execute_from_command_line(['manage.py', 'runserver', '0.0.0.0:8000'])

    elif args.command == 'bot':
        # Запускаем только бота
        from run_bot import main as run_bot_main
        asyncio.run(run_bot_main())

    elif args.command == 'migrate':
        # Выполняем миграции
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'yadisk_explorer.settings')
        from django.core.management import execute_from_command_line
        execute_from_command_line(['manage.py', 'migrate'])

    elif args.command == 'collectstatic':
        # Собираем статику
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'yadisk_explorer.settings')
        from django.core.management import execute_from_command_line
        execute_from_command_line(['manage.py', 'collectstatic', '--noinput'])


if __name__ == '__main__':
    main()