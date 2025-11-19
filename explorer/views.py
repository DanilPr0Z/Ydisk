
from django.shortcuts import render
from django.core.cache import cache
from django.db.models import Q
from .models import FileIndex
from .utils.yandex_disk import YandexDiskClient
import time
import re
import difflib
import concurrent.futures
import threading


class FileView:
    @staticmethod
    def get_file_type(file_name, media_type):
        """Быстрое определение типа файла"""
        file_ext = file_name.lower().split('.')[-1] if '.' in file_name else ''

        if media_type.startswith('image'):
            return 'image'
        elif media_type.startswith('video'):
            return 'video'
        elif media_type.startswith('audio'):
            return 'audio'
        elif file_ext == 'pdf':
            return 'pdf'
        elif file_ext in ['doc', 'docx']:
            return 'word'
        elif file_ext in ['xls', 'xlsx']:
            return 'excel'
        elif file_ext in ['zip', 'rar']:
            return 'archive'
        elif file_ext in ['txt', 'md']:
            return 'text'
        else:
            return 'file'


class SmartSearch:
    """Класс для умного поиска как в Google"""

    # Список стоп-слов (игнорируются при поиске)
    STOP_WORDS = {
        'для', 'на', 'в', 'с', 'по', 'из', 'у', 'о', 'от', 'до', 'за', 'к', 'со', 'во', 'не', 'ни',
        'об', 'под', 'над', 'при', 'про', 'до', 'после', 'через', 'между', 'среди', 'вокруг',
        'перед', 'возле', 'около', 'вдоль', 'поперек', 'сквозь', 'благодаря', 'вопреки', 'согласно',
        'вследствие', 'ввиду', 'насчет', 'вроде', 'включая', 'исключая', 'не считая', 'спустя',
        'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by',
        'из-за', 'из-под', 'по-над', 'по-под', 'и', 'или', 'да', 'но', 'зато', 'однако', 'же', 'ведь',
        'что', 'как', 'когда', 'где', 'куда', 'откуда', 'почему', 'зачем', 'сколько', 'который',
        'какой', 'чей', 'кто', 'что', 'это', 'то', 'все', 'всё', 'весь', 'каждый', 'любой', 'никакой',
        'некий', 'некоторый', 'мой', 'твой', 'его', 'её', 'наш', 'ваш', 'их', 'свой', 'сам', 'самый',
        'другой', 'иной', 'каковой', 'который', 'чей', 'сколько', 'столько', 'такой', 'эдакий',
        'оный', 'сей', 'всякий', 'каждый', 'любой', 'никакой', 'некий', 'некоторый'
    }

    @staticmethod
    def normalize_text(text):
        """Нормализует текст для поиска"""
        if not text:
            return ""

        # Приводим к нижнему регистру и убираем лишние пробелы
        text = str(text).lower().strip()

        # Убираем пунктуацию кроме дефисов и точек в расширениях
        text = re.sub(r'[^\w\s\-\.]', ' ', text)

        # Заменяем множественные пробелы на один
        text = re.sub(r'\s+', ' ', text)

        return text

    @staticmethod
    def filter_stop_words(words):
        """Фильтрует стоп-слова из списка слов"""
        return [word for word in words if word not in SmartSearch.STOP_WORDS and len(word) > 2]

    @staticmethod
    def get_word_variations(word):
        """Генерирует варианты слова для поиска"""
        if len(word) <= 3:
            return [word]

        variations = set()
        variations.add(word)

        # Базовые формы для русского языка
        if word.endswith('ь'):
            variations.add(word[:-1])  # дверь -> двер
        if word.endswith('и'):
            variations.add(word[:-1] + 'а')  # двери -> дверь
            variations.add(word[:-1] + 'ь')  # двери -> дверь
        if word.endswith('ой'):
            variations.add(word[:-2] + 'ая')  # дверной -> дверная
        if word.endswith('ая'):
            variations.add(word[:-2] + 'ой')  # дверная -> дверной
        if word.endswith('ый'):
            variations.add(word[:-2] + 'ая')  # дверный -> дверная
        if word.endswith('ом'):
            variations.add(word[:-2])  # двером -> дверь
        if word.endswith('ам'):
            variations.add(word[:-2])  # дверям -> дверь

        # Добавляем основу
        base = word
        if len(word) > 4:
            if word.endswith(('ой', 'ая', 'ое', 'ые', 'ий', 'ый')):
                base = word[:-2]
            elif word.endswith(('ь', 'и', 'ы', 'а', 'я', 'о', 'е', 'у', 'ю')):
                base = word[:-1]

            if base and len(base) > 3:
                variations.add(base)

        return list(variations)

    @staticmethod
    def calculate_similarity(word1, word2):
        """Вычисляет схожесть между двумя словами"""
        if not word1 or not word2:
            return 0

        # Полное совпадение
        if word1 == word2:
            return 1.0

        # Получаем варианты слов
        variations1 = SmartSearch.get_word_variations(word1)
        variations2 = SmartSearch.get_word_variations(word2)

        # Проверяем совпадение вариантов
        for var1 in variations1:
            for var2 in variations2:
                if var1 == var2:
                    return 0.95

        # Проверяем вхождение одной основы в другую
        for var1 in variations1:
            for var2 in variations2:
                if var1 in var2 or var2 in var1:
                    if len(var1) >= 3 and len(var2) >= 3:
                        return 0.8

        # Используем SequenceMatcher для похожих слов
        similarity = difflib.SequenceMatcher(None, word1, word2).ratio()

        if similarity > 0.7:
            return similarity

        return 0

    @staticmethod
    def smart_search(query, file_name):
        """Умный поиск как в Google"""
        if not query or not file_name:
            return 0

        # Нормализуем текст
        query_norm = SmartSearch.normalize_text(query)
        file_name_norm = SmartSearch.normalize_text(file_name)

        # Если запрос полностью содержится в названии - максимальный рейтинг
        if query_norm in file_name_norm:
            return 100

        # Разбиваем на слова и ФИЛЬТРУЕМ СТОП-СЛОВА
        query_words = SmartSearch.filter_stop_words(query_norm.split())
        file_words = [w for w in file_name_norm.split() if len(w) > 2]

        # Если после фильтрации не осталось значимых слов
        if not query_words:
            return 0

        total_score = 0
        matched_words = 0

        for q_word in query_words:
            word_found = False
            word_score = 0

            for f_word in file_words:
                similarity = SmartSearch.calculate_similarity(q_word, f_word)

                if similarity > 0.9:
                    word_score = max(word_score, 1.0)
                    word_found = True
                    break  # Нашли идеальное совпадение
                elif similarity > 0.8:
                    word_score = max(word_score, 0.8)
                    word_found = True
                elif similarity > 0.7:
                    word_score = max(word_score, 0.6)
                    word_found = True
                elif similarity > 0.6:
                    word_score = max(word_score, 0.4)
                    word_found = True

            if word_found:
                total_score += word_score
                matched_words += 1

        # Если не нашли ни одного похожего слова - возвращаем 0
        if matched_words == 0:
            return 0

        # Вычисляем общий рейтинг релевантности
        base_score = (total_score / len(query_words)) * 80

        # Бонус за совпадение всех слов запроса
        if matched_words == len(query_words):
            base_score += 20

        return min(100, base_score)


