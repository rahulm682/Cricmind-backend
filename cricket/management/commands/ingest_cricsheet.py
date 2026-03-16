import json
import os
from django.core.management.base import BaseCommand
from django.db import transaction, connection
from cricket.models import Player, Match, InningMetadata, Powerplay, Delivery

class Command(BaseCommand):
    help = 'Ingests actual Cricsheet JSON files into the database and refreshes Materialized Views'

    def add_arguments(self, parser):
        parser.add_argument('path', type=str, help='Path to the Cricsheet JSON file or directory')

    def handle(self, *args, **kwargs):
        path = kwargs['path']

        if os.path.isfile(path):
            self.process_file(path)
        elif os.path.isdir(path):
            for filename in os.listdir(path):
                if filename.endswith('.json'):
                    self.process_file(os.path.join(path, filename))
        else:
            self.stdout.write(self.style.ERROR(f"Path does not exist: {path}"))
            return

        self.stdout.write(self.style.WARNING("Refreshing Materialized Views... This may take a moment."))
        try:
            with connection.cursor() as cursor:
                cursor.execute("REFRESH MATERIALIZED VIEW vw_match_summary;")
                cursor.execute("REFRESH MATERIALIZED VIEW vw_delivery_analytics;")
            self.stdout.write(self.style.SUCCESS("Materialized Views refreshed successfully! Backend is ready."))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to refresh views: {str(e)}"))


    def process_file(self, filepath):
        filename = os.path.basename(filepath)
        match_id = filename.split('.')[0] # e.g., '1336129' from '1336129.json'

        if Match.objects.filter(match_id=match_id).exists():
            self.stdout.write(self.style.WARNING(f"Match {match_id} already exists. Skipping."))
            return

        with open(filepath, 'r') as f:
            data = json.load(f)

        try:
            with transaction.atomic():
                self.stdout.write(f"Parsing Match: {match_id}...")
                
                info = data.get('info', {})
                innings_data = data.get('innings', [])

                registry = info.get('registry', {}).get('people', {})
                player_instances = {}
                
                for name, p_id in registry.items():
                    player, created = Player.objects.get_or_create(
                        player_id=p_id,
                        defaults={'full_name': name}
                    )
                    player_instances[name] = player

                outcome = info.get('outcome', {})
                winner = outcome.get('winner')
                by = outcome.get('by', {})
                
                # Extract teams
                teams = info.get('teams', ['Unknown', 'Unknown'])
                team_a = teams[0] if len(teams) > 0 else 'Unknown'
                team_b = teams[1] if len(teams) > 1 else 'Unknown'
                
                # Extract Player of the Match
                pom_list = info.get('player_of_match', [])
                player_of_match = pom_list[0] if pom_list else None

                captains_dict = info.get('registry', {}).get('captains', {})
                team_a_captain = None
                team_b_captain = None
                
                match = Match.objects.create(
                    match_id=match_id,
                    team_a=team_a,
                    team_b=team_b,
                    match_type=info.get('match_type', 'Unknown'),
                    match_date=info.get('dates', ['1970-01-01'])[0],
                    venue=info.get('venue', 'Unknown'),
                    event_name=info.get('event', {}).get('name'),
                    event_stage=info.get('event', {}).get('stage'),             
                    event_match_number=info.get('event', {}).get('match_number'),
                    toss_winner=info.get('toss', {}).get('winner'),
                    toss_decision=info.get('toss', {}).get('decision'),
                    match_winner=winner,
                    win_margin_runs=by.get('runs'),
                    win_margin_wickets=by.get('wickets'),
                    player_of_match=player_of_match,
                    team_a_captain=team_a_captain,
                    team_b_captain=team_b_captain
                )

                deliveries_to_create = []

                for inning_idx, inning in enumerate(innings_data):
                    team = inning.get('team')
                    overs = inning.get('overs', [])
                    target_runs = inning.get('target', {}).get('runs')

                    InningMetadata.objects.create(
                        match=match,
                        inning_number=inning_idx + 1,
                        batting_team=team,
                        target_runs=target_runs
                    )

                    # Extract and save Powerplays
                    powerplays = inning.get('powerplays', [])
                    for pp in powerplays:
                        Powerplay.objects.create(
                            match=match,
                            inning_number=inning_idx + 1,
                            powerplay_type=pp.get('type', 'unknown'),
                            start_over=pp.get('from', 0.0),
                            end_over=pp.get('to', 0.0)
                        )

                    # Loop through overs
                    for over in overs:
                        over_num = over.get('over')
                        
                        # Loop through individual balls in the over
                        for ball_idx, ball in enumerate(over.get('deliveries', [])):
                            runs = ball.get('runs', {})
                            extras = ball.get('extras', {})
                            wickets = ball.get('wickets', [])
                            
                            is_wicket = len(wickets) > 0
                            dismissal_kind = None
                            player_out_instance = None
                            fielder_instance = None

                            if is_wicket:
                                wicket_info = wickets[0]
                                dismissal_kind = wicket_info.get('kind')
                                
                                # Get who got out
                                player_out_name = wicket_info.get('player_out')
                                player_out_instance = player_instances.get(player_out_name)
                                
                                # Grab the fielder if it was a catch/run out
                                fielders = wicket_info.get('fielders', [])
                                if fielders:
                                    fielder_name = fielders[0].get('name')
                                    fielder_instance = player_instances.get(fielder_name)

                            # Assemble the delivery object
                            delivery = Delivery(
                                match=match,
                                inning_number=inning_idx + 1,
                                over_number=over_num,
                                ball_number=ball_idx + 1,
                                batter=player_instances.get(ball.get('batter')),
                                bowler=player_instances.get(ball.get('bowler')),
                                non_striker=player_instances.get(ball.get('non_striker')),
                                batter_runs=runs.get('batter', 0),
                                extra_runs=runs.get('extras', 0),
                                total_runs=runs.get('total', 0),
                                is_wide='wides' in extras,
                                is_noball='noballs' in extras,
                                is_wicket=is_wicket,
                                dismissal_kind=dismissal_kind,
                                player_out=player_out_instance,
                                fielder=fielder_instance # New Field!
                            )
                            deliveries_to_create.append(delivery)

                # Bulk insert all deliveries for this match at once
                Delivery.objects.bulk_create(deliveries_to_create, batch_size=5000)
                
                self.stdout.write(self.style.SUCCESS(f"Successfully ingested {match_id} with {len(deliveries_to_create)} balls."))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to process {match_id}: {str(e)}"))



'''
    docker-compose exec web python manage.py flush -> to remove all the data from database
    docker-compose exec web python manage.py ingest_cricsheet jsons/ipl_json
'''