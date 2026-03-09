import google.generativeai as genai
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from pgvector.django import CosineDistance
from .models import SemanticCache
from .llm_agent import generate_and_execute_sql, contextualize_query

class AskAIView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        raw_question = request.data.get('question')
        chat_history = request.data.get('history', [])
        
        if not raw_question:
            return Response({"error": "No question provided."}, status=400)

        try:
            question = contextualize_query(raw_question, chat_history)

            # 1. Generate the embedding (Fast, cheap API call)
            embedding_response = genai.embed_content(
                model="models/gemini-embedding-001",
                content=question,
                task_type="retrieval_query",
                output_dimensionality=768
            )
            query_vector = embedding_response['embedding']

            # 2. Vector Similarity Search
            # CosineDistance calculates the angle between two vectors. 
            # 0.0 means identical meaning, 1.0 means completely unrelated.
            closest_match = SemanticCache.objects.annotate(
                distance=CosineDistance('question_embedding', query_vector)
            ).order_by('distance').first()

            match_distance = closest_match.distance if closest_match else None

            # 3. Check the Semantic Threshold
            # A distance of < 0.1 usually means the intent is practically identical
            if closest_match and closest_match.distance < 0.02:
                payload = closest_match.response_payload
                payload['cached_via'] = 'semantic_cache'
                payload['distance'] = closest_match.distance # Included so you can see the math!
                return Response(payload, status=status.HTTP_200_OK)

            # 4. Cache Miss: Run the full LLM Text-to-SQL Pipeline
            result = generate_and_execute_sql(question)
            
            # 5. Save successful results to the Vector Cache
            if result.get("success"):
                SemanticCache.objects.create(
                    original_question=question,
                    question_embedding=query_vector,
                    response_payload=result
                )
                result['cached_via'] = 'fresh_llm_generation'
                result['missed_by_distance'] = match_distance
                return Response(result, status=status.HTTP_200_OK)
                
            return Response(result, status=status.HTTP_400_BAD_REQUEST)
            
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
