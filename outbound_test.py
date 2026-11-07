import requests

AUTH_ID = "MA_9CZ9ATEL" 
AUTH_TOKEN = "1qsXeC2Qi9wn1ozgYcm2jXGC92gkk23TccPqWwVolsn6g9XJCr32xC9353w85gWy"
FROM_NUMBER = "+918064270297" 
TO_NUMBER = "+916284101406" 

# NEW CLOUDFLARE LINK
ANSWER_URL = "https://decade-carlo-hugh-dresses.trycloudflare.com/answer"

url = f"https://api.vobiz.ai/api/v1/Account/{AUTH_ID}/Call/"
headers = {"X-Auth-ID": AUTH_ID, "X-Auth-Token": AUTH_TOKEN, "Content-Type": "application/json"}
payload = {"from": FROM_NUMBER, "to": TO_NUMBER, "answer_url": ANSWER_URL, "answer_method": "POST"}

print(f"Calling {TO_NUMBER}...")
response = requests.post(url, headers=headers, json=payload)
print("Status Code:", response.status_code)
print("Response:", response.text)
