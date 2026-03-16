import logging
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from .models import ChatSession

logger = logging.getLogger(__name__)

class ChatHistoryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Fetch all chats for the logged-in user"""
        chats = ChatSession.objects.filter(user=request.user)
        
        chat_data = [
            {
                "id": chat.id,
                "title": chat.title,
                "messages": chat.messages
            } for chat in chats
        ]
        return Response(chat_data, status=status.HTTP_200_OK)

    def post(self, request):
        """Create or Update a chat session"""
        chat_id = request.data.get('id')
        title = request.data.get('title')
        messages = request.data.get('messages', [])

        if not chat_id or not title:
            return Response({"error": "Missing chat ID or title"}, status=status.HTTP_400_BAD_REQUEST)

        chat, created = ChatSession.objects.update_or_create(
            id=chat_id,
            user=request.user,
            defaults={
                'title': title,
                'messages': messages
            }
        )
        
        return Response({"status": "success", "chat_id": chat.id}, status=status.HTTP_200_OK)

    def delete(self, request):
        """Delete a chat session"""
        chat_id = request.data.get('id')
        
        try:
            chat = ChatSession.objects.get(id=chat_id, user=request.user)
            chat.delete()
            return Response({"status": "deleted"}, status=status.HTTP_200_OK)
        except ChatSession.DoesNotExist:
            return Response({"error": "Chat not found"}, status=status.HTTP_404_NOT_FOUND)

