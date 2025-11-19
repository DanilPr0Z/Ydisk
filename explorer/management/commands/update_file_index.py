
from django.core.management.base import BaseCommand
from django.core.cache import cache
from explorer.models import FileIndex
from explorer.utils.yandex_disk import YandexDiskClient
from explorer.views import FileView
import time


class Command(BaseCommand):
    help = 'Обновляет индекс файлов для быстрого поиска'

    def add_arguments(self, parser):
        parser.add_argument(
            '--skip-preload',
            action='store_true',
            help='Пропустить предзагрузку share-ссылок',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=100,
            help='Размер батча для обработки (по умолчанию: 100)',
        )
        parser.add_argument(
            '--workers',
            type=int,
            default=16,
            help='Количество потоков (по умолчанию: 16)',
        )

    def handle(self, *args, **options):
        start_time = time.time()

        # Настраиваем клиент
        yandex_client = YandexDiskClient()
        yandex_client.max_workers = options['workers']

        self.stdout.write(f'🚀 Запуск обновления индекса с {options["workers"]} потоками...')

        # Получаем все файлы
        self.stdout.write('📁 Получение списка файлов с Яндекс.Диска...')
        all_files = yandex_client.get_flat_file_list()

        self.stdout.write(f'✅ Получено {len(all_files)} файлов')

        # МАССОВАЯ ПРЕДЗАГРУЗКА ССЫЛОК
        if not options['skip_preload']:
            self.stdout.write('🔗 Многопоточная предзагрузка всех ссылок...')
            successful_links = yandex_client.mass_preload_all_links(
                all_files,
                batch_size=options['batch_size']
            )
            self.stdout.write(f'✅ Успешно загружено ссылок: {successful_links}/{len(all_files)}')
        else:
            self.stdout.write('⏭️  Пропущена предзагрузка share-ссылок')
            successful_links = 0

        # Очищаем старый индекс
        self.stdout.write('🗑️ Очистка старого индекса...')
        FileIndex.objects.all().delete()

        # Создаем новый индекс
        self.stdout.write('💾 Создание индекса в базе данных...')

        batch_size = options['batch_size']
        total_files = len(all_files)
        processed = 0
        file_objects = []

        for i in range(0, total_files, batch_size):
            batch_files = all_files[i:i + batch_size]

            # Получаем ссылки для батча (используем оптимизированную версию)
            file_paths = [{'path': file['path']} for file in batch_files]
            links_results = yandex_client.batch_get_links_hyper_optimized(file_paths)
            links_dict = {result['path']: result for result in links_results}

            for file_item in batch_files:
                file_links = links_dict.get(file_item['path'], {})

                file_obj = FileIndex(
                    name=file_item['name'],
                    path=file_item['path'],
                    public_link=file_links.get('public_link'),
                    download_link=file_links.get('download_link'),
                    size=file_item.get('size', 0),
                    modified=file_item.get('modified', ''),
                    media_type=file_item.get('media_type', 'file'),
                    file_type=FileView.get_file_type(file_item['name'], file_item.get('media_type', 'file')),
                    search_vector=file_item['name'].lower()
                )
                file_objects.append(file_obj)

                processed += 1
                if processed % 200 == 0:
                    elapsed = time.time() - start_time
                    speed = processed / elapsed if elapsed > 0 else 0
                    self.stdout.write(f'📊 Обработано {processed}/{total_files} файлов '
                                      f'({speed:.1f} файлов/сек)...')

            # Сохраняем батч в базу
            FileIndex.objects.bulk_create(file_objects, batch_size=batch_size)
            file_objects = []  # Очищаем для следующего батча

        total_time = time.time() - start_time

        # Финальная статистика
        files_with_public_links = FileIndex.objects.exclude(public_link__isnull=True).count()
        files_with_download_links = FileIndex.objects.exclude(download_link__isnull=True).count()

        self.stdout.write(
            self.style.SUCCESS(
                f'✅ ИНДЕКС ОБНОВЛЕН! {total_files} файлов за {total_time:.2f} сек '
                f'({total_files / total_time:.1f} файлов/сек)'
            )
        )

        self.stdout.write(
            self.style.SUCCESS(
                f'🔗 СТАТИСТИКА ССЫЛОК:\n'
                f'   • Публичные: {files_with_public_links}/{total_files} '
                f'({files_with_public_links / total_files * 100:.1f}%)\n'
                f'   • Скачивание: {files_with_download_links}/{total_files} '
                f'({files_with_download_links / total_files * 100:.1f}%)'
            )
        )

