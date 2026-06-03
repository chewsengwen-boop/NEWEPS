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
  - key: EPS_STAFF_ACCOUNTS_JSON
    scope: RUN_TIME
    type: SECRET
  - key: EPS_ALLOWED_EMAIL
    scope: RUN_TIME
    type: SECRET
  - key: EPS_ALLOWED_PASSWORD
    scope: RUN_TIME
    type: SECRET
  - key: DOC2US_EMAIL
    scope: RUN_TIME
    type: SECRET
  - key: DOC2US_PASSWORD
    scope: RUN_TIME
    type: SECRET
