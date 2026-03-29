/**
 * GOOGLE APPS SCRIPT — Webhook for Shajra System
 * 
 * HOW TO SET UP:
 * 1. Open your Google Form in edit mode
 * 2. Click the three-dot menu → Script Editor
 * 3. Paste this entire script
 * 4. Replace YOUR_BACKEND_URL with your actual backend URL
 * 5. Click "Save"
 * 6. In the Script Editor, go to Triggers (clock icon on left)
 * 7. Click "+ Add Trigger"
 * 8. Set: onFormSubmit | From Form | On Form Submit
 * 9. Click Save and authorize
 * 
 * Your Google Form should have these fields (in order):
 * - Full Name
 * - Father's Name
 * - Mother's Name
 * - Spouse's Name
 * - Date of Birth
 * - Date of Death
 * - Location (City, Country)
 * - Burial Location
 * - Biography / Notes
 * - Gender
 */

const BACKEND_URL = "YOUR_BACKEND_URL"; // e.g., "https://your-app.onrender.com"

function onFormSubmit(e) {
  try {
    const responses = e.response.getItemResponses();
    
    const payload = {
      fullName: responses[0] ? responses[0].getResponse() : "",
      fatherName: responses[1] ? responses[1].getResponse() : "",
      motherName: responses[2] ? responses[2].getResponse() : "",
      spouseName: responses[3] ? responses[3].getResponse() : "",
      dateOfBirth: responses[4] ? responses[4].getResponse() : "",
      dateOfDeath: responses[5] ? responses[5].getResponse() : "",
      location: responses[6] ? responses[6].getResponse() : "",
      burialLocation: responses[7] ? responses[7].getResponse() : "",
      biography: responses[8] ? responses[8].getResponse() : "",
      gender: responses[9] ? responses[9].getResponse() : "",
      timestamp: new Date().toISOString(),
    };

    const options = {
      method: "post",
      contentType: "application/json",
      payload: JSON.stringify(payload),
      muteHttpExceptions: true,
    };

    const response = UrlFetchApp.fetch(
      BACKEND_URL + "/api/webhook/google-form",
      options
    );

    Logger.log("Response: " + response.getContentText());
  } catch (error) {
    Logger.log("Error sending to backend: " + error.toString());
  }
}
