EPS Shared Web Automation

What this is
- A browser-based shared web app for colleagues.
- Colleagues login using the same Doc2Us/EPS login style.
- Pilot login enabled first:
  qsbjc1@alpropharmacy.com / Alpro-123
- Upload Octopus Poison B/C Excel.
- Enter pharmacist name and registration number.
- App generates READY / REVIEW / OMIT EPS plan and downloadable Excel.

Important
- This tool does not prescribe. It structures and submits data to reduce repeated entry.
- Pharmacist review is still required before any EPS submission.
- Current version completes shared upload/review/export. Live EPS submit should be enabled after pilot verification and EPS selector mapping.

Run locally for testing
cd /home/johnny/eps-web-automation
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8088
Open: http://localhost:8088

Deploy options
- Render/Railway/Fly.io/DigitalOcean VPS.
- Install requirements.txt.
- Start command:
  uvicorn app.main:app --host 0.0.0.0 --port $PORT

Security upgrades before public internet use
1. Put behind HTTPS.
2. Replace pilot hardcoded login with real encrypted user/session management or Doc2Us verification flow.
3. Store uploads with retention policy or auto-delete.
4. Add audit log per pharmacist and uploaded file.
5. Keep OpenAI/ChatGPT medicine classification limited to item name/quantity only, not patient IC/name.

AI/ChatGPT integration design
- Existing rule table runs first.
- Unknown medicine becomes REVIEW.
- Optional next step: call OpenAI only for unknown medicines to suggest class/indication/dose/frequency from handbook context.
- Pharmacist approves suggestion, then saves it into medication_rules.csv/database for future use.
