# explorer/utils/user_sync.py
import asyncio
import os
from aiogram import Bot
from django.core.management.base import BaseCommand
from explorer.models import AllowedUser
import time
from asgiref.sync import sync_to_async


class UserSyncService:
    def __init__(self, bot_token):
        self.bot = Bot(token=bot_token)
        self.allowed_group_ids = [int(group_id.strip()) for group_id in
                                  os.getenv('ALLOWED_GROUP_IDS', '').split(',')
                                  if group_id.strip()]

    @sync_to_async
    def update_user_in_db(self, user_data):
        """Обновляет или создает пользователя в БД"""
        try:
            user, created = AllowedUser.objects.update_or_create(
                user_id=user_data['user_id'],
                defaults={
                    'username': user_data.get('username'),
                    'first_name': user_data.get('first_name'),
                    'last_name': user_data.get('last_name'),
                    'is_active': True,
                    'source': 'admin'  # Помечаем как администратора
                }
            )
            return created
        except Exception as e:
            print(f"❌ Ошибка сохранения пользователя {user_data['user_id']}: {e}")
            return False

    @sync_to_async
    def deactivate_all_users(self):
        """Деактивирует всех пользователей перед синхронизацией"""
        # Деактивируем только администраторов, обычных участников не трогаем
        AllowedUser.objects.filter(source='admin', is_active=True).update(is_active=False)

    @sync_to_async
    def get_active_users_count(self):
        """Возвращает количество активных пользователей"""
        return AllowedUser.objects.filter(is_active=True).count()

    async def sync_group_members(self, group_id):
        """Синхронизирует администраторов группы"""
        members_count = 0
        try:
            print(f"📦 Синхронизируем администраторов группы {group_id}...")

            # Получаем только администраторов
            admins = await self.bot.get_chat_administrators(group_id)

            for admin in admins:
                user = admin.user
                # Пропускаем ботов
                if user.is_bot:
                    continue

                user_data = {
                    'user_id': user.id,
                    'username': user.username,
                    'first_name': user.first_name,
                    'last_name': user.last_name
                }

                await self.update_user_in_db(user_data)
                members_count += 1

            print(f"✅ Группа {group_id}: добавлено {members_count} администраторов")

        except Exception as e:
            print(f"⚠️ Ошибка синхронизации группы {group_id}: {e}")

        return members_count

    async def full_sync(self):
        """Полная синхронизация администраторов"""
        if not self.allowed_group_ids:
            print("✅ Группы не указаны, пропускаем синхронизацию")
            return

        start_time = time.time()
        print(f"🚀 Начинаем синхронизацию администраторов из {len(self.allowed_group_ids)} групп...")

        # Деактивируем только администраторов
        await self.deactivate_all_users()

        # Синхронизируем каждую группу
        total_members = 0
        for group_id in self.allowed_group_ids:
            members_count = await self.sync_group_members(group_id)
            total_members += members_count
            await asyncio.sleep(1)  # Пауза между группами

        # Получаем итоговое количество активных пользователей
        active_count = await self.get_active_users_count()
        total_time = time.time() - start_time

        print(f"🎉 СИНХРОНИЗАЦИЯ АДМИНИСТРАТОРОВ ЗАВЕРШЕНА!")
        print(f"⏱ Время: {total_time:.2f} секунд")
        print(f"👥 Администраторов добавлено: {total_members}")
        print(f"✅ Всего активных пользователей в БД: {active_count}")

    async def close(self):
        """Закрывает соединение"""
        await self.bot.session.close()