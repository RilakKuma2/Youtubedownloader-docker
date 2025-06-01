import os
import shutil
import yt_dlp
import requests
import io
import re
import time
from celery import Celery
from mutagen.mp4 import MP4, MP4Cover
from mutagen.id3 import ID3, APIC
from datetime import datetime
import logging
from celery.schedules import crontab

logger = logging.getLogger(__name__)

REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
celery_app = Celery('youtube_tasks', broker=REDIS_URL, backend=REDIS_URL)
celery_app.conf.update(
    task_serializer='json', result_serializer='json', accept_content=['json'],
    timezone='Asia/Seoul', enable_utc=True, result_expires=3600 * 24, # 24시간 후 결과 만료
    task_track_started=True,
)

TEMP_DOWNLOAD_BASE_DIR = "task_temp_downloads"
if not os.path.exists(TEMP_DOWNLOAD_BASE_DIR):
    os.makedirs(TEMP_DOWNLOAD_BASE_DIR, exist_ok=True)

def sanitize_filename_for_task(filename):
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', filename)
    filename = re.sub(r'\s+', ' ', filename).strip()
    return filename[:200]

def add_album_art_for_task(audio_filepath, thumbnail_url, file_ext):
    if not thumbnail_url:
        logger.info(f"Task {celery_app.current_task.request.id if celery_app.current_task else 'N/A'}: 썸네일 URL 없음 - {audio_filepath}")
        return False # 성공 여부 반환
    task_id_log = celery_app.current_task.request.id if celery_app.current_task else 'N/A'
    try:
        response = requests.get(thumbnail_url, stream=True, timeout=15)
        response.raise_for_status()
        image_data = response.content
        if file_ext.lower() in ['m4a', 'mp4']:
            audio = MP4(audio_filepath)
            img_format = MP4Cover.FORMAT_JPEG
            if thumbnail_url.lower().endswith(".png"): img_format = MP4Cover.FORMAT_PNG
            audio['covr'] = [MP4Cover(image_data, imageformat=img_format)]
            audio.save()
        elif file_ext.lower() == 'mp3':
            audio = ID3(audio_filepath)
            mime_type = 'image/jpeg'
            if thumbnail_url.lower().endswith(".png"): mime_type = 'image/png'
            audio.add(APIC(encoding=3, mime=mime_type, type=3, desc='Cover', data=image_data))
            audio.save(v2_version=3)
        else:
            logger.info(f"Task {task_id_log}: 앨범 아트 미지원 형식: {file_ext} ({audio_filepath})")
            return False
        logger.info(f"Task {task_id_log}: 앨범 아트 추가됨: {audio_filepath}")
        return True
    except requests.RequestException as e:
        logger.error(f"Task {task_id_log}: 썸네일 다운로드 실패 ({thumbnail_url}): {e}")
    except Exception as e:
        logger.error(f"Task {task_id_log}: 앨범 아트 처리 오류 ({audio_filepath}): {e}", exc_info=True)
    return False


def _update_task_meta(task_instance, status_message, progress_percent, new_log_message=None, 
                      current_item_info_prefix="", newly_completed_file_info=None, all_completed_files_list=None):
    """Celery 작업의 메타데이터(info)를 업데이트합니다."""
    current_task_id = task_instance.request.id
    
    # 현재 로그 및 완료된 파일 목록 가져오기 (주의: 경쟁조건 가능성, 더 복잡한 큐/DB 사용 가능)
    current_meta_snapshot = task_instance.AsyncResult(current_task_id).info
    existing_logs = []
    # all_completed_files는 이 함수 호출 시 인자로 받은 것을 사용 (누적)
    
    if isinstance(current_meta_snapshot, dict):
        existing_logs = current_meta_snapshot.get('logs', [])
        # all_completed_files는 외부에서 관리하고 전달받음
    
    full_status_message = f"{current_item_info_prefix}{status_message}" if current_item_info_prefix else status_message

    if new_log_message:
        log_entry = f"[{datetime.now().strftime('%H:%M:%S')}] {current_item_info_prefix}{new_log_message}"
        if not existing_logs or existing_logs[-1] != log_entry:
             existing_logs.append(log_entry)

    new_meta = {
        'status': full_status_message,
        'progress': round(min(progress_percent, 99.99), 2), # 100%는 최종 완료 시에만
        'logs': existing_logs[-50:], # 최근 50개 로그
        'newly_completed_file': newly_completed_file_info, # 방금 완료된 파일 정보
        'all_completed_files': all_completed_files_list if all_completed_files_list is not None else [] # 현재까지 완료된 모든 파일
    }
    task_instance.update_state(state='PROGRESS', meta=new_meta)


