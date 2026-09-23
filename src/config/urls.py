"""application URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/2.2/topics/http/urls/
Examples:
Function views
    1. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including a URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

# from django.contrib import admin
from django.urls import path
from rest_framework.routers import DefaultRouter

from apps.datasource import views as datasource_views
from apps.knowledge import views as knowledge_views
from apps.test import views as test_views

# System name prefix for URL paths (change this when initializing the project)
SYS_NAME = "my-dba"

urlpatterns = [
    path(f"{SYS_NAME}/api/test", test_views.index),
]

router = DefaultRouter(trailing_slash=False)
router.register(rf"{SYS_NAME}/api/v1/test", test_views.TestView, basename="test")
router.register(rf"{SYS_NAME}/v1/datasource", datasource_views.DatasourceView, basename="datasource")
router.register(rf"{SYS_NAME}/v1/catalog", datasource_views.CatalogView, basename="catalog")
router.register(rf"{SYS_NAME}/v1/llm-provider", knowledge_views.LlmProviderView, basename="llm_provider")
router.register(rf"{SYS_NAME}/v1/knowledge-base", knowledge_views.KnowledgeBaseView, basename="knowledge_base")
router.register(rf"{SYS_NAME}/v1/kb-document", knowledge_views.KbDocumentView, basename="kb_document")
router.register(rf"{SYS_NAME}/v1/qa-session", knowledge_views.QaSessionView, basename="qa_session")

urlpatterns += router.urls
