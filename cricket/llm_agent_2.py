import os
from google import genai
from django.db import connection
from decimal import Decimal
from groq import Groq
import wikipedia

gemini_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
groq_client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

# --- AGENT 1: THE DATA ENGINEER ---
SQL_SYSTEM_PROMPT = """
You are an expert cricket data analyst and a master PostgreSQL developer. 
Your task is to convert the user's natural language question into a highly optimized, read-only PostgreSQL query.

Here is the exact schema of the database you are querying:

Table: cricket_player
- player_id (VARCHAR, Primary Key)
- full_name (VARCHAR)

Table: cricket_match
- match_id (VARCHAR, Primary Key)
- team_a (VARCHAR), team_b (VARCHAR)
- match_date (DATE), venue (VARCHAR), match_winner (VARCHAR)

Table: cricket_powerplay
- match_id (VARCHAR, Foreign Key to cricket_match)
- inning_number (INT)
- powerplay_type (VARCHAR)
- start_over (DECIMAL), end_over (DECIMAL)

Table: cricket_delivery
- match_id (VARCHAR, Foreign Key to cricket_match)
- inning_number (INT), over_number (INT), ball_number (INT)
- batter_id (VARCHAR, Foreign Key to cricket_player)
- bowler_id (VARCHAR, Foreign Key to cricket_player)
- batter_runs (INT), extra_runs (INT), total_runs (INT)
- is_wide (BOOLEAN), is_noball (BOOLEAN), is_wicket (BOOLEAN)
- dismissal_kind (VARCHAR)

CRITICAL RULES:
1. ONLY return the raw SQL query. Do not include markdown formatting (like ```sql).
2. Use ILIKE for string matching on player names (e.g., p.full_name ILIKE '%MS Dhoni%').
3. Strike Rate: (SUM(batter_runs) * 100.0) / NULLIF(COUNT(CASE WHEN is_wide = FALSE AND is_noball = FALSE THEN 1 END), 0)
4. Economy Rate: (SUM(total_runs) * 6.0) / NULLIF(COUNT(CASE WHEN is_noball = FALSE AND is_wide = FALSE THEN 1 END), 0)
5. NEVER write an INSERT, UPDATE, DELETE, or DROP query. Only SELECT.
6. OVER NUMBERING IS 0-INDEXED. The 1st over is over_number = 0. The 20th over is over_number = 19. If a user asks for the Nth over, query for N-1.
7. PLAYER NAMES: Cricsheet often uses initials for first names (e.g., 'V Kohli', 'RG Sharma'). To avoid missing data, ALWAYS search using just the last name with wildcards (e.g., p.full_name ILIKE '%Kohli%' OR p.full_name ILIKE '%Sharma%').
8. SUBJECTIVE METRICS: If a user asks subjective questions about "power", "aggressive", or "dangerous" batting, include their strike rate AND calculate the number of sixes hit: SUM(CASE WHEN d.batter_runs = 6 THEN 1 ELSE 0 END) AS total_sixes.
9. OUT OF DOMAIN QUERIES: If the user asks for historical facts or data that absolutely do not exist in this schema (e.g., T20 World Cup, ODI matches, International teams, live/today's matches, IPL trophies, or captains), DO NOT invent columns or force queries. You MUST return EXACTLY this fallback query: SELECT 'OUT_OF_DOMAIN' AS status;
10. COMPARISONS: If the user explicitly asks to compare two or more players (e.g., "who hit more sixes, A or B?"), NEVER use `LIMIT 1`. You MUST return the stats for ALL requested players so the downstream analyst can see the full data to write the comparison.
11. SQL PRECEDENCE: When combining `AND` with `OR` conditions in a `WHERE` clause, you MUST ALWAYS wrap the `OR` conditions in parentheses to prevent logical bleeding. Example: `WHERE condition_a AND (condition_b OR condition_c)`.
12. NON-CRICKET QUERIES: If the user asks a general knowledge question completely unrelated to cricket (e.g., politics, weather, movies, history), DO NOT return OUT_OF_DOMAIN. You MUST return EXACTLY this fallback query: SELECT 'NOT_CRICKET' AS status;
13. BALLS VS OVERS: The `cricket_delivery` table contains one row per BALL. If the user asks for the "number of overs" or "overs bowled", do NOT just use `COUNT(*)`. You must calculate the overs by dividing the total balls by 6.0 (e.g., `COUNT(*) / 6.0 AS total_overs`).
"""

# --- AGENT 2: THE SPORTS ANALYST ---
NL_SYSTEM_PROMPT = """
You are a friendly, expert cricket commentator and data analyst. 
Your job is to take a user's original question and the raw JSON data returned from a SQL database, and output a concise, conversational answer.

CRITICAL RULES:
1. If the database result is missing a specific parameter, explicitly state that it doesn't exist.
2. Keep the response engaging, factual, and passionate about cricket. 
3. Do NOT mention the SQL query, database schemas, or JSON formatting.
4. LANGUAGE MATCHING: You MUST reply in the exact same language or dialect the user used. e.g. If they ask in Hindi or Hinglish, reply in natural, conversational Hinglish.
5. DEBATE RESOLUTION: If comparing two players, use the provided stats (like strike rate and total sixes) to declare a statistical winner based on the data.
6. LIVE WEB KNOWLEDGE: If you receive 'Web Search Context' instead of a database result, use that exact real-time information to answer the question. Do not rely on your outdated training memory. Trust the web context completely to provide accurate, up-to-date facts (like the 2024 T20 World Cup winners).
"""

def contextualize_query(raw_question, chat_history):
    if not chat_history:
        return raw_question
        
    history_text = "\n".join([f"{msg['role']}: {msg['content']}" for msg in chat_history[-3:]])
    prompt = f"""Given the conversation history, rewrite the user's latest question to be a standalone question. Replace pronouns (he, she, they, it) with the actual player or team names. Output ONLY the rewritten question, no extra conversational text.

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


def generate_and_execute_sql(user_question, chat_history=None):
    try:
        sql_response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": SQL_SYSTEM_PROMPT},
                {"role": "user", "content": f"User Question: {user_question}\nSQL Query:"}
            ],
            temperature=0.0 
        )
        raw_llm_response = sql_response.choices[0].message.content
    except Exception as e:
        return {"error": f"Groq SQL Generation failed: {str(e)}"}
    
    cleaned_sql = raw_llm_response.strip().replace("```sql", "").replace("```", "").strip()
    
    forbidden_keywords = ['DROP', 'DELETE', 'UPDATE', 'INSERT', 'ALTER', 'TRUNCATE']
    if any(keyword in cleaned_sql.upper() for keyword in forbidden_keywords):
        return {"error": "Forbidden destructive query detected.", "sql_used": cleaned_sql}

    # --- PHASE 2: EXECUTE SQL & GENERATE NATURAL LANGUAGE ---
    try:
        with connection.cursor() as cursor:
            cursor.execute(cleaned_sql)
            columns = [col[0] for col in cursor.description]
            results = cursor.fetchall()
            formatted_data = []
            for row in results:
                row_dict = {}
                for i, col in enumerate(columns):
                    value = row[i]
                    if isinstance(value, Decimal):
                        value = float(value) # Sanitize the Decimal!
                    row_dict[col] = value
                formatted_data.append(row_dict)

            # --- THE WEB SEARCH ROUTER ---
            context_type = "Database Result"
            context_data = formatted_data

            if len(formatted_data) == 1:
                status = formatted_data[0].get('status')
                
                # The Bouncer: Block non-cricket queries immediately
                if status == 'NOT_CRICKET':
                    context_type = "System Message"
                    context_data = [{"body": "The user asked a non-cricket question. Politely remind them that you are Cricmind, an AI specialized in cricket, and ask them a fun cricket trivia question to get them back on topic."}]
                
                # The Researcher: Fetch cricket history from Wikipedia
                elif status == 'OUT_OF_DOMAIN':
                    try:
                        # 1. Ask Groq to format the question as a Wikipedia Page Title
                        search_prompt = f"Convert this cricket question into a specific Wikipedia search query. If the user asks about a final match, explicitly include the word 'final' (e.g., '2024 ICC Men's T20 World Cup final'). Output ONLY the title, no quotes: {user_question}"
                        keyword_response = groq_client.chat.completions.create(
                            model="llama-3.3-70b-versatile",
                            messages=[{"role": "user", "content": search_prompt}],
                            temperature=0.0 
                        )
                        search_query = keyword_response.choices[0].message.content.strip().replace('"', '')
                        
                        # 2. Search Wikipedia for the closest matching page
                        wiki_search_results = wikipedia.search(search_query)
                        
                        if wiki_search_results:
                            # Grab the exact summary from the top result (first 4 sentences usually contains the winner)
                            best_page = wiki_search_results[0]
                            try:
                                page_summary = wikipedia.summary(best_page, sentences=10)
                                context_data = [{"source": best_page, "body": page_summary}]
                            except wikipedia.exceptions.DisambiguationError as e:
                                # If Wiki gets confused between two pages, grab the first option
                                page_summary = wikipedia.summary(e.options[0], sentences=10)
                                context_data = [{"source": e.options[0], "body": page_summary}]
                        else:
                            context_data = [{"body": f"Wikipedia returned no results for '{search_query}'."}]

                        context_type = "Web Search Context"
                    except Exception as e:
                        context_type = "Web Search Context"
                        context_data = [{"body": f"Wikipedia search failed: {str(e)}"}]
                
            # --- AGENT 2 KICKS IN HERE ---
            try:
                nl_prompt = f"User Question: {user_question}\n{context_type}: {context_data}\nProvide a natural language answer:"
                
                nl_response = groq_client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[
                        {"role": "system", "content": NL_SYSTEM_PROMPT},
                        {"role": "user", "content": nl_prompt}
                    ],
                    temperature=0.3 # Lowered slightly so it sticks strictly to the web facts
                )
                nl_text = nl_response.choices[0].message.content.strip()
            except Exception:
                nl_text = "Here is the data you requested."

            return {
                "success": True,
                "answer": nl_text,          # Human-readable text for the chat UI
                "data": context_data,         # Raw data for React charts/tables
                "sql_used": cleaned_sql if context_type == "Database Result" else "Routed to Live Web Search"
            }
            
    except Exception as e:
        return {
            "success": False, 
            "error": f"Database execution failed: {str(e)}", 
            "sql_used": cleaned_sql
        }