@celery_app.task(bind=True)
def download_video_task(self, celery_internal_task_id_arg_not_used,
                        base_url, video_format_id, audio_format_id, audio_only,
                        playlist_item_ids_or_urls, use_thumbnail_as_cover,
                        title_override=None, thumbnail_url_override=None):
    task_id = self.request.id
    task_specific_temp_dir = os.path.join(TEMP_DOWNLOAD_BASE_DIR, str(task_id))
    os.makedirs(task_specific_temp_dir, exist_ok=True)
    
    initial_log = f"작업 시작됨 (ID: {task_id}). 임시 폴더: {task_specific_temp_dir}"
    logger.info(f"Task {task_id}: {initial_log}")
    _update_task_meta(self, "작업 초기화 중...", 0, initial_log, all_completed_files_list=[])

    # 이 리스트는 작업 전체에서 완료된 파일들을 누적합니다.
    master_completed_files_list = [] 
    
    urls_to_process = []
    if playlist_item_ids_or_urls:
        for item_id_or_url in playlist_item_ids_or_urls:
            if isinstance(item_id_or_url, str) and item_id_or_url.startswith(('http://', 'https://')):
                urls_to_process.append(item_id_or_url)
            else:
                urls_to_process.append(f"https://www.youtube.com/watch?v={item_id_or_url}")
    else:
        urls_to_process.append(base_url)

    total_items = len(urls_to_process)
    current_item_progress_weight = 100.0 / total_items if total_items > 0 else 100.0
    
    # hook 함수가 urls_to_process와 task_id를 참조할 수 있도록 함
    # nonlocal 대신, hook 함수를 download_video_task 내부에 정의하여 클로저로 사용
    
    def celery_progress_hook(d):
        hook_url = None
        if 'info_dict' in d:
            hook_url = d['info_dict'].get('original_url') or d['info_dict'].get('webpage_url')

        current_item_idx_for_hook = 0
        if hook_url and total_items > 0:
            try: current_item_idx_for_hook = urls_to_process.index(hook_url)
            except ValueError:
                logger.warning(f"Task {task_id}: Hook URL '{hook_url}' not in list. Using last known index.")
                current_meta_fallback = self.AsyncResult(task_id).info
                if isinstance(current_meta_fallback, dict):
                    current_item_idx_for_hook = current_meta_fallback.get('current_item_index_being_processed', 0)
        
        hook_status_text = "진행 상태 알 수 없음"
        hook_item_progress_percent = 0
        current_dl_filename = os.path.basename(d.get('filename','')) if d.get('filename','').strip() and d.get('filename','').strip() != '-' else ""
        if not current_dl_filename and 'info_dict' in d and d['info_dict'].get('filename'):
             current_dl_filename = os.path.basename(d['info_dict']['filename'])

        if d['status'] == 'downloading':
            # ... (이전 hook의 다운로딩 상태 로직과 동일)
            total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate')
            downloaded_bytes = d.get('downloaded_bytes')
            if total_bytes and downloaded_bytes:
                hook_item_progress_percent = (downloaded_bytes / total_bytes) * 100
                speed_str = f"({d.get('speed', 0)/1024:.1f} KB/s)" if d.get('speed') else ""
                hook_status_text = f"{current_dl_filename} {hook_item_progress_percent:.1f}% 다운로드 중 {speed_str}"
            else:
                hook_status_text = f"{current_dl_filename} {downloaded_bytes/1024/1024:.1f}MB 다운로드 중..."
                try: hook_item_progress_percent = float(d.get('_percent_str', '0%').replace('%',''))
                except: hook_item_progress_percent = 0
        elif d['status'] == 'error':
            hook_status_text = f"{current_dl_filename} 처리 중 오류 발생"
            hook_item_progress_percent = 100 # 해당 아이템은 완료(실패)로 간주
        elif d['status'] == 'finished': # yt-dlp가 하나의 파일 처리를 '완료'했을 때
            final_filepath = d.get('info_dict', {}).get('filepath') or d.get('filename')
            final_filename_log = os.path.basename(final_filepath) if final_filepath else current_dl_filename
            hook_status_text = f"{final_filename_log} 후처리(병합 등) 중..."
            hook_item_progress_percent = 99.9 # 거의 완료 (아직 메인 루프에서 최종 처리 전)

        progress_base = current_item_idx_for_hook * current_item_progress_weight
        overall_progress_percent = progress_base + (hook_item_progress_percent / 100.0) * current_item_progress_weight
        
        item_info_for_display = f"({current_item_idx_for_hook + 1}/{total_items}) " if total_items > 0 else ""
        # hook에서는 newly_completed_file을 None으로 보내거나, yt-dlp의 finished 상태에서 파일 정보를 추출할 수 있다면 전달 가능
        # 여기서는 메인 루프에서 파일 완료를 확정하고 newly_completed_file을 설정
        _update_task_meta(self, hook_status_text, overall_progress_percent, hook_status_text, item_info_for_display, 
                          newly_completed_file_info=None, # hook에서는 아직 최종 완료 파일 아님
                          all_completed_files_list=master_completed_files_list)


    for i, current_url in enumerate(urls_to_process):
        item_info_prefix = f"({i + 1}/{total_items}) " if total_items > 0 else ""
        base_progress_for_this_item_start = i * current_item_progress_weight
        
        # 현재 처리 중인 아이템 정보와 기본 진행률 업데이트
        status_msg_item_processing = f"처리 시작: {os.path.basename(current_url)}"
        self.update_state(state='PROGRESS', meta={
            'status': f"{item_info_prefix}{status_msg_item_processing}",
            'progress': round(base_progress_for_this_item_start, 2),
            'logs': self.AsyncResult(task_id).info.get('logs', []), # 이전 로그 유지
            'newly_completed_file': None, # 새 아이템 시작 시에는 없음
            'all_completed_files': master_completed_files_list,
            'current_item_index_being_processed': i # hook에서 폴백으로 사용
        })
        
        log_msg_item_start_full = f"처리 시작: {current_url}"
        logger.info(f"Task {task_id}: {item_info_prefix}{log_msg_item_start_full}")
        _update_task_meta(self, f"정보 가져오는 중...", base_progress_for_this_item_start, log_msg_item_start_full, 
                          item_info_prefix, newly_completed_file_info=None, all_completed_files_list=master_completed_files_list)

        current_item_title_for_file = "제목_없음"
        current_item_thumbnail_url_for_art = None
        try:
            with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True, 'skip_download': True, 'noplaylist': True}) as ydl_item_info:
                item_info_dict = ydl_item_info.extract_info(current_url, download=False)
                current_item_title_for_file = item_info_dict.get("title", f"항목_{i+1}")
                current_item_thumbnail_url_for_art = item_info_dict.get("thumbnail")
        except Exception as e:
            err_msg = f"항목 정보 가져오기 실패: {e}"
            logger.warning(f"Task {task_id}: {item_info_prefix}{err_msg} ({current_url})")
            _update_task_meta(self, "정보 가져오기 실패", base_progress_for_this_item_start, err_msg, item_info_prefix, 
                              newly_completed_file_info=None, all_completed_files_list=master_completed_files_list)
            if title_override and total_items > 1: current_item_title_for_file = f"{sanitize_filename_for_task(title_override)}_항목_{i+1}"
            elif title_override: current_item_title_for_file = sanitize_filename_for_task(title_override)

        sanitized_title = sanitize_filename_for_task(current_item_title_for_file)
        output_template_pattern = os.path.join(task_specific_temp_dir, f"{sanitized_title}.%(ext)s")

        ydl_opts = {
            'quiet': False, 'no_warnings': True, 'outtmpl': output_template_pattern,
            'progress_hooks': [celery_progress_hook], 'noplaylist': True, 'ignoreerrors': True,
        }
        if audio_only:
            ydl_opts['format'] = audio_format_id or 'bestaudio[ext=m4a]/bestaudio[ext=mp3]/bestaudio/best'
            ydl_opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'm4a', 'preferredquality': '192'}]
            if audio_format_id and 'mp3' in audio_format_id.lower(): ydl_opts['postprocessors'][0]['preferredcodec'] = 'mp3'
        else:
            selected_format = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best"
            if video_format_id and audio_format_id: selected_format = f"{video_format_id}+{audio_format_id}/{selected_format}"
            elif video_format_id: selected_format = f"{video_format_id}/{selected_format}"
            ydl_opts['format'] = selected_format
            ydl_opts['merge_output_format'] = 'mp4'
        
        actual_downloaded_filepath = None
        newly_completed_file_this_iteration = None # 이번 반복에서 완료된 파일 정보
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                result_info = ydl.extract_info(current_url, download=True) 
                if result_info:
                    actual_downloaded_filepath = result_info.get('filepath') or result_info.get('_filename')
                    if not actual_downloaded_filepath and result_info.get('requested_downloads'):
                        dl_info_list = result_info.get('requested_downloads', [])
                        if dl_info_list: actual_downloaded_filepath = dl_info_list[0].get('filepath') or dl_info_list[0].get('_filename')
            
            if actual_downloaded_filepath and os.path.exists(actual_downloaded_filepath):
                actual_filename = os.path.basename(actual_downloaded_filepath)
                newly_completed_file_this_iteration = {"name": actual_filename, "task_id": str(task_id)}
                master_completed_files_list.append(newly_completed_file_this_iteration) # 전체 완료 목록에 추가

                log_dl_complete = f"항목 완료: {actual_filename}"
                logger.info(f"Task {task_id}: {item_info_prefix}{log_dl_complete}")
                
                # 현재 아이템 완료 시, 진행률 업데이트하고 newly_completed_file 정보 전달
                current_item_final_progress = base_progress_for_this_item_start + current_item_progress_weight
                _update_task_meta(self, f"완료: {actual_filename}", current_item_final_progress, log_dl_complete, 
                                  item_info_prefix, newly_completed_file_info=newly_completed_file_this_iteration, 
                                  all_completed_files_list=master_completed_files_list)

                if audio_only and use_thumbnail_as_cover and current_item_thumbnail_url_for_art:
                    log_album_art_start = f"앨범 커버 추가 시도: {actual_filename}"
                    _update_task_meta(self, f"앨범 커버 추가 중...", current_item_final_progress, log_album_art_start, 
                                      item_info_prefix, newly_completed_file_info=None, # 앨범아트 추가중에는 새 파일 완료 아님
                                      all_completed_files_list=master_completed_files_list)
                    art_added = add_album_art_for_task(actual_downloaded_filepath, current_item_thumbnail_url_for_art, os.path.splitext(actual_filename)[1].lstrip('.'))
                    art_log_msg = f"앨범 커버 추가됨: {actual_filename}" if art_added else f"앨범 커버 추가 실패 또는 미지원 ({actual_filename})"
                    _update_task_meta(self, f"앨범 커버 처리 완료", current_item_final_progress, art_log_msg, 
                                      item_info_prefix, newly_completed_file_info=None,
                                      all_completed_files_list=master_completed_files_list)
            else:
                log_file_not_found = f"오류: 파일 경로를 찾을 수 없습니다."
                logger.error(f"Task {task_id}: {item_info_prefix}{log_file_not_found} (URL: {current_url}). Result: {result_info}")
                _update_task_meta(self, "파일 경로 오류", base_progress_for_this_item_start, log_file_not_found, 
                                  item_info_prefix, newly_completed_file_info=None, all_completed_files_list=master_completed_files_list)

        except yt_dlp.utils.DownloadError as de:
            err_msg_dl = f"다운로드 오류: {str(de)}"
            logger.error(f"Task {task_id}: {item_info_prefix}yt-dlp DownloadError for {current_url}: {de}")
            _update_task_meta(self, "다운로드 오류", base_progress_for_this_item_start, err_msg_dl, 
                              item_info_prefix, newly_completed_file_info=None, all_completed_files_list=master_completed_files_list)
        except Exception as e:
            err_msg_general = f"일반 오류: {str(e)}"
            logger.error(f"Task {task_id}: {item_info_prefix}General error for {current_url}: {e}", exc_info=True)
            _update_task_meta(self, "일반 오류", base_progress_for_this_item_start, err_msg_general, 
                              item_info_prefix, newly_completed_file_info=None, all_completed_files_list=master_completed_files_list)
        
        # 각 아이템 루프 후, newly_completed_file을 None으로 초기화하여 다음 폴링 시 중복 처리 방지
        # (하지만 _update_task_meta 호출 시마다 newly_completed_file을 명시적으로 전달하므로 이 방식이 더 안전)
        if newly_completed_file_this_iteration:
             # 바로 다음 업데이트에서 newly_completed_file을 None으로 설정하여 클라이언트가 한 번만 받도록 함
             # (또는 클라이언트가 처리했음을 서버에 알리는 방식도 가능)
             # 여기서는 간단히 다음 _update_task_meta 호출 시 None으로 전달.
             # 하지만 루프 다음 반복 시작 시 update_state에서 None으로 설정됨.
             pass


    final_status_msg = "모든 다운로드 완료!"
    if not master_completed_files_list and total_items > 0: # master_completed_files_list 사용
        final_status_msg = "다운로드된 파일 없음 (오류 발생 가능성)"
    elif len(master_completed_files_list) < total_items and total_items > 0:
         final_status_msg = f"일부 항목 다운로드 완료 ({len(master_completed_files_list)}/{total_items})"
    
    logger.info(f"Task {task_id}: 완료. 최종 상태: {final_status_msg}")
    
    current_meta_final = self.AsyncResult(task_id).info
    final_logs = current_meta_final.get('logs', []) if isinstance(current_meta_final, dict) else []
    final_log_entry = f"[{datetime.now().strftime('%H:%M:%S')}] {final_status_msg}"
    if not final_logs or final_logs[-1] != final_log_entry : final_logs.append(final_log_entry)

    # 최종 SUCCESS 상태에서는 newly_completed_file은 의미 없음 (이미 개별적으로 전달됨)
    return {
        'status': final_status_msg, 'progress': 100,
        'files': master_completed_files_list, # 최종적으로 모든 완료된 파일 목록
        'logs': final_logs[-50:],
        'newly_completed_file': None # 최종 상태에서는 null
    }

