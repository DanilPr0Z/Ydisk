
from django.db import models


class FileIndex(models.Model):
    """Модель для быстрого поиска файлов"""
    name = models.CharField(max_length=500, db_index=True)
    path = models.CharField(max_length=1000, db_index=True)
    public_link = models.URLField(max_length=1000, blank=True, null=True)
    download_link = models.URLField(max_length=1000, blank=True, null=True)
    size = models.BigIntegerField(default=0)
    modified = models.CharField(max_length=100, blank=True)
    media_type = models.CharField(max_length=100, default='file')
    file_type = models.CharField(max_length=50, default='file')

    # Для полнотекстового поиска
    search_vector = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'file_index'
        indexes = [
            models.Index(fields=['name']),
            models.Index(fields=['path']),
        ]

    def __str__(self):
        return self.name


# models.py - ОБНОВЛЕННАЯ МОДЕЛЬ
class AllowedUser(models.Model):
    """Модель для хранения пользователей с доступом к боту"""
    user_id = models.BigIntegerField(primary_key=True)
    username = models.CharField(max_length=255, blank=True, null=True)
    first_name = models.CharField(max_length=255, blank=True, null=True)
    last_name = models.CharField(max_length=255, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    source = models.CharField(max_length=50, default='admin', help_text='admin или member')
    last_seen = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'allowed_users'
        verbose_name = 'Разрешенный пользователь'
        verbose_name_plural = 'Разрешенные пользователи'

    def __str__(self):
        return f"{self.user_id} ({self.username or 'No username'})"

