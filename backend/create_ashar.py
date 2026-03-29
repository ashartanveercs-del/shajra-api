import airtable_client as db
fields = {
    'FullName': 'Ashar Tanveer',
    'Gender': 'Male',
    'Generation': 4,
    'FatherRecordId': 'recreFPNBR6r5hsXW',
    'MotherRecordId': 'recf5lbMQYr3pcKay',
    'CurrentCity': 'Karachi',
    'CurrentCountry': 'Pakistan',
    'IsAlive': True,
    'Biography': 'Creator of the Shajra Heritage digital system.'
}
try:
    ashar = db.create_member(fields)
    print(f"SUCCESS: Created Ashar {ashar['id']}")
except Exception as e:
    print(f"FAILURE: {e}")
