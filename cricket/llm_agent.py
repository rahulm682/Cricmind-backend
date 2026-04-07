import os
from google import genai
from google.genai import types
from django.db import connection
from decimal import Decimal
from groq import Groq
import wikipedia
import datetime
import logging

from pgvector.django import CosineDistance
from cricket.models import SmartSQLExample

gemini_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
groq_client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

logger = logging.getLogger(__name__)

# --- AGENT 1: THE DATA ENGINEER ---
SQL_SYSTEM_PROMPT = """
You are an expert cricket data analyst and a master PostgreSQL developer. 
Your task is to convert the user's natural language question into a highly optimized, read-only PostgreSQL query.

You are querying TWO highly optimized Materialized Views. Do NOT use the base tables.

Table 1: vw_match_summary
- match_id, season_year, match_date, venue, team_a, team_b
- match_winner, team_a_captain, team_b_captain, toss_winner, toss_decision
- win_margin_type ('runs' or 'wickets'), win_margin_amount, player_of_match
- event_name, event_stage ('Final', 'Qualifier 1', 'Eliminator', etc.), event_match_number, match_type

Table 2: vw_delivery_analytics (Ball-by-ball data)
- match_id, match_date, season_year, inning_number, batting_team, bowling_team, phase_of_play ('Powerplay', 'Middle', 'Death')
- batter_name, bowler_name, non_striker_name
- over_number, ball_number
- batter_runs, extra_runs, total_runs
- is_four (1/0), is_six (1/0), is_boundary (1/0)
- is_legal_ball (1/0), is_dot_ball (1/0)
- is_wicket (1/0), is_bowler_wicket (1/0), dismissal_kind, player_dismissed, fielder_name

CRITICAL RULES:
1. STRICTLY ONLY return the ONE final raw SQL query. NO explanations, NO preamble, NO conversational text. Do NOT output multiple queries. Your entire response MUST be executable PostgreSQL.
2. NEVER write an INSERT, UPDATE, DELETE, or DROP query. Only SELECT.
3. OVER NUMBERING: over_number is 0-indexed. The 1st over is over_number = 0.
4. OUT OF DOMAIN: If the query is completely outside this schema (e.g., ODI stats, live scores, international teams), output: SELECT 'OUT_OF_DOMAIN' AS status;
5. NOT CRICKET: If it is not about cricket (e.g., politics, movies), output: SELECT 'NOT_CRICKET' AS status;
6. NO UNIONS: NEVER use UNION or UNION ALL. If you need to calculate different sets of stats (like batting and fielding) for a single player, calculate them in separate CTEs (WITH clause) and JOIN them together to return a single row of columns.
7. VIEW ISOLATION: NEVER join `vw_match_summary` and `vw_delivery_analytics` together in the same query. If the user asks for a player's stats or just types their name, you must ONLY query `vw_delivery_analytics`.
8. PRECISE NAME MATCHING: Players are stored in the database as Initials + Last Name (e.g., 'V Kohli', 'SS Iyer'). If the user asks for a full name like 'Shreyas Iyer', you MUST use a strict ILIKE pattern combining the first initial and last name: `ILIKE 'S%Iyer%'`. Do NOT just use the last name (e.g., `ILIKE '%Iyer%'`) as it will incorrectly return multiple different players.
9. MILESTONES: To count centuries (100+) or fifties (50+), always use a CTE to first SUM batter_runs per match_id, then COUNT the results in the outer query.

Here are highly relevant SQL examples retrieved from the database to help you answer this specific question. USE THESE EXACT PATTERNS AND NAMES:
{retrieved_examples}
"""

