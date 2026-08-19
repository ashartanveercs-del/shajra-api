"""
Shajra System — AI Processing Service
Parses raw form submissions into clean, structured data and matches relationships.
"""
import json

import airtable_client as db
from config import get_settings
from fastapi import HTTPException
from groq import Groq
from public_data import exact_relationship_ids, normalize_name, unique_member_by_name


def get_client() -> Groq:
    groq_api_key = get_settings().groq_api_key
    if groq_api_key is None or not groq_api_key.get_secret_value().strip():
        raise HTTPException(
            status_code=503,
            detail={"code": "AI_NOT_CONFIGURED", "message": "AI processing is not configured."},
        )
    return Groq(api_key=groq_api_key.get_secret_value())

SYSTEM_PROMPT = """You are a family genealogy data processing assistant. Your job is to take raw, unstructured form submissions about family members and return clean, standardized JSON.

You must:
1. Standardize names (proper capitalization, remove extra spaces).
2. Standardize dates to YYYY-MM-DD format when possible. If only a year is given, use YYYY. If unclear, keep the original text.
3. Determine gender from context clues.
4. Do not infer contact, location, burial, biography, image, or record-ID data. Those fields are handled locally and are not provided to you.
5. CRITICAL - RELATIONSHIP MATCHING & DUAL PARENTS: Every child has a Father AND a Mother. You MUST identify both. Even for cousin marriages, identify both parent names. IMPORTANT: only extract the parent/spouse NAMES — record-ID matching is handled separately and deterministically by the system, so do NOT attempt to guess or return record IDs.

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
    "IsDuplicate": true/false,
    "DuplicateOfName": "FullName string or empty - if this is a literal duplicate submission of an existing member",
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
            gender = m.get("Gender", "")
            lines.append(
                f"- {name} (Father: {father}, Mother: {mother}, "
                f"Spouse: {spouse}, Gender: {gender})"
            )

        return "Existing family members (for reference and duplicate detection):\n" + "\n".join(lines)
    except Exception:  # noqa: BLE001 - Preserve the v1 external datastore context fallback.
        return "Existing member context is temporarily unavailable."


def _normalize_name(name) -> str:
    """Casefold, strip diacritics, collapse whitespace."""
    return normalize_name(name)


def _match_member_id(name, members, gender=None) -> str:
    """Deterministic, ambiguity-safe name -> member record ID.

    Returns the matched record ID, or "" when there is no unique match. A name
    is only linked when a key matches EXACTLY ONE candidate; any ambiguity
    resolves to "" so the wrong same-name relative is never linked. This
    replaces the LLM's unreliable ID guessing.
    """
    match = unique_member_by_name(name, members, gender=gender)
    return str(match.get("id", "")) if match else ""


def _build_submission_prompt(raw_data: dict, existing_context: str) -> str:
    """Build the provider prompt from relationship-matching data only."""
    return f"""Here is a raw family member submission:

Full Name: {raw_data.get('RawFullName', '')}
Father's Name: {raw_data.get('RawFatherName', '')}
Mother's Name: {raw_data.get('RawMotherName', '')}
Spouse's Name: {raw_data.get('RawSpouseName', '')}
Date of Birth: {raw_data.get('RawDateOfBirth', '')}
Date of Death: {raw_data.get('RawDateOfDeath', '')}
Gender: {raw_data.get('RawGender', '')}

{existing_context}

Please clean and standardize the names and dates, handle cousin linkages properly,
and suggest relationship matches. Return ONLY valid JSON."""


def _apply_local_only_fields(provider_result: dict, raw_data: dict) -> dict:
    """Replace fields withheld from the provider with locally derived values."""
    result = dict(provider_result)
    raw_location = str(raw_data.get("RawLocation", "") or "").strip()
    city, separator, country = raw_location.partition(",")
    result.update(
        {
            "CleanCity": city.strip(),
            "CleanCountry": country.strip() if separator else "",
            "CleanBurialLocation": raw_data.get("RawBurialLocation", ""),
            "CleanEmail": raw_data.get("RawEmail", ""),
            "CleanPhoneNumber": raw_data.get("RawPhoneNumber", ""),
            "CleanProfileImage": raw_data.get("RawProfileImage", ""),
        }
    )
    return result


def _raw_submission_fallback(raw_data: dict, notes: str) -> dict:
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
        "Notes": notes,
        "IsDuplicate": False,
    }


def process_submission(raw_data: dict) -> dict:
    try:
        client = get_client()
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 - Preserve the v1 external AI fallback to raw submission data.
        return _raw_submission_fallback(raw_data, "AI processing failed. Using raw data.")

    existing_context = get_existing_members_context()

    user_message = _build_submission_prompt(raw_data, existing_context)

    try:
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
        return _apply_local_only_fields(result, raw_data)

    except HTTPException:
        raise
    except json.JSONDecodeError:
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
            "Notes": "AI response could not be parsed. Using raw data.",
            "IsDuplicate": False,
        }
    except Exception:  # noqa: BLE001 - Preserve the v1 external AI fallback to raw submission data.
        return _raw_submission_fallback(raw_data, "AI processing failed. Using raw data.")


def process_and_store_submission(raw_data: dict) -> dict:
    ai_result = process_submission(raw_data)

    # Deterministic relationship matching. The LLM must NOT be trusted to return
    # record IDs (it hallucinated mismatches, e.g. attaching Sobia's ID as the
    # mother of Aiemen's children). Match exact raw relationship names against
    # existing members here, using the same ambiguity-safe
    # rules as get_tree(). Provider-cleaned names are display data only.
    try:
        members = db.get_all_members()
    except Exception:  # noqa: BLE001 - degrade to no record links if the store is unavailable.
        members = []

    relationship_ids = exact_relationship_ids(
        members,
        father_name=raw_data.get("RawFatherName"),
        mother_name=raw_data.get("RawMotherName"),
        spouse_name=raw_data.get("RawSpouseName"),
        subject_gender=raw_data.get("RawGender") or ai_result.get("CleanGender", ""),
    )

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

        "AIMatchedFatherId": relationship_ids["FatherRecordId"],
        "AIMatchedMotherId": relationship_ids["MotherRecordId"],
        "AIMatchedSpouseId": relationship_ids["SpouseRecordId"],
        "AIConfidence": ai_result.get("Confidence", 0.0),
        "AIDuplicateFlag": ai_result.get("IsDuplicate", False),
        "AINotes": ai_result.get("Notes", ""),
        "Status": "Pending",
    }

    stored = db.create_pending(pending_record)
    return stored
