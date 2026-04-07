import logging
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from cricket.models import SemanticCache 

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Deletes Semantic Cache records older than 7 days to save vector database space.'

    def handle(self, *args, **kwargs):
        self.stdout.write("Starting Semantic Cache cleanup...")
        
        cutoff_date = timezone.now() - timedelta(days=2)
        
        try:
            deleted_count, _ = SemanticCache.objects.filter(created_at__lt=cutoff_date).delete()
            
            success_msg = f'Successfully deleted {deleted_count} old cache records.'
            
            self.stdout.write(self.style.SUCCESS(success_msg))
            logger.info(success_msg)
            
        except Exception as e:
            error_msg = f'Error during cache cleanup: {str(e)}'
            self.stdout.write(self.style.ERROR(error_msg))
            logger.error(error_msg)

