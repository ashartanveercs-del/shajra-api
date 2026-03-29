import urllib.request
import json
import time

API_BASE = "http://127.0.0.1:8000"

def do_request(endpoint, method="GET", data=None, token=None):
    url = API_BASE + endpoint
    req = urllib.request.Request(url, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
        req.data = json.dumps(data).encode("utf-8")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        res = urllib.request.urlopen(req)
        return json.loads(res.read().decode())
    except urllib.error.HTTPError as e:
        print(f"HTTPError on {method} {endpoint}: {e.code} - {e.read().decode()}")
        return None
    except Exception as e:
        print(f"Exception on {method} {endpoint}: {e}")
        return None

print("1. Admin Login...")
token_res = do_request("/api/admin/login", method="POST", data={"username": "admin", "password": "shajrasecure123"})
if not token_res or "access_token" not in token_res:
    print("Login failed")
    exit(1)
token = token_res["access_token"]
print("Login success.")

print("\n2. Submitting dummy member...")
dummy_data = {
    "fullName": "Test E2E Member",
    "fatherName": "Test E2E Father",
    "biography": "E2E Automated Form Testing",
    "gender": "Male"
}
submit_res = do_request("/api/submit", method="POST", data=dummy_data)
if not submit_res:
    print("Submit failed")
    exit(1)
print("Submit success:", submit_res)

print("\n3. Waiting 2 seconds for Airtable sync...")
time.sleep(2)

print("\n4. Fetching Pending Submissions...")
pending_list = do_request("/api/admin/pending", method="GET", token=token)
if not pending_list:
    print("No pending submissions returned.")
    exit(1)

# Find the one we just submitted
target = None
for p in pending_list:
    if p.get("RawFullName") == "Test E2E Member":
        target = p
        break

if not target:
    print("Dummy member not found in pending list.")
    exit(1)

print("Found pending submission:", target["id"])

print("\n5. Approving Submission (this triggers AI matching, may take 10-20 seconds)...")
approve_res = do_request(f"/api/admin/approve/{target['id']}", method="POST", token=token)
if not approve_res:
    print("Approval failed!")
    exit(1)

print("Approval successful:", approve_res.get("status"))

print("\n6. Fetching Public Tree to verify...")
tree_res = do_request("/api/tree", method="GET")
if not tree_res:
    print("Fetch Tree failed.")
    exit(1)

def search_tree(nodes, name):
    for n in nodes:
        if n.get("FullName") == name:
            return True
        if "children" in n and n["children"]:
            if search_tree(n["children"], name):
                return True
    return False

found = search_tree(tree_res, "Test E2E Member")
print("Verification complete. Is Test E2E Member on the tree?", "YES" if found else "NO")

