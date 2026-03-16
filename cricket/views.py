import os
import logging
from google import genai
import requests
from django.core.cache import cache
from google.genai import types
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from pgvector.django import CosineDistance
from django.db import connection
from django.db.models import Q
from rest_framework.permissions import IsAuthenticated
from .models import SemanticCache, Player
from .news_service import get_news_provider
from .llm_agent import groq_client
from .llm_agent import generate_and_execute_sql, contextualize_query

# Set up the logger
logger = logging.getLogger(__name__)

gemini_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

class AskAIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        raw_question = request.data.get('question')
        chat_history = request.data.get('history', [])
        
        logger.info(f"--- NEW REQUEST RECEIVED ---")
        logger.info(f"Raw Question: {raw_question}")
        
        if not raw_question:
            logger.warning("Request rejected: No question provided.")
            return Response({"error": "No question provided."}, status=400)

        try:
            question = contextualize_query(raw_question, chat_history)
            logger.info(f"Contextualized Question: {question}")

            logger.info("Generating vector embedding for Semantic Cache lookup...")
            embedding_response = gemini_client.models.embed_content(
                model="gemini-embedding-001",
                contents=question,
                config=types.EmbedContentConfig(output_dimensionality=768)
            )
            query_vector = embedding_response.embeddings[0].values

            closest_match = SemanticCache.objects.annotate(
                distance=CosineDistance('question_embedding', query_vector)
            ).order_by('distance').first()

            match_distance = closest_match.distance if closest_match else None
            logger.info(f"Closest cache match distance: {match_distance}")

            if closest_match and closest_match.distance < 0.02:
                logger.info("🟢 CACHE HIT! Returning cached response.")
                payload = closest_match.response_payload
                payload['cached_via'] = 'semantic_cache'
                payload['distance'] = closest_match.distance
                return Response(payload, status=status.HTTP_200_OK)

            logger.info("🔴 CACHE MISS! Routing to LLM Agent...")
            result = generate_and_execute_sql(question)
            
            if result.get("success"):
                logger.info("Saving successful LLM response to Semantic Cache.")
                SemanticCache.objects.create(
                    original_question=question,
                    question_embedding=query_vector,
                    response_payload=result
                )
                result['cached_via'] = 'fresh_llm_generation'
                result['missed_by_distance'] = match_distance
                return Response(result, status=status.HTTP_200_OK)
                
            logger.error(f"LLM Pipeline failed: {result.get('error')}")
            return Response(result, status=status.HTTP_400_BAD_REQUEST)
            
        except Exception as e:
            logger.exception(f"CRITICAL ERROR in AskAIView: {str(e)}")
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class NewsHubView(APIView):
    permission_classes = []

    def get(self, request):
        user_query = request.GET.get('q', 'cricket')
        
        cache_key = f"news_{user_query.replace(' ', '_').lower()}"
        
        cached_data = cache.get(cache_key)
        if cached_data:
            logger.info(f"News Cache HIT for query: {user_query}")
            return Response(cached_data, status=status.HTTP_200_OK)

        logger.info(f"News Cache MISS for query: {user_query}. Fetching fresh data...")
        
        api_query = user_query
        cricket_keywords = ['cricket', 'ipl', 'bcci', 'icc', 't20', 'odi', 'test', 'match']
        if not any(word in user_query.lower() for word in cricket_keywords):
            api_query = f"{user_query} cricket" # Forces the API to only return articles containing BOTH words
            
        provider = get_news_provider()
        articles = provider.fetch_news(api_query)

        if not articles:
            return Response({"error": "No cricket-related news found for this search."}, status=400)

        headlines_text = "\n".join([f"- {a['title']}: {a['description']}" for a in articles[:8]])
        
        prompt = f"""You are an elite sports journalist. The user searched for '{user_query}'. 
Read these recent headlines and write a punchy, 3-bullet-point executive summary of the most important updates. 

CRITICAL RULES:
- STRICTLY IGNORE any articles that are NOT about the sport of cricket (e.g., ignore restaurant reviews, Bollywood news, or business investments).
- If none of the articles are about cricket, reply: "No major cricket updates found for this search."
- Output ONLY the bullet points, starting each with a relevant emoji.

Headlines:
{headlines_text}"""

        try:
            summary_response = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2
            )
            ai_summary = summary_response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Groq News Summarization failed: {e}")
            ai_summary = "AI summary currently unavailable. Please read the articles below."

        response_data = {
            "query": user_query, 
            "ai_summary": ai_summary,
            "articles": articles
        }

        cache.set(cache_key, response_data, timeout=3600)

        return Response(response_data, status=status.HTTP_200_OK)


