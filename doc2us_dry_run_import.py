<!doctype html>
<html><head><meta charset="utf-8"><title>Upload EPS Data</title><link rel="stylesheet" href="/static/style.css"></head>
<body><main class="card wide">
<h1>Upload Octopus Poison B/C Excel</h1>
<p>Logged in as {{ email }}</p>
<form method="post" action="/process" enctype="multipart/form-data">
<label>Pharmacist name as per IC <input name="pharmacist_name" required placeholder="e.g. Johnny Chew Seng Wen"></label>
<label>Registration number <input name="reg_no" required placeholder="e.g. 018161"></label>
<label>Application date <input name="apply_date" type="date" value="{{ today }}" required></label>
<label>Raw Excel file <input name="excel_file" type="file" accept=".xlsx,.xls" required></label>
<button type="submit">Generate EPS Plan</button>
</form>
<section class="note"><b>Default questionnaire:</b> BP 120/80, HR 75, Glucose 6.0, Allergy NKDA, LTM, remarks refill medication. Email defaults to IC@doc2us.com.</section>
</main></body></html>
