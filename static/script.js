document.addEventListener('DOMContentLoaded', () => {
    // ... (이전 const 선언들, URL 자동 입력 로직, appendToLog, resetUI 등은 이전 답변과 동일하게 유지) ...
    const urlEntry = document.getElementById('urlEntry');
    const fetchButton = document.getElementById('fetchButton');
    const downloadButton = document.getElementById('downloadButton');
    const audioOnlyButton = document.getElementById('audioOnlyButton');
    const resetButton = document.getElementById('resetButton');

    const infoSection = document.getElementById('infoSection');
    const thumbnailImage = document.getElementById('thumbnailImage');
    const titleText = document.getElementById('titleText');
    const videoFormatSelect = document.getElementById('videoFormatSelect');
    const audioFormatSelect = document.getElementById('audioFormatSelect');
    
    const useThumbnailCheckbox = document.getElementById('useThumbnailAsCover');
    const autoDownloadCheckbox = document.getElementById('autoDownloadCheckbox');

    const playlistSection = document.getElementById('playlistSection');
    const playlistItemsContainer = document.getElementById('playlistItemsContainer');
    const selectAllButton = document.getElementById('selectAllPlaylist');
    const deselectAllButton = document.getElementById('deselectAllPlaylist');
    
    const progressSection = document.getElementById('progressSection');
    const progressBarFill = document.getElementById('progressBarFill');
    const statusMessage = document.getElementById('statusMessage');
    const logMessages = document.getElementById('logMessages');
    const downloadedFilesList = document.getElementById('downloadedFilesList');

    let currentVideoInfo = null;
    let currentProgressInterval = null;
    let processedFileNames = new Set(); // 이미 처리(링크 생성/자동 다운로드)한 파일명 추적

    if (typeof preLoadYouTubeUrl !== 'undefined' && preLoadYouTubeUrl) {
        urlEntry.value = preLoadYouTubeUrl;
        appendToLog(`URL: ${preLoadYouTubeUrl} 로드됨. '정보 가져오기'를 클릭하세요.`);
    } else {
        resetUI();
    }

    function appendToLog(messageOrArray, type = 'info') {
        const messages = Array.isArray(messageOrArray) ? messageOrArray : [messageOrArray];
        const logContainer = document.getElementById('logMessagesContainer');
        messages.forEach(message => {
            const logEntry = document.createElement('div');
            logEntry.textContent = message;
            if (type === 'error') logEntry.style.color = 'red';
            else if (type === 'success') logEntry.style.color = 'green';
            logMessages.appendChild(logEntry);
        });
        if (logContainer && logMessages.children.length > 0) {
            logContainer.scrollTop = logContainer.scrollHeight;
        }
    }

    function resetUI() {
        urlEntry.value = '';
        infoSection.classList.add('hidden'); thumbnailImage.src = '#'; titleText.textContent = '제목';
        videoFormatSelect.innerHTML = '<option value="">-- 선택 --</option>'; audioFormatSelect.innerHTML = '<option value="">-- 선택 --</option>';
        playlistSection.classList.add('hidden'); playlistItemsContainer.innerHTML = '';
        progressSection.classList.add('hidden'); progressBarFill.style.width = '0%'; statusMessage.textContent = '';
        logMessages.innerHTML = ''; downloadedFilesList.innerHTML = '';
        downloadButton.disabled = true; audioOnlyButton.disabled = true; fetchButton.disabled = false; resetButton.disabled = false;
        currentVideoInfo = null;
        if (currentProgressInterval) { clearInterval(currentProgressInterval); currentProgressInterval = null; }
        useThumbnailCheckbox.checked = false; autoDownloadCheckbox.checked = true;
        processedFileNames.clear(); // 처리된 파일 목록 초기화
    }

    resetButton.addEventListener('click', resetUI);

    fetchButton.addEventListener('click', async () => {
        // ... (fetchButton 로직은 이전과 동일)
        const url = urlEntry.value.trim();
        if (!url) { appendToLog('URL을 입력해주세요.', 'error'); return; }
        infoSection.classList.add('hidden'); thumbnailImage.src = '#'; titleText.textContent = '정보 로딩 중...';
        videoFormatSelect.innerHTML = '<option value="">-- 로딩 중 --</option>'; audioFormatSelect.innerHTML = '<option value="">-- 로딩 중 --</option>';
        playlistSection.classList.add('hidden'); playlistItemsContainer.innerHTML = '';
        progressSection.classList.add('hidden'); progressBarFill.style.width = '0%'; statusMessage.textContent = '정보를 가져오는 중...';
        logMessages.innerHTML = ''; downloadedFilesList.innerHTML = ''; // 새 fetch 시 이전 다운로드 링크 제거
        downloadButton.disabled = true; audioOnlyButton.disabled = true; fetchButton.disabled = true;
        processedFileNames.clear(); // 새 fetch 시 처리된 파일 목록 초기화

        appendToLog(`정보 가져오기 시작: ${url}`);
        try {
            const response = await fetch('/fetch_info', {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url: url })
            });
            if (!response.ok) { const errorData = await response.json(); throw new Error(errorData.error || `HTTP error ${response.status}`); }
            currentVideoInfo = await response.json();
            updateUIWithInfo(currentVideoInfo);
            appendToLog('정보 가져오기 완료.'); statusMessage.textContent = '정보 가져오기 완료.';
            downloadButton.disabled = false; audioOnlyButton.disabled = false;
        } catch (error) {
            console.error('Fetch error:', error); appendToLog(`정보 가져오기 오류: ${error.message}`, 'error');
            statusMessage.textContent = `정보 가져오기 오류: ${error.message}`; titleText.textContent = '오류 발생';
        } finally {
            fetchButton.disabled = false;
        }
    });

    // ... (updateUIWithInfo, populateFormatSelect, selectAll/DeselectAll 이전과 동일) ...
    function updateUIWithInfo(data) {
        infoSection.classList.remove('hidden');
        thumbnailImage.src = data.thumbnail_url || '#';
        titleText.textContent = data.title || '제목 없음';
        populateFormatSelect(videoFormatSelect, data.video_formats, data.default_video_format_id);
        populateFormatSelect(audioFormatSelect, data.audio_formats, data.default_audio_format_id);
        if (data.playlist_entries && data.playlist_entries.length > 0) {
            playlistSection.classList.remove('hidden');
            playlistItemsContainer.innerHTML = '';
            data.playlist_entries.forEach((entry, index) => {
                const div = document.createElement('div'); div.className = 'playlist-item';
                const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.id = `playlist_item_${index}`;
                checkbox.value = entry.id || entry.url; checkbox.checked = true;
                const label = document.createElement('label'); label.htmlFor = `playlist_item_${index}`; label.textContent = entry.title || `항목 ${index + 1}`;
                div.appendChild(checkbox); div.appendChild(label); playlistItemsContainer.appendChild(div);
            });
        } else {
            playlistSection.classList.add('hidden');
        }
    }
    function populateFormatSelect(selectElement, formats, defaultFormatId) {
        selectElement.innerHTML = '';
        if (!formats || formats.length === 0) {
            const opt = document.createElement('option'); opt.value = ""; opt.textContent = '사용 가능한 포맷 없음'; selectElement.appendChild(opt); return;
        }
        formats.forEach(f => {
            const opt = document.createElement('option'); opt.value = f.format_id;
            let label = `${f.format_id} - ${f.format_note || (f.resolution || (f.abr ? f.abr + 'k' : 'N/A'))} - ${f.ext}`;
            if (f.filesize_approx) label += ` (~${(f.filesize_approx / 1024 / 1024).toFixed(2)} MB)`;
            opt.textContent = label; selectElement.appendChild(opt);
        });
        if (defaultFormatId && selectElement.querySelector(`option[value="${defaultFormatId}"]`)) selectElement.value = defaultFormatId;
        else if (formats.length > 0) selectElement.value = formats[0].format_id;
    }
    selectAllButton.addEventListener('click', () => playlistItemsContainer.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = true));
    deselectAllButton.addEventListener('click', () => playlistItemsContainer.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = false));


    function triggerFileDownload(fileUrl, fileName) {
        const link = document.createElement('a');
        link.href = fileUrl;
        link.download = fileName;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        appendToLog(`"${fileName}" 자동 다운로드 시도됨.`);
    }

    // 개별 파일에 대한 링크 생성 및 자동 다운로드 처리 함수
    function processSingleCompletedFile(fileInfo) {
        if (!fileInfo || !fileInfo.name || processedFileNames.has(fileInfo.name)) {
            return; // 파일 정보가 없거나 이미 처리된 파일이면 스킵
        }

        // 수동 다운로드 링크 추가
        if (downloadedFilesList.innerHTML.includes("<h3>") === false) { // 제목 한 번만 추가
             downloadedFilesList.innerHTML = '<h3>다운로드된 파일 (수동):</h3>';
        }
        const p = document.createElement('p');
        const link = document.createElement('a');
        link.href = fileInfo.url; 
        link.textContent = fileInfo.name;
        link.className = 'button download-link';
        link.target = '_blank'; link.download = fileInfo.name;
        p.appendChild(link); 
        downloadedFilesList.appendChild(p);

        // 자동 다운로드 처리
        if (autoDownloadCheckbox.checked) {
            appendToLog(`"${fileInfo.name}" 자동 다운로드를 시작합니다...`);
            // 여러 파일이 동시에 완료될 경우를 대비해 약간의 지연은 유지할 수 있으나,
            // newly_completed_file은 하나씩 오므로 바로 실행해도 무방할 수 있음.
            // 여기서는 즉시 실행.
            triggerFileDownload(fileInfo.url, fileInfo.name);
        }
        processedFileNames.add(fileInfo.name); // 처리된 파일로 기록
    }


    async function startDownload(audioOnly = false) {
        // ... (startDownload 함수 시작 부분, payload 생성 등은 이전과 동일)
        if (!currentVideoInfo && !urlEntry.value.trim()) { appendToLog('먼저 URL을 입력하고 정보를 가져오세요.', 'error'); return; }
        const urlToDownload = currentVideoInfo ? currentVideoInfo.original_url : urlEntry.value.trim();
        const selectedVideoFormat = videoFormatSelect.value; const selectedAudioFormat = audioFormatSelect.value;
        if (!audioOnly && !selectedVideoFormat && currentVideoInfo && currentVideoInfo.video_formats && currentVideoInfo.video_formats.length > 0) { appendToLog('영상 포맷을 선택해주세요.', 'error'); return; }
        if (!selectedAudioFormat && currentVideoInfo && currentVideoInfo.audio_formats && currentVideoInfo.audio_formats.length > 0) { appendToLog('음성 포맷을 선택해주세요.', 'error'); return; }
        let playlistItemsToSubmit = [];
        if (currentVideoInfo && currentVideoInfo.playlist_entries && currentVideoInfo.playlist_entries.length > 0) {
            playlistItemsContainer.querySelectorAll('input[type="checkbox"]:checked').forEach(cb => playlistItemsToSubmit.push(cb.value));
            if (playlistItemsToSubmit.length === 0) { appendToLog('다운로드할 플레이리스트 항목을 선택해주세요.', 'error'); return; }
        }
        downloadButton.disabled = true; audioOnlyButton.disabled = true; fetchButton.disabled = true; resetButton.disabled = true;
        statusMessage.textContent = audioOnly ? '음성 다운로드 요청 중...' : '다운로드 요청 중...';
        progressBarFill.style.width = '0%'; progressSection.classList.remove('hidden');
        logMessages.innerHTML = ''; downloadedFilesList.innerHTML = ''; // 다운로드 시작 시 이전 목록 초기화
        processedFileNames.clear(); // 새 다운로드 시작 시 처리된 파일 목록 초기화
        appendToLog(`${audioOnly ? '음성' : '영상/음성'} 다운로드 시작 요청.`);
        const payload = {
            url: urlToDownload, video_format_id: audioOnly ? null : selectedVideoFormat, audio_format_id: selectedAudioFormat,
            audio_only: audioOnly, playlist_items: playlistItemsToSubmit, use_thumbnail_as_cover: useThumbnailCheckbox.checked,
            title_override: currentVideoInfo ? currentVideoInfo.title : null, 
            thumbnail_url_override: currentVideoInfo ? currentVideoInfo.thumbnail_url : null,
        };
        
        if (currentProgressInterval) clearInterval(currentProgressInterval);

        try {
            const response = await fetch('/download', {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
            });
            if (!response.ok) { const errorData = await response.json(); throw new Error(errorData.error || `HTTP error ${response.status}`); }
            
            const initialAck = await response.json();
            if (!initialAck.success || !initialAck.task_id) { throw new Error(initialAck.error || "다운로드 작업 생성 실패"); }
            
            const celeryTaskId = initialAck.task_id;
            appendToLog(`서버에서 다운로드 작업 시작됨 (Task ID: ${celeryTaskId})`);
            statusMessage.textContent = "작업 대기 중...";

            currentProgressInterval = setInterval(async () => {
                try {
                    const progressResponse = await fetch(`/progress/${celeryTaskId}`);
                    if (!progressResponse.ok) {
                        // ... (404 등 에러 처리)
                        console.warn(`Progress polling for ${celeryTaskId} failed: ${progressResponse.status}`);
                        if (progressResponse.status === 404) {
                            clearInterval(currentProgressInterval); currentProgressInterval = null;
                            statusMessage.textContent = "작업 정보 없음 (정리됨).";
                            downloadButton.disabled = false; audioOnlyButton.disabled = false; fetchButton.disabled = false; resetButton.disabled = false;
                        }
                        return; 
                    }

                    const progressData = await progressResponse.json();
                    statusMessage.textContent = progressData.status_text || "상태 업데이트 중...";
                    progressBarFill.style.width = `${progressData.progress || 0}%`;
                    
                    logMessages.innerHTML = ''; 
                    if (progressData.logs && progressData.logs.length > 0) {
                        appendToLog(progressData.logs);
                    }

                    // --- 개별 파일 완료 처리 ---
                    if (progressData.newly_completed_file) {
                        processSingleCompletedFile(progressData.newly_completed_file);
                    }
                    // 만약 newly_completed_file이 아니라 all_completed_files로 온다면,
                    // all_completed_files를 순회하며 processedFileNames에 없는 파일들을 처리.
                    if (progressData.all_completed_files && progressData.all_completed_files.length > 0) {
                        progressData.all_completed_files.forEach(fileInfo => {
                            if (fileInfo && fileInfo.name && !processedFileNames.has(fileInfo.name)) {
                                processSingleCompletedFile(fileInfo); // 새로 발견된 완료 파일 처리
                            }
                        });
                    }


                    // --- 전체 작업 완료 처리 ---
                    if (['SUCCESS', 'FAILURE', 'REVOKED'].includes(progressData.state) || progressData.progress >= 100) {
                        clearInterval(currentProgressInterval); currentProgressInterval = null;
                        statusMessage.textContent = progressData.status_text || (progressData.state === 'SUCCESS' ? "완료" : "종료됨");
                        
                        // 최종 SUCCESS 시, 혹시 누락된 파일이 있다면 all_completed_files 기준으로 한 번 더 처리
                        if (progressData.state === 'SUCCESS' && progressData.all_completed_files && progressData.all_completed_files.length > 0) {
                            progressData.all_completed_files.forEach(fileInfo => {
                                if (fileInfo && fileInfo.name && !processedFileNames.has(fileInfo.name)) {
                                    processSingleCompletedFile(fileInfo);
                                }
                            });
                        }
                        
                        if (progressData.state === 'SUCCESS' && (!progressData.all_completed_files || progressData.all_completed_files.length === 0) && downloadedFilesList.innerHTML === '') {
                             appendToLog("완료되었지만 다운로드할 파일이 없습니다.", "info");
                        } else if (progressData.state === 'FAILURE') {
                             appendToLog(`작업 실패: ${progressData.status_text || '알 수 없는 오류'}`, 'error');
                        }
                        
                        downloadButton.disabled = false; audioOnlyButton.disabled = false; fetchButton.disabled = false; resetButton.disabled = false;
                    }
                } catch (pollError) {
                    console.warn('Progress polling error:', pollError);
                }
            }, 2000);

        } catch (error) {
            // ... (기존 에러 처리)
            if(currentProgressInterval) clearInterval(currentProgressInterval); currentProgressInterval = null;
            console.error('Download error:', error);
            statusMessage.textContent = `다운로드 요청 오류: ${error.message}`;
            appendToLog(`다운로드 요청 오류: ${error.message}`, 'error');
            downloadButton.disabled = false; audioOnlyButton.disabled = false; fetchButton.disabled = false; resetButton.disabled = false;
        }
    }

    downloadButton.addEventListener('click', () => startDownload(false));
    audioOnlyButton.addEventListener('click', () => startDownload(true));

    // resetUI(); // 페이지 로드 시 자동 실행 로직에서 조건부 호출로 변경됨
});
