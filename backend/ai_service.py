"""
Shajra System — AI Processing Service
Parses raw form submissions into clean, structured data and matches relationships.
"""
import json
from groq import Groq
import airtable_client as db
from settings_manager import get_groq_api_key

def get_client() -> Groq:
    return Groq(api_key=get_groq_api_key())

SYSTEM_PROMPT = """You are a family genealogy data processing assistant. Your job is to take raw, unstructured form submissions about family members and return clean, standardized JSON.

You must:
1. Standardize names (proper capitalization, remove extra spaces).
2. Standardize dates to YYYY-MM-DD format when possible. If only a year is given, use YYYY. If unclear, keep the original text.
3. Standardize locations: extract city and country separately. Use full country names (e.g., "Pakistan" not "PK").
4. Determine gender from context clues.
5. Extract contact info (Email and Phone Number) and Profile Picture URL if provided in the input (often trapped in Biography notes).
6. CRITICAL - RELATIONSHIP MATCHING & DUAL PARENTS: Every child has a Father AND a Mother. You MUST identify both. Even for cousin marriages, identify both existing record IDs.

Return ONLY valid JSON. It must match these exact fields:
{
    "CleanFullName": "string",
    "CleanFatherName": "string or empty",
    "CleanMotherName": "string or empty",
    "CleanSpouseName": "string or empty",
    "CleanDOB": "string or empty",
    "CleanDOD": "string or empty",
    "CleanCity": "string or empty",
    "CleanCountry": "string or empty",
    "CleanBurialLocation": "string or empty",
    "CleanGender": "Male" or "Female" or "Other",
    "CleanEmail": "string or empty",
    "CleanPhoneNumber": "string or empty",
    "CleanProfileImage": "string or empty",
    "PossibleFatherMatch": "record_id or empty",
    "PossibleMotherMatch": "record_id or empty",
    "PossibleSpouseMatch": "record_id or empty",
    "IsDuplicate": true/false,
    "DuplicateOfId": "record_id or empty - if this is a literal duplicate submission of an existing member",
    "Confidence": 0.0 to 1.0,
    "Notes": "any observations about data quality or complex relationship logic (e.g. cousin marriage identified)"
}"""


def get_existing_members_context():
    """Build a concise context string of existing members for the AI."""
    try:
        members = db.get_all_members()
        if not members:
            return "No existing members in the database yet."

        lines = []
        for m in members:
            name = m.get("FullName", "Unknown")
            father = m.get("FatherName", "")
            mother = m.get("MotherName", "")
            spouse = m.get("SpouseName", "")
            rec_id = m.get("id", "")
            gender = m.get("Gender", "")
            city = m.get("CurrentCity", "")
            lines.append(f"- {name} (ID: {rec_id}, Father: {father}, Mother: {mother}, Spouse: {spouse}, Gender: {gender}, City: {city})")

        return "Existing family members (Use these IDs for ALL relationship matches):\n" + "\n".join(lines)
    except Exception as e:
        return f"Could not fetch existing members: {str(e)}"


