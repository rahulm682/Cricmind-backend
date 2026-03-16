import logging
from django.contrib.auth.models import User
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.db import transaction
from .models import UserProfile

logger = logging.getLogger(__name__)


class RegisterView(APIView):
    permission_classes = []

    def post(self, request):
        logger.info("--- NEW REQUEST: User Registration ---")
        
        email = request.data.get('email', '').strip().lower()
        password = request.data.get('password', '')
        first_name = request.data.get('first_name', '').strip()
        last_name = request.data.get('last_name', '').strip()
        phone_number = request.data.get('phone_number', '').strip()
        favorite_team = request.data.get('favorite_team', '').strip()

        if not email or not password:
            logger.warning("Registration failed: Missing email or password.")
            return Response(
                {"error": "Please provide both an email and a password."}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        if User.objects.filter(username=email).exists():
            logger.warning(f"Registration failed: Email '{email}' already exists.")
            return Response(
                {"error": "An account with this email already exists."}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    username=email, 
                    email=email, 
                    password=password,
                    first_name=first_name,
                    last_name=last_name
                )
                
                UserProfile.objects.create(
                    user=user, 
                    phone_number=phone_number,
                    favorite_team=favorite_team
                )

            logger.info(f"Successfully created new user: {user.email}")
            return Response(
                {"message": "Account created successfully! You can now log in."}, 
                status=status.HTTP_201_CREATED
            )
            
        except Exception as e:
            logger.error(f"Failed to create user: {str(e)}", exc_info=True)
            return Response(
                {"error": "An internal error occurred while creating the account."}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

