#!/usr/bin/env python
import os
import sys
import signal
import subprocess


def kill_existing_bots():
    """Убивает все запущенные процессы бота"""
    print("🔫 Остановка всех процессов бота...")

    # Команда для поиска и убийства процессов
    commands = [
        "pkill -f 'python.*search_bot'",
        "pkill -f 'python.*run_bot'",
        "pkill -f 'python.*run_all'",
    ]

    for cmd in commands:
        try:
            subprocess.run(cmd, shell=True, capture_output=True)
        except Exception:
            pass

    print("✅ Все процессы бота остановлены")


if __name__ == '__main__':
    kill_existing_bots()