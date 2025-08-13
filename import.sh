curl --request POST \
  --url "https://$TENANT.api.identitynow.com/v3/access-profiles" \
  --header "Authorization: Bearer $ACCESS_TOKEN" \
  --header "Content-Type: application/json" \
  --data @"test.json"
