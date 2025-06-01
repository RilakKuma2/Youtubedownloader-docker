import os
import re
import time
from flask import Flask, render_template, request, jsonify, send_from_directory, abort # url_for는 현재 미사용
from celery.result import AsyncResult
import configparser
import yt_dlp
from datetime import datetime
import logging # Flask 기본 로거 사용 또는 logging 모듈 직접 사용

# tasks.py에서 Celery 앱 인스턴스 및 작업 가져오기
from tasks import celery_app, download_video_task, TEMP_DOWNLOAD_BASE_DIR

app = Flask(__name__)
# Flask 앱 로거 설정 (필요에 따라 레벨 등 조정)
# app.logger.setLevel(logging.INFO) 
# gunicorn 사용 시 gunicorn 로거를 따를 수 있음

# --- Configuration ---
CONFIG_FILE = "settings.ini" # Flask 앱용 설정 (필요시)
config = configparser.ConfigParser()
if os.path.exists(CONFIG_FILE):
    config.read(CONFIG_FILE)
    # 여기서 Flask 앱 레벨의 설정을 읽어올 수 있습니다.

DEFAULT_VIDEO_FORMATS_PREF = ["616", "22", "18"] # 사용자가 선호하는 비디오 포맷 ID (선호도 순)
DEFAULT_AUDIO_FORMATS_PREF = ["140", "251", "250", "249", "139"] # 선호하는 오디오 포맷 ID

# --- Helper Functions ---
def get_best_format_id(formats_list, preferred_ids, is_video=False):
    """주어진 포맷 리스트에서 가장 적합한 포맷 ID를 반환합니다."""
    if not formats_list: return None
    for pref_id in preferred_ids:
        for f_dict in formats_list: # formats_list는 이제 dict의 리스트
            if f_dict.get('format_id') == pref_id:
                return pref_id
    # 선호하는 ID가 없는 경우, (이미 정렬된) 목록의 첫 번째 항목을 기본값으로 사용
    if formats_list:
        return formats_list[0].get('format_id')
    return None

# --- Routes ---
@app.route('/')
def index():
    youtube_url_from_query = request.args.get('url', None)
    # URL 파라미터로 자동 정보 가져오기 기능 비활성화: auto_fetch_on_load를 항상 False로 설정
    auto_fetch_flag = False 
    return render_template('index.html', 
                           pre_load_youtube_url=youtube_url_from_query, 
                           auto_fetch_on_load=auto_fetch_flag)