def process_submission(raw_data: dict) -> dict:
    existing_context = get_existing_members_context()

    user_message = f"""Here is a raw family member submission:

Full Name: {raw_data.get('RawFullName', '')}
Father's Name: {raw_data.get('RawFatherName', '')}
Mother's Name: {raw_data.get('RawMotherName', '')}
Spouse's Name: {raw_data.get('RawSpouseName', '')}
Date of Birth: {raw_data.get('RawDateOfBirth', '')}
Date of Death: {raw_data.get('RawDateOfDeath', '')}
Location: {raw_data.get('RawLocation', '')}
Burial Location: {raw_data.get('RawBurialLocation', '')}
Email: {raw_data.get('RawEmail', '')}
Phone: {raw_data.get('RawPhoneNumber', '')}
Profile Img: {raw_data.get('RawProfileImage', '')}
Gender: {raw_data.get('RawGender', '')}
Biography/Notes: {raw_data.get('RawBiography', '')}

{existing_context}

Please clean and standardize this data, handle cousin linkages properly, and suggest relationship matches. Return ONLY valid JSON."""

    try:
        client = get_client()

        # The parameters exactly as requested
        completion_stream = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0.3,
            max_tokens=4096,
            top_p=1,
            stream=True,
            stop=None,
            timeout=30.0,
        )

        # Accumulating the stream into a single string
        response_text = ""
        for chunk in completion_stream:
            # We must gracefully handle deltas where content might be None
            if chunk.choices and hasattr(chunk.choices[0], 'delta') and chunk.choices[0].delta.content:
                response_text += chunk.choices[0].delta.content

        response_text = response_text.strip()

        # Extract JSON from response if surrounded by markdown blocks
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0].strip()

        result = json.loads(response_text)
        return result

    except json.JSONDecodeError as e:
        return {
            "CleanFullName": raw_data.get("RawFullName", ""),
            "CleanFatherName": raw_data.get("RawFatherName", ""),
            "CleanMotherName": raw_data.get("RawMotherName", ""),
            "CleanSpouseName": raw_data.get("RawSpouseName", ""),
            "CleanDOB": raw_data.get("RawDateOfBirth", ""),
            "CleanDOD": raw_data.get("RawDateOfDeath", ""),
            "CleanCity": "",
            "CleanCountry": "",
            "CleanBurialLocation": raw_data.get("RawBurialLocation", ""),
            "CleanGender": raw_data.get("RawGender", "Other"),
            "CleanEmail": raw_data.get("RawEmail", ""),
            "CleanPhoneNumber": raw_data.get("RawPhoneNumber", ""),
            "CleanProfileImage": raw_data.get("RawProfileImage", ""),
            "Confidence": 0.0,
            "Notes": f"AI JSON parsing failed: {str(e)}. Using raw data.",
            "IsDuplicate": False,
        }
    except Exception as e:
        return {
            "CleanFullName": raw_data.get("RawFullName", ""),
            "CleanFatherName": raw_data.get("RawFatherName", ""),
            "CleanMotherName": raw_data.get("RawMotherName", ""),
            "CleanSpouseName": raw_data.get("RawSpouseName", ""),
            "CleanDOB": raw_data.get("RawDateOfBirth", ""),
            "CleanDOD": raw_data.get("RawDateOfDeath", ""),
            "CleanCity": "",
            "CleanCountry": "",
            "CleanBurialLocation": raw_data.get("RawBurialLocation", ""),
            "CleanGender": raw_data.get("RawGender", "Other"),
            "CleanEmail": raw_data.get("RawEmail", ""),
            "CleanPhoneNumber": raw_data.get("RawPhoneNumber", ""),
            "CleanProfileImage": raw_data.get("RawProfileImage", ""),
            "Confidence": 0.0,
            "Notes": f"AI service error: {str(e)}. Using raw data.",
            "IsDuplicate": False,
        }


def process_and_store_submission(raw_data: dict) -> dict:
    ai_result = process_submission(raw_data)

    pending_record = {
        "RawFullName": raw_data.get("RawFullName", ""),
        "RawFatherName": raw_data.get("RawFatherName", ""),
        "RawMotherName": raw_data.get("RawMotherName", ""),
        "RawSpouseName": raw_data.get("RawSpouseName", ""),
        "RawDateOfBirth": raw_data.get("RawDateOfBirth", ""),
        "RawDateOfDeath": raw_data.get("RawDateOfDeath", ""),
        "RawLocation": raw_data.get("RawLocation", ""),
        "RawBurialLocation": raw_data.get("RawBurialLocation", ""),
        "RawBiography": raw_data.get("RawBiography", ""),
        "RawGender": raw_data.get("RawGender", ""),
        "RawEmail": raw_data.get("RawEmail", ""),
        "RawPhoneNumber": raw_data.get("RawPhoneNumber", ""),
        "RawProfileImage": raw_data.get("RawProfileImage", ""),
        "SubmittedAt": raw_data.get("SubmittedAt", ""),

        "CleanFullName": ai_result.get("CleanFullName", ""),
        "CleanFatherName": ai_result.get("CleanFatherName", ""),
        "CleanMotherName": ai_result.get("CleanMotherName", ""),
        "CleanSpouseName": ai_result.get("CleanSpouseName", ""),
        "CleanDOB": ai_result.get("CleanDOB", ""),
        "CleanDOD": ai_result.get("CleanDOD", ""),
        "CleanCity": ai_result.get("CleanCity", ""),
        "CleanCountry": ai_result.get("CleanCountry", ""),
        "CleanBurialLocation": ai_result.get("CleanBurialLocation", ""),
        "CleanGender": ai_result.get("CleanGender", "Other"),
        "CleanEmail": ai_result.get("CleanEmail", ""),
        "CleanPhoneNumber": ai_result.get("CleanPhoneNumber", ""),
        "CleanProfileImage": ai_result.get("CleanProfileImage", ""),

        "AIMatchedFatherId": ai_result.get("PossibleFatherMatch", ""),
        "AIMatchedMotherId": ai_result.get("PossibleMotherMatch", ""),
        "AIMatchedSpouseId": ai_result.get("PossibleSpouseMatch", ""),
        "AIConfidence": ai_result.get("Confidence", 0.0),
        "AIDuplicateFlag": ai_result.get("IsDuplicate", False),
        "AINotes": ai_result.get("Notes", ""),
        "Status": "Pending",
    }

    stored = db.create_pending(pending_record)
    return stored