class PlayerProfileView(APIView):
    permission_classes = []

    def get(self, request):
        player_name = request.GET.get('name', '')
        logger.info("--- NEW REQUEST: PlayerProfileView ---")
        logger.info(f"Requested Player: '{player_name}'")
        
        if not player_name:
            logger.warning("Rejected: No player name provided in query parameters.")
            return Response({"error": "Please provide a player name."}, status=status.HTTP_400_BAD_REQUEST)

        profile_data = {
            "name": player_name.title(),
            "role": "Unknown",
            "batting": None,
            "bowling": None,
            "batting_splits": [],
            "bowling_splits": []
        }

        try:
            with connection.cursor() as cursor:
                logger.info("Executing Batting Stats query...")
                batting_query = """
                    WITH match_scores AS (
                        SELECT match_id, SUM(batter_runs) as match_runs
                        FROM vw_delivery_analytics
                        WHERE batter_name ILIKE %s
                        GROUP BY match_id
                    ),
                    milestones AS (
                        SELECT 
                            MAX(match_runs) as highest_score,
                            SUM(CASE WHEN match_runs >= 50 AND match_runs < 100 THEN 1 ELSE 0 END) as fifties,
                            SUM(CASE WHEN match_runs >= 100 THEN 1 ELSE 0 END) as hundreds
                        FROM match_scores
                    )
                    SELECT 
                        d.batter_name,
                        COUNT(DISTINCT d.match_id) as innings,
                        SUM(d.batter_runs) as total_runs,
                        SUM(CASE WHEN d.batter_runs = 4 THEN 1 ELSE 0 END) as fours,
                        SUM(CASE WHEN d.batter_runs = 6 THEN 1 ELSE 0 END) as sixes,
                        SUM(CASE WHEN d.is_wicket = 1 AND d.player_dismissed = d.batter_name THEN 1 ELSE 0 END) as dismissals,
                        COUNT(d.ball_number) as balls_faced,
                        (SELECT highest_score FROM milestones) as highest_score,
                        (SELECT fifties FROM milestones) as fifties,
                        (SELECT hundreds FROM milestones) as hundreds
                    FROM vw_delivery_analytics d
                    WHERE d.batter_name ILIKE %s
                    GROUP BY d.batter_name;
                """
                cursor.execute(batting_query, [f"%{player_name}%", f"%{player_name}%"])
                bat_row = cursor.fetchone()

                if bat_row:
                    logger.info(f"Batting data found for: {bat_row[0]}")
                    profile_data["name"] = bat_row[0]
                    runs = bat_row[2] or 0
                    dismissals = bat_row[5] or 0
                    balls = bat_row[6] or 0
                    
                    profile_data["batting"] = {
                        "innings": bat_row[1],
                        "runs": runs,
                        "fours": bat_row[3],
                        "sixes": bat_row[4],
                        "highest_score": bat_row[7] or 0,
                        "fifties": bat_row[8] or 0,
                        "hundreds": bat_row[9] or 0,
                        "average": round(runs / dismissals, 2) if dismissals > 0 else float(runs),
                        "strike_rate": round((runs / balls) * 100, 2) if balls > 0 else 0.0
                    }

                logger.info("Executing Bowling Stats query...")
                bowling_query = """
                    WITH match_bowling AS (
                        SELECT match_id, 
                               SUM(CASE WHEN is_bowler_wicket = 1 THEN 1 ELSE 0 END) as match_wickets,
                               SUM(total_runs) as match_runs
                        FROM vw_delivery_analytics
                        WHERE bowler_name ILIKE %s
                        GROUP BY match_id
                    ),
                    best_bowling AS (
                        SELECT match_wickets, match_runs
                        FROM match_bowling
                        ORDER BY match_wickets DESC, match_runs ASC
                        LIMIT 1
                    )
                    SELECT 
                        d.bowler_name,
                        COUNT(DISTINCT d.match_id) as innings_bowled,
                        COUNT(d.ball_number) as balls_bowled,
                        SUM(d.total_runs) as runs_conceded,
                        SUM(CASE WHEN d.is_bowler_wicket = 1 THEN 1 ELSE 0 END) as wickets,
                        (SELECT match_wickets FROM best_bowling) as best_wickets,
                        (SELECT match_runs FROM best_bowling) as best_runs
                    FROM vw_delivery_analytics d
                    WHERE d.bowler_name ILIKE %s
                    GROUP BY d.bowler_name;
                """
                cursor.execute(bowling_query, [f"%{player_name}%", f"%{player_name}%"])
                bowl_row = cursor.fetchone()

                if bowl_row:
                    logger.info(f"Bowling data found for: {bowl_row[0]}")
                    profile_data["name"] = bowl_row[0]
                    balls_bowled = bowl_row[2] or 0
                    runs_conceded = bowl_row[3] or 0
                    wickets = bowl_row[4] or 0
                    overs = balls_bowled / 6.0
                    
                    best_w = bowl_row[5] or 0
                    best_r = bowl_row[6] or 0

                    profile_data["bowling"] = {
                        "innings": bowl_row[1],
                        "wickets": wickets,
                        "best_figure": f"{best_w}/{best_r}" if best_w > 0 else "0/0",
                        "economy": round(runs_conceded / overs, 2) if overs > 0 else 0.0,
                        "average": round(runs_conceded / wickets, 2) if wickets > 0 else 0.0,
                        "strike_rate": round(balls_bowled / wickets, 2) if wickets > 0 else 0.0
                    }

                if profile_data["batting"]:
                    logger.info("Fetching batting splits...")
                    cursor.execute("""
                        SELECT bowling_team, SUM(batter_runs) as runs, ROUND((SUM(batter_runs)::numeric / NULLIF(COUNT(ball_number), 0)) * 100, 2) as sr
                        FROM vw_delivery_analytics WHERE batter_name ILIKE %s GROUP BY bowling_team ORDER BY runs DESC;
                    """, [f"%{player_name}%"])
                    profile_data["batting_splits"] = [{"team": row[0], "runs": row[1], "strike_rate": float(row[2]) if row[2] else 0} for row in cursor.fetchall()]

                if profile_data["bowling"]:
                    logger.info("Fetching bowling splits...")
                    cursor.execute("""
                        SELECT batting_team, SUM(CASE WHEN is_bowler_wicket = 1 THEN 1 ELSE 0 END) as wickets
                        FROM vw_delivery_analytics WHERE bowler_name ILIKE %s GROUP BY batting_team ORDER BY wickets DESC;
                    """, [f"%{player_name}%"])
                    profile_data["bowling_splits"] = [{"team": row[0], "wickets": row[1]} for row in cursor.fetchall()]

                if profile_data["batting"] and profile_data["bowling"]:
                    if profile_data["batting"]["runs"] > 1000 and profile_data["bowling"]["wickets"] > 50:
                        profile_data["role"] = "Elite All-Rounder"
                    else:
                        profile_data["role"] = "All-Rounder"
                elif profile_data["bowling"]:
                    profile_data["role"] = "Bowler"
                elif profile_data["batting"]:
                    profile_data["role"] = "Batter"
                else:
                    logger.warning(f"Player profile build failed: No data found for '{player_name}'")
                    return Response({"error": "Player not found in database."}, status=status.HTTP_404_NOT_FOUND)

            logger.info(f"Successfully built profile for {profile_data['name']} as {profile_data['role']}")
            return Response(profile_data, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"PostgreSQL Execution failed in PlayerProfileView: {str(e)}", exc_info=True)
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)




