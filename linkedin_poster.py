"""
LinkedIn Automation - OAuth + Posting Script
Post text + images automatically to a personal LinkedIn profile.
"""
import os
import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI ="http://localhost:8000/callback"
LINKEDIN_API_VERSION = "202608"  # LinkedIn-Version header, updated monthly by LinkedIn.

def _check(resp: requests.Response) -> None:
    """Print the real LinkedIn error message instead of guessing on failure."""
    if not resp.ok:
        print("---- LinkedIn error response ----")
        print("Status:", resp.status_code)
        print("Body:", resp.text)
        print("----------------------------------")
    resp.raise_for_status()


def get_authorization_url() -> str:
    """Return the URL the user visits to authorize (only needed once)."""
    return (
        "https://www.linkedin.com/oauth/v2/authorization"
        f"?response_type=code&client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        "&scope=openid%20profile%20w_member_social"
    )


def exchange_code_for_token(auth_code: str) -> str:
    """Swap the callback 'code' for an access token."""
    url = "https://www.linkedin.com/oauth/v2/accessToken"
    payload = {
        "grant_type": "authorization_code",
        "code": auth_code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }
    resp = requests.post(url, data=payload)
    _check(resp)
    data = resp.json()
    return data["access_token"]  # Token lasts ~60 days; store it and add a refresh flow.


def get_person_urn(access_token: str) -> str:
    """Return the unique Person URN required as the 'author' when posting."""
    url = "https://api.linkedin.com/v2/userinfo"
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = requests.get(url, headers=headers)
    _check(resp)
    sub = resp.json()["sub"]
    return f"urn:li:person:{sub}"


def register_image_upload(access_token: str, person_urn: str) -> tuple[str, str]:
    """Request an upload URL from LinkedIn, returning (upload_url, image_urn)."""
    url = "https://api.linkedin.com/rest/images?action=initializeUpload"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "LinkedIn-Version": LINKEDIN_API_VERSION,
        "X-Restli-Protocol-Version": "2.0.0",
    }
    body = {"initializeUploadRequest": {"owner": person_urn}}
    resp = requests.post(url, headers=headers, json=body)
    _check(resp)
    value = resp.json()["value"]
    return value["uploadUrl"], value["image"]


def upload_image_binary(upload_url: str, image_path: str, access_token: str) -> None:
    """Upload the actual image file to the given upload URL."""
    headers = {"Authorization": f"Bearer {access_token}"}
    with open(image_path, "rb") as f:
        resp = requests.put(upload_url, headers=headers, data=f)
    _check(resp)


def create_post(access_token: str, person_urn: str, text: str, image_urn: str) -> str:
    """Create the final post (text + image), returning the post ID."""
    url = "https://api.linkedin.com/rest/posts"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "LinkedIn-Version": LINKEDIN_API_VERSION,
        "X-Restli-Protocol-Version": "2.0.0",
    }
    body = {
        "author": person_urn,
        "commentary": text,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "content": {"media": {"id": image_urn}},
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    resp = requests.post(url, headers=headers, json=body)
    _check(resp)
    return resp.headers.get("x-restli-id")  # The post ID.


if __name__ == "__main__":
    # One-time authorization flow - run these lines only once.
    if not CLIENT_ID or not CLIENT_SECRET:
        raise ValueError("Set CLIENT_ID and CLIENT_SECRET in your .env file first")
    print("Authorize here:", get_authorization_url())
    auth_code = input("Paste the 'code' from the redirect URL: ")
    token = exchange_code_for_token(auth_code)
    print("ACCESS TOKEN (copy this into .env as LINKEDIN_TOKEN):", token)
    exit()  # Only need the token for now - the test-post code below will not run.

    # Daily automation continues from here, reusing the saved token.
    person_urn = get_person_urn(token)
    upload_url, image_urn = register_image_upload(token, person_urn)
    upload_image_binary(upload_url, "post_image.png", token)  # Path to your generated image.

    post_text = "Unique post text generated here by the content node"  # Replace with the content node output.
    post_id = create_post(token, person_urn, post_text, image_urn)
    print("Post published:", post_id)