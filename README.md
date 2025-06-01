![image](https://github.com/user-attachments/assets/fbb99ac2-0541-4476-9167-d49e52e95634)

![image](https://github.com/user-attachments/assets/7594472c-70bf-4349-9695-ac4465869c8d)


(주소)/?url=(유튜브주소)
로 접속 시 유튜브 주소 자동 입력


예시 docker-compose
'''
version: '3.8'

services:
  redis:
    image: "redis:alpine"
    ports:
      - "16379:6379"
    volumes:
      - redis_data:/data # Redis 데이터 영속화 (선택적)
    restart: unless-stopped

  app:
    image: rilakkumamama/youtubedl-app:latest # Docker Hub 이미지 사용 (태그는 필요에 따라 변경)
    # build: . # 로컬 빌드 지시어 제거 또는 주석 처리
    ports:
      - "5001:5001"
    depends_on:
      - redis
    environment:
      - REDIS_URL=redis://redis:6379/0
      - FLASK_ENV=production # 프로덕션 환경에서는 production으로 설정하는 것이 좋음
      # - PYTHONUNBUFFERED=1 # 로그 즉시 출력
    volumes:
      # 개발 중 로컬 코드 변경 사항을 즉시 반영하고 싶다면 아래 주석을 해제하고,
      # Docker Hub 이미지에는 기본 코드만 포함되도록 빌드합니다.
      # 이 경우, 이미지 업데이트는 코드 변경이 아닌 의존성 변경 시에만 필요할 수 있습니다.
      # - .:/app
      - task_temp_downloads_volume:/app/task_temp_downloads # 임시 다운로드 폴더는 여전히 볼륨 사용
    command: ["gunicorn", "--bind", "0.0.0.0:5001", "--workers", "2", "--threads", "4", "--worker-class", "eventlet", "app:app"]
    restart: unless-stopped

  celery_worker:
    image: rilakkumamama/youtubedl-app:latest # Docker Hub 이미지 사용
    # build: . # 로컬 빌드 지시어 제거 또는 주석 처리
    command: ["celery", "-A", "tasks.celery_app", "worker", "-l", "info", "-P", "eventlet", "-c", "2"]
    depends_on:
      - redis
    environment:
      - REDIS_URL=redis://redis:6379/0
    volumes:
      # - .:/app # 개발 중 코드 변경 반영 필요시 주석 해제
      - task_temp_downloads_volume:/app/task_temp_downloads
    restart: unless-stopped

  celery_beat:
    image: rilakkumamama/youtubedl-app:latest
    command: ["celery", "-A", "tasks.celery_app", "beat", "-l", "info", "--schedule=/app/celerybeat-schedule.db"] # 스케줄 파일 경로 명시 (확장자는 .db 등 아무거나 가능)
    depends_on:
      - redis
    environment:
      - REDIS_URL=redis://redis:6379/0
    volumes:
      - .:/app # 로컬 코드를 /app에 마운트 (개발 시). 이로 인해 /app/celerybeat-schedule.db도 호스트에 저장됨
      # - celery_beat_schedule_data:/app/schedule_data # 또는 스케줄 파일만 별도 볼륨에 저장하려면
    restart: unless-stopped

volumes:
  redis_data:
  task_temp_downloads_volume:
  # celery_beat_schedule_data: # 위에서 celery_beat_schedule_data 사용 시 여기에 정의
  '''


  