@app.route('/fetch_info', methods=['POST'])
def fetch_info_route():
    data = request.get_json()
    url = data.get('url')
    if not url:
        return jsonify({"error": "URL이 제공되지 않았습니다."}), 400
    try:
        # 플레이리스트 정보 우선 가져오기 (항목 목록 등)
        ydl_opts_playlist = {
            'quiet': True, 'no_warnings': True, 'skip_download': True,
            'extract_flat': 'in_playlist', 'noplaylist': False # 플레이리스트 허용
        }
        with yt_dlp.YoutubeDL(ydl_opts_playlist) as ydl:
            playlist_info_dict = ydl.extract_info(url, download=False)

        processed_info = {
            "title": playlist_info_dict.get("title", "제목 없음"),
            "thumbnail_url": playlist_info_dict.get("thumbnail"), # 플레이리스트 썸네일 또는 첫 항목 썸네일
            "original_url": url, # Celery 작업에 전달할 원본 URL
            "video_formats": [], "audio_formats": [],
            "default_video_format_id": None, "default_audio_format_id": None,
            "playlist_entries": []
        }

        # 포맷 정보는 첫 번째 유효한 항목에서 가져옴
        first_entry_for_formats_url = None
        if 'entries' in playlist_info_dict and playlist_info_dict['entries']:
            # 플레이리스트 항목 처리
            processed_info["playlist_entries"] = [
                {"id": entry.get("id"), 
                 "url": entry.get("url") or f"https://www.youtube.com/watch?v={entry.get('id')}", 
                 "title": entry.get("title", f"항목 {idx+1}")}
                for idx, entry in enumerate(playlist_info_dict['entries']) if entry # None인 entry 필터링
            ]
            if processed_info["playlist_entries"]: # 유효한 항목이 있다면 첫 번째 항목 URL 사용
                first_entry_for_formats_url = processed_info["playlist_entries"][0]['url']
        else: # 단일 영상인 경우
            first_entry_for_formats_url = url # 원본 URL 사용

        formats_info_dict_for_ui = None # UI 포맷 표시에 사용할 정보
        if first_entry_for_formats_url:
            ydl_opts_single = {'quiet': True, 'no_warnings': True, 'skip_download': True, 'noplaylist': True}
            with yt_dlp.YoutubeDL(ydl_opts_single) as ydl_single:
                formats_info_dict_for_ui = ydl_single.extract_info(first_entry_for_formats_url, download=False)
                # 단일 영상의 경우, 가져온 정보로 썸네일 업데이트 (플레이리스트 썸네일보다 우선)
                if not ('entries' in playlist_info_dict and playlist_info_dict['entries']):
                    processed_info["thumbnail_url"] = formats_info_dict_for_ui.get("thumbnail", processed_info["thumbnail_url"])

        if formats_info_dict_for_ui and 'formats' in formats_info_dict_for_ui:
            all_formats = formats_info_dict_for_ui.get("formats", [])
            
            # 비디오 포맷 정렬 (None 값 처리, mp4 선호)
            video_formats_raw = [f for f in all_formats if f.get('vcodec') != 'none' and f.get('acodec') == 'none'] # 영상만
            if not video_formats_raw : # 영상+음성도 포함 (영상만 없을 시)
                video_formats_raw = [f for f in all_formats if f.get('vcodec') != 'none']
            
            video_formats_raw.sort(key=lambda x: (
                x.get('height') if x.get('height') is not None else -1, 
                x.get('fps') if x.get('fps') is not None else -1,
                x.get('tbr') if x.get('tbr') is not None else -1,
                1 if x.get('ext') == 'mp4' else (2 if x.get('ext') == 'webm' else 3) # mp4, webm 순으로 선호
            ), reverse=True)
            processed_info["video_formats"] = [
                {"format_id": f.get("format_id"), "format_note": f.get("format_note"), 
                 "ext": f.get("ext"), 
                 "filesize_approx": f.get("filesize_approx") or f.get("filesize"), 
                 "resolution": f.get("resolution") or (f"{f.get('width')}x{f.get('height')}" if f.get('width') and f.get('height') else "N/A")} 
                for f in video_formats_raw
            ]

            # 오디오 포맷 정렬 (None 값 처리, m4a/opus 선호)
            audio_formats_raw = [f for f in all_formats if f.get('acodec') != 'none' and f.get('vcodec') == 'none'] # 음성만
            audio_formats_raw.sort(key=lambda x: (
                x.get('abr') if x.get('abr') is not None else -1, # Audio Bitrate
                1 if x.get('ext') == 'm4a' else (2 if x.get('ext') == 'opus' else (3 if x.get('ext') == 'webm' else 4))
            ), reverse=True)
            processed_info["audio_formats"] = [
                {"format_id": f.get("format_id"), 
                 "format_note": f.get("format_note") or (f"{f.get('abr')}k" if f.get('abr') else "N/A"), 
                 "ext": f.get("ext"), 
                 "filesize_approx": f.get("filesize_approx") or f.get("filesize"), 
                 "abr": f.get('abr')} 
                for f in audio_formats_raw
            ]
            
            processed_info["default_video_format_id"] = get_best_format_id(processed_info["video_formats"], DEFAULT_VIDEO_FORMATS_PREF, is_video=True)
            processed_info["default_audio_format_id"] = get_best_format_id(processed_info["audio_formats"], DEFAULT_AUDIO_FORMATS_PREF)

        return jsonify(processed_info)

    except yt_dlp.utils.DownloadError as e:
        app.logger.error(f"yt-dlp 정보 가져오기 오류: {e}")
        return jsonify({"error": f"정보 가져오기 실패: {str(e)}"}), 500
    except TypeError as te:
        app.logger.error(f"fetch_info 중 TypeError 발생: {te}", exc_info=True) # 스택 트레이스 포함 로깅
        return jsonify({"error": f"서버 데이터 처리 오류: {str(te)}"}), 500
    except Exception as e:
        app.logger.error(f"fetch_info 중 일반 예외 발생: {e}", exc_info=True)
        return jsonify({"error": f"서버 오류 발생: {str(e)}"}), 500

