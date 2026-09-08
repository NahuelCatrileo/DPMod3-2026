import os
import pickle
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
from google_auth_oauthlib.flow import Flow

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload"
]

CLIENT_SECRETS_FILE = os.path.join(
    "credentials",
    "client_secret.json"
)

TOKEN_FILE = "token.json"

REDIRECT_URI = "http://localhost:8000/oauth2/callback"

def create_authorization_url():
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )

    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent"
    )

    return authorization_url, state, flow.code_verifier

def exchange_code_for_token(authorization_response: str,code_verifier: str):
    flow = Flow.from_client_secrets_file(
    CLIENT_SECRETS_FILE,
    scopes=SCOPES,
    redirect_uri=REDIRECT_URI
    )

    flow.fetch_token(
    authorization_response=authorization_response,
    code_verifier=code_verifier
    )

    credentials = flow.credentials

    with open(TOKEN_FILE, "wb") as token:
        pickle.dump(credentials, token)

    return credentials