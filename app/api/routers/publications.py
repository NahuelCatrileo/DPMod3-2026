from fastapi import APIRouter, Depends, Header
from src.api.dependencies import get_publisher_adapter
from src.domain.ports.publisher_port import PublisherPort
import uuid

router = APIRouter(prefix="/publications", tags=["publications"])

@router.post("/test-publish")
async def test_publish(
    x_correlation_id: str = Header(..., description="ID de trazabilidad de la transacción"),
    publisher: PublisherPort = Depends(get_publisher_adapter)
):
    # Generamos UUID de prueba simulando el `content_id`
    dummy_pub_id = str(uuid.uuid4())
    
    try:
        video_id = await publisher.publish_video(
            publication_id=dummy_pub_id,
            file_path="/tmp/mock_video.mp4",
            title="Video de Prueba Hexagonal",
            description="Testing de arquitectura"
        )
        
        return {
            "status": "ok",
            "data": {
                "publication_id": dummy_pub_id,
                "youtube_video_id": video_id,
                "publisher_type": publisher.__class__.__name__
            }
        }
    except Exception as e:
        return {
            "status": "error",
            "code": "PUBLISHER_ERROR",
            "message": str(e)
        }