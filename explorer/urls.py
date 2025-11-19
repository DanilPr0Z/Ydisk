from django.urls import path
from . import views
from . import api_views  # Новый файл для API

urlpatterns = [
    path('', views.index, name='index'),
    path('folder/<path:path>/', views.index, name='folder'),
    path('search/', views.search, name='search'),
    path('content/', views.content_page, name='content'),

    # API endpoints
    path('api/search/', api_views.api_search, name='api_search'),
    path('api/file-info/<path:file_path>/', api_views.api_file_info, name='api_file_info'),
]