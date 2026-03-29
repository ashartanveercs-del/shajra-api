"""Fix the duplicate Abrar branch by setting correct MotherRecordId links."""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

import airtable_client as db

# FIX 1: Shafqat Abrar's MotherRecordId should point to Shaheen Abrar
SHAFQAT_ID = "recad1UdlbhCYhKW2"
SHAHEEN_ABRAR_ID = "recxZcppwbQVQkIgX"
print("Fix 1: Shafqat Abrar -> MotherRecordId = Shaheen Abrar")
result = db.update_member(SHAFQAT_ID, {"MotherRecordId": SHAHEEN_ABRAR_ID})
print(f"  Done: MotherRecordId = {result.get('MotherRecordId')}")

# FIX 2: Nighat Haroon has no SpouseRecordId but should be linked to Haroon Rashid  
NIGHAT_ID = "recheZlpvSFhWnk7R"
HAROON_ID = "recBGp7TaHn6AxrWY"
nighat = db.get_member_by_id(NIGHAT_ID)
if not nighat.get("SpouseRecordId"):
    print("\nFix 2: Nighat Haroon -> SpouseRecordId = Haroon Rashid")
    result = db.update_member(NIGHAT_ID, {"SpouseRecordId": HAROON_ID})
    print(f"  Done: SpouseRecordId = {result.get('SpouseRecordId')}")
else:
    print(f"\nNighat already has SpouseRecordId = {nighat.get('SpouseRecordId')}")

# FIX 3: Sayyam Haq's MotherName says "Fauzia Shafqat" but no MotherRecordId
# There's no real record for Fauzia Shafqat yet - she's only a name.
# Check if SpouseName fields need fixing too.
SAYYAM_ID = "recIwwExwnmfEUQu6"
sayyam = db.get_member_by_id(SAYYAM_ID)
print(f"\nSayyam Haq: MotherRecordId={sayyam.get('MotherRecordId')}, SpouseRecordId={sayyam.get('SpouseRecordId')}")

print("\n✅ All fixes applied. Now verify with debug_tree.py")
