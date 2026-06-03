<!doctype html>
<html><head><meta charset="utf-8"><title>Review EPS Plan</title><link rel="stylesheet" href="/static/style.css"></head>
<body><main class="wide">
<h1>EPS Plan Review</h1>
<p>Logged in as {{ email }}</p>
<div class="summary">
{% for k,v in job.counts.items() %}<span class="pill {{k}}">{{k}}: {{v}}</span>{% endfor %}
</div>
<p><a class="button" href="/download/{{ job.job_id }}">Download Excel Review Workbook</a> <a href="/upload">Upload another file</a></p>
<table>
<thead><tr><th>Status</th><th>Reason</th><th>Patient</th><th>IC</th><th>Item</th><th>Qty</th><th>Class</th><th>Indication</th><th>Freq</th><th>Days</th><th>Amount</th><th>Next Appt</th></tr></thead>
<tbody>
{% for r in job.preview %}
<tr class="{{ r.status }}"><td>{{ r.status }}</td><td>{{ r.skip_reason }}</td><td>{{ r.patient_name }}</td><td>{{ r.patient_ic }}</td><td>{{ r.item_name }}</td><td>{{ r.qty }}</td><td>{{ r.medication_class }}</td><td>{{ r.indication }}</td><td>{{ r.frequency }}</td><td>{{ r.duration_days }}</td><td>{{ r.prescribed_amount }}</td><td>{{ r.next_appointment_date }}</td></tr>
{% endfor %}
</tbody></table>
<p class="note">READY rows can proceed after pharmacist review. REVIEW rows require checking. OMIT rows should not be submitted via EPS.</p>
</main></body></html>