class PlayerSearchView(APIView):
    permission_classes = []

    def get(self, request):
        raw_query = request.GET.get('q', '').strip().lower()
        logger.info("--- NEW REQUEST: PlayerSearchView ---")
        logger.info(f"Raw Search Query: '{raw_query}'")
        
        if len(raw_query) < 2:
            return Response([], status=status.HTTP_200_OK)
            
        aliases = {
            "virat": "v kohli",
            "rohit": "rg sharma",
            "dhoni": "ms dhoni",
            "mahi": "ms dhoni",
            "thala": "ms dhoni",
            "sachin": "sr tendulkar",
            "bumrah": "jj bumrah",
            "jadeja": "ra jadeja",
            "ashwin": "r ashwin",
            "hardik": "hh pandya",
            "surya": "sa yadav",
            "sky": "sa yadav",
            "pant": "rr pant",
            "kl": "kl rahul",
            "shami": "md shami",
            "gill": "shubman gill"
        }

        db_query = aliases.get(raw_query, raw_query)
        if raw_query in aliases:
            logger.info(f"Alias matched: '{raw_query}' -> '{db_query}'")

        search_condition = Q(full_name__icontains=db_query)

        parts = raw_query.split()
        if len(parts) >= 2:
            first_initial = parts[0][0]
            last_name = parts[-1]
            smart_condition = Q(full_name__istartswith=first_initial) & Q(full_name__icontains=last_name)
            search_condition |= smart_condition
            logger.info(f"Applied Smart Middle Initial Bypass: Starts with '{first_initial}', Contains '{last_name}'")

        try:
            matching_players = Player.objects.filter(
                search_condition
            ).values_list('full_name', flat=True)[:5]
            
            unique_players = list(dict.fromkeys(matching_players))
            logger.info(f"Search successful. Found {len(unique_players)} matches: {unique_players}")
            
            return Response(unique_players, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.error(f"Database query failed in PlayerSearchView: {str(e)}", exc_info=True)
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class LiveMatchesView(APIView):
    permission_classes = []

    def get(self, request):
        logger.info("--- NEW REQUEST: LiveMatchesView ---")
        api_key = os.environ.get("CRICAPI_KEY")

        if not api_key:
            logger.error("CRICAPI_KEY is missing from environment variables.")
            return Response({"error": "Live scores are currently unavailable (API key missing)."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        try:
            url = f"https://api.cricapi.com/v1/currentMatches?apikey={api_key}&offset=0"
            logger.info("Fetching live matches from external API...")
            
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()

            if data.get("status") != "success":
                logger.warning(f"External API returned an error: {data}")
                return Response({"error": "Failed to fetch live matches from provider."}, status=status.HTTP_502_BAD_GATEWAY)

            matches = data.get("data", [])
            logger.info(f"Successfully fetched {len(matches)} live matches.")
            
            return Response(matches, status=status.HTTP_200_OK)

        except requests.exceptions.RequestException as e:
            logger.error(f"Network error fetching live matches: {str(e)}", exc_info=True)
            return Response({"error": "Network error communicating with live score provider."}, status=status.HTTP_502_BAD_GATEWAY)
        except Exception as e:
            logger.error(f"Unexpected error in LiveMatchesView: {str(e)}", exc_info=True)
            return Response({"error": "An unexpected error occurred."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

