import urllib.request
import time

print("Testing GET /api/tree...")
try:
    res1 = urllib.request.urlopen("http://127.0.0.1:8000/api/tree", timeout=5)
    print("GET /api/tree works:", res1.getcode())
except Exception as e:
    print("GET error:", e)

print("Testing OPTIONS /api/admin/members/dummy...")
try:
    req = urllib.request.Request("http://127.0.0.1:8000/api/admin/members/dummy", method="OPTIONS")
    req.add_header("Origin", "http://localhost:3000")
    req.add_header("Access-Control-Request-Method", "PUT")
    res2 = urllib.request.urlopen(req, timeout=5)
    print("OPTIONS works:", res2.getcode())
except Exception as e:
    print("OPTIONS error:", e)
    
print("Testing PUT payload...")
import json
try:
    data = json.dumps({"FatherRecordId": "recXYZ", "FullName": "Test Name"})
    req3 = urllib.request.Request("http://127.0.0.1:8000/api/admin/members/dummy", data=data.encode('utf-8'), method="PUT")
    req3.add_header("Content-Type", "application/json")
    req3.add_header("Origin", "http://localhost:3000")
    res3 = urllib.request.urlopen(req3, timeout=5)
    print("PUT works:", res3.getcode())
except Exception as e:
    print("PUT error:", e)
