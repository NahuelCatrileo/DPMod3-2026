import logging
from app.domain.ports.publisher_port import PublisherPort

logger = logging.getLogger(__name__)

class MockPublisherAdapter(PublisherPort):
    async def publish_video(
        self, 
        publication_id: str, 
        file_path: str, 
        title: str, 
        description: str
    ) -> str:
        logger.info(
            f"[MOCK] Simulando publicación del video '{title}' "
            f"(pub_id: {publication_id}) desde la ruta: {file_path}"
        )
        # Retornamos un ID de video de prueba predecible
        return f"mock_yt_id_{publication_id[:8]}"