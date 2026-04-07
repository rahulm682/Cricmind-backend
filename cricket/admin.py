from django.contrib import admin
from .models import Player, Match, InningMetadata, Delivery, Powerplay, SemanticCache, SmartSQLExample

@admin.register(Player)
class PlayerAdmin(admin.ModelAdmin):
    lsit_diaplay = ('player_id', 'full_name')
    search_fields = ('player_id', 'full_name')


@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):
    list_display = ('match_id', 'match_date', 'venue', 'match_type', 'match_winner')
    search_fields = ('match_id', 'venue', 'event_name')
    list_filter = ('match_type', 'match_date')

@admin.register(Delivery)
class DeliveryAdmin(admin.ModelAdmin):
    list_display = ('match', 'inning_number', 'over_number', 'ball_number', 'batter', 'bowler', 'total_runs')
    search_fields = ('match__match_id', 'batter__full_name', 'bowler__full_name')
    list_filter = ('inning_number', 'is_wicket', 'is_wide', 'is_noball')

@admin.register(InningMetadata)
class InningMetadataAdmin(admin.ModelAdmin):
    list_display = ('match', 'inning_number', 'batting_team', 'target_runs')
    ordering = ('match', 'inning_number')
    
    list_filter = ('inning_number', 'batting_team')
    search_fields = ('match__match_id', 'batting_team')

@admin.register(Powerplay)
class PowerplayAdmin(admin.ModelAdmin):
    list_display = ('match', 'inning_number', 'powerplay_type', 'start_over', 'end_over')
    ordering = ('match', 'inning_number', 'start_over')
    list_filter = ('powerplay_type', 'inning_number')    
    search_fields = ('match__match_id',)


@admin.register(SmartSQLExample)
class SmartSQLExampleAdmin(admin.ModelAdmin):
    list_display = ('question', 'sql_query')
    list_filter = ('question', 'sql_query')


@admin.register(SemanticCache)
class SemanticCacheAdmin(admin.ModelAdmin):
    list_display = ('original_question', 'response_payload')
