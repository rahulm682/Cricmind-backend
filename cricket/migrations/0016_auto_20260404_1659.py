from django.db import migrations

class Migration(migrations.Migration):

    dependencies = [
        ('cricket', '0015_auto_20260404_1439'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            -- ==========================================
            -- 1. THE TEAM STATS VIEW (WITH CHASING/DEFENDING)
            -- ==========================================
            DROP MATERIALIZED VIEW IF EXISTS vw_team_stats CASCADE;
            
            CREATE MATERIALIZED VIEW vw_team_stats AS
            WITH TeamMatches AS (
                SELECT team_a AS team_name, match_id, match_winner, toss_winner, win_margin_type FROM vw_match_summary
                UNION ALL
                SELECT team_b AS team_name, match_id, match_winner, toss_winner, win_margin_type FROM vw_match_summary
            ),
            TeamMatchAgg AS (
                SELECT 
                    team_name,
                    COUNT(match_id) AS matches_played,
                    SUM(CASE WHEN match_winner = team_name THEN 1 ELSE 0 END) AS matches_won,
                    SUM(CASE WHEN toss_winner = team_name THEN 1 ELSE 0 END) AS tosses_won,
                    -- Advanced: Won Defending vs Won Chasing
                    SUM(CASE WHEN match_winner = team_name AND win_margin_type = 'runs' THEN 1 ELSE 0 END) AS matches_won_defending,
                    SUM(CASE WHEN match_winner = team_name AND win_margin_type = 'wickets' THEN 1 ELSE 0 END) AS matches_won_chasing
                FROM TeamMatches
                GROUP BY team_name
            ),
            TeamInnings AS (
                SELECT batting_team AS team_name, match_id, SUM(total_runs) as score, SUM(is_legal_ball) as balls_faced
                FROM vw_delivery_analytics
                GROUP BY batting_team, match_id
            ),
            TeamScoreAgg AS (
                SELECT 
                    ti.team_name,
                    MAX(ti.score) AS highest_score,
                    MIN(CASE WHEN ti.balls_faced >= 30 THEN ti.score ELSE NULL END) AS lowest_score,
                    -- Advanced: Highest score successfully chased down
                    MAX(CASE WHEN m.match_winner = ti.team_name AND m.win_margin_type = 'wickets' THEN ti.score ELSE NULL END) AS highest_successful_chase
                FROM TeamInnings ti
                JOIN vw_match_summary m ON ti.match_id = m.match_id
                GROUP BY ti.team_name
            )
            SELECT 
                m.team_name,
                m.matches_played,
                m.matches_won,
                (m.matches_played - m.matches_won) AS matches_lost_or_tied,
                ROUND((m.matches_won * 100.0) / NULLIF(m.matches_played, 0), 2) AS win_percentage,
                m.tosses_won,
                m.matches_won_defending,
                m.matches_won_chasing,
                s.highest_score,
                s.lowest_score,
                s.highest_successful_chase
            FROM TeamMatchAgg m
            JOIN TeamScoreAgg s ON m.team_name = s.team_name;

            CREATE UNIQUE INDEX idx_vw_team_stats_name ON vw_team_stats(team_name);


            -- ==========================================
            -- 2. THE UPGRADED BATTER VIEW (WITH IMPACT METRICS)
            -- ==========================================
            DROP MATERIALIZED VIEW IF EXISTS vw_batter_stats CASCADE;
            
            CREATE MATERIALIZED VIEW vw_batter_stats AS
            WITH MatchMilestones AS (
                SELECT 
                    batter_name, 
                    match_id, 
                    SUM(batter_runs) as match_runs,
                    MAX(CASE WHEN player_dismissed = batter_name THEN 1 ELSE 0 END) as was_dismissed
                FROM vw_delivery_analytics
                GROUP BY batter_name, match_id
            ),
            AggMilestones AS (
                SELECT
                    batter_name,
                    MAX(match_runs) AS highest_score,
                    SUM(CASE WHEN match_runs >= 30 AND match_runs < 50 THEN 1 ELSE 0 END) AS thirties,
                    SUM(CASE WHEN match_runs >= 50 AND match_runs < 100 THEN 1 ELSE 0 END) AS fifties,
                    SUM(CASE WHEN match_runs >= 100 THEN 1 ELSE 0 END) AS centuries,
                    SUM(CASE WHEN match_runs = 0 AND was_dismissed = 1 THEN 1 ELSE 0 END) AS ducks
                FROM MatchMilestones
                GROUP BY batter_name
            )
            SELECT 
                d.batter_name AS player_name,
                COUNT(DISTINCT d.match_id) AS innings_batted,
                SUM(d.batter_runs) AS total_runs,
                SUM(d.is_legal_ball) AS balls_faced,
                SUM(CASE WHEN d.player_dismissed = d.batter_name THEN 1 ELSE 0 END) AS dismissals,
                
                -- New Advanced Stats
                ROUND(SUM(d.batter_runs) * 1.0 / NULLIF(SUM(CASE WHEN d.player_dismissed = d.batter_name THEN 1 ELSE 0 END), 0), 2) AS batting_average,
                ROUND((SUM(d.batter_runs) * 100.0) / NULLIF(SUM(d.is_legal_ball), 0), 2) AS strike_rate,
                
                -- Phase of Play Stats
                ROUND((SUM(CASE WHEN d.phase_of_play = 'Powerplay' THEN d.batter_runs ELSE 0 END) * 100.0) / NULLIF(SUM(CASE WHEN d.phase_of_play = 'Powerplay' THEN d.is_legal_ball ELSE 0 END), 0), 2) AS pp_strike_rate,
                ROUND((SUM(CASE WHEN d.phase_of_play = 'Death' THEN d.batter_runs ELSE 0 END) * 100.0) / NULLIF(SUM(CASE WHEN d.phase_of_play = 'Death' THEN d.is_legal_ball ELSE 0 END), 0), 2) AS death_strike_rate,
                
                -- Impact Metrics
                ROUND(((SUM(d.is_four) * 4) + (SUM(d.is_six) * 6)) * 100.0 / NULLIF(SUM(d.batter_runs), 0), 2) AS boundary_percentage,
                ROUND((SUM(d.is_dot_ball) * 100.0) / NULLIF(SUM(d.is_legal_ball), 0), 2) AS dot_ball_percentage,
                
                SUM(d.is_four) AS fours,
                SUM(d.is_six) AS sixes,
                m.highest_score,
                m.thirties,
                m.fifties,
                m.centuries,
                m.ducks
            FROM vw_delivery_analytics d
            JOIN AggMilestones m ON d.batter_name = m.batter_name
            GROUP BY d.batter_name, m.highest_score, m.thirties, m.fifties, m.centuries, m.ducks;

            CREATE UNIQUE INDEX idx_vw_batter_stats_name ON vw_batter_stats(player_name);


            -- ==========================================
            -- 3. THE UPGRADED BOWLER VIEW (WITH IMPACT METRICS)
            -- ==========================================
            DROP MATERIALIZED VIEW IF EXISTS vw_bowler_stats CASCADE;
            
            CREATE MATERIALIZED VIEW vw_bowler_stats AS
            WITH BowlerMatchStats AS (
                SELECT 
                    bowler_name, 
                    match_id, 
                    SUM(is_bowler_wicket) as w, 
                    SUM(total_runs) as r
                FROM vw_delivery_analytics 
                GROUP BY bowler_name, match_id
            ),
            BowlerBestFigures AS (
                SELECT DISTINCT ON (bowler_name) 
                    bowler_name, 
                    CONCAT(w, '/', r) as best_bowling_figures
                FROM BowlerMatchStats
                ORDER BY bowler_name, w DESC, r ASC
            ),
            BowlerMilestones AS (
                SELECT 
                    bowler_name,
                    SUM(CASE WHEN w = 4 THEN 1 ELSE 0 END) as four_wicket_hauls,
                    SUM(CASE WHEN w >= 5 THEN 1 ELSE 0 END) as five_wicket_hauls,
                    MAX(r) as worst_spell_runs
                FROM BowlerMatchStats 
                GROUP BY bowler_name
            )
            SELECT 
                d.bowler_name AS player_name,
                COUNT(DISTINCT d.match_id) AS innings_bowled,
                SUM(d.is_bowler_wicket) AS total_wickets,
                SUM(d.total_runs) AS runs_conceded,
                SUM(d.is_legal_ball) AS legal_balls_bowled,
                SUM(d.extra_runs) AS extras_bowled,
                SUM(d.is_dot_ball) AS dot_balls_bowled,
                
                -- Core Stats
                ROUND((SUM(d.total_runs) * 6.0) / NULLIF(SUM(d.is_legal_ball), 0), 2) AS bowling_economy,
                ROUND(SUM(d.total_runs) * 1.0 / NULLIF(SUM(d.is_bowler_wicket), 0), 2) AS bowling_average,
                ROUND(SUM(d.is_legal_ball) * 1.0 / NULLIF(SUM(d.is_bowler_wicket), 0), 2) AS bowling_strike_rate,
                
                -- Phase of Play Stats
                ROUND((SUM(CASE WHEN d.phase_of_play = 'Powerplay' THEN d.total_runs ELSE 0 END) * 6.0) / NULLIF(SUM(CASE WHEN d.phase_of_play = 'Powerplay' THEN d.is_legal_ball ELSE 0 END), 0), 2) AS pp_economy,
                ROUND((SUM(CASE WHEN d.phase_of_play = 'Death' THEN d.total_runs ELSE 0 END) * 6.0) / NULLIF(SUM(CASE WHEN d.phase_of_play = 'Death' THEN d.is_legal_ball ELSE 0 END), 0), 2) AS death_economy,
                
                bm.four_wicket_hauls,
                bm.five_wicket_hauls,
                bm.worst_spell_runs,
                bbf.best_bowling_figures
            FROM vw_delivery_analytics d
            JOIN BowlerMilestones bm ON d.bowler_name = bm.bowler_name
            JOIN BowlerBestFigures bbf ON d.bowler_name = bbf.bowler_name
            GROUP BY d.bowler_name, bm.four_wicket_hauls, bm.five_wicket_hauls, bm.worst_spell_runs, bbf.best_bowling_figures;

            CREATE UNIQUE INDEX idx_vw_bowler_stats_name ON vw_bowler_stats(player_name);


            -- ==========================================
            -- 4. THE UPGRADED MASTER VIEW (WITH PLAYER OF MATCH)
            -- ==========================================
            DROP MATERIALIZED VIEW IF EXISTS vw_player_master CASCADE;
            
            CREATE MATERIALIZED VIEW vw_player_master AS
            WITH PoM_Awards AS (
                SELECT player_of_match AS player_name, COUNT(*) AS pom_awards
                FROM vw_match_summary
                WHERE player_of_match IS NOT NULL
                GROUP BY player_of_match
            )
            SELECT 
                COALESCE(bat.player_name, bowl.player_name) AS player_name,
                COALESCE(pom.pom_awards, 0) AS pom_awards,
                
                -- Batting
                COALESCE(bat.innings_batted, 0) AS innings_batted,
                COALESCE(bat.total_runs, 0) AS total_runs,
                COALESCE(bat.highest_score, 0) AS highest_score,
                COALESCE(bat.batting_average, 0.00) AS batting_average,
                COALESCE(bat.strike_rate, 0.00) AS strike_rate,
                COALESCE(bat.pp_strike_rate, 0.00) AS pp_strike_rate,
                COALESCE(bat.death_strike_rate, 0.00) AS death_strike_rate,
                COALESCE(bat.boundary_percentage, 0.00) AS boundary_percentage,
                COALESCE(bat.thirties, 0) AS thirties,
                COALESCE(bat.fifties, 0) AS fifties,
                COALESCE(bat.centuries, 0) AS centuries,
                COALESCE(bat.ducks, 0) AS ducks,
                
                -- Bowling
                COALESCE(bowl.innings_bowled, 0) AS innings_bowled,
                COALESCE(bowl.total_wickets, 0) AS total_wickets,
                COALESCE(bowl.bowling_economy, 0.00) AS economy_rate,
                COALESCE(bowl.bowling_average, 0.00) AS bowling_average,
                COALESCE(bowl.bowling_strike_rate, 0.00) AS bowling_strike_rate,
                COALESCE(bowl.pp_economy, 0.00) AS pp_economy,
                COALESCE(bowl.death_economy, 0.00) AS death_economy,
                COALESCE(bowl.best_bowling_figures, '0/0') AS best_bowling_figures,
                COALESCE(bowl.four_wicket_hauls, 0) AS four_wicket_hauls,
                COALESCE(bowl.five_wicket_hauls, 0) AS five_wicket_hauls,
                COALESCE(bowl.worst_spell_runs, 0) AS worst_spell_runs

            FROM vw_batter_stats bat
            FULL OUTER JOIN vw_bowler_stats bowl ON bat.player_name = bowl.player_name
            LEFT JOIN PoM_Awards pom ON COALESCE(bat.player_name, bowl.player_name) = pom.player_name;

            CREATE UNIQUE INDEX idx_vw_player_master_name ON vw_player_master(player_name);
            """,
            
            reverse_sql="""
            DROP MATERIALIZED VIEW IF EXISTS vw_team_stats CASCADE;
            DROP MATERIALIZED VIEW IF EXISTS vw_player_master CASCADE;
            DROP MATERIALIZED VIEW IF EXISTS vw_bowler_stats CASCADE;
            DROP MATERIALIZED VIEW IF EXISTS vw_batter_stats CASCADE;
            """
        )
    ]