@celery_app.task
def cleanup_old_task_folders():
    logger.info("오래된 작업 임시 폴더 정리 시작...")
    now_ts = time.time()
    max_age_seconds = 6 * 60 * 60
    if not os.path.exists(TEMP_DOWNLOAD_BASE_DIR):
        logger.info(f"{TEMP_DOWNLOAD_BASE_DIR} 없음.")
        return 0
    deleted_count = 0
    for task_folder_name in os.listdir(TEMP_DOWNLOAD_BASE_DIR):
        task_folder_path = os.path.join(TEMP_DOWNLOAD_BASE_DIR, task_folder_name)
        if os.path.isdir(task_folder_path):
            try:
                if (now_ts - os.path.getmtime(task_folder_path)) > max_age_seconds:
                    shutil.rmtree(task_folder_path)
                    logger.info(f"오래된 임시 폴더 삭제: {task_folder_path}")
                    deleted_count += 1
            except FileNotFoundError: logger.warning(f"임시 폴더 정리 중 찾을 수 없음: {task_folder_path}")
            except Exception as e: logger.error(f"임시 폴더 삭제 실패 {task_folder_path}: {e}")
    logger.info(f"오래된 작업 임시 폴더 정리 완료. {deleted_count}개 폴더 삭제.")
    return deleted_count

celery_app.conf.beat_schedule = {
    'cleanup-temp-folders-every-6-hours': {
        'task': 'tasks.cleanup_old_task_folders',
        'schedule': crontab(minute=0, hour='*/6'),
    },
}
