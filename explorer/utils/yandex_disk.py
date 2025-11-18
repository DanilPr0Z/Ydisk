import requests
from django.conf import settings
from django.core.cache import cache
import urllib.parse
import concurrent.futures
import time
import threading
import re


class YandexDiskClient:
    def __init__(self):
        self.api_base_url = settings.YANDEX_DISK_CONFIG['API_BASE_URL']
        self.oauth_token = settings.YANDEX_DISK_CONFIG['OAUTH_TOKEN']
        self.root_folder = settings.YANDEX_DISK_CONFIG['ROOT_FOLDER']
        self.max_workers = getattr(settings, 'YANDEX_MAX_WORKERS', 8)
        self.request_timeout = getattr(settings, 'REQUEST_TIMEOUT', 20)
        self.headers = {
            'Authorization': f'OAuth {self.oauth_token}',
            'Accept': 'application/json'
        }
        self._rate_limit_semaphore = threading.Semaphore(10)
        self._last_request_time = 0
        self._min_request_interval = 0.05

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
                    time.sleep(2)
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

        print(f"🔍 Fetching contents for path: '{full_path}' (workers: {self.max_workers})")

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
        cache_key = "all_files_optimized_v3"
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
                            'name_lower': item['name'].lower()  # Добавляем для быстрого поиска
                        })
                    elif item['type'] == 'dir':
                        new_folders.append(item['path'])

            return batch_files, new_folders

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            while folders_to_process:
                batch_size = min(len(folders_to_process), self.max_workers * 3)
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
        """Параллельное получение ссылок для скачивания"""
        cache_key = f"download_{hash(path)}"
        cached_link = cache.get(cache_key)

        if cached_link:
            return cached_link

        url = f"{self.api_base_url}/download"
        params = {'path': path}

        data = self._make_request(url, params)
        if data and 'href' in data:
            cache.set(cache_key, data['href'], timeout=7200)
            return data['href']
        return None

    def get_public_share_link(self, path):
        """Параллельное получение публичных ссылок"""
        cache_key = f"public_{hash(path)}"
        cached_link = cache.get(cache_key)

        if cached_link:
            return cached_link

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

    def batch_get_links(self, file_paths):
        """Массовое получение ссылок для списка файлов"""

        def get_single_links(file_item):
            path = file_item['path']
            return {
                'path': path,
                'download_link': self.get_file_download_link(path),
                'public_link': self.get_public_share_link(path)
            }

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(get_single_links, file_paths))

        return results

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
