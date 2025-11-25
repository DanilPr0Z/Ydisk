# explorer/management/commands/sync_users_daily.py
import os
import time
from django.core.management.base import BaseCommand
from django.core.management import call_command
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Запускает ежедневную синхронизацию пользователей по расписанию'

    def add_arguments(self, parser):
        parser.add_argument(
            '--immediate',
            action='store_true',
            help='Выполнить синхронизацию сразу при запуске',
        )
        parser.add_argument(
            '--once',
            action='store_true',
            help='Выполнить синхронизацию только один раз и завершить',
        )

    def handle(self, *args, **options):
        if options['once']:
            # Однократная синхронизация
            self.stdout.write('🔄 Запуск однократной синхронизации...')
            self.sync_users()
            self.stdout.write(self.style.SUCCESS('✅ Синхронизация завершена'))
            return

        # Запуск планировщика для ежедневной синхронизации
        scheduler = BackgroundScheduler()

        # Синхронизация каждый день в 3:00 утра
        scheduler.add_job(
            self.sync_users,
            trigger=CronTrigger(hour=3, minute=0),
            id='daily_user_sync',
            name='Ежедневная синхронизация пользователей'
        )

        # Дополнительная синхронизация в 12:00 для тестирования
        scheduler.add_job(
            self.sync_users,
            trigger=CronTrigger(hour=12, minute=0),
            id='midday_user_sync',
            name='Дневная синхронизация пользователей'
        )

        # Синхронизация при старке если указана опция
        if options['immediate']:
            self.stdout.write('🔄 Запуск немедленной синхронизации...')
            self.sync_users()

        try:
            scheduler.start()
            self.stdout.write(self.style.SUCCESS('✅ Планировщик синхронизации запущен'))
            self.stdout.write('📅 Расписание синхронизации:')
            self.stdout.write('   • Ежедневно в 03:00')
            self.stdout.write('   • Ежедневно в 12:00')
            self.stdout.write('⏹️  Для остановки нажмите Ctrl+C')

            # Бесконечный цикл для поддержания работы планировщика
            while True:
                time.sleep(3600)  # Спим 1 час

        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING('⏹️  Планировщик остановлен'))
            scheduler.shutdown()
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ Ошибка планировщика: {e}'))
            scheduler.shutdown()

    def sync_users(self):
        """Выполняет синхронизацию пользователей"""
        try:
            self.stdout.write('🔄 Запуск синхронизации пользователей...')
            start_time = time.time()

            call_command('sync_users')

            execution_time = time.time() - start_time
            self.stdout.write(
                self.style.SUCCESS(f'✅ Синхронизация завершена за {execution_time:.2f} сек')
            )

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ Ошибка синхронизации: {e}'))
            logger.error(f"Ошибка синхронизации пользователей: {e}")