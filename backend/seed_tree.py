import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from airtable_client import create_member, add_approved_email

print("Seeding database...")

try:
    m1 = create_member({
        "FullName": "Tariq Ali",
        "Gender": "Male",
        "CurrentCity": "Lahore",
        "CurrentCountry": "Pakistan",
        "Latitude": 31.5204,
        "Longitude": 74.3587,
        "DateOfBirth": "1945",
        "Biography": "The patriarch of the Ali family. He moved to Lahore in his early twenties.",
        "IsAlive": False,
        "DateOfDeath": "2015",
        "BurialLocation": "Miani Sahib Cemetery, Lahore",
        "BurialLatitude": 31.5369,
        "BurialLongitude": 74.3168
    })

    m2 = create_member({
        "FullName": "Zainab Begum",
        "Gender": "Female",
        "CurrentCity": "Lahore",
        "CurrentCountry": "Pakistan",
        "SpouseRecordId": m1.get("id"),
        "Latitude": 31.5204,
        "Longitude": 74.3587,
        "DateOfBirth": "1950",
        "Biography": "Tariq's beloved wife.",
        "IsAlive": False,
        "DateOfDeath": "2020"
    })

    m3 = create_member({
        "FullName": "Salman Tariq",
        "Gender": "Male",
        "CurrentCity": "London",
        "CurrentCountry": "United Kingdom",
        "FatherRecordId": m1.get("id"),
        "MotherRecordId": m2.get("id"),
        "Latitude": 51.5074,
        "Longitude": -0.1278,
        "DateOfBirth": "1975",
        "Biography": "Moved to London for university and settled there.",
        "IsAlive": True
    })

    m4 = create_member({
        "FullName": "Fatima Salman",
        "Gender": "Female",
        "CurrentCity": "London",
        "CurrentCountry": "United Kingdom",
        "FatherRecordId": m3.get("id"),
        "Latitude": 51.5074,
        "Longitude": -0.1278,
        "DateOfBirth": "2005",
        "Biography": "Currently attending college in London.",
        "IsAlive": True
    })

    add_approved_email({
        "Email": "test@family.com",
        "Name": "Test Agent",
        "Notes": "Auto-seeded for testing"
    })

    print("Success! Dummy tree and test email added.")
except Exception as e:
    print(f"Failed: {e}")
