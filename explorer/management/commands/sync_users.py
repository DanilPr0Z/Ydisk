# explorer/management/commands/sync_users.py
import os
import asyncio
from django.core.management.base import BaseCommand
from explorer.utils.user_sync import UserSyncService


class Command(BaseCommand):
    help = 'Синхронизирует администраторов из разрешенных групп с БД'

    def handle(self, *args, **options):
        bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        if not bot_token:
            self.stdout.write(self.style.ERROR('❌ TELEGRAM_BOT_TOKEN не установлен'))
            return

        sync_service = UserSyncService(bot_token)

        try:
            asyncio.run(sync_service.full_sync())
            self.stdout.write(self.style.SUCCESS('✅ Синхронизация администраторов завершена'))
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING('⏹️ Синхронизация прервана'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ Ошибка синхронизации: {e}'))
        finally:
            asyncio.run(sync_service.close())