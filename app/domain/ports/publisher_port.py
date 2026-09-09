from typing import Protocol

class PublisherPort(Protocol):
    async def publish_video(
        self, 
        publication_id: str, 
        file_path: str, 
        title: str, 
        description: str
    ) -> str:
        """
        Publica un video en la plataforma de destino.
        Retorna el ID de publicación remoto (ej. youtube_video_id).
        """
        ...