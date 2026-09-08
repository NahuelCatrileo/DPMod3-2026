from googleapiclient.discovery import build


def create_youtube_service(credentials):
    return build(
        "youtube",
        "v3",
        credentials=credentials
    )