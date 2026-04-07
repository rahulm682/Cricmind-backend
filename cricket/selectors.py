import logging
from django.db import connection

logger = logging.getLogger(__name__)

def get_player_profile_data(player_name: str) -> dict:
    """
    Executes raw SQL against materialized views to build a complete player profile.
    Returns the profile dictionary or None if the player isn't found.
    """
    logger.info(f"Selector execution started: Building profile for '{player_name}'")
    
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
            # Batting Stats
            logger.debug("Executing Batting Stats query...")
            cursor.execute("""
                SELECT player_name, innings_batted, total_runs, fours, sixes, 
                       highest_score, fifties, centuries, batting_average, strike_rate
                FROM vw_batter_stats WHERE player_name ILIKE %s LIMIT 1;
            """, [f"%{player_name}%"])
            bat_row = cursor.fetchone()

            if bat_row:
                logger.debug(f"Batting data found for: {bat_row[0]}")
                profile_data["name"] = bat_row[0]
                profile_data["batting"] = {
                    "innings": bat_row[1], "runs": bat_row[2], "fours": bat_row[3],
                    "sixes": bat_row[4], "highest_score": bat_row[5], "fifties": bat_row[6],
                    "hundreds": bat_row[7], "average": float(bat_row[8] or 0), "strike_rate": float(bat_row[9] or 0)
                }

            # Bowling Stats
            logger.debug("Executing Bowling Stats query...")
            cursor.execute("""
                SELECT player_name, innings_bowled, total_wickets, best_bowling_figures, 
                       bowling_economy, bowling_average, bowling_strike_rate
                FROM vw_bowler_stats WHERE player_name ILIKE %s LIMIT 1;
            """, [f"%{player_name}%"])
            bowl_row = cursor.fetchone()

            if bowl_row:
                logger.debug(f"Bowling data found for: {bowl_row[0]}")
                profile_data["name"] = bowl_row[0]
                profile_data["bowling"] = {
                    "innings": bowl_row[1], "wickets": bowl_row[2], "best_figure": bowl_row[3],
                    "economy": float(bowl_row[4] or 0), "average": float(bowl_row[5] or 0), "strike_rate": float(bowl_row[6] or 0)
                }

            # Splits
            if profile_data["batting"]:
                logger.debug("Fetching batting splits...")
                cursor.execute("""
                    SELECT bowling_team, SUM(batter_runs) as runs, 
                           ROUND((SUM(batter_runs)::numeric / NULLIF(SUM(is_legal_ball), 0)) * 100, 2) as sr
                    FROM vw_delivery_analytics WHERE batter_name = %s 
                    GROUP BY bowling_team ORDER BY runs DESC LIMIT 5;
                """, [profile_data["name"]])
                profile_data["batting_splits"] = [{"team": r[0], "runs": r[1], "strike_rate": float(r[2] or 0)} for r in cursor.fetchall()]

            if profile_data["bowling"]:
                logger.debug("Fetching bowling splits...")
                cursor.execute("""
                    SELECT batting_team, SUM(is_bowler_wicket) as wickets
                    FROM vw_delivery_analytics WHERE bowler_name = %s 
                    GROUP BY batting_team ORDER BY wickets DESC LIMIT 5;
                """, [profile_data["name"]])
                profile_data["bowling_splits"] = [{"team": r[0], "wickets": r[1]} for r in cursor.fetchall()]

            # Role Determination
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
                return None

        logger.info(f"Successfully built profile for {profile_data['name']} as {profile_data['role']}")
        return profile_data

    except Exception as e:
        logger.error(f"PostgreSQL Execution failed in PlayerProfileSelector for '{player_name}': {str(e)}", exc_info=True)
        raise e
