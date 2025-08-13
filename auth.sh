#!/bin/bash

export TENANT="igmfinancial-uat"  # e.g., igmfinancial
export CLIENT_ID="54143f0ab6ba4dc0958c54c3cc4002d9"
export CLIENT_SECRET="ffb8004a10a0ae943fb00a47928d715ceecf7fc4a3578b047fc844d683c05894"


RESPONSE=$(curl -s --request POST \
  --url "https://$TENANT.api.identitynow.com/oauth/token" \
  --header 'Content-Type: application/x-www-form-urlencoded' \
  --data "grant_type=client_credentials&client_id=$CLIENT_ID&client_secret=$CLIENT_SECRET")

# Show the full response
echo "$RESPONSE"

# Extract token (optional)
ACCESS_TOKEN=$(echo "$RESPONSE" | jq -r '.access_token')

# Optional: confirm it's valid
if [[ "$ACCESS_TOKEN" == "null" || -z "$ACCESS_TOKEN" ]]; then
  echo "❌ Failed to get access token. Check credentials."
else
  echo "✅ Access Token:"
  echo "$ACCESS_TOKEN"
fi


#eyJ0eXAiOiJKV1QiLCJqa3UiOiJodHRwczovL2lnbWZpbmFuY2lhbC11YXQuYXBpLmlkZW50aXR5bm93LmNvbS9vYXV0aC9qd2tzIiwiYWxnIjoiRVMyNTYiLCJraWQiOiJjYjQ1ZTBkZS1iODc2LWE1M2EtNGEyYy0wZjEwMzQ5OGJmYjYifQ.eyJ0ZW5hbnRfaWQiOiIyZGJmZTQ0NS0zYzAwLTQ5ZWMtYjgxNC01NTkzZGI5ZDBlZGMiLCJwb2QiOiJzdGcwOS11c2Vhc3QxIiwib3JnIjoiaWdtZmluYW5jaWFsLXVhdCIsImlkZW50aXR5X2lkIjoiMzU0NDhkN2VjNDMxNDg5NDkwZDJiYzZkYWRkYzg1OTQiLCJ1c2VyX25hbWUiOiJNb2hhbW1lZC5ZYWhheWEiLCJzdHJvbmdfYXV0aCI6dHJ1ZSwiYXV0aG9yaXRpZXMiOlsiT1JHX0FETUlOIiwic3A6dXNlciJdLCJjbGllbnRfaWQiOiIzY2Q5M2YyZWRhZTA0NGQwOGRlMDNlODM3MmEwZTQzMSIsInN0cm9uZ19hdXRoX3N1cHBvcnRlZCI6ZmFsc2UsInNjb3BlIjpbIkJnPT0iXSwiZXhwIjoxNzUxNDczNjcwLCJqdGkiOiJhZ2s0TkNVa0szaUFQeDh0VXFQbEhLWWRxRWMifQ.3Kh9MnUqeWDGiT3XprAXj8NQ7jnBaaL7nbUeS-wh8T2GvNsjNJ7ctm-TI_k3u1-FqshVQJYDJM-BLD7Ze_i6dA