from django.db import models
from pgvector.django import VectorField

class Player(models.Model):
    # 8-character Cricsheet ID as the primary key
    player_id = models.CharField(max_length=15, primary_key=True)
    full_name = models.CharField(max_length=100, db_index=True)

    def __str__(self):
        return f"{self.player_id} - {self.full_name}"

class Match(models.Model):
    match_id = models.CharField(max_length=20, primary_key=True)
    team_a = models.CharField(max_length=50, db_index=True, default="Unknown")
    team_b = models.CharField(max_length=50, db_index=True, default="Unknown")
    match_type = models.CharField(max_length=10, db_index=True)
    match_date = models.DateField(db_index=True)
    venue = models.CharField(max_length=100, db_index=True)
    event_name = models.CharField(max_length=100, null=True, blank=True)
    event_stage = models.CharField(max_length=50, null=True, blank=True) 
    event_match_number = models.IntegerField(null=True, blank=True)
    toss_winner = models.CharField(max_length=50)
    toss_decision = models.CharField(max_length=10) # 'bat' or 'field'
    match_winner = models.CharField(max_length=50, null=True, blank=True)
    win_margin_runs = models.IntegerField(null=True, blank=True)
    win_margin_wickets = models.IntegerField(null=True, blank=True)

    team_a_captain = models.CharField(max_length=100, null=True, blank=True)
    team_b_captain = models.CharField(max_length=100, null=True, blank=True)
    player_of_match = models.CharField(max_length=100, null=True, blank=True)

    def __str__(self):
        return f"{self.match_id} - {self.match_date}"

class InningMetadata(models.Model):
    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name='innings_meta')
    inning_number = models.IntegerField()
    batting_team = models.CharField(max_length=50)
    target_runs = models.IntegerField(null=True, blank=True)
    # powerplay_type = models.CharField(max_length=20, null=True, blank=True)
    # powerplay_start = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    # powerplay_end = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)

class Powerplay(models.Model):
    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name='powerplays')
    inning_number = models.IntegerField()
    powerplay_type = models.CharField(max_length=20) # e.g., 'mandatory'
    start_over = models.DecimalField(max_digits=4, decimal_places=1)
    end_over = models.DecimalField(max_digits=4, decimal_places=1)

    class Meta:
        indexes = [
            models.Index(fields=['match', 'inning_number']),
        ]

class Delivery(models.Model):
    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name='deliveries')
    inning_number = models.IntegerField()
    over_number = models.IntegerField(db_index=True)
    ball_number = models.IntegerField()
    
    batter = models.ForeignKey(Player, on_delete=models.CASCADE, related_name='balls_faced')
    bowler = models.ForeignKey(Player, on_delete=models.CASCADE, related_name='balls_bowled')
    non_striker = models.ForeignKey(Player, on_delete=models.CASCADE, related_name='balls_at_non_striker')
    
    batter_runs = models.IntegerField(default=0)
    extra_runs = models.IntegerField(default=0)
    total_runs = models.IntegerField(default=0)
    
    is_wide = models.BooleanField(default=False)
    is_noball = models.BooleanField(default=False)
    
    is_wicket = models.BooleanField(default=False)
    dismissal_kind = models.CharField(max_length=30, null=True, blank=True)
    player_out = models.ForeignKey(Player, on_delete=models.SET_NULL, null=True, blank=True, related_name='dismissals')

    fielder = models.ForeignKey(Player, on_delete=models.SET_NULL, null=True, blank=True, related_name='fielding_dismissals')

    class Meta:
        indexes = [
            models.Index(fields=['batter', 'over_number']),
            models.Index(fields=['bowler', 'over_number']),
        ]

class SemanticCache(models.Model):
    original_question = models.TextField()
    # Stores the mathematical representation of the question
    question_embedding = VectorField(dimensions=768) 
    response_payload = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.original_question