@app.route('/download', methods=['POST'])
def download_route():
    data = request.get_json()
    url = data.get('url')
    video_format_id = data.get('video_format_id')
    audio_format_id = data.get('audio_format_id')
    audio_only = data.get('audio_only', False)
    playlist_items = data.get('playlist_items', []) # 클라이언트에서 선택된 항목의 ID 또는 URL 리스트
    use_thumbnail_as_cover = data.get('use_thumbnail_as_cover', False)
    title_override = data.get('title_override') # 플레이리스트 전체 제목 (항목별 제목은 Celery 작업 내에서 다시 조회)
    thumbnail_url_override = data.get('thumbnail_url_override') # 플레이리스트 대표 썸네일

    if not url:
        return jsonify({"error": "URL이 제공되지 않았습니다."}), 400

    # Celery 작업 호출
    task = download_video_task.apply_async(args=[
        None, # 첫 번째 arg는 task_id지만, Celery가 자동 생성 (bind=True 사용 시 self.request.id로 접근)
        url, video_format_id, audio_format_id, audio_only,
        playlist_items, use_thumbnail_as_cover,
        title_override, thumbnail_url_override
    ])
    
    app.logger.info(f"Celery 작업 생성됨: {task.id} (요청 URL: {url[:50]}...)")
    return jsonify({"success": True, "message": "다운로드 작업이 요청되었습니다.", "task_id": task.id})

@app.route('/progress/<task_id>', methods=['GET'])
def progress_status(task_id):
    task_result = AsyncResult(task_id, app=celery_app)
    
    # 클라이언트에 전달할 기본 응답 구조
    response_data = {
        "task_id": task_id, 
        "status_text": "상태 조회 중...", 
        "progress": 0,
        "files": [], # 최종 완료 시 전체 파일 목록 (SUCCESS 상태에서만 유효)
        "logs": [], 
        "state": task_result.state,
        "newly_completed_file": None, # 방금 완료된 단일 파일 정보 (PROGRESS 상태에서 유효)
        "all_completed_files": [] # 현재까지 완료된 모든 파일 목록 (PROGRESS 상태에서 유효)
    }

    task_info_meta = task_result.info      # PROGRESS, STARTED, FAILURE 등의 상태에서 Celery meta (dict)
    task_final_output = task_result.result # SUCCESS 상태에서 Celery 작업의 return 값

    if task_result.state == 'PENDING':
        response_data['status_text'] = '작업 대기 중...'
    elif task_result.state == 'STARTED':
        response_data['status_text'] = '작업 시작됨...'
        if isinstance(task_info_meta, dict):
            # STARTED 상태에서도 초기 meta 정보가 있을 수 있음 (예: 초기 로그)
            response_data['status'] = task_info_meta.get('status', response_data['status_text']) # 'status'는 오타, 'status_text' 일관성 유지
            response_data['status_text'] = task_info_meta.get('status', response_data['status_text'])
            response_data['progress'] = task_info_meta.get('progress', 0)
            response_data['logs'] = task_info_meta.get('logs', [])
            response_data['newly_completed_file'] = task_info_meta.get('newly_completed_file')
            response_data['all_completed_files'] = task_info_meta.get('all_completed_files', [])
    elif task_result.state == 'PROGRESS':
        if isinstance(task_info_meta, dict):
            # tasks.py에서 _update_task_meta를 통해 설정한 모든 키를 가져옴
            response_data['status_text'] = task_info_meta.get('status', '진행 중...')
            response_data['progress'] = task_info_meta.get('progress', 0)
            response_data['logs'] = task_info_meta.get('logs', [])
            response_data['newly_completed_file'] = task_info_meta.get('newly_completed_file')
            response_data['all_completed_files'] = task_info_meta.get('all_completed_files', [])
        elif isinstance(task_info_meta, (int, float)): # 단순 진행률만 올 경우 (드묾)
            response_data['progress'] = task_info_meta
            response_data['status_text'] = f"진행률: {task_info_meta}%"
    elif task_result.state == 'SUCCESS':
        if isinstance(task_final_output, dict):
            response_data['status_text'] = task_final_output.get('status', '작업 완료!')
            response_data['progress'] = task_final_output.get('progress', 100)
            response_data['files'] = task_final_output.get('files', []) # 최종 모든 완료 파일
            response_data['logs'] = task_final_output.get('logs', [])
            response_data['newly_completed_file'] = None # 최종 완료 시에는 새로 완료된 단일 파일 없음
            response_data['all_completed_files'] = task_final_output.get('files', []) # files와 동일
        else:
            response_data['status_text'] = '작업 완료 (결과 형식 불일치)'
            response_data['progress'] = 100
            app.logger.warning(f"Task {task_id} SUCCESS, but result is not dict: {task_final_output}")
    elif task_result.state == 'FAILURE':
        response_data['status_text'] = '작업 실패'
        response_data['progress'] = 100 
        error_log_detail = str(task_info_meta) if task_info_meta else "알 수 없는 오류"
        # FAILURE 시 task_info_meta에 예외 객체가 있을 수 있음
        if isinstance(task_info_meta, dict) and 'logs' in task_info_meta:
            response_data['logs'] = task_info_meta['logs']
            if not any("오류:" in log for log in response_data['logs']) and not any("Traceback" in log for log in response_data['logs']):
                 response_data['logs'].append(f"오류: {error_log_detail}")
        else:
            response_data['logs'].append(f"오류: {error_log_detail}")
        app.logger.error(f"Task {task_id} FAILED: {task_info_meta}")
    else: # REVOKED, RETRY 등 기타 상태
        response_data['status_text'] = f"작업 상태: {task_result.state}"
        if isinstance(task_info_meta, dict) and 'logs' in task_info_meta:
            response_data['logs'] = task_info_meta['logs']
        elif task_info_meta:
            response_data['logs'] = [str(task_info_meta)]

    # 파일 URL 생성 (newly_completed_file 및 all_completed_files에 대해)
    def add_url_to_file_info(file_info_obj):
        if file_info_obj and isinstance(file_info_obj, dict) and 'name' in file_info_obj and 'task_id' in file_info_obj:
            # URL 인코딩 (특히 # 같은 문자 처리)
            safe_filename = file_info_obj['name'].replace('#', '%23').replace('?', '%3F')
            file_info_obj['url'] = f"/task_files/{file_info_obj['task_id']}/{safe_filename}"
        return file_info_obj

    response_data['newly_completed_file'] = add_url_to_file_info(response_data.get('newly_completed_file'))
    
    if response_data.get('all_completed_files'):
        response_data['all_completed_files'] = [add_url_to_file_info(f) for f in response_data['all_completed_files'] if f] # None 필터링

    # SUCCESS 상태일 때 'files' 키에도 URL이 포함된 전체 완료 목록 설정
    if response_data['state'] == 'SUCCESS':
        response_data['files'] = response_data['all_completed_files']

    return jsonify(response_data)

