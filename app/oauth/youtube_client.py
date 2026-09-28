"""Cliente de la YouTube Data API v3.

Preservado del trabajo del Sprint 0 (antes app/youtube.py).
Lo usará YouTubePublisher en el Sprint 3 (US-C3).
"""

from googleapiclient.discovery import build


def create_youtube_service(credentials):
    return build("youtube", "v3", credentials=credentials)
