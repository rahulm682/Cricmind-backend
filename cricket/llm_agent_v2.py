import os
import logging
from typing import List, Dict

from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_classic.agents import AgentExecutor, create_openai_tools_agent
from langchain_community.agent_toolkits import SQLDatabaseToolkit, create_sql_agent
from langchain_community.utilities import SQLDatabase
from langchain_community.tools.wikipedia.tool import WikipediaQueryRun
from langchain_community.utilities.wikipedia import WikipediaAPIWrapper

from groq import Groq
from google import genai
from google.genai import types

from django.db import connection
from pgvector.django import CosineDistance
from cricket.models import SmartSQLExample

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

groq_client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

def contextualize_query(raw_question, chat_history):
    """Rewrites the user's question to be standalone based on chat history."""
    if not chat_history:
        return raw_question
        
    history_text = "\n".join([f"{msg.get('role', 'user')}: {msg.get('content', '')}" for msg in chat_history[-3:]])
    prompt = f"""Given the conversation history, rewrite the user's latest question to be a standalone question. 
    CRITICAL RULES: 
    1. TRANSLATION: If the question is in Hindi, Hinglish, or any other language, strictly translate it into standard English.
    2. Replace pronouns (he, she, they, it) with the actual player or team names. 
    3. Do NOT add any extra words, context, or assumptions. Output exactly the standalone English question and nothing else.
    4. REMOVE any raw database IDs or technical SQL artifacts.
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
    except Exception as e:
        logger.warning(f"Contextualization failed: {e}")
        return raw_question

def get_rag_context(user_question: str) -> str:
    """Fetches relevant SQL cheat codes using your existing pgvector logic."""
    try:
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
            return "\n\n".join([f"Q: {ex.question}\nSQL: {ex.sql_query}" for ex in similar_examples])
    except Exception as e:
        logger.error(f"RAG Lookup failed: {e}")
    return "No similar examples found."

def build_cricmind_agent(rag_context: str):
    """Initializes the production-grade LangChain Agent."""
    
    llm = ChatGroq(
        api_key=GROQ_API_KEY,
        model_name="llama-3.3-70b-versatile",
        temperature=0.3
    )
    
    raw_db_url = DATABASE_URL
    if raw_db_url and raw_db_url.startswith("postgres://"):
        safe_db_url = raw_db_url.replace("postgres://", "postgresql+psycopg2://", 1)
    else:
        safe_db_url = raw_db_url

    try:
        db = SQLDatabase.from_uri(
            safe_db_url,
            include_tables=['vw_match_summary', 'vw_delivery_analytics', 'vw_batter_stats', 'vw_bowler_stats', 'vw_player_master', 'vw_team_stats'],
            view_support=True,
            schema="public"
        )
    except ValueError:
        db = SQLDatabase.from_uri(
            safe_db_url,
            view_support=True,
            schema="public"
        )

    db._usable_tables = {
        'vw_match_summary', 'vw_delivery_analytics', 
        'vw_batter_stats', 'vw_bowler_stats', 'vw_player_master',
        'vw_team_stats'
    }
    
    sql_toolkit = SQLDatabaseToolkit(db=db, llm=llm)
    wiki = WikipediaQueryRun(api_wrapper=WikipediaAPIWrapper())
    
    extra_tools = [wiki]

    template = """You are CricMind, an elite Cricket AI Analyst. 
    You have access to a SQL database with IPL match summaries and ball-by-ball data.
    
    CRITICAL RULES:
    1. For stats/match results: Always use the SQL tools.
    2. STRICT NAME FORMATTING: Players are stored using initials. You MUST convert full names to a wildcard format: `FirstInitial%LastName%`. 
       - "Ishant Sharma" -> ILIKE 'I%Sharma%'
       - "Bhuvneshwar Kumar" -> ILIKE 'B%Kumar%'
       - "Virat Kohli" -> ILIKE 'V%Kohli%'
       - "Shreyas Iyer" -> ILIKE 'S%Iyer%'
       NEVER use the full first name inside the ILIKE clause.
    3. NEVER use the 'sql_db_query_checker' tool. It causes system crashes. You must pass your query directly to 'sql_db_query'.
    4. Out of Domain: If the database cannot answer (e.g., ODI stats), use the Wikipedia tool.
    5. Non-Cricket: Refuse politely if the topic isn't cricket.
    6. ANTI-HALLUCINATION: If your SQL query returns an empty result `[]` or no rows, you MUST reply with "I could not find data for that player in the database." DO NOT invent stats.
    7. TRUST THE RAG: If a query matches one of the RELEVANT SQL EXAMPLES, execute it DIRECTLY using the 'sql_db_query' tool.
    8. NEVER return raw player IDs. Always use human names.

    RELEVANT SQL EXAMPLES:
    {rag_context}

    Note: You are using the {{dialect}} dialect and {{top_k}} limit.
    """

    formatted_system_prompt = template.format(rag_context=rag_context)

    personality_suffix = """
    When you provide the Final Answer:
    1. Act like an enthusiastic and expert Cricket Commentator.
    2. Present your analysis in a conversational paragraph.
    3. NEVER mention "SQL", "Database", or "Table names".

   CRITICAL CRICKET LOGIC (DEFAULT METRICS):
    - "Top", "Best", or "Leading" BATSMEN: Rank by total RUNS scored (descending).
    - "Top", "Best", or "Leading" BOWLERS: Rank by total WICKETS taken (descending).
    - "Top", "Best", or "Leading" FIELDERS: Rank by total CATCHES and RUN OUTS (descending).
    - "Top", "Best", or "Leading" WICKETKEEPERS: Rank by total DISMISSALS (Catches + Stumpings) (descending).
    - "Top", "Best", or "Leading" TEAMS: Rank by total MATCH WINS. Just count the 'match_winner' column in vw_match_summary.
   
    CRITICAL DATA RULES:
    - COMPREHENSIVE STATS: If a user asks for a player's "stats", "profile", or "details", you MUST explicitly query these exact columns: `SELECT player_name, innings_batted, total_runs, highest_score, batting_average, strike_rate, fifties, centuries, innings_bowled, total_wickets, economy_rate, bowling_average, best_bowling_figures FROM vw_player_master`. NEVER use `SELECT *`.
    - In your final response for comprehensive stats, you MUST list their Batting stats AND their Bowling stats if those values are greater than 0. Give a complete picture of the player.
    - TRUST THE RAG: If a query matches one of the RELEVANT SQL EXAMPLES, execute it DIRECTLY using the 'sql_db_query' tool. DO NOT use 'sql_db_query_checker'.
    - TERMINOLOGY MAPPING: If asked for a batter's "best figure" or "best score", you MUST use the 'highest_score' column. There is NO 'best_batting_figures' column.
    - For ANY player career stats (total runs, wickets, strike rate, averages, highest scores, fifties, centuries, innings), YOU MUST query 'vw_player_master', 'vw_batter_stats', or 'vw_bowler_stats'. 
    - DO NOT calculate player career stats manually from the raw 'vw_delivery_analytics' table using SUM or CTEs. Use the aggregated views.

    CRITICAL TIME BOUNDARY (NO LIVE DATA):
    - You ONLY have access to historical IPL data. You do NOT have live scores, schedules, or today's match data.
    - If a user asks about "today's match", "live score", or "who is playing right now", DO NOT write a SQL query. 
    - Instead, politely reply: "I only have access to historical data! To see what's happening on the pitch right now, please check the 'Live Matches' tab on your dashboard."

    CRITICAL CHARTING RULE:
    If your answer naturally involves comparing multiple players, teams, or showing stats over time based on the SQL results, you MUST append a JSON block at the very end of your response.
    - ANTI-HALLUCINATION: ONLY chart data that is explicitly returned by your SQL query. 
    - DO NOT invent comparison data (like adding other players) just to create a chart. 
    - If the SQL query only returns data for ONE player or ONE team, you MUST silently omit the chart. DO NOT explain why you are omitting the chart. DO NOT output phrases like "Since the query only returned data for one player, there is no need to generate a chart." Just end your response naturally.
    
    STRICT JSON RULES:
    - You must enclose the JSON exactly inside ```json and ```
    - Use DOUBLE QUOTES for all strings and keys.
    - NO trailing commas at the end of arrays or objects.
    - The JSON must strictly match this exact schema:
    
    ```json
    {
      "chart_type": "bar", 
      "title": "Runs Comparison",
      "labels": ["Rohit Sharma", "Virat Kohli"],
      "datasets": [
        {"label": "Total Runs", "data": [7048, 8671]},
        {"label": "Strike Rate", "data": [132.56, 133.30]}
      ]
    }
    ```
    """

    agent_executor = create_sql_agent(
        llm=llm,
        toolkit=sql_toolkit,
        verbose=True,
        agent_type="openai-tools",
        extra_tools=extra_tools,
        prefix=formatted_system_prompt,
        suffix=personality_suffix,
        max_iterations=5
    )
    
    return agent_executor

def ask_cricmind_v2(user_question: str, chat_history: List[Dict] = None):
    """The main entry point called by your Django view."""
    
    rag_context = get_rag_context(user_question)
    
    agent = build_cricmind_agent(rag_context)
    
    try:
        response = agent.invoke({"input": user_question})
        
        return {
            "success": True,
            "answer": response["output"],
            "sql_used": response.get("intermediate_steps", "Check logs for SQL details")
        }
    except Exception as e:
        logger.error(f"Agent failed: {e}")
        return {"success": False, "error": str(e)}
    
