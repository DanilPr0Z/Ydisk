from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from .utils.yandex_disk import YandexDiskClient
import json


@csrf_exempt
@require_http_methods(["GET", "POST"])
def api_search(request):
    """
    API для умного поиска файлов
    Пример запроса: GET /api/search/?q=запрос
    или POST /api/search/ с JSON {"query": "запрос"}
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

    # Выполняем поиск
    yandex_client = YandexDiskClient()
    all_files = yandex_client.get_flat_file_list()

    results = []
    for file_item in all_files:
        if query.lower() in file_item['name'].lower():
            relative_path = yandex_client.get_relative_path(file_item['path'])
            path_parts = relative_path.split('/')

            display_path = ' / '.join(path_parts[:-1]) if len(path_parts) > 1 else 'Корневая папка'

            download_link = yandex_client.get_file_download_link(file_item['path'])
            public_link = yandex_client.get_public_share_link(file_item['path'])

            results.append({
                'name': file_item['name'],
                'path': display_path,
                'full_path': file_item['path'],
                'size': file_item.get('size', 0),
                'size_formatted': format_size(file_item.get('size', 0)),
                'modified': file_item.get('modified', ''),
                'download_link': download_link,
                'public_link': public_link,
                'media_type': file_item.get('media_type', 'file')
            })

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