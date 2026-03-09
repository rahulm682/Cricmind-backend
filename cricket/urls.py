from django.urls import path
from .views import AskAIView

urlpatterns = [
    path('ask-ai/', AskAIView.as_view(), name='ask-ai'),
]