# --- AGENT 2: THE SPORTS ANALYST ---
NL_SYSTEM_PROMPT = """
You are an expert, enthusiastic, and conversational Cricket Data Analyst. 
Your job is to take a user's original question and the raw JSON data returned from a database, and output a concise, friendly, and strictly factual answer.

CRITICAL RULES:
1. STRICT DATA ADHERENCE: Base your answer *only* on the provided data. Never guess or assume.
2. MISSING DATA: If the data is missing, explicitly state it is unavailable.
3. TONE & STYLE: Act like a friendly sports anchor. Present the stats naturally in a flowing, conversational paragraph. Use bullet points only if listing out more than 3 stats.
4. HIDE THE TECH: NEVER mention "rows", "records", "JSON", "SQL queries", or "database". Just give the answer directly.
5. NATURAL NAME RESOLUTION: If the database returns initials like "SS Iyer", just use the full name "Shreyas Iyer" naturally. NEVER explain the name mapping.
6. TARGET LOCK: If the database accidentally returns data for multiple players (e.g., Shreyas Iyer and Venkatesh Iyer), ONLY talk about the exact player the user originally asked about. Completely ignore the other players in the data.
7. LANGUAGE MATCHING: You MUST reply in the exact same language or dialect the user used.
8. LIVE WEB KNOWLEDGE: If you receive 'Web Search Context' instead of a database result, use that exact real-time information to answer the question. Do not rely on your outdated training memory.
"""

def contextualize_query(raw_question, chat_history):
    if not chat_history:
        return raw_question
        
    history_text = "\n".join([f"{msg['role']}: {msg['content']}" for msg in chat_history[-3:]])
    prompt = f"""Given the conversation history, rewrite the user's latest question to be a standalone question. Replace pronouns (he, she, they, it) with the actual player or team names. 
    CRITICAL: Do NOT add any extra words, context, or assumptions. If the user just types a name (e.g., "rohit sharma"), output exactly that name and nothing else.

History:
{history_text}

Latest Question: {raw_question}
Rewritten Question:"""

    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0
        )
        return response.choices[0].message.content.strip().replace('"', '')
    except Exception:
        return raw_question



