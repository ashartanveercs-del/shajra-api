import os
import time
from dotenv import load_dotenv
load_dotenv('../.env')

import airtable_client as db

def main():
    print("🧹 Cleaning existing records...")
    all_m = db.get_all_members()
    for m in all_m:
        db.delete_member(m['id'])
    
    print("✨ Adding Roots: Habib ur Rehan & Shakila Begum...")
    habib = db.create_member({
        "FullName": "Habib ur Rehan",
        "Gender": "Male",
        "Generation": 1,
        "IsAlive": False
    })
    shakila = db.create_member({
        "FullName": "Shakila Begum",
        "Gender": "Female",
        "Generation": 1,
        "IsAlive": False,
        "SpouseRecordId": habib['id']
    })
    # Update Habib with spouse link
    db.update_member(habib['id'], {"SpouseRecordId": shakila['id']})

    children_data = [
        {"name": "Haroon Rashid", "gender": "Male", "spouse": "Nighat Yasmin"},
        {"name": "Tanveer Kamal", "gender": "Male", "spouse": "Shehla Tanveer"},
        {"name": "Humayun Rashid", "gender": "Male", "spouse": "Zahida Begum"},
        {"name": "Shaheen Abrar", "gender": "Female", "spouse": "Abrar ul Haq"},
        {"name": "Saba Nazli", "gender": "Female", "spouse": "Musharraf"},
        {"name": "Mumtaz Shireen", "gender": "Female", "spouse": "Qamar Alam"},
        {"name": "Sabiha Rasheed", "gender": "Female", "spouse": "Abdul Rashid"},
        {"name": "Iqbal Rashid", "gender": "Male", "spouse": "Farhat Iqbal"},
        {"name": "Aftab Alamgir", "gender": "Male", "spouse": "Sobia Alamgir"},
    ]

    print(f"🌲 Adding {len(children_data)} children...")
    for child in children_data:
        # Create Spouse first if they are not roots
        spouse = db.create_member({
            "FullName": child['spouse'],
            "Gender": "Female" if child['gender'] == "Male" else "Male",
            "Generation": 2
        })
        
        # Create Child
        m = db.create_member({
            "FullName": child['name'],
            "Gender": child['gender'],
            "Generation": 2,
            "FatherRecordId": habib['id'],
            "MotherRecordId": shakila['id'],
            "SpouseRecordId": spouse['id']
        })
        
        # Link Spouse back to Child
        db.update_member(spouse['id'], {"SpouseRecordId": m['id']})
        print(f"✅ Added {child['name']} & {child['spouse']}")

    print("🎉 Family Data Populated successfully!")

if __name__ == "__main__":
    main()
