
import requests
from django.conf import settings
from django.core.cache import cache
import urllib.parse
import concurrent.futures
import time
import threading
import re
from urllib.parse import urlparse
import queue
import asyncio
import aiohttp


class YandexDiskClient:
    def __init__(self):
        self.api_base_url = settings.YANDEX_DISK_CONFIG['API_BASE_URL']
        self.oauth_token = settings.YANDEX_DISK_CONFIG['OAUTH_TOKEN']
        self.root_folder = settings.YANDEX_DISK_CONFIG['ROOT_FOLDER']
        self.max_workers = getattr(settings, 'YANDEX_MAX_WORKERS', 16)  # Увеличиваем воркеры
        self.request_timeout = getattr(settings, 'REQUEST_TIMEOUT', 30)
        self.headers = {
            'Authorization': f'OAuth {self.oauth_token}',
            'Accept': 'application/json'
        }
        self._rate_limit_semaphore = threading.Semaphore(20)  # Увеличиваем лимит
        self._last_request_time = 0
        self._min_request_interval = 0.02  # Уменьшаем интервал
        self._share_cache = {}
        self._download_cache = {}
        self._cache_lock = threading.Lock()

    def _make_request(self, url, params=None, method='GET'):
        """Оптимизированный метод для выполнения запросов с rate limiting"""
        with self._rate_limit_semaphore:
            current_time = time.time()
            time_since_last_request = current_time - self._last_request_time
            if time_since_last_request < self._min_request_interval:
                time.sleep(self._min_request_interval - time_since_last_request)

            self._last_request_time = time.time()

            try:
                if method == 'GET':
                    response = requests.get(url, headers=self.headers, params=params, timeout=self.request_timeout)
                elif method == 'PUT':
                    response = requests.put(url, headers=self.headers, params=params, timeout=self.request_timeout)

                if response.status_code == 404:
                    return None
                elif response.status_code == 429:
                    print("⚠️ Rate limit hit, implementing backoff...")
                    time.sleep(3)  # Увеличиваем паузу при rate limit
                    return None
                elif response.status_code != 200:
                    return None

                return response.json()
            except requests.exceptions.Timeout:
                print("⏰ Request timeout")
                return None
            except requests.exceptions.RequestException as e:
                print(f"❌ API Request error: {e}")
                return None

    def get_folder_contents(self, path=''):
        """Высокопроизводительное получение содержимого папки"""
        if not path:
            path = self.root_folder

        if not path.startswith('disk:/'):
            full_path = f"disk:/{path}"
        else:
            full_path = path

        cache_key = f"folder_{hash(full_path)}"
        cached_data = cache.get(cache_key)

        if cached_data:
            return cached_data

        print(f"🔍 Fetching contents for path: '{full_path}'")

        url = f"{self.api_base_url}"
        params = {
            'path': full_path,
            'limit': 1000
        }

        data = self._make_request(url, params)

        if data and '_embedded' in data and 'items' in data['_embedded']:
            items = data['_embedded']['items']
            print(f"✅ Found {len(items)} items in '{full_path}'")
            cache.set(cache_key, items, timeout=7200)
            return items

        return []

    def get_flat_file_list(self):
        """Оптимизированный параллельный сбор всех файлов"""
        cache_key = "all_files_optimized_v5"
        cached_data = cache.get(cache_key)

        if cached_data:
            print("✅ Using optimized file cache")
            return cached_data

        print(f"🚀 HIGH-PERFORMANCE: Building file list with {self.max_workers} parallel workers...")
        start_time = time.time()

        all_files = []
        folders_to_process = [self.root_folder]
        processed_folders = set()
        folder_lock = threading.Lock()

        def process_folder_batch(folder_batch):
            """Обрабатывает батч папок параллельно"""
            batch_files = []
            new_folders = []

            for folder_path in folder_batch:
                if folder_path in processed_folders:
                    continue

                with folder_lock:
                    processed_folders.add(folder_path)

                items = self.get_folder_contents(folder_path)
                if not items:
                    continue

                for item in items:
                    if item['type'] == 'file':
                        batch_files.append({
                            'name': item['name'],
                            'path': item['path'],
                            'size': item.get('size', 0),
                            'modified': item.get('modified', ''),
                            'media_type': item.get('media_type', 'file'),
                            'name_lower': item['name'].lower()
                        })
                    elif item['type'] == 'dir':
                        new_folders.append(item['path'])

            return batch_files, new_folders

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            while folders_to_process:
                batch_size = min(len(folders_to_process), self.max_workers * 5)  # Увеличиваем батч
                current_batch = folders_to_process[:batch_size]
                folders_to_process = folders_to_process[batch_size:]

                future_to_batch = {
                    executor.submit(process_folder_batch, [folder]): folder
                    for folder in current_batch
                }

                for future in concurrent.futures.as_completed(future_to_batch):
                    try:
                        batch_files, new_folders = future.result()
                        all_files.extend(batch_files)
                        folders_to_process.extend(new_folders)
                    except Exception as e:
                        print(f"❌ Error processing folder batch: {e}")

        total_time = time.time() - start_time
        print(f"✅ HIGH-PERFORMANCE: Built file list with {len(all_files)} files in {total_time:.2f}s "
              f"({len(all_files) / total_time:.1f} files/sec)")

        cache.set(cache_key, all_files, timeout=7200)
        return all_files

    def get_file_download_link(self, path):
        """Многопоточное получение ссылок для скачивания"""
        # Проверяем кэш в памяти
        with self._cache_lock:
            if path in self._download_cache:
                return self._download_cache[path]

        # Проверяем кэш в Django cache
        cache_key = f"download_{hash(path)}"
        cached_link = cache.get(cache_key)

        if cached_link:
            with self._cache_lock:
                self._download_cache[path] = cached_link
            return cached_link

        # Получаем новую ссылку
        url = f"{self.api_base_url}/download"
        params = {'path': path}

        data = self._make_request(url, params)
        if data and 'href' in data:
            download_link = data['href']
            # Сохраняем в кэши
            cache.set(cache_key, download_link, timeout=7200)
            with self._cache_lock:
                self._download_cache[path] = download_link
            return download_link

        return None

    def get_public_share_link(self, path):
        """Многопоточное получение публичных ссылок"""
        # Проверяем кэш в памяти
        with self._cache_lock:
            if path in self._share_cache:
                return self._share_cache[path]

        # Проверяем кэш в Django cache
        cache_key = f"public_{hash(path)}"
        cached_link = cache.get(cache_key)

        if cached_link:
            with self._cache_lock:
                self._share_cache[path] = cached_link
            return cached_link

        # Получаем новую ссылку
        public_link = self._get_fresh_public_link(path)

        if public_link:
            # Сохраняем в кэши
            cache.set(cache_key, public_link, timeout=86400)
            with self._cache_lock:
                self._share_cache[path] = public_link

        return public_link

    def _get_fresh_public_link(self, path):
        """Получает новую публичную ссылку с обработкой ошибок"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # Публикуем файл
                publish_url = f"{self.api_base_url}/publish"
                publish_params = {'path': path}

                publish_data = self._make_request(publish_url, publish_params, method='PUT')
                if not publish_data:
                    if attempt < max_retries - 1:
                        time.sleep(0.5 * (attempt + 1))  # Exponential backoff
                        continue
                    return None

                # Ждем немного перед получением ссылки
                time.sleep(0.1)

                # Получаем публичную ссылку
                share_url = f"{self.api_base_url}"
                share_params = {
                    'path': path,
                    'fields': 'public_url'
                }

                share_data = self._make_request(share_url, share_params)
                if share_data and 'public_url' in share_data:
                    return share_data['public_url']
                else:
                    if attempt < max_retries - 1:
                        time.sleep(0.5 * (attempt + 1))
                        continue

            except Exception as e:
                print(f"❌ Error getting public link for {path}: {e}")
                if attempt < max_retries - 1:
                    time.sleep(0.5 * (attempt + 1))
                    continue

        return None

    def _process_single_file_links(self, file_path):
        """Обрабатывает получение ссылок для одного файла"""
        path = file_path['path']
        try:
            download_link = self.get_file_download_link(path)
            public_link = self.get_public_share_link(path)

            return {
                'path': path,
                'download_link': download_link,
                'public_link': public_link,
                'success': True
            }
        except Exception as e:
            print(f"❌ Error processing links for {path}: {e}")
            return {
                'path': path,
                'download_link': None,
                'public_link': None,
                'success': False,
                'error': str(e)
            }

    def batch_get_links_hyper_optimized(self, file_paths):
        """ГИПЕР-ОПТИМИЗИРОВАННОЕ многопоточное получение ссылок"""
        print(f"🚀 HYPER-OPTIMIZED: Processing {len(file_paths)} files with {self.max_workers} threads...")
        start_time = time.time()

        results = []
        total_files = len(file_paths)

        # Используем ThreadPoolExecutor для максимальной параллелизации
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Создаем futures для всех файлов
            future_to_path = {
                executor.submit(self._process_single_file_links, fp): fp['path']
                for fp in file_paths
            }

            # Обрабатываем результаты по мере готовности
            completed = 0
            for future in concurrent.futures.as_completed(future_to_path):
                path = future_to_path[future]
                try:
                    result = future.result()
                    results.append(result)
                    completed += 1

                    # Прогресс каждые 50 файлов
                    if completed % 50 == 0:
                        elapsed = time.time() - start_time
                        speed = completed / elapsed if elapsed > 0 else 0
                        print(f"📊 Progress: {completed}/{total_files} "
                              f"({completed / total_files * 100:.1f}%) - "
                              f"{speed:.1f} files/sec")

                except Exception as e:
                    print(f"❌ Unexpected error for {path}: {e}")
                    results.append({
                        'path': path,
                        'download_link': None,
                        'public_link': None,
                        'success': False,
                        'error': str(e)
                    })
                    completed += 1

        # Статистика успешных операций
        successful = sum(1 for r in results if r.get('success', False))
        total_time = time.time() - start_time

        print(f"✅ HYPER-OPTIMIZED: Completed {len(results)} files in {total_time:.2f}s "
              f"({len(results) / total_time:.1f} files/sec) - "
              f"Success: {successful}/{len(results)} ({successful / len(results) * 100:.1f}%)")

        return results

    def mass_preload_all_links(self, all_files, batch_size=100):
        """МАССОВАЯ предзагрузка всех ссылок с прогрессом"""
        print(f"🚀 MASS PRELOAD: Starting mass links preloading for {len(all_files)} files...")
        start_time = time.time()

        total_files = len(all_files)
        total_processed = 0
        total_successful = 0

        # Обрабатываем файлы батчами
        for i in range(0, total_files, batch_size):
            batch_files = all_files[i:i + batch_size]
            file_paths = [{'path': file['path']} for file in batch_files]

            # Получаем ссылки для батча
            batch_results = self.batch_get_links_hyper_optimized(file_paths)

            # Статистика батча
            batch_successful = sum(1 for r in batch_results if r.get('success', False))
            total_successful += batch_successful
            total_processed += len(batch_results)

            progress = min(i + batch_size, total_files)
            elapsed = time.time() - start_time
            overall_speed = total_processed / elapsed if elapsed > 0 else 0

            print(f"📈 BATCH {i // batch_size + 1}: {batch_successful}/{len(batch_results)} successful | "
                  f"Overall: {total_successful}/{total_processed} | "
                  f"Speed: {overall_speed:.1f} files/sec")

            # Динамическая пауза между батчами
            if i + batch_size < total_files:
                pause = max(0.5, 2.0 - (overall_speed / 10))  # Адаптивная пауза
                time.sleep(pause)

        total_time = time.time() - start_time
        success_rate = (total_successful / total_files) * 100

        print(f"🎉 MASS PRELOAD COMPLETED: {total_files} files in {total_time:.2f}s "
              f"({total_files / total_time:.1f} files/sec)")
        print(f"📊 SUCCESS RATE: {total_successful}/{total_files} ({success_rate:.1f}%)")

        return total_successful

    def get_relative_path(self, full_path):
        """Получить относительный путь от корневой папки"""
        if full_path.startswith('disk:/'):
            full_path = full_path[6:]

        if full_path.startswith(self.root_folder):
            relative = full_path[len(self.root_folder):].lstrip('/')
            return relative
        return full_path

    def build_search_index(self):
        """Создает поисковый индекс для мгновенного поиска"""
        cache_key = "search_index"
        cached_index = cache.get(cache_key)

        if cached_index:
            return cached_index

        print("🔍 Building search index...")
        all_files = self.get_flat_file_list()

        # Создаем простой индекс: слово -> список файлов
        search_index = {}
        for file_item in all_files:
            file_name_lower = file_item['name'].lower()
            words = re.findall(r'\b\w+\b', file_name_lower)

            for word in words:
                if len(word) > 2:  # Игнорируем короткие слова
                    if word not in search_index:
                        search_index[word] = []
                    search_index[word].append(file_item)

        cache.set(cache_key, search_index, timeout=3600)
        print(f"✅ Search index built: {len(search_index)} words")
        return search_index

    def get_folder_public_link(self, path):
        """Получить публичную ссылку для папки"""
        cache_key = f"folder_public_{hash(path)}"
        cached_link = cache.get(cache_key)

        if cached_link:
            return cached_link

        # Для папок тоже можно получить публичную ссылку
        publish_url = f"{self.api_base_url}/publish"
        publish_params = {'path': path}

        publish_data = self._make_request(publish_url, publish_params, method='PUT')
        if not publish_data:
            return None

        time.sleep(0.3)

        share_url = f"{self.api_base_url}"
        share_params = {
            'path': path,
            'fields': 'public_url'
        }

        share_data = self._make_request(share_url, share_params)
        if share_data and 'public_url' in share_data:
            public_url = share_data['public_url']
            cache.set(cache_key, public_url, timeout=86400)
            return public_url

        return None