def generate_and_execute_sql(user_question):
    logger.info(">>> Starting generate_and_execute_sql pipeline")
    
    retrieved_examples_text = ""
    try:
        logger.info("Step 0: Fetching RAG cheat codes from pgvector...")
        embed_response = gemini_client.models.embed_content(
            model="gemini-embedding-001",
            contents=user_question,
            config=types.EmbedContentConfig(output_dimensionality=768) 
        )
        query_vector = embed_response.embeddings[0].values

        similar_examples = SmartSQLExample.objects.order_by(
            CosineDistance('embedding', query_vector)
        )[:3]

        if similar_examples:
            logger.info(f"Found {len(similar_examples)} RAG examples. Injecting into prompt.")
            retrieved_examples_text = "\n\n".join(
                [f"- Question Context: {ex.question}\n  SQL Pattern: {ex.sql_query}" for ex in similar_examples]
            )
    except Exception as e:
        logger.warning(f"RAG Retrieval failed (Proceeding without RAG): {e}")

    dynamic_system_prompt = SQL_SYSTEM_PROMPT.format(retrieved_examples=retrieved_examples_text)

    logger.info("Step 1: Generating SQL via Groq...")
    try:
        sql_response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": dynamic_system_prompt},
                {"role": "user", "content": f"User Question: {user_question}\nSQL Query:"}
            ],
            temperature=0.0 
        )
        raw_llm_response = sql_response.choices[0].message.content
    except Exception as e:
        logger.error(f"Groq SQL Generation failed: {str(e)}")
        return {"error": f"Groq SQL Generation failed: {str(e)}"}
    
    cleaned_sql = raw_llm_response.strip().replace("```sql", "").replace("```", "").strip()
    logger.info(f"Generated Raw SQL: {cleaned_sql}")
    
    forbidden_keywords = ['DROP', 'DELETE', 'UPDATE', 'INSERT', 'ALTER', 'TRUNCATE']
    if any(keyword in cleaned_sql.upper() for keyword in forbidden_keywords):
        logger.critical(f"Security Alert: Forbidden SQL keyword detected in query: {cleaned_sql}")
        return {"error": "Forbidden destructive query detected.", "sql_used": cleaned_sql}

    logger.info("Step 2: Executing SQL in PostgreSQL...")
    try:
        with connection.cursor() as cursor:
            cursor.execute(cleaned_sql)
            columns = [col[0] for col in cursor.description]
            results = cursor.fetchall()
            
            logger.info(f"SQL execution successful. Retrieved {len(results)} rows.")
            
            formatted_data = []
            for row in results:
                row_dict = {}
                for i, col in enumerate(columns):
                    value = row[i]
                    if isinstance(value, Decimal):
                        value = float(value)
                    elif isinstance(value, (datetime.date, datetime.datetime)):
                        value = value.isoformat()
                    row_dict[col] = value
                formatted_data.append(row_dict)

            context_type = "Database Result"
            context_data = formatted_data

            if len(formatted_data) == 1:
                status = formatted_data[0].get('status')
                
                if status == 'NOT_CRICKET':
                    logger.info("Router: Detected Non-Cricket Query.")
                    context_type = "System Message"
                    context_data = [{"body": "The user asked a non-cricket question. Politely remind them that you are Cricmind, an AI specialized in cricket, and ask them a fun cricket trivia question to get them back on topic."}]
                
                elif status == 'OUT_OF_DOMAIN':
                    logger.info("Router: Detected Out of Domain Query. Executing Wikipedia fallback...")
                    try:
                        search_prompt = f"Convert this cricket question into a specific Wikipedia search query. Output ONLY the title, no quotes: {user_question}"
                        keyword_response = groq_client.chat.completions.create(
                            model="llama-3.3-70b-versatile",
                            messages=[{"role": "user", "content": search_prompt}],
                            temperature=0.0 
                        )
                        search_query = keyword_response.choices[0].message.content.strip().replace('"', '')
                        logger.info(f"Searching Wikipedia for: {search_query}")
                        
                        wiki_search_results = wikipedia.search(search_query)
                        
                        if wiki_search_results:
                            best_page = wiki_search_results[0]
                            try:
                                page_summary = wikipedia.summary(best_page, sentences=10)
                                context_data = [{"source": best_page, "body": page_summary}]
                            except wikipedia.exceptions.DisambiguationError as e:
                                page_summary = wikipedia.summary(e.options[0], sentences=10)
                                context_data = [{"source": e.options[0], "body": page_summary}]
                        else:
                            context_data = [{"body": f"Wikipedia returned no results for '{search_query}'."}]

                        context_type = "Web Search Context"
                    except Exception as e:
                        logger.error(f"Wikipedia search failed: {str(e)}")
                        context_type = "Web Search Context"
                        context_data = [{"body": f"Wikipedia search failed: {str(e)}"}]
                
            logger.info("Step 3: Generating final Natural Language summary...")
            try:
                nl_prompt = f"User Question: {user_question}\n{context_type}: {context_data}\nProvide a natural language answer:"
                
                nl_response = groq_client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[
                        {"role": "system", "content": NL_SYSTEM_PROMPT},
                        {"role": "user", "content": nl_prompt}
                    ],
                    temperature=0.3 
                )
                nl_text = nl_response.choices[0].message.content.strip()
                logger.info("Pipeline complete. Returning answer.")
            except Exception as e:
                logger.error(f"Agent 2 summary failed: {str(e)}")
                nl_text = "Here is the data you requested."

            return {
                "success": True,
                "answer": nl_text,
                "data": context_data,
                "sql_used": cleaned_sql if context_type == "Database Result" else "Routed to Live Web Search"
            }
            
    except Exception as e:
        logger.error(f"PostgreSQL Execution failed: {str(e)}")
        return {
            "success": False, 
            "error": f"Database execution failed: {str(e)}", 
            "sql_used": cleaned_sql
        }

         