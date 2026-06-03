#!/usr/bin/env python3
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from http.cookies import SimpleCookie
import html, mimetypes, re

from app.web_logic import authenticate, process_upload

BASE=Path(__file__).resolve().parent
JOBS=BASE/'jobs'


def page(title, body):
    css='<style>body{font-family:Arial;background:#f6f8fb;padding:32px}.card,.wide{max-width:1100px;margin:auto;background:white;padding:24px;border-radius:14px;box-shadow:0 8px 30px #0001}label{display:block;margin:12px 0;font-weight:700}input{display:block;width:100%;padding:10px;margin-top:5px}button,.button{background:#0f766e;color:white;padding:10px 14px;border:0;border-radius:8px;text-decoration:none}.err{background:#fee2e2;color:#991b1b;padding:10px}.pill{padding:8px 12px;border-radius:999px;margin-right:8px;font-weight:700}.READY{background:#dcfce7}.REVIEW{background:#fef3c7}.OMIT{background:#fee2e2}table{border-collapse:collapse;width:100%;font-size:13px}td,th{border:1px solid #ddd;padding:6px;vertical-align:top}th{background:#f1f5f9}</style>'
    return f'<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>{css}</head><body>{body}</body></html>'.encode()

def parse_multipart(body, ctype):
    m=re.search('boundary=(.+)', ctype)
    if not m: return {}, {}
    boundary=('--'+m.group(1)).encode()
    fields={}; files={}
    for part in body.split(boundary):
        if b'\r\n\r\n' not in part: continue
        head, data = part.split(b'\r\n\r\n',1)
        data=data.rstrip(b'\r\n--')
        h=head.decode(errors='ignore')
        nm=re.search(r'name="([^"]+)"', h)
        if not nm: continue
        name=nm.group(1)
        fn=re.search(r'filename="([^"]*)"', h)
        if fn and fn.group(1): files[name]=(fn.group(1), data)
        else: fields[name]=data.decode(errors='ignore')
    return fields, files

class H(BaseHTTPRequestHandler):
    def logged(self):
        c=SimpleCookie(self.headers.get('Cookie',''))
        return c.get('eps_email').value if c.get('eps_email') else ''
    def send_html(self, b, code=200, cookie=None):
        self.send_response(code); self.send_header('Content-Type','text/html; charset=utf-8')
        if cookie: self.send_header('Set-Cookie', cookie)
        self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        path=urlparse(self.path).path
        if path=='/health':
            self.send_response(200); self.end_headers(); self.wfile.write(b'ok'); return
        if path.startswith('/download/'):
            job=path.split('/')[-1]
            if not job.isalnum(): self.send_error(400); return
            files=list((JOBS/job).glob('*_EPS_PLAN.xlsx')) if (JOBS/job).exists() else []
            if not files: self.send_error(404); return
            data=files[0].read_bytes(); self.send_response(200)
            self.send_header('Content-Type','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            self.send_header('Content-Disposition', f'attachment; filename="{files[0].name}"')
            self.end_headers(); self.wfile.write(data); return
        if path=='/upload':
            if not self.logged(): self.send_response(303); self.send_header('Location','/'); self.end_headers(); return
            body='<main class="card"><h1>Upload Octopus Excel</h1><form method="post" action="/process" enctype="multipart/form-data"><label>Pharmacist name <input name="pharmacist_name" required></label><label>Registration number <input name="reg_no" required></label><label>Application date <input type="date" name="apply_date" value="2026-06-01" required></label><label>Raw Excel <input type="file" name="excel_file" required></label><button>Generate EPS Plan</button></form><p>Defaults: BP 120/80, HR 75, Glucose 6.0, LTM, refill medication.</p></main>'
            self.send_html(page('Upload',body)); return
        body='<main class="card"><h1>EPS Shared Automation Login</h1><form method="post" action="/login"><label>Email <input type="email" name="email" placeholder="staff@alpropharmacy.com" required></label><label>Password <input type="password" name="password" required></label><button>Login</button></form><p>This tool submits data flow; it does not prescribe. Pharmacist review remains required.</p></main>'
        self.send_html(page('Login',body))
    def do_POST(self):
        n=int(self.headers.get('Content-Length','0')); body=self.rfile.read(n); path=urlparse(self.path).path
        if path=='/login':
            f=parse_qs(body.decode()); email=f.get('email',[''])[0]; pw=f.get('password',[''])[0]
            if authenticate(email,pw):
                self.send_response(303); self.send_header('Location','/upload'); self.send_header('Set-Cookie',f'eps_email={email}; HttpOnly; SameSite=Lax'); self.end_headers(); return
            self.send_html(page('Login','<main class="card"><div class="err">Invalid login. Pilot account only.</div><a href="/">Try again</a></main>'),401); return
        if path=='/process':
            if not self.logged(): self.send_response(303); self.send_header('Location','/'); self.end_headers(); return
            fields,files=parse_multipart(body,self.headers.get('Content-Type',''))
            fn,data=files.get('excel_file',('upload.xlsx',b''))
            job=process_upload(data,fn,fields.get('pharmacist_name',''),fields.get('reg_no',''),fields.get('apply_date','2026-06-01'),JOBS)
            rows=''.join(f"<tr class='{r['status']}'><td>{html.escape(str(r['status']))}</td><td>{html.escape(str(r['skip_reason']))}</td><td>{html.escape(str(r['patient_name']))}</td><td>{html.escape(str(r['patient_ic']))}</td><td>{html.escape(str(r['item_name']))}</td><td>{r['qty']}</td><td>{html.escape(str(r['indication']))}</td><td>{r['duration_days']}</td><td>{r['prescribed_amount']}</td><td>{r['next_appointment_date']}</td></tr>" for r in job['preview'])
            pills=' '.join(f"<span class='pill {k}'>{k}: {v}</span>" for k,v in job['counts'].items())
            b=f"<main class='wide'><h1>Review EPS Plan</h1><p>{pills}</p><p><a class='button' href='/download/{job['job_id']}'>Download Excel</a> <a href='/upload'>Upload another</a></p><table><tr><th>Status</th><th>Reason</th><th>Patient</th><th>IC</th><th>Item</th><th>Qty</th><th>Indication</th><th>Days</th><th>Amount</th><th>Next Appt</th></tr>{rows}</table></main>"
            self.send_html(page('Review',b)); return
        self.send_error(404)

if __name__=='__main__':
    print('Open http://127.0.0.1:8088')
    ThreadingHTTPServer(('127.0.0.1',8088), H).serve_forever()
