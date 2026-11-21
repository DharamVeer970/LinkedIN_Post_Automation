"""Generate a LinkedIn OAuth access token for the publishing pipeline."""
import os
import requests
from dotenv import load_dotenv
from urllib.parse import urlencode

load_dotenv()

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI", "http://localhost:8000/callback")

def _check(resp: requests.Response) -> None:
    """Print the real LinkedIn error message instead of guessing on failure."""
    if not resp.ok:
        print("---- LinkedIn error response ----")
        print("Status:", resp.status_code)
        print("Headers:", dict(resp.headers))
        print("Body:", resp.text)
        print("----------------------------------")
    resp.raise_for_status()


def get_authorization_url() -> str:
    """Return the URL the user visits to authorize (only needed once)."""
    query = urlencode({
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": "openid profile w_member_social",
    })
    return f"https://www.linkedin.com/oauth/v2/authorization?{query}"


def exchange_code_for_token(auth_code: str) -> str:
    """Swap the callback 'code' for an access token."""
    url = "https://www.linkedin.com/oauth/v2/accessToken"
    payload = {
        "grant_type": "authorization_code",
        "code": auth_code,
        "redirect_uri": REDIRECT_URI,
    }

    resp = requests.post(
        url,
        data=payload,
        auth=(CLIENT_ID, CLIENT_SECRET),
        timeout=30,
    )
    _check(resp)
    data = resp.json()
    return data["access_token"]


if __name__ == "__main__":
    import sys
    if not CLIENT_ID or not CLIENT_SECRET:
        raise ValueError("Set CLIENT_ID and CLIENT_SECRET in your .env file first")
    print("Authorize here:", get_authorization_url())

    if len(sys.argv) > 1:
        auth_code = sys.argv[1]
    else:
        auth_code = input("Paste the 'code' from the redirect URL: ")

    token = exchange_code_for_token(auth_code)
    print("ACCESS TOKEN (copy this into .env as LINKEDIN_TOKEN):", token)
