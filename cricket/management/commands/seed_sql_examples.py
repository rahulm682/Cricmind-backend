import os
from google import genai
from google.genai import types
from django.core.management.base import BaseCommand
from cricket.models import SmartSQLExample

class Command(BaseCommand):
    help = "Seeds the pgvector database with few-shot SQL examples for RAG"

    def handle(self, *args, **kwargs):
        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

        examples = [
            {
                "question": "How to calculate a batter's strike rate",
                "sql_query": "Strike Rate is calculated as: (SUM(batter_runs) * 100.0) / NULLIF(SUM(is_legal_ball), 0)"
            },
            {
                "question": "How to calculate a bowler's economy rate",
                "sql_query": "Economy Rate is calculated as: (SUM(total_runs) * 6.0) / NULLIF(SUM(is_legal_ball), 0)"
            },
            {
                "question": "How to calculate catches and stumpings by a fielder or wicketkeeper against each opponent",
                "sql_query": "For fielding stats against an opponent, the opponent is the `batting_team`. Example: SELECT batting_team AS opponent, SUM(CASE WHEN dismissal_kind IN ('caught', 'caught and bowled') THEN 1 ELSE 0 END) AS catches FROM vw_delivery_analytics WHERE fielder_name = 'MS Dhoni' GROUP BY batting_team"
            },
            {
                "question": "How to calculate total tournament trophies or championships won by each team in the IPL",
                "sql_query": "SELECT match_winner AS team, COUNT(*) AS trophies FROM vw_match_summary WHERE event_stage = 'Final' AND match_winner IS NOT NULL GROUP BY match_winner ORDER BY trophies DESC"
            },
            {
                "question": "What are the exact database names for star players like Rohit Sharma, Virat Kohli, MS Dhoni, Ravindra Jadeja, and Hardik Pandya?",
                "sql_query": "You MUST use these exact string matches for these specific players: Rohit Sharma is 'RG Sharma', Virat Kohli is 'V Kohli', MS Dhoni is 'MS Dhoni', Ravindra Jadeja is 'RA Jadeja', and Hardik Pandya is 'HH Pandya'. For others, use initials like ILIKE 'S% Dhawan'."
            },
            {
                "question": "How to find runs scored by a batter against a specific opponent team",
                "sql_query": "When calculating batting stats against an opponent, the opponent is the `bowling_team`. Example: SUM(batter_runs) WHERE batter_name = 'V Kohli' AND bowling_team ILIKE '%Mumbai Indians%'"
            },
            {
                "question": "Give me a batting summary or overview for a batsman (e.g., Rohit Sharma, Virat Kohli).",
                "sql_query": "When asked for a general overview of a batsman, provide their total runs, strike rate, and boundaries. Example: SELECT batter_name, SUM(batter_runs) AS total_runs, (SUM(batter_runs) * 100.0) / NULLIF(SUM(is_legal_ball), 0) AS strike_rate, SUM(is_boundary) AS total_boundaries FROM vw_delivery_analytics WHERE batter_name = 'RG Sharma' GROUP BY batter_name"
            },
            {
                "question": "Give me a bowling summary or overview for a bowler (e.g., Jasprit Bumrah, Rashid Khan).",
                "sql_query": "When asked for a general overview of a bowler, provide their total wickets, economy rate, and total legal overs bowled. Example: SELECT bowler_name, SUM(is_bowler_wicket) AS total_wickets, (SUM(total_runs) * 6.0) / NULLIF(SUM(is_legal_ball), 0) AS economy_rate, SUM(is_legal_ball) / 6.0 AS overs_bowled FROM vw_delivery_analytics WHERE bowler_name ILIKE 'J% Bumrah' GROUP BY bowler_name"
            },
            {
                "question": "Give me an all-rounder summary for a player who bats and bowls (e.g., Ravindra Jadeja, Hardik Pandya).",
                "sql_query": "To safely get both batting and bowling stats for a single player without using a UNION, use CTEs and a FULL OUTER JOIN. Example: WITH Batting AS (SELECT batter_name AS player, SUM(batter_runs) AS runs FROM vw_delivery_analytics WHERE batter_name = 'RA Jadeja' GROUP BY batter_name), Bowling AS (SELECT bowler_name AS player, SUM(is_bowler_wicket) AS wickets FROM vw_delivery_analytics WHERE bowler_name = 'RA Jadeja' GROUP BY bowler_name) SELECT COALESCE(Batting.player, Bowling.player) AS player_name, Batting.runs, Bowling.wickets FROM Batting FULL OUTER JOIN Bowling ON Batting.player = Bowling.player;"
            },
            {
                "question": "ms dhoni records or stats",
                "sql_query": ''
            }
        ]

        self.stdout.write("Wiping old examples...")
        SmartSQLExample.objects.all().delete()

        self.stdout.write(f"Generating vectors and seeding {len(examples)} examples...")

        for ex in examples:
            try:
                response = client.models.embed_content(
                    model="gemini-embedding-001",
                    contents=ex['question'],
                    config=types.EmbedContentConfig(output_dimensionality=768)
                )
                
                vector_values = response.embeddings[0].values
                
                SmartSQLExample.objects.create(
                    question=ex['question'],
                    sql_query=ex['sql_query'],
                    embedding=vector_values
                )
                self.stdout.write(self.style.SUCCESS(f"✔ Saved: {ex['question']}"))
                
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"✖ Failed on {ex['question']}: {str(e)}"))

        self.stdout.write(self.style.SUCCESS("Database seeding complete! Your RAG memory is ready."))