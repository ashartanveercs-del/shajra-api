import asyncio
import traceback

def run_debug():
    try:
        from main import direct_submit
        from pydantic import BaseModel
        from typing import Optional

        class MockPayload(BaseModel):
            fullName: str
            fatherName: Optional[str] = ""
            motherName: Optional[str] = ""
            spouseName: Optional[str] = ""
            dateOfBirth: Optional[str] = ""
            dateOfDeath: Optional[str] = ""
            location: Optional[str] = ""
            burialLocation: Optional[str] = ""
            biography: Optional[str] = ""
            gender: Optional[str] = ""
            email: Optional[str] = ""
            phoneNumber: Optional[str] = ""
            profileImage: Optional[str] = ""

        payload = MockPayload(fullName="Test E2E Member", fatherName="Test E2E Father")
        
        loop = asyncio.get_event_loop()
        res = loop.run_until_complete(direct_submit(payload))
        print("Success:", res)
    except Exception as e:
        print("Exception caught directly:")
        traceback.print_exc()

run_debug()