@app.route('/task_files/<task_id>/<path:filename>')
def serve_task_file(task_id, filename):
    # filename 디코딩 (웹 브라우저는 %23 등을 자동으로 원래 문자로 변환해서 서버에 요청할 수 있음)
    # Flask는 기본적으로 URL 경로를 디코딩하지만, 이중 확인 또는 명시적 처리가 필요할 수 있음.
    # 여기서는 send_from_directory가 처리한다고 가정.

    directory = os.path.join(TEMP_DOWNLOAD_BASE_DIR, str(task_id))
    # 기본적인 경로 조작 시도 방어
    if ".." in task_id or ".." in filename:
        app.logger.warning(f"Potentially malicious path detected: task_id={task_id}, filename={filename}")
        abort(400) # 잘못된 요청
        
    if not os.path.isdir(directory): # 작업 디렉토리 존재 확인
        app.logger.error(f"Task directory not found for serving file: {directory}")
        abort(404)
    
    # send_from_directory는 directory를 기준으로 filename을 찾음.
    # filename에 슬래시가 포함된 하위 경로 접근은 기본적으로 차단됨 (보안 기능).
    # 정규화된 경로를 얻어 비교하는 것이 더 안전할 수 있음:
    # safe_path = os.path.abspath(os.path.join(directory, filename)))
    # if not safe_path.startswith(os.path.abspath(directory)): abort(403)
    
    app.logger.info(f"Attempting to serve file: {filename} from task directory: {directory}")
    try:
        return send_from_directory(directory, filename, as_attachment=True)
    except FileNotFoundError:
        app.logger.error(f"File not found by send_from_directory: {filename} in {directory}")
        abort(404)
    except Exception as e: # 기타 예외 (권한 문제 등)
        app.logger.error(f"Error serving task file {filename} from {task_id}: {e}")
        abort(500)

if __name__ == '__main__':
    # Docker 환경에서는 이 부분이 직접 실행되지 않고, docker-compose.yml의 command가 실행됩니다.
    # 로컬 개발/테스트 시: python app.py
    app.run(debug=True, host='0.0.0.0', port=5001, threaded=True, use_reloader=False)
