name: eps-web-automation
services:
- name: web
  environment_slug: python
  instance_count: 1
  instance_size_slug: basic-xxs
  http_port: 8080
  run_command: uvicorn app.main:app --host 0.0.0.0 --port $PORT
  source_dir: /
  routes:
  - path: /
  envs:
  - key: EPS_ALLOWED_EMAIL
    value: qsbjc1@alpropharmacy.com
    scope: RUN_TIME
  - key: EPS_ALLOWED_PASSWORD
    value: Alpro-123
    scope: RUN_TIME