def index(request, path=''):
    """Оптимизированная главная страница с кэшированием навигации"""
    start_time = time.time()
    yandex_client = YandexDiskClient()

    # Получаем общее количество файлов из базы (быстро!)
    total_files_count = FileIndex.objects.count()

    # Кэшируем навигацию по текущей папке
    current_path = f"{yandex_client.root_folder}/{path}" if path else yandex_client.root_folder
    cache_key = f"nav_{hash(current_path)}"
    cached_navigation = cache.get(cache_key)

    if cached_navigation:
        print(f"✅ Using cached navigation for: '{current_path}'")
        folders, files = cached_navigation
    else:
        print(f"🔍 Building navigation for: '{current_path}'")

        # Получаем содержимое текущей папки
        folder_contents = yandex_client.get_folder_contents(current_path)

        folders = []
        files = []

        if folder_contents:
            for item in folder_contents:
                if item['type'] == 'dir':
                    rel_path = yandex_client.get_relative_path(item['path'])
                    folders.append({
                        'name': item['name'],
                        'path': rel_path,
                        'modified': item.get('modified', '')[:10]
                    })
                elif item['type'] == 'file':
                    # Получаем ссылки из базы данных (быстро!)
                    file_index = FileIndex.objects.filter(path=item['path']).first()

                    file_data = {
                        'name': item['name'],
                        'size': item.get('size', 0),
                        'modified': item.get('modified', '')[:10],
                        'path': item['path'],
                        'media_type': item.get('media_type', 'file'),
                        'file_type': FileView.get_file_type(item['name'], item.get('media_type', 'file'))
                    }

                    if file_index:
                        file_data['download_link'] = file_index.download_link
                        file_data['public_link'] = file_index.public_link

                    files.append(file_data)

        # Кэшируем навигацию на 1 час
        cache.set(cache_key, (folders, files), timeout=3600)
        print(f"✅ Cached navigation for: '{current_path}'")

    # Формируем хлебные крошки
    breadcrumbs = []
    if path:
        path_parts = path.split('/')
        for i, part in enumerate(path_parts):
            if part:
                breadcrumb_path = '/'.join(path_parts[:i + 1])
                breadcrumbs.append({
                    'name': part,
                    'path': breadcrumb_path
                })

    context = {
        'total_files_count': total_files_count,
        'folders': folders,
        'files': files,
        'breadcrumbs': breadcrumbs,
        'current_path': path,
        'root_folder': yandex_client.root_folder,
        'view': FileView(),
        'load_time': round(time.time() - start_time, 2)
    }

    return render(request, 'explorer/index.html', context)


def search(request):
    """УМНЫЙ поиск как в Google"""
    query = request.GET.get('q', '').strip()

    if not query:
        context = {
            'query': '',
            'results': [],
            'results_count': 0,
            'view': FileView()
        }
        return render(request, 'explorer/search_results.html', context)

    start_time = time.time()

    # Получаем ВСЕ файлы из базы
    all_files_db = FileIndex.objects.all()

    print(f"🔍 SMART SEARCH: '{query}' в {all_files_db.count()} файлах...")

    # Применяем умный поиск ко всем файлам
    scored_results = []

    for file_item in all_files_db:
        # Вычисляем релевантность с помощью умного поиска
        relevance = SmartSearch.smart_search(query, file_item.name)

        if relevance > 5:  # НИЗКИЙ порог чтобы найти больше файлов
            yandex_client = YandexDiskClient()
            relative_path = yandex_client.get_relative_path(file_item.path)
            path_parts = relative_path.split('/')
            display_path = ' / '.join(path_parts[:-1]) if len(path_parts) > 1 else 'Корневая папка'

            scored_results.append({
                'name': file_item.name,
                'path': display_path,
                'full_path': file_item.path,
                'size': file_item.size,
                'modified': file_item.modified,
                'download_link': file_item.download_link,
                'public_link': file_item.public_link,
                'media_type': file_item.media_type,
                'file_type': file_item.file_type,
                'relevance': relevance
            })

    # Сортируем по релевантности (убывание)
    scored_results.sort(key=lambda x: x['relevance'], reverse=True)

    # Ограничиваем количество результатов для производительности
    final_results = scored_results[:100]

    search_time = round(time.time() - start_time, 2)

    print(f"🚀 SMART SEARCH: Найдено {len(final_results)} файлов за {search_time}s "
          f"(макс. релевантность: {max(r['relevance'] for r in final_results) if final_results else 0}%)")

    context = {
        'query': query,
        'results': final_results,
        'results_count': len(final_results),
        'view': FileView(),
        'search_time': search_time
    }

    return render(request, 'explorer/search_results.html', context)


# Глобальная переменная для хранения автоматического содержания
_AUTO_CONTENT_CACHE = None


class ContentBuilder:
    """Класс для многопоточного построения древовидного содержания"""

    def __init__(self, max_workers=15):
        self.yandex_client = YandexDiskClient()
        self.max_workers = max_workers
        self.folder_links_cache = {}
        self.cache_lock = threading.Lock()

    def get_folder_public_link_threadsafe(self, folder_path):
        """Потокобезопасное получение публичной ссылки для папки"""
        with self.cache_lock:
            if folder_path in self.folder_links_cache:
                return self.folder_links_cache[folder_path]

        # Получаем ссылку
        public_link = self.yandex_client.get_folder_public_link(folder_path)

        with self.cache_lock:
            self.folder_links_cache[folder_path] = public_link

        return public_link

    def get_folder_contents_only_dirs(self, path):
        """Получает содержимое папки ТОЛЬКО папки (игнорирует файлы)"""
        try:
            items = self.yandex_client.get_folder_contents(path)
            if not items:
                return []

            # ФИЛЬТРУЕМ - оставляем ТОЛЬКО папки
            folders = [item for item in items if item['type'] == 'dir']
            return folders

        except Exception as e:
            print(f"Error getting folder contents for {path}: {e}")
            return []

    def build_folder_tree_parallel(self, path=''):
        """Строит древовидную структуру папок многопоточно"""
        try:
            # Получаем ТОЛЬКО папки в текущей директории
            folders = self.get_folder_contents_only_dirs(path)
            if not folders:
                return []

            # Получаем публичные ссылки для текущего уровня папок многопоточно
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                # Создаем задачи для получения ссылок
                link_futures = {
                    executor.submit(self.get_folder_public_link_threadsafe, folder['path']): folder
                    for folder in folders
                }

                # Собираем ссылки
                folder_links = {}
                for future in concurrent.futures.as_completed(link_futures):
                    folder = link_futures[future]
                    try:
                        public_link = future.result()
                        folder_links[folder['path']] = public_link
                    except Exception as e:
                        print(f"Error getting link for {folder['path']}: {e}")
                        folder_links[folder['path']] = None

            # Рекурсивно строим дерево для дочерних папок многопоточно
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                # Создаем задачи для построения дочерних структур
                children_futures = {
                    executor.submit(self.build_folder_tree_parallel, folder['path']): folder
                    for folder in folders
                }

                # Собираем дочерние структуры
                children_structures = {}
                for future in concurrent.futures.as_completed(children_futures):
                    folder = children_futures[future]
                    try:
                        children_structure = future.result()
                        children_structures[folder['path']] = children_structure
                    except Exception as e:
                        print(f"Error building children for {folder['path']}: {e}")
                        children_structures[folder['path']] = []

            # Собираем финальную древовидную структуру в формате для твоего шаблона
            tree_structure = []
            for folder in folders:
                folder_data = {
                    'name': folder['name'],
                    'public_link': folder_links.get(folder['path']),
                    'path': folder['path'],
                    'type': 'folder',
                    'children': children_structures.get(folder['path'], [])
                }
                tree_structure.append(folder_data)

            return tree_structure

        except Exception as e:
            print(f"Error building folder tree for {path}: {e}")
            return []

    def convert_tree_to_accordion_format(self, folder_tree):
        """Конвертирует древовидную структуру в формат для аккордеона"""
        content_structure = []
        category_id = 1

        # Основные категории для группировки верхнего уровня
        main_categories = {
            'Алюминиевые Двери': ['алюмин', 'alum', 'fly', 'livia', 'milano', 'next', 'astra', 'cristal', 'alta'],
            'Тамбуратные Двери': ['тамбурат', 'tamburat', 'nuovo', 'rock', 'complanar'],
            'Гардеробы': ['гардероб', 'гардеробн', 'wardrobe', 'шкаф', 'avola', 'ampio', 'fiato', 'spirito'],
            'Мебель': ['мебель', 'furniture', 'стеллаж', 'strada', 'lego', 'romb', 'кубо', 'kubo'],
            'Мягкая мебель': ['диван', 'кровать', 'sofa', 'bed', 'мягк', 'soft', 'pezzo', 'tina', 'gina'],
            'Стеновые панели': ['стенов', 'wall', 'панел', 'panel'],
            'Столы': ['стол', 'table', 'desk'],
            'Инструкции': ['инструкц', 'instruction', 'manual', 'монтаж', 'установк'],
            'Сервисные видео': ['видео', 'video', 'сервис', 'service', 'рекламац'],
            'Информационные письма': ['письмо', 'letter', 'рассылка', 'announce', 'анонс'],
            'Прайсы': ['прайс', 'price', 'стоимость', 'cost'],
            'Каталоги': ['каталог', 'catalog', 'брошюр', 'brochure'],
            'Бланки': ['бланк', 'form', 'акт', 'act', 'заявк'],
            'Фото продукции': ['фото', 'photo', 'изображен', 'image', 'рендер']
        }

        # Функция для поиска папок по ключевым словам
        def find_folders_by_keywords(tree, keywords):
            found_folders = []

            def search_recursive(items):
                for item in items:
                    item_name_lower = item['name'].lower()
                    # Проверяем совпадение с ключевыми словами
                    if any(keyword in item_name_lower for keyword in keywords):
                        found_folders.append(item)
                    # Рекурсивно ищем в дочерних папках
                    if item['children']:
                        search_recursive(item['children'])

            search_recursive(tree)
            return found_folders

        # Функция для преобразования древовидной структуры в плоский список с подпунктами
        def convert_folder_to_items(folder):
            items = []

            for child in folder.get('children', []):
                if child['children']:
                    # Если у папки есть дети, создаем подпункт с вложенными элементами
                    subitems = []
                    for subchild in child['children']:
                        subitems.append({
                            'title': subchild['name'],
                            'link': subchild.get('public_link')
                        })

                    items.append({
                        'title': child['name'],
                        'subitems': subitems
                    })
                else:
                    # Обычный пункт без вложенности
                    items.append({
                        'title': child['name'],
                        'link': child.get('public_link')
                    })

            return items

        # Создаем структуру для аккордеона
        used_folders = set()

        for category_name, keywords in main_categories.items():
            category_folders = find_folders_by_keywords(folder_tree, keywords)

            # Добавляем только уникальные папки верхнего уровня
            unique_folders = []
            for folder in category_folders:
                if folder['path'] not in used_folders:
                    unique_folders.append(folder)
                    used_folders.add(folder['path'])

            if unique_folders:
                # Для каждой категории берем первую подходящую папку как основную
                main_folder = unique_folders[0] if unique_folders else None

                if main_folder:
                    content_structure.append({
                        'id': category_id,
                        'title': category_name,
                        'link': main_folder.get('public_link'),
                        'items': convert_folder_to_items(main_folder)
                    })
                    category_id += 1

        # Добавляем оставшиеся папки в категорию "Прочие папки"
        def get_all_remaining_folders(tree, excluded_paths):
            remaining = []

            def collect_recursive(items):
                for item in items:
                    if item['path'] not in excluded_paths:
                        remaining.append(item)
                    if item['children']:
                        collect_recursive(item['children'])

            collect_recursive(tree)
            return remaining

        remaining_folders = get_all_remaining_folders(folder_tree, used_folders)
        if remaining_folders:
            # Берем первую оставшуюся папку как основную для категории "Прочие"
            main_remaining = remaining_folders[0] if remaining_folders else None
            if main_remaining:
                content_structure.append({
                    'id': category_id,
                    'title': 'Прочие папки',
                    'link': main_remaining.get('public_link'),
                    'items': convert_folder_to_items(main_remaining)
                })

        return content_structure

    def build_content_structure(self):
        """Строит древовидное содержание и конвертирует в формат для аккордеона"""
        print(f"🚀 MULTITHREADED TREE: Building folder tree structure with {self.max_workers} threads...")
        start_time = time.time()

        # Строим полное древовидное содержание
        folder_tree = self.build_folder_tree_parallel(self.yandex_client.root_folder)

        # Конвертируем в формат для твоего аккордеона
        accordion_structure = self.convert_tree_to_accordion_format(folder_tree)

        total_time = time.time() - start_time

        # Считаем общее количество элементов
        total_items = sum(len(section['items']) for section in accordion_structure)
        print(
            f"✅ MULTITHREADED TREE: Content structure built in {total_time:.2f}s - {len(accordion_structure)} sections, {total_items} total items")

        return accordion_structure


