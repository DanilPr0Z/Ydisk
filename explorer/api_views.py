from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.db.models import Q
from .models import FileIndex
from .utils.yandex_disk import YandexDiskClient
import json


@csrf_exempt
@require_http_methods(["GET", "POST"])
def api_search(request):
    """
    API для СУПЕРБЫСТРОГО поиска через базу данных
    """
    # Получаем поисковый запрос
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            query = data.get('query', '').strip()
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON'}, status=400)
    else:
        query = request.GET.get('q', '').strip()

    if not query:
        return JsonResponse({'error': 'Query parameter "q" is required'}, status=400)

    query_lower = query.lower()
    query_words = [word for word in query_lower.split() if len(word) > 2]
    first_word = query_words[0] if query_words else query_lower

    # БЫСТРЫЙ ПОИСК В БАЗЕ
    if not query_words:
        db_results = FileIndex.objects.filter(
            search_vector__icontains=query_lower
        )
    else:
        search_conditions = Q()
        for word in query_words:
            search_conditions |= Q(search_vector__icontains=word)

        db_results = FileIndex.objects.filter(search_conditions)

    # ВОЗВРАЩАЕМ ВСЕ РЕЗУЛЬТАТЫ БЕЗ ОГРАНИЧЕНИЙ
    results = []
    yandex_client = YandexDiskClient()

    for file_item in db_results:
        file_name_lower = file_item.name.lower()

        match_score = 0
        matched_words = []
        has_first_word = False

        if not query_words:
            if query_lower in file_name_lower:
                match_score = 100
                matched_words = [query_lower]
                has_first_word = True
        else:
            for i, word in enumerate(query_words):
                if word in file_name_lower:
                    match_score += 1
                    matched_words.append(word)
                    if i == 0:
                        has_first_word = True

        if match_score > 0:
            relevance_percent = int((match_score / len(query_words)) * 100) if query_words else 100

            relative_path = yandex_client.get_relative_path(file_item.path)
            path_parts = relative_path.split('/')
            display_path = ' / '.join(path_parts[:-1]) if len(path_parts) > 1 else 'Корневая папка'

            results.append({
                'name': file_item.name,
                'path': display_path,
                'full_path': file_item.path,
                'size': file_item.size,
                'size_formatted': format_size(file_item.size),
                'modified': file_item.modified,
                'download_link': file_item.download_link,
                'public_link': file_item.public_link,
                'media_type': file_item.media_type,
                'relevance': relevance_percent,
                'matched_words': matched_words,
                'has_first_word': has_first_word,
                'match_score': match_score
            })

    # СОРТИРОВКА
    results.sort(key=lambda x: (
        not x['has_first_word'],
        -x['match_score'],
        -x['relevance']
    ))

    return JsonResponse({
        'query': query,
        'results_count': len(results),
        'results': results
    })

@csrf_exempt
@require_http_methods(["GET"])
def api_file_info(request, file_path):
    """
    API для получения информации о конкретном файле
    Пример: GET /api/file-info/disk:/path/to/file.pdf
    """
    yandex_client = YandexDiskClient()

    # Декодируем путь если нужно
    if file_path.startswith('encoded:'):
        import urllib.parse
        file_path = urllib.parse.unquote(file_path[8:])

    # Ищем файл в базе данных
    file_index = FileIndex.objects.filter(path=file_path).first()

    if file_index:
        file_info = {
            'name': file_index.name,
            'path': file_index.path,
            'size': file_index.size,
            'size_formatted': format_size(file_index.size),
            'modified': file_index.modified,
            'media_type': file_index.media_type,
            'download_link': file_index.download_link,
            'public_link': file_index.public_link
        }
        return JsonResponse({'file': file_info})

    # Если не нашли в базе, ищем через Яндекс.Диск
    download_link = yandex_client.get_file_download_link(file_path)
    public_link = yandex_client.get_public_share_link(file_path)

    # Получаем информацию о файле
    all_files = yandex_client.get_flat_file_list()
    file_info = None

    for file_item in all_files:
        if file_item['path'] == file_path:
            file_info = {
                'name': file_item['name'],
                'path': file_item['path'],
                'size': file_item.get('size', 0),
                'size_formatted': format_size(file_item.get('size', 0)),
                'modified': file_item.get('modified', ''),
                'media_type': file_item.get('media_type', 'file'),
                'download_link': download_link,
                'public_link': public_link
            }
            break

    if not file_info:
        return JsonResponse({'error': 'File not found'}, status=404)

    return JsonResponse({'file': file_info})


def format_size(size_bytes):
    """Форматирует размер файла в читаемый вид"""
    if size_bytes == 0:
        return "0 Б"

    size_names = ["Б", "КБ", "МБ", "ГБ", "ТБ"]
    i = 0
    while size_bytes >= 1024 and i < len(size_names) - 1:
        size_bytes /= 1024.0
        i += 1

    return f"{size_bytes:.2f} {size_names[i]}"

