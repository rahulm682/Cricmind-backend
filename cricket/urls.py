from django.urls import path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from .views import AskAIView, NewsHubView, PlayerProfileView, PlayerSearchView, LiveMatchesView
from .auth_views import RegisterView
from .chat_views import ChatHistoryView

urlpatterns = [
    path('auth/register/', RegisterView.as_view(), name='register'),
    path('auth/login/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('auth/refresh/', TokenRefreshView.as_view(), name='token_refresh'), 

    path('ask-ai/', AskAIView.as_view(), name='ask-ai'),
    path('news/', NewsHubView.as_view(), name='news-hub'),
    path('player/', PlayerProfileView.as_view(), name='player-profile'),
    path('player-search/', PlayerSearchView.as_view(), name='player-search'),
    path('live-matches/', LiveMatchesView.as_view(), name='live-matches'),

    path('chats/', ChatHistoryView.as_view(), name='chat-history'),
]