import os
import google.generativeai as genai
from django.db import connection
from decimal import Decimal
from groq import Groq
import wikipedia
import datetime

genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
groq_client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

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
2. Use ILIKE with wildcards for string matching on player names (e.g., batter_name ILIKE '%Kohli%').
3. NEVER write an INSERT, UPDATE, DELETE, or DROP query. Only SELECT.
4. OVER NUMBERING: over_number is 0-indexed. The 1st over is over_number = 0.
5. OUT OF DOMAIN: If the query is completely outside this schema (e.g., ODI stats, live scores, international teams), output: SELECT 'OUT_OF_DOMAIN' AS status;
6. NOT CRICKET: If it is not about cricket (e.g., politics, movies), output: SELECT 'NOT_CRICKET' AS status;

7. AMBIGUITY OF 'FINAL': In sports, "final" means the tournament Championship match, NOT the most recent chronological match. If the user asks for "final matches", do NOT use ORDER BY date DESC LIMIT 1;

EXAMPLES OF SMART COLUMN USAGE:
- Strike Rate: (SUM(batter_runs) * 100.0) / NULLIF(SUM(is_legal_ball), 0)
- Economy Rate: (SUM(total_runs) * 6.0) / NULLIF(SUM(is_legal_ball), 0)
- Bowler Wickets taken: SUM(is_bowler_wicket)
- Overs Bowled: SUM(is_legal_ball) / 6.0
- Catches taken by a fielder/keeper: COUNT(*) WHERE fielder_name ILIKE '%Dhoni%' AND dismissal_kind IN ('caught', 'caught and bowled')
- Stumpings by a keeper: COUNT(*) WHERE fielder_name ILIKE '%Dhoni%' AND dismissal_kind = 'stumped'
- Run outs by a fielder: COUNT(*) WHERE fielder_name ILIKE '%Dhoni%' AND dismissal_kind = 'run out'
- Wicketkeepers only: Filter by players who have stumpings: WHERE fielder_name IN (SELECT fielder_name FROM vw_delivery_analytics WHERE dismissal_kind = 'stumped')
- Runs vs Opponent: SUM(batter_runs) WHERE batter_name ILIKE '%Dhoni%' AND bowling_team ILIKE '%Mumbai Indians%'
- Tournament Finals: SELECT * FROM vw_match_summary WHERE event_stage = 'Final'
- Other Playoff Stages (Qualifiers, Eliminators, Semi Finals): SELECT * FROM vw_match_summary WHERE event_stage ILIKE '%Eliminator%' OR event_stage ILIKE '%Qualifier%' OR event_stage ILIKE '%Semi Final%'
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
5. DEBATE RESOLUTION: If comparing two players, use the provided stats to declare a statistical winner based on the data.
6. LIVE WEB KNOWLEDGE: If you receive 'Web Search Context' instead of a database result, use that exact real-time information to answer the question. Do not rely on your outdated training memory. Trust the web context completely.
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


def generate_and_execute_sql(user_question):
    # --- PHASE 1: GENERATE SQL ---
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
                        value = float(value)
                    elif isinstance(value, (datetime.date, datetime.datetime)):
                        value = value.isoformat()
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
                            # Grab the exact summary from the top result
                            best_page = wiki_search_results[0]
                            try:
                                page_summary = wikipedia.summary(best_page, sentences=10)
                                context_data = [{"source": best_page, "body": page_summary}]
                            except wikipedia.exceptions.DisambiguationError as e:
                                # If Wiki gets confused, grab the first option
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
                    temperature=0.3 # Lowered slightly to stick strictly to facts
                )
                nl_text = nl_response.choices[0].message.content.strip()
            except Exception:
                nl_text = "Here is the data you requested."

            return {
                "success": True,
                "answer": nl_text,
                "data": context_data,
                "sql_used": cleaned_sql if context_type == "Database Result" else "Routed to Live Web Search"
            }
            
    except Exception as e:
        return {
            "success": False, 
            "error": f"Database execution failed: {str(e)}", 
            "sql_used": cleaned_sql
        }