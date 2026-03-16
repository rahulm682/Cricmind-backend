import os
import requests
import logging

logger = logging.getLogger(__name__)

class BaseNewsProvider:
    """The blueprint for all future news providers."""
    def fetch_news(self, query):
        raise NotImplementedError("Subclasses must implement fetch_news")

class GNewsProvider(BaseNewsProvider):
    def fetch_news(self, query):
        api_key = os.environ.get("NEWS_API_KEY")
        url = f"https://gnews.io/api/v4/search?q={query}&lang=en&max=10&apikey={api_key}"
        
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            standardized_articles = []
            for article in data.get('articles', []):
                standardized_articles.append({
                    "title": article.get('title'),
                    "description": article.get('description'),
                    "url": article.get('url'),
                    "image_url": article.get('image'),
                    "source": article.get('source', {}).get('name'),
                    "published_at": article.get('publishedAt')
                })
            return standardized_articles
        except Exception as e:
            logger.error(f"GNews API failed: {e}")
            return []

class NewsAPIProvider(BaseNewsProvider):
    def fetch_news(self, query):
        api_key = os.environ.get("NEWS_API_KEY")
        url = f"https://newsapi.org/v2/everything?q={query}&language=en&pageSize=10&apiKey={api_key}"
        
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            standardized_articles = []
            for article in data.get('articles', []):
                standardized_articles.append({
                    "title": article.get('title'),
                    "description": article.get('description'),
                    "url": article.get('url'),
                    "image_url": article.get('urlToImage'),
                    "source": article.get('source', {}).get('name'),
                    "published_at": article.get('publishedAt')
                })
            return standardized_articles
        except Exception as e:
            logger.error(f"NewsAPI failed: {e}")
            return []

def get_news_provider():
    """Factory function to easily swap providers via environment variables."""
    provider_name = os.environ.get("NEWS_PROVIDER", "gnews").lower()
    
    if provider_name == "newsapi":
        return NewsAPIProvider()

    return GNewsProvider()


