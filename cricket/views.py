import os
import logging
import re
import json
import requests

from django.core.cache import cache
from django.db.models import Q
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from pgvector.django import CosineDistance

from google import genai
from google.genai import types

from .models import SemanticCache, Player
from .news_service import get_news_provider
from .llm_agent import groq_client
from .llm_agent_v2 import ask_cricmind_v2, contextualize_query
from .selectors import get_player_profile_data

logger = logging.getLogger(__name__)
gemini_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

# --- CONSTANTS ---
PLAYER_ALIASES = {
    "virat": "v kohli", "rohit": "rg sharma", "dhoni": "ms dhoni", "mahi": "ms dhoni",
    "thala": "ms dhoni", "sachin": "sr tendulkar", "bumrah": "jj bumrah",
    "jadeja": "ra jadeja", "ashwin": "r ashwin", "hardik": "hh pandya",
    "surya": "sa yadav", "sky": "sa yadav", "pant": "rr pant",
    "kl": "kl rahul", "shami": "md shami", "gill": "shubman gill"
}

class AskAIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        raw_question = request.data.get('question')
        chat_history = request.data.get('history', [])
        
        logger.info("--- NEW REQUEST: AskAIView ---")
        
        if not raw_question:
            logger.warning("Rejected: No question provided in payload.")
            return Response({"error": "No question provided."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            logger.info(f"raw_question: {raw_question}")
            question = contextualize_query(raw_question, chat_history)
            logger.info(f"Contextualized Question: {question}")
            
            embedding_response = gemini_client.models.embed_content(
                model="gemini-embedding-001",
                contents=question,
                config=types.EmbedContentConfig(output_dimensionality=768)
            )
            query_vector = embedding_response.embeddings[0].values

            closest_match = SemanticCache.objects.annotate(
                distance=CosineDistance('question_embedding', query_vector)
            ).filter(distance__lt=0.10).order_by('distance').first()

            if closest_match:
                logger.info(f"🟢 CACHE HIT! Distance: {closest_match.distance:.3f}")
                payload = closest_match.response_payload
                payload.update({'cached_via': 'semantic_cache', 'distance': closest_match.distance})
                return Response(payload, status=status.HTTP_200_OK)

            logger.info("🔴 CACHE MISS! Routing to LangChain Agent...")
            result = ask_cricmind_v2(question, chat_history)
            
            if not result.get("success"):
                logger.error(f"LangChain Agent failed: {result.get('error')}")
                return Response({"success": False, "error": "AI Agent failed."}, status=status.HTTP_400_BAD_REQUEST)

            raw_answer = result["answer"]
            chart_config = None
            
            json_match = re.search(r'```json\n(.*?)\n```', raw_answer, re.DOTALL)
            if json_match:
                try:
                    chart_config = json.loads(json_match.group(1))
                    raw_answer = raw_answer.replace(json_match.group(0), "").strip()
                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to parse chart JSON from LLM: {e}")

            payload = {
                "success": True,
                "answer": raw_answer,
                "chart_config": chart_config,
                "sql_used": result.get("sql_used"),
                "cached_via": "fresh_llm_generation",
            }

            logger.info("Saving successful LangChain response to Semantic Cache.")
            SemanticCache.objects.create(
                original_question=question,
                question_embedding=query_vector,
                response_payload=payload
            )
            return Response(payload, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.exception(f"CRITICAL ERROR in AskAIView: {str(e)}")
            return Response({"success": False, "error": "Critical error occurred."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class NewsHubView(APIView):
    permission_classes = []

    def get(self, request):
        user_query = request.GET.get('q', 'cricket')
        logger.info("--- NEW REQUEST: NewsHubView ---")
        
        cache_key = f"news_{user_query.replace(' ', '_').lower()}"
        
        if cached_data := cache.get(cache_key):
            logger.info(f"News Cache HIT for query: {user_query}")
            return Response(cached_data, status=status.HTTP_200_OK)

        logger.info(f"News Cache MISS for query: {user_query}. Fetching fresh data...")
        api_query = user_query
        cricket_keywords = {'cricket', 'ipl', 'bcci', 'icc', 't20', 'odi', 'test', 'match'}
        
        if not any(word in user_query.lower() for word in cricket_keywords):
            api_query = f"{user_query} cricket"
            
        provider = get_news_provider()
        articles = provider.fetch_news(api_query)

        if not articles:
            logger.warning(f"No cricket-related news found for query: {user_query}")
            return Response({"error": "No cricket-related news found."}, status=status.HTTP_404_NOT_FOUND)

        headlines_text = "\n".join([f"- {a['title']}: {a['description']}" for a in articles[:8]])
        prompt = f"Summarize these headlines in 3 bullet points with emojis. Ignore non-cricket news.\n{headlines_text}"

        try:
            summary_response = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2
            )
            ai_summary = summary_response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Groq News Summarization failed: {e}")
            ai_summary = "AI summary currently unavailable."

        response_data = {"query": user_query, "ai_summary": ai_summary, "articles": articles}
        cache.set(cache_key, response_data, timeout=3600)
        return Response(response_data, status=status.HTTP_200_OK)


class PlayerProfileView(APIView):
    permission_classes = []

    def get(self, request):
        player_name = request.GET.get('name', '').strip()
        logger.info("--- NEW REQUEST: PlayerProfileView ---")
        logger.info(f"Requested Player: '{player_name}'")
        
        if not player_name:
            logger.warning("Rejected: No player name provided in query parameters.")
            return Response({"error": "Please provide a player name."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            profile_data = get_player_profile_data(player_name)
            
            if not profile_data:
                logger.warning(f"Player profile build failed: No data found for '{player_name}'")
                return Response({"error": "Player not found in database."}, status=status.HTTP_404_NOT_FOUND)
                
            return Response(profile_data, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.error(f"Execution failed in PlayerProfileView: {str(e)}", exc_info=True)
            return Response({"error": "Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class PlayerSearchView(APIView):
    permission_classes = []

    def get(self, request):
        raw_query = request.GET.get('q', '').strip().lower()
        logger.info("--- NEW REQUEST: PlayerSearchView ---")
        logger.info(f"Raw Search Query: '{raw_query}'")
        
        if len(raw_query) < 2:
            return Response([], status=status.HTTP_200_OK)
            
        db_query = PLAYER_ALIASES.get(raw_query, raw_query)
        if raw_query in PLAYER_ALIASES:
            logger.info(f"Alias matched: '{raw_query}' -> '{db_query}'")
            
        search_condition = Q(full_name__icontains=db_query)

        parts = raw_query.split()
        if len(parts) >= 2:
            first_initial = parts[0][0]
            last_name = parts[-1]
            search_condition |= Q(full_name__istartswith=first_initial) & Q(full_name__icontains=last_name)
            logger.info(f"Applied Smart Middle Initial Bypass: Starts with '{first_initial}', Contains '{last_name}'")

        try:
            matching_players = Player.objects.filter(search_condition).values_list('full_name', flat=True).distinct()[:5]
            unique_players = list(matching_players)
            logger.info(f"Search successful. Found {len(unique_players)} matches: {unique_players}")
            return Response(unique_players, status=status.HTTP_200_OK)
        except Exception as e:
            logger.error(f"Database query failed in PlayerSearchView: {str(e)}", exc_info=True)
            return Response({"error": "Search failed"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class LiveMatchesView(APIView):
    permission_classes = []

    def get(self, request):
        logger.info("--- NEW REQUEST: LiveMatchesView ---")
        api_key = os.environ.get("CRICAPI_KEY")
        
        if not api_key:
            logger.error("CRICAPI_KEY is missing from environment variables.")
            return Response({"error": "API key missing."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        try:
            logger.info("Fetching live matches from external API (Synchronous)...")
            all_raw_matches = []
            offsets = [0, 25, 50, 75]
            
            for offset in offsets:
                url = f"https://api.cricapi.com/v1/currentMatches?apikey={api_key}&offset={offset}"
                response = requests.get(url, timeout=5)
                
                if response.status_code == 200:
                    data = response.json()
                    if data.get("status") == "success":
                        all_raw_matches.extend(data.get("data", []))
                    else:
                        break
                else:
                    break

            if not all_raw_matches:
                logger.warning("Failed to fetch matches from provider.")
                return Response({"error": "Failed to fetch matches."}, status=status.HTTP_502_BAD_GATEWAY)

            seen_ids = set()
            unique_matches = []
            for match in all_raw_matches:
                if match['id'] not in seen_ids:
                    seen_ids.add(match['id'])
                    unique_matches.append(match)

            premium_matches = []
            standard_matches = []
            for match in unique_matches:
                name = match.get("name", "").lower()
                if any(x in name for x in ["ipl", "indian premier league", "world cup"]):
                    premium_matches.append(match)
                else:
                    standard_matches.append(match)
            
            sorted_matches = premium_matches + standard_matches
            logger.info(f"Successfully compiled {len(sorted_matches)} unique live matches.")
            return Response(sorted_matches, status=status.HTTP_200_OK)

        except requests.exceptions.RequestException as e:
            logger.error(f"Network error fetching live matches: {str(e)}", exc_info=True)
            return Response({"error": "Network error communicating with live score provider."}, status=status.HTTP_502_BAD_GATEWAY)
        except Exception as e:
            logger.error(f"Unexpected error in LiveMatchesView: {str(e)}", exc_info=True)
            return Response({"error": "Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