def get_auto_content_structure():
    """Автоматически генерирует структуру содержания в формате для твоего шаблона"""
    global _AUTO_CONTENT_CACHE

    # Используем глобальный кэш чтобы содержание сохранялось до перезапуска приложения
    if _AUTO_CONTENT_CACHE is not None:
        return _AUTO_CONTENT_CACHE

    # Строим древовидное содержание многопоточно
    content_builder = ContentBuilder(max_workers=15)
    _AUTO_CONTENT_CACHE = content_builder.build_content_structure()

    return _AUTO_CONTENT_CACHE


def content_page(request):
    """Страница с содержанием файлов на Яндекс.Диске"""

    # Структура содержания с ссылками на Яндекс.Диск
    content_structure = [
        {
            'id': 1,
            'title': 'Алюминиевые Двери',
            'link': 'https://disk.yandex.ru/d/gpnBeBkHzeouQQ',
            'items': [
                {'title': 'Все виды моделей дверей для КП', 'link': ''},
                {'title': 'Схемы декоров Перехлест телескоп', 'link': ''},
                {'title': 'Вырезы в стекле под ручки с вкладышем', 'link': ''},
                {'title': 'Вырезы Перехлест раздельно', 'link': ''},
                {'title': 'Двери одностворчатые и фрамуги. Горизонтальный разрез', 'link': ''},
                {'title': 'Прокрас рамки стекла', 'link': ''},
                {'title': 'Регламент по заказу стеклянных вставок для дверей', 'link': ''},
                {'title': 'Определение глянцевости стекла по спецификации заказа', 'link': ''},
            ]
        },
        {
            'id': 2,
            'title': 'Тамбуратные Двери',
            'link': 'https://disk.yandex.ru/d/4UzV1DDeROLvFw',
            'items': [
                {'title': 'NUOVO 60', 'link': '', 'subitems': [
                    {'title': 'Схема сборки стеновых панелей с коробкой Complanar 60', 'link': ''},
                    {'title': 'Варианты зазоров отделок коллекция NUOVO 60', 'link': ''},
                    {'title': 'Размеры дверей и проемов для короба Complanar 60', 'link': ''},
                    {'title': 'Короб COMPLANAR 60 все размеры', 'link': ''},
                    {'title': 'Размеры вставки керамика Nuovo 60. Открывание – Внутрь', 'link': ''},
                    {'title': 'Размеры вставки керамика Nuovo 60. Открывание - Наружу', 'link': ''},
                ]},
                {'title': 'Все виды моделей дверей для КП', 'link': ''},
                {'title': 'Распашные двери NUOVO 60', 'link': ''},
                {'title': 'Распашные двери NUOVO', 'link': ''},
                {'title': 'Распашные двери ROCK', 'link': ''},
                {'title': 'Варианты врезного алюминиевого декора LINEA', 'link': ''},
                {'title': 'Варианты декоров группы подбор шпона', 'link': ''},
                {'title': 'Варианты зазоров отделок коллекция NUOVO', 'link': ''},
                {'title': 'Виды фрезеровок шпона 5мм и 2,5мм для тамбуратных дверей и стеновых панелей', 'link': ''},
                {'title': 'Вырезы в стекле под ручки с вкладышем. ТАМБУРАТ-ЗЕРКАЛО', 'link': ''},
                {'title': 'Комбинации отделок полотен с двух сторон для моделей NUOVO ROCK', 'link': ''},
                {'title': 'Комбинации отделок профиля для всех коллекций дверей', 'link': ''},
                {'title': 'Модели ручек для раздвижных дверей Nuovo Rock', 'link': ''},
                {'title': 'Описание декоров подбора шпона', 'link': ''},
                {'title': 'Отделки шпона для 3Д фрезеровок', 'link': ''},
                {'title': 'Положение под ручки раздвижные двери NUOVO', 'link': ''},
                {'title': 'Размеры дверей и стеновых панелей в отделке PELLE (кожа)', 'link': ''},
                {'title': 'Памятка покупателю NUOVO ROCK', 'link': ''},
                {'title': 'Паз под флажок Nuovo раздвижная', 'link': ''},
            ]
        },
        {
            'id': 3,
            'title': 'Информация для всех видов дверей',
            'link': 'https://disk.yandex.ru/d/WnyvxVwFNkYn9w',
            'items': [
                {'title': 'Complanar 50 скрытый монтаж все размеры', 'link': ''},
                {'title': 'Виды комплектации полотен для различных моделей', 'link': ''},
                {'title': 'Все размеры дверной короб Complanar IN-OUT', 'link': ''},
                {'title': 'Все размеры дверной короб STANDART IN-OUT', 'link': ''},
                {'title': 'Двери одностворчатые и фрамуги. Горизонтальный разрез', 'link': ''},
                {'title': 'Двустворчатые двери. Все разрезы с размерами', 'link': ''},
                {'title': 'Инструкция по оформлению раздвижной двери', 'link': ''},
                {'title': 'Инструкция по оформлению распашной двери', 'link': ''},
                {'title': 'Инструкция по созданию заказа на базе фрамуг', 'link': ''},
                {'title': 'Комбинации отделок профиля для всех коллекций дверей', 'link': ''},
                {'title': 'Короб Complanar _Наличники с обратной стороны (все варианты)', 'link': ''},
                {'title': 'Наличник Terzo _ Размеры дверей и проемов', 'link': ''},
                {'title': 'Наличник Wave _ Размеры дверей и проемов', 'link': ''},
                {'title': 'Одностворчатые двери. Все разрезы с размерами', 'link': ''},
                {'title': 'Прайс на ручки Розница', 'link': ''},
                {'title': 'Размерная сетка полотен фабрики Cascate', 'link': ''},
                {'title': 'Размеры дверей и проемов наличника Mini (Штукатурка с одной стороны)', 'link': ''},
                {'title': 'Роторная дверь - Схемы расчета', 'link': ''},
                {'title': 'Роторная дверь - схемы отступов петли', 'link': ''},
                {'title': 'Соответствие цвета добора и профиля', 'link': ''},
                {'title': 'Соответствие цвета профиля с цветом петель', 'link': ''},
                {'title': 'Схема наложения наличников на стену', 'link': ''},
                {'title': 'Схемы стыковки фрамуг (Угловые через трубу 50*50)', 'link': ''},
                {'title': 'Схемы стыковки фрамуг (Угловые через уголок)', 'link': ''},
                {'title': 'Схемы фрамужной конструкции с распашной дверью', 'link': ''},
                {'title': 'Размерность цилиндров YALE для всех моделей', 'link': ''},
            ]
        },
        {
            'id': 4,
            'title': 'Гардеробы',
            'link': 'https://disk.yandex.ru/d/VXEvfRWUfbJToQ',
            'items': [
                {'title': 'Описания моделей', 'link': '', 'subitems': [
                    {'title': 'Описание гардеробных Ampio', 'link': ''},
                    {'title': 'Описание гардеробных Ampio Doors', 'link': ''},
                    {'title': 'Описание гардеробных Avola doors', 'link': ''},
                    {'title': 'Описание гардеробных Avola', 'link': ''},
                    {'title': 'Описание гардеробных Fiato', 'link': ''},
                    {'title': 'Описание гардеробных Fiato Doors', 'link': ''},
                    {'title': 'Описание гардеробных Spirito', 'link': ''},
                ]},
                {'title': 'Узлы стыковки фасадов со стеновыми панелями', 'link': ''},
                {'title': 'Ячейки органайзера', 'link': ''},
                {'title': 'Boxter, Costa, Kuber', 'link': ''},
                {'title': 'Габариты рубашниц 600-1200', 'link': ''},
                {'title': 'Гардеробные. Отделка стенок изнутри и снаружи фасадов и боковых стенок', 'link': ''},
                {'title': 'Доступные цвета ЛДСП', 'link': ''},
                {'title': 'Инструкция по оформлению Гардеробов с фасадом TWIN', 'link': ''},
                {'title': 'Инструкция по оформлению заказа AVOLLA. AVOLLA DORS', 'link': ''},
                {'title': 'Каталог премиальных плечиков Bengaleiro', 'link': ''},
                {'title': 'Комод LAM. Новое исполнение', 'link': ''},
                {'title': 'Матрица совмещенных корзин', 'link': ''},
                {'title': 'Новые модули для Гардеробных', 'link': ''},
                {'title': 'Обновление габарита гардероб', 'link': ''},
                {'title': 'Памятка для создания шкафа или гардероба', 'link': ''},
                {'title': 'Полка FREEDOM в гардеробных системах', 'link': ''},
                {'title': 'Расположение обуви на тамбуратных обувницах', 'link': ''},
                {'title': 'Розничный Прайс на коврики для полок FLIPER', 'link': ''},
                {'title': 'Стеновые панели SPIRITO с глубокой фрезеровкой F15 -2,5mm', 'link': ''},
                {'title': 'Схема подключения датчика движения', 'link': ''},
                {'title': 'Фасад LEM разрез вид сверху модуля', 'link': ''},
            ]
        },
        {
            'id': 5,
            'title': 'Мебель',
            'link': 'https://disk.yandex.ru/d/LH64r-bZf67SWg',
            'items': [
                {'title': 'Остров на базе гардеробных модулей картинки', 'link': ''},
                {'title': 'Остров на базе мебельных модулей картинки', 'link': ''},
                {'title': 'Стеллажи', 'link': '', 'subitems': [
                    {'title': 'Все виды стеллажей для КП', 'link': ''},
                    {'title': 'Общий прайс на книжные стеллажи', 'link': ''},
                    {'title': 'Прайс STRADA', 'link': ''},
                    {'title': 'Прайс Lego Assymetric', 'link': ''},
                    {'title': 'Прайс Lego Symetric', 'link': ''},
                    {'title': 'Прайс Romb', 'link': ''},
                    {'title': 'Прайс Un lego', 'link': ''},
                    {'title': 'Размерная линейка Стеллаж Lego Asymmetric', 'link': ''},
                    {'title': 'Размерная линейка Стеллаж Lego Symmetric', 'link': ''},
                    {'title': 'Размерная линейка Стеллаж Romb', 'link': ''},
                    {'title': 'Размерная линейка Стеллаж Un Lego', 'link': ''},
                    {'title': 'Розница Livello', 'link': ''},
                    {'title': 'Сетка размеров Strada (высота 1845)', 'link': ''},
                    {'title': 'Сетка размеров Strada (высота 2187)', 'link': ''},
                    {'title': 'Сетка размеров Strada (высота 2507)', 'link': ''},
                    {'title': 'Сетка размеров Strada (высота 3000)', 'link': ''},
                ]},
                {'title': 'Доступные цвета ЛДСП', 'link': ''},
                {'title': 'Памятка для создания заказа мебель', 'link': ''},
                {'title': 'Развертка островного решения', 'link': ''},
                {'title': 'Размерная сетка стеклянных модулей KUBO', 'link': ''},
                {'title': 'Тех. описание полки AXIS M', 'link': ''},
                {'title': 'Тех. описание полки RIGHE', 'link': ''},
            ]
        },
        {
            'id': 6,
            'title': 'Мягкая мебель',
            'link': 'https://disk.yandex.ru/d/vzj4FHmwGyd1Eg',
            'items': [
                {'title': 'Картинки и фото', 'link': '', 'subitems': [
                    {'title': 'Enzo', 'link': ''},
                    {'title': 'Gina', 'link': ''},
                    {'title': 'Pezzo', 'link': ''},
                    {'title': 'Tina', 'link': ''},
                ]},
                {'title': 'Схемы сборки', 'link': '', 'subitems': [
                    {'title': 'Схема сборки без ПМ', 'link': ''},
                    {'title': 'Схема сборки кровати Pizzo', 'link': ''},
                    {'title': 'Схема сборки кровати Tina', 'link': ''},
                    {'title': 'Схема сборки с ПМ', 'link': ''},
                ]},
                {'title': 'Анонс Модульных диванов PEZZO', 'link': ''},
                {'title': 'Каталог тканей Cascate', 'link': ''},
                {'title': 'Кровати фабрики Cascate', 'link': ''},
                {'title': 'Прайс Мягкая Мебель CASCATE', 'link': ''},
                {'title': 'Схема сборки кровати Lois', 'link': ''},
                {'title': 'Ткани 2023', 'link': ''},
                {'title': 'Памятка по наличию тканей для мягкой мебели', 'link': ''},
            ]
        },
        {
            'id': 7,
            'title': 'Стеновые панели',
            'link': 'https://disk.yandex.ru/d/WhTFdxDqBUi5LA',
            'items': [
                {'title': 'Образец чертежа для заказа стеновых панелей', 'link': ''},
                {'title': 'COMPLANAR 50. Расчет стеновых панелей', 'link': ''},
                {'title': 'Схема сборки стеновых панелей с коробкой Complanar 60', 'link': ''},
                {'title': 'NUOVO 60. Расчет стеновых панелей', 'link': ''},
                {'title': 'Виды профильных 3D фрезеровок', 'link': ''},
                {'title': 'Допустимые размеры стеновых панелей', 'link': ''},
                {'title': 'Прайс на полки STEP', 'link': ''},
                {'title': 'Размеры дверей и стеновых панелей в отделке PELLE (кожа)', 'link': ''},
                {'title': 'Регламент по запуску заказов на СТЕНОВЫЕ ПАНЕЛИ', 'link': ''},
                {'title': 'Создание заказа на стеновые панели через ЛК', 'link': ''},
                {'title': 'Схема монтажа стеновых панелей Mirror откр. Внутрь', 'link': ''},
                {'title': 'Схема монтажа стеновых панелей Mirror откр. Наружу', 'link': ''},
                {'title': 'Узлы стеновых панелей Nuovo 50', 'link': ''},
                {'title': 'Узлы стеновых панелей Nuovo 60', 'link': ''},
                {'title': 'Стыковка и допустимые размеры стеновых панелей', 'link': ''},
            ]
        },
        {
            'id': 8,
            'title': 'Столы',
            'link': 'https://disk.yandex.ru/d/WZvRhIxh433VGg',
            'items': [
                {'title': 'Расчёт стоимости столы Cascate', 'link': ''},
                {'title': 'Приставной столик STIK', 'link': ''},
            ]
        },
        {
            'id': 9,
            'title': 'Каталоги',
            'link': 'https://disk.yandex.ru/d/Tm7L-rKel5myFw',
            'items': [
                {'title': 'Гардеробы', 'link': ''},
                {'title': 'Домашне-офисные перегородки и стеновые панели', 'link': ''},
                {'title': 'Книжные стеллажи Lego, Un Lego, Rombo', 'link': ''},
                {'title': 'Мебель CASCATE', 'link': ''},
                {'title': 'Новинки 2022', 'link': ''},
                {'title': 'Новинки 2023 (большой каталог)', 'link': ''},
                {'title': 'Новинки 2023 (малый каталог)', 'link': ''},
                {'title': 'Новинки ЛЕТО 2021', 'link': ''},
                {'title': 'Распашные и раздвижные двери', 'link': ''},
                {'title': 'Технический каталог 2024', 'link': ''},
            ]
        },
        {
            'id': 10,
            'title': 'Инструкции',
            'link': 'https://disk.yandex.ru/d/9WiYqA2dH5I3Og',
            'items': [
                {'title': 'Гардеробные', 'link': '', 'subitems': [
                    {'title': 'Ampio doors', 'link': ''},
                    {'title': 'Ampio', 'link': ''},
                    {'title': 'AVOLA-AVOLA DOORS', 'link': ''},
                    {'title': 'Fiato Doors', 'link': ''},
                    {'title': 'Fiato Twin', 'link': ''},
                    {'title': 'Spirito', 'link': ''},
                    {'title': 'Инструкция. Гардероб AVOLA LIGHT', 'link': ''},
                    {'title': 'Схема подключения датчика движения', 'link': ''},
                ]},
                {'title': 'Двери Complanar монтаж', 'link': '', 'subitems': [
                    {'title': 'Монтаж распашной двери c коробом', 'link': ''},
                    {'title': 'Инструкция по монтажу фрамуги Nuovo', 'link': ''},
                    {'title': 'Инструкция по транспортировке и хранению алюминиевых дверей', 'link': ''},
                    {'title': 'Информация по замку-электрозащелке', 'link': ''},
                    {'title': 'Монтаж двери-гармошки', 'link': ''},
                    {'title': 'Монтаж раздвижной двери', 'link': ''},
                    {'title': 'Монтаж распашной двери с ФРАМУГАМИ с 1 стороны, угловая', 'link': ''},
                    {'title': 'Монтаж распашной двери с ФРАМУГАМИ с 2 сторон, угловая', 'link': ''},
                    {'title': 'Монтаж распашной двери с накладным коробом на стену Front Wall', 'link': ''},
                    {'title': 'Монтаж распашной двери с коробом Infinite', 'link': ''},
                    {'title': 'Монтаж роторных дверей', 'link': ''},
                    {'title': 'Сборка и монтаж двери с коробом STANDART', 'link': ''},
                    {'title': 'Инструкция по монтажу угловой фрамуги (труба 50*50)', 'link': ''},
                ]},
                {'title': 'Мебель', 'link': '', 'subitems': [
                    {'title': 'Инструкции по монтажу мебели раздельно', 'link': ''},
                    {'title': 'Инструкция Strada', 'link': ''},
                    {'title': 'Инструкция по монтажу модулей (с планкой для навеса)', 'link': ''},
                    {'title': 'Инструкция по монтажу модулей', 'link': ''},
                    {'title': 'Инструкция по монтажу подвесных полок', 'link': ''},
                    {'title': 'Инструкция по соединению модулей между собой', 'link': ''},
                    {'title': 'Инструкция по установке столешниц и боковин (МЕТАЛЛ)', 'link': ''},
                    {'title': 'Инструкция по установке столешниц и боковин (СТЕКЛО)', 'link': ''},
                    {'title': 'Инструкция сборки стеллажа Freedom с компенсатором', 'link': ''},
                    {'title': 'Инструкция, стеллаж Freedom', 'link': ''},
                    {'title': 'Инструкция, стеллаж Lego', 'link': ''},
                    {'title': 'Инструкция, стеллаж Livello', 'link': ''},
                    {'title': 'Инструкция, стеллаж ROMB', 'link': ''},
                    {'title': 'Инструкция, стеллаж UnLego', 'link': ''},
                    {'title': 'Инструкция. AVOLA стеллаж', 'link': ''},
                    {'title': 'Развертка островного решения', 'link': ''},
                ]},
                {'title': 'Тамбуратные двери', 'link': '', 'subitems': [
                    {'title': 'Монтаж раздвижной двери', 'link': ''},
                    {'title': 'Монтаж распашной двери c коробом Complanar', 'link': ''},
                    {'title': 'Монтаж распашной двери с коробом STANDART', 'link': ''},
                    {'title': 'Монтаж распашной двери с накладным коробом Front Wall', 'link': ''},
                    {'title': 'Монтаж распашной двери с фрамугами', 'link': ''},
                    {'title': 'Монтаж роторной двери', 'link': ''},
                ]},
                {'title': 'Стеновые панели', 'link': '', 'subitems': [
                    {'title': 'Инструкция _ Полка Step на стеновые панели', 'link': ''},
                    {'title': 'Инструкция по установке обрамления стеновые панели (фальшфрамуги)', 'link': ''},
                    {'title': 'Инструкция по установке стеновых панелей 50', 'link': ''},
                    {'title': 'Инструкция по установке стеновых панелей NUOVO 60', 'link': ''},
                ]},
            ]
        },
        {
            'id': 11,
            'title': 'Сервисные видео для устранения рекламаций',
            'link': 'https://disk.yandex.ru/d/FXobhgRJEoeqFA',
            'items': [
                {'title': 'Алюминиевые двери', 'link': '', 'subitems': [
                    {'title': 'Видео инструкция по подклейке стекла на Fly', 'link': ''},
                    {'title': 'Инструкция по ремонту подвеса для раздвижных дверей', 'link': ''},
                    {'title': 'Инструкция по установке фиксатора в трек', 'link': ''},
                    {'title': 'Исправление кривизны next, astra, cristal, alta', 'link': ''},
                    {'title': 'Корректировка положения ручки S285', 'link': ''},
                    {'title': 'Подклейка стекла fly livia Milano', 'link': ''},
                    {'title': 'Разбор двери next, cristal, astra, atlantic', 'link': ''},
                    {'title': 'Удаление пятен и разводов с матового стекла', 'link': ''},
                    {'title': 'Установка стекла во фрамугу', 'link': ''},
                    {'title': 'Электромагнитный замок принцип работы', 'link': ''},
                    {'title': 'Замена МДФ (3 части)', 'link': ''},
                    {'title': 'Замена накладки под ручку для Fly', 'link': ''},
                    {'title': 'Замена стекла (3 части)', 'link': ''},
                    {'title': 'Установка накладки под ручку fly livia milano', 'link': ''},
                ]},
                {'title': 'Мебель и гардероб', 'link': '', 'subitems': [
                    {'title': 'Сборка фасада TWIN', 'link': ''},
                    {'title': 'Скотч амортизирующий на выкатные элементы гардероба', 'link': ''},
                    {'title': 'Устранение дребезжания стекла на фасадах', 'link': ''},
                    {'title': 'Устранение мерцания LED подсветки', 'link': ''},
                ]},
                {'title': 'Тамбуратные двери', 'link': '', 'subitems': [
                    {'title': 'Доработка присадки под петлю NUOVO 60', 'link': ''},
                    {'title': 'Устранение искривления тамбуратного полотна', 'link': ''},
                ]},
            ]
        },
        {
            'id': 12,
            'title': 'Обрамление проёма',
            'link': 'https://disk.yandex.ru/d/3e3uKJSo5oEbug',
            'items': [
                {'title': 'Расчет обрамления проема CASCATE', 'link': ''},
            ]
        },
        {
            'id': 13,
            'title': 'Памятки клиентам',
            'link': 'https://disk.yandex.ru/d/IFzfc6YufYizBA',
            'items': [
                {'title': 'Особенности отделки Шпона Rovere Retro, Rovere Country', 'link': ''},
                {'title': 'Памятка - заказ раздвижной двери со скрытым треком', 'link': ''},
                {'title': 'Памятка по креплению модулей и гардеробов к стене, подключению сети 220V', 'link': ''},
                {'title': 'Памятка по подготовке проёма для короба FRONT WALL', 'link': ''},
                {'title': 'Памятка по усилению проемов для раздвижных дверей', 'link': ''},
                {'title': 'Схема подключения датчика движения', 'link': ''},
                {'title': 'Требования к транспортировке дверных полотен', 'link': ''},
            ]
        },
        {
            'id': 14,
            'title': 'Схемы дверей гармошек',
            'link': 'https://disk.yandex.ru/d/qZi2LlIm4dyeqA',
            'items': [
                {'title': 'Схемы дверей гармошек. С раздвижением в 1-у сторону', 'link': ''},
                {'title': 'Схемы дверей гармошек. С раздвижением с 2-х сторон. Часть 1', 'link': ''},
                {'title': 'Схемы дверей гармошек. С раздвижением с 2-х сторон. Часть 2', 'link': ''},
            ]
        },
        {
            'id': 15,
            'title': 'Фото продукции',
            'link': 'https://disk.yandex.ru/d/Gy8lI733DP1fGw',
            'items': [
                {'title': 'Алюминиевые двери', 'link': ''},
                {'title': 'Гардеробные', 'link': ''},
                {'title': 'Диваны', 'link': ''},
                {'title': 'Кровати', 'link': ''},
                {'title': 'Мебель', 'link': ''},
                {'title': 'Столы', 'link': ''},
                {'title': 'Тамбуратные двери и стеновые панели', 'link': ''},
                {'title': 'Рендеры новинок 2024', 'link': ''},
                {'title': 'Рендеры углов наличников', 'link': ''},
            ]
        },
        {
            'id': 16,
            'title': 'Бланки',
            'link': 'https://disk.yandex.ru/d/hag8kxyB7yXhgg',
            'items': [
                {'title': 'Бланк дополнительного заказа', 'link': ''},
                {'title': 'Акт рекламации', 'link': ''},
                {'title': 'Положение о рекламациях', 'link': ''},
            ]
        },
        {
            'id': 17,
            'title': 'Информационные письма и рассылки',
            'link': 'https://disk.yandex.ru/d/UKeVWo2CJ5_nBw',
            'items': [
                {'title': 'Алюминиевые двери', 'link': '', 'subitems': [
                    {'title': 'Анонс Декор JAP', 'link': ''},
                    {'title': 'Анонс декоративных накладок замка FLY, LIVIA, FLY50', 'link': ''},
                    {'title': 'Анонс декоров ALB1-ALB6', 'link': ''},
                    {'title': 'Анонс декоров LONG1-3', 'link': ''},
                    {'title': 'Письмо о выводе печатных декоров', 'link': ''},
                    {'title': 'Анонс декора Long4', 'link': ''},
                ]},
                {'title': 'Гардеробы и мебель', 'link': '', 'subitems': [
                    {'title': 'STRADA новая высота 2507 и 3000', 'link': ''},
                    {'title': 'Анонс гардеробных AVOLA', 'link': ''},
                    {'title': 'Анонс на стеклянный модуль Kubo', 'link': ''},
                    {'title': 'Анонс. Выдвижная полка тамбурат', 'link': ''},
                    {'title': 'Анонс. Обувницы для модуля 600 мм', 'link': ''},
                    {'title': 'Анонс. Полка алюминий 450 мм и резиновый модуль', 'link': ''},
                    {'title': 'Анонс. Элементы островных решений', 'link': ''},
                    {'title': 'ИНФОПИСЬМО. О применении новых полкодержателях', 'link': ''},
                    {'title': 'Изменение по расположению обуви на тамбуратных обувницах', 'link': ''},
                    {'title': 'Инструкция по оформлению Гардеробов с фасадом TWIN', 'link': ''},
                    {'title': 'Комод LAM. Новое исполнение', 'link': ''},
                    {'title': 'Комод SENZA', 'link': ''},
                    {'title': 'Новая отделка лдсп GRAFIT', 'link': ''},
                    {'title': 'Новое исполнение полок с подсветкой', 'link': ''},
                    {'title': 'Новые модули для Гардеробных', 'link': ''},
                    {'title': 'Новый вешалодержатель', 'link': ''},
                    {'title': 'Новый вид крепежа стоек к потолку', 'link': ''},
                    {'title': 'Новый уплотнитель для алюминиевых полок со стеклом', 'link': ''},
                    {'title': 'Обновление габарита гардероб', 'link': ''},
                    {'title': 'Описание Комод BORDO', 'link': ''},
                    {'title': 'Описание Комод LAM с увеличенной высотой фасадов', 'link': ''},
                    {'title': 'Описание комод BASIC 160 мм', 'link': ''},
                    {'title': 'Письмо шпон на Ящики витрин', 'link': ''},
                    {'title': 'Полка FREEDOM в гардеробных системах', 'link': ''},
                    {'title': 'Полки AXIS M', 'link': ''},
                    {'title': 'Стеновые панели SPIRITO с глубокой фрезеровкой F15 -2,5mm', 'link': ''},
                    {'title': 'Тех. описание полки AXIS M', 'link': ''},
                    {'title': 'Тех. описание полки RIGHE', 'link': ''},
                    {'title': 'Устранение дребезжания выкатных элементов гардеробных систем', 'link': ''},
                ]},
                {'title': 'Мягкая мебель', 'link': '', 'subitems': [
                    {'title': 'Анонс. Новые виды опор мягой мебели', 'link': ''},
                    {'title': 'Рекомендуемые ткани в наличии для экспозиций', 'link': ''},
                ]},
                {'title': 'Общая рассылка', 'link': '', 'subitems': [
                    {'title': 'MARRONE вывод отделки', 'link': ''},
                    {'title': 'Анонс Infinite', 'link': ''},
                    {'title': 'Анонс TERZO WAVE', 'link': ''},
                    {'title': 'Анонс новых ручек', 'link': ''},
                    {'title': 'Изменения отделки ROVERE FUME', 'link': ''},
                    {'title': 'Оф. сроки производства продукции', 'link': ''},
                    {'title': 'Оформление заказа из шпона заказчика', 'link': ''},
                    {'title': 'Ошибка компланарная коробка', 'link': ''},
                    {'title': 'Перезапуск заказов на Раздвижные двери', 'link': ''},
                    {'title': 'Письмо по петлям', 'link': ''},
                    {'title': 'Предупреждение об уходе за продукцией', 'link': ''},
                    {'title': 'Распашные двери скрытого монтажа', 'link': ''},
                    {'title': 'Регламент отгрузки склад МСК', 'link': ''},
                    {'title': 'Стандарт 2400 мм', 'link': ''},
                    {'title': 'Реестр отделок 2025', 'link': ''},
                ]},
                {'title': 'Столы', 'link': '', 'subitems': [
                    {'title': 'Приставной столик STIK', 'link': ''},
                ]},
                {'title': 'Тамбуратные двери и стеновые панели', 'link': '', 'subitems': [
                    {'title': 'Анонс Новые виды натурального шпона', 'link': ''},
                    {'title': 'Анонс новых композитов', 'link': ''},
                    {'title': 'Анонс. Новые декоры подбора шпона', 'link': ''},
                    {'title': 'Виды профильных 3D фрезеровок', 'link': ''},
                    {'title': 'Вывод отделки EUCALIPTO', 'link': ''},
                    {'title': 'Вывод отделки ROVERE RETRO', 'link': ''},
                    {'title': 'Запуск заказов с отделкой Rovere retro, Rovere country', 'link': ''},
                    {'title': 'Изменение цен на 3D фрезеровки', 'link': ''},
                    {'title': 'Исключение отделки Gloss Bianco', 'link': ''},
                    {'title': 'Исключение ручки HAF', 'link': ''},
                    {'title': 'Новые цвета отделки эмалей', 'link': ''},
                    {'title': 'Памятка покупателю', 'link': ''},
                    {'title': 'Письмо остановка приема заказов по трещинам дуб', 'link': ''},
                    {'title': 'Письмо по фиксаторам', 'link': ''},
                    {'title': 'Повышение цены композит 1 мая', 'link': ''},
                    {'title': 'Срок поставки COMPLANAR 60', 'link': ''},
                ]},
            ]
        },
        {
            'id': 18,
            'title': 'Прайс доп. продукции',
            'link': 'https://disk.yandex.ru/d/uQBULnl7i14j2g',
            'items': [
                {'title': 'Органайзеры для ящиков', 'link': '', 'subitems': [
                    {'title': 'BOXTER органайзер прайс (4мм алюминий и ЛДСП)', 'link': ''},
                    {'title': 'COSTA орагнайзер прайс (4 мм алюминий и шпон)', 'link': ''},
                    {'title': 'KUBER органайзер прайс (ЛДСП)', 'link': ''},
                ]},
                {'title': 'Бланк дополнительного заказа', 'link': ''},
                {'title': 'Оформление заказа из шпона заказчика', 'link': ''},
                {'title': 'Прайс доп продукции', 'link': ''},
                {'title': 'Прайс на коврики для полок FLIPER', 'link': ''},
                {'title': 'Прайс на ручки Розница', 'link': ''},
            ]
        },
        {
            'id': 19,
            'title': 'Комплектации пеналов',
            'link': 'https://disk.yandex.ru/d/uQBULnl7i14j2g',
            'items': [
                {'title': 'Схема пенала _ Крепление трека к потолку. Накладной притвор', 'link': ''},
                {'title': 'Схема пенала _ Крепление трека к брусу. Накладной притвор', 'link': ''},
                {'title': 'Схема пенала _ Крепление трека к брусу. Скрытый притвор', 'link': ''},
                {'title': 'Схема пенала _ Крепление трека к брусу. Притвор с обрамлением', 'link': ''},
                {'title': 'Схема пенала _ Крепление трека к брусу. Скрытый притвор с обрамлением', 'link': ''},
                {'title': 'Инструкция _ Пенал _ 1.1 _ Трек в потолок. Накладной притвор', 'link': ''},
                {'title': 'Инструкция_Пенал_2_1_Трек_к_брусу_Накладной_притвор', 'link': ''},
                {'title': 'Оптовый прайс на универсальный комплект пенала', 'link': ''},
            ]
        },
    ]

    context = {
        'content_structure': content_structure,
        'title': 'Автоматическое содержание Яндекс.Диска',
        'is_auto_content': True
    }

    return render(request, 'explorer/content.html', context)


# Старая функция content оставлена для обратной совместимости
def content(request):
    """Страница содержания (редирект на автоматическое содержание)"""
    return content_page(request)


def clear_content_cache(request):
    """Очистка кэша содержания (для админов)"""
    global _AUTO_CONTENT_CACHE
    _AUTO_CONTENT_CACHE = None
    return render(request, 'explorer/cache_cleared.html')

