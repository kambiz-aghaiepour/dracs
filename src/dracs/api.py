import os
from datetime import datetime
from typing import Dict, List, Tuple, Union

import requests

from dracs.exceptions import APIError, ValidationError


def dell_api_warranty_date(
    svctags: Union[str, List[str]],
) -> Dict[str, Tuple[int, str]]:
    if isinstance(svctags, str):
        svctags = [svctags]

    if not svctags:
        raise ValidationError("At least one service tag is required")

    CLIENT_ID = os.getenv("CLIENT_ID")
    CLIENT_SECRET = os.getenv("CLIENT_SECRET")

    if not CLIENT_ID or not CLIENT_SECRET:
        raise APIError(
            "Dell API credentials not found! "
            "Please set CLIENT_ID and CLIENT_SECRET in your .env file. "
            "Visit https://techdirect.dell.com to obtain API credentials"
        )

    TOKEN_URL = (
        "https://apigtwb2c.us.dell.com/auth/oauth/v2/token"
    )

    auth_response = requests.post(
        TOKEN_URL,
        data={"grant_type": "client_credentials"},
        auth=(CLIENT_ID, CLIENT_SECRET),
    )

    token = auth_response.json().get("access_token")

    WARRANTY_API_URL = (
        "https://apigtwb2c.us.dell.com/PROD/sbil/eapi/v5/asset-entitlements"
    )

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    BATCH_SIZE = 100
    warranty_data = []
    for i in range(0, len(svctags), BATCH_SIZE):
        batch = svctags[i : i + BATCH_SIZE]
        payload = {"servicetags": batch}

        response = requests.get(
            WARRANTY_API_URL, headers=headers, params=payload
        )

        if response.status_code == 200:
            warranty_data.extend(response.json())
        else:
            raise APIError(
                f"Dell API request failed: {response.status_code} - {response.text}"
            )

    results: Dict[str, Tuple[int, str]] = {}
    for s in warranty_data:
        tag = s["serviceTag"]
        cur_eed = 0
        cur_eed_string = "January 1, 1970"
        for e in s["entitlements"]:
            eed = e["endDate"]
            eed_dt = datetime.fromisoformat(eed.replace("Z", "+00:00"))
            eed_dt_epoch = int(eed_dt.strftime("%s"))
            eed_dt_string = eed_dt.strftime("%B %e, %Y")
            if eed_dt_epoch > cur_eed:
                cur_eed = eed_dt_epoch
                cur_eed_string = eed_dt_string
        results[tag] = (cur_eed, cur_eed_string)

    return results
