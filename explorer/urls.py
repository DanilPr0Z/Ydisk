from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('folder/<path:path>/', views.index, name='folder'),
    path('search/', views.search, name='search'),
    path('content/', views.content_page, name='content'),
]
