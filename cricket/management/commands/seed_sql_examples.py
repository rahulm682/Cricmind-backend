import os
from google import genai
from google.genai import types
from django.core.management.base import BaseCommand
from cricket.models import SmartSQLExample

class Command(BaseCommand):
    help = "Seeds the pgvector database with production-grade SQL examples for RAG"

    def handle(self, *args, **kwargs):
        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

        examples = [
            # ==========================================
            # --- 1. TEAM STATS & ACHIEVEMENTS ---
            # ==========================================
            {
                "question": "What is the win percentage of Chennai Super Kings? How many matches have they won?",
                "sql_query": "SELECT team_name, matches_won, matches_played, win_percentage FROM vw_team_stats WHERE team_name ILIKE '%Chennai Super Kings%';"
            },
            {
                "question": "What is the highest and lowest score of Mumbai Indians?",
                "sql_query": "SELECT team_name, highest_score, lowest_score FROM vw_team_stats WHERE team_name ILIKE '%Mumbai Indians%';"
            },
            {
                "question": "Which team has won the most matches while chasing?",
                "sql_query": "SELECT team_name, matches_won_chasing FROM vw_team_stats ORDER BY matches_won_chasing DESC LIMIT 5;"
            },
            {
                "question": "Which team is the best at defending a total? (Most wins defending)",
                "sql_query": "SELECT team_name, matches_won_defending FROM vw_team_stats ORDER BY matches_won_defending DESC LIMIT 5;"
            },
            {
                "question": "What is the highest target successfully chased by RCB?",
                "sql_query": "SELECT team_name, highest_successful_chase FROM vw_team_stats WHERE team_name ILIKE '%Challengers%';"
            },

            # ==========================================
            # --- 2. ADVANCED BATTING METRICS ---
            # ==========================================
            {
                "question": "How to get a batter's strike rate, average, or total runs?",
                "sql_query": "SELECT player_name, total_runs, batting_average, strike_rate FROM vw_batter_stats WHERE player_name ILIKE '%Kohli%';"
            },
            {
                "question": "Who has the most ducks (zero runs) in the IPL?",
                "sql_query": "SELECT player_name, ducks FROM vw_batter_stats ORDER BY ducks DESC LIMIT 5;"
            },
            {
                "question": "What is Rohit Sharma's strike rate in the powerplay and death overs?",
                "sql_query": "SELECT player_name, pp_strike_rate, death_strike_rate FROM vw_batter_stats WHERE player_name ILIKE '%RG Sharma%';"
            },
            {
                "question": "What percentage of Chris Gayle's runs are boundaries (fours and sixes)?",
                "sql_query": "SELECT player_name, boundary_percentage FROM vw_batter_stats WHERE player_name ILIKE '%Gayle%';"
            },
            {
                "question": "How many centuries, fifties, or 30+ scores has a player scored?",
                "sql_query": "SELECT player_name, centuries, fifties, thirties FROM vw_batter_stats WHERE player_name ILIKE '%Warner%';"
            },

            # ==========================================
            # --- 3. ADVANCED BOWLING METRICS ---
            # ==========================================
            {
                "question": "What are Jasprit Bumrah's best bowling figures?",
                "sql_query": "SELECT player_name, best_bowling_figures FROM vw_bowler_stats WHERE player_name ILIKE '%Bumrah%';"
            },
            {
                "question": "Who has the most 5 wicket hauls (5W) or 4 wicket hauls (4W)?",
                "sql_query": "SELECT player_name, five_wicket_hauls, four_wicket_hauls FROM vw_bowler_stats ORDER BY five_wicket_hauls DESC, four_wicket_hauls DESC LIMIT 5;"
            },
            {
                "question": "Who is the most economical bowler in the death overs?",
                "sql_query": "SELECT player_name, death_economy FROM vw_bowler_stats WHERE innings_bowled > 20 ORDER BY death_economy ASC LIMIT 5;"
            },
            {
                "question": "Who has bowled the most dot balls?",
                "sql_query": "SELECT player_name, dot_balls_bowled FROM vw_bowler_stats ORDER BY dot_balls_bowled DESC LIMIT 5;"
            },
            {
                "question": "What is a bowler's economy rate, average, and strike rate?",
                "sql_query": "SELECT player_name, bowling_economy, bowling_average, bowling_strike_rate FROM vw_bowler_stats WHERE player_name ILIKE '%Rashid%Khan%';"
            },

            # ==========================================
            # --- 4. MASTER VIEW & AWARDS ---
            # ==========================================
            {
                "question": "Who has the most Man of the Match or Player of the Match awards?",
                "sql_query": "SELECT player_name, pom_awards FROM vw_player_master ORDER BY pom_awards DESC LIMIT 5;"
            },
            {
                "question": "Give me an all-rounder summary for a player (e.g., Hardik Pandya, Jadeja)",
                "sql_query": "SELECT player_name, total_runs, strike_rate, total_wickets, economy_rate FROM vw_player_master WHERE player_name ILIKE '%Jadeja%';"
            },

            # ==========================================
            # --- 5. REQUIRED RAW TABLE JOINS (THE EXCEPTIONS) ---
            # ==========================================
            {
                "question": "Who are the best wicketkeepers? Who has the most dismissals as a keeper?",
                "sql_query": "SELECT fielder_name, SUM(CASE WHEN dismissal_kind IN ('caught', 'stumped') THEN 1 ELSE 0 END) AS total_dismissals FROM vw_delivery_analytics WHERE fielder_name IS NOT NULL GROUP BY fielder_name ORDER BY total_dismissals DESC LIMIT 5;"
            },
            {
                "question": "Runs scored by a batter against a specific team",
                "sql_query": "SELECT batter_name, SUM(batter_runs) AS total_runs FROM vw_delivery_analytics WHERE batter_name ILIKE '%V%Kohli%' AND bowling_team ILIKE '%Mumbai Indians%' GROUP BY batter_name;"
            },
            {
                "question": "What is the head-to-head record between two teams? (e.g., MI vs CSK)",
                "sql_query": "SELECT match_winner, COUNT(*) as wins FROM vw_match_summary WHERE ((team_a ILIKE '%Mumbai Indians%' AND team_b ILIKE '%Chennai Super Kings%') OR (team_a ILIKE '%Chennai Super Kings%' AND team_b ILIKE '%Mumbai Indians%')) AND match_winner IS NOT NULL GROUP BY match_winner;"
            },
            # ==========================================
            # --- 6. MATCH CONTEXT & RECORD DETAILS ---
            # ==========================================
            {
                "question": "What is the highest score for a batsman and against which team did they score it?",
                "sql_query": "WITH MatchScores AS (SELECT match_id, batter_name, bowling_team, SUM(batter_runs) AS match_runs FROM vw_delivery_analytics WHERE batter_name ILIKE '%Gaikwad%' GROUP BY match_id, batter_name, bowling_team) SELECT batter_name, match_runs AS highest_score, bowling_team AS opponent FROM MatchScores ORDER BY highest_score DESC LIMIT 1;"
            },
            {
                "question": "What is the best bowling figure for a bowler and against which team did they take it?",
                "sql_query": "WITH MatchBowling AS (SELECT match_id, bowler_name, batting_team AS opponent, SUM(is_bowler_wicket) AS wickets, SUM(total_runs) AS runs FROM vw_delivery_analytics WHERE bowler_name ILIKE '%Bumrah%' GROUP BY match_id, bowler_name, batting_team) SELECT bowler_name, CONCAT(wickets, '/', runs) AS best_figure, opponent FROM MatchBowling ORDER BY wickets DESC, runs ASC LIMIT 1;"
            },
            # ==========================================
            # --- GENERAL PLAYER STATS (CATCH-ALL) ---
            # ==========================================
            {
                "question": "What are the stats for Bhuvneshwar Kumar? / Give me player stats.",
                "sql_query": "SELECT * FROM vw_player_master WHERE player_name ILIKE '%B%Kumar%';"
            },
        ]

        self.stdout.write("Wiping old examples...")
        SmartSQLExample.objects.all().delete()

        self.stdout.write(f"Generating vectors and seeding {len(examples)} examples...")

        for ex in examples:
            try:
                # Generate Embedding using Gemini
                response = client.models.embed_content(
                    model="gemini-embedding-001",
                    contents=ex['question'],
                    config=types.EmbedContentConfig(output_dimensionality=768)
                )
                
                vector_values = response.embeddings[0].values
                
                # Save to pgvector model
                SmartSQLExample.objects.create(
                    question=ex['question'],
                    sql_query=ex['sql_query'],
                    embedding=vector_values
                )
                self.stdout.write(self.style.SUCCESS(f"✔ Saved: {ex['question']}"))
                
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"✖ Failed on {ex['question']}: {str(e)}"))

        self.stdout.write(self.style.SUCCESS("\n🚀 RAG memory seeded successfully! Cricmind is now smarter."))
