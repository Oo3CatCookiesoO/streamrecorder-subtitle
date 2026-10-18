// ==UserScript==
// @name         StreamRecorder 字幕生成器
// @namespace    http://tampermonkey.net/
// @version      3.2
// @description  自动为 StreamRecorder 录播生成字幕，独立管理界面（API 同步）
// @author       You
// @match        *://streamrecorder.io/*
// @grant        GM_xmlhttpRequest
// @grant        GM_addStyle
// @connect      127.0.0.1
// @connect      localhost
// @connect      streamrecorder.io
// ==/UserScript==

(function() {
    'use strict';

    // ==================== 配置 ====================
    const API_BASE = 'http://127.0.0.1:8000/api';
    const WEB_URL = 'http://127.0.0.1:8000/';
    const SR_API_BASE = 'https://streamrecorder.io/api/user';

    // ==================== 全局变量 ====================
    let currentTargetId = null;  // 当前展开的主播 ID
    let authToken = null;        // 认证 token

    // ==================== 拦截 API 请求获取 targetId 和 token ====================
    
    // 先从 localStorage 里找 token
    function findTokenInStorage() {
        if (authToken) return;
        
        // 遍历 localStorage，找看起来像 token 的值
        for (let i = 0; i < localStorage.length; i++) {
            const key = localStorage.key(i);
            const value = localStorage.getItem(key);
            if (!value) continue;
            
            // 找包含 token、auth 关键词的 key
            if (key.toLowerCase().includes('token') || key.toLowerCase().includes('auth')) {
                // 如果值看起来像 JWT token（很长的字符串，包含 .）
                if (value.length > 50 && value.includes('.')) {
                    authToken = value.startsWith('Bearer ') ? value : 'Bearer ' + value;
                    console.log('[字幕脚本] 从 localStorage 找到 token:', key);
                    return;
                }
            }
        }
        
        // 遍历 sessionStorage
        for (let i = 0; i < sessionStorage.length; i++) {
            const key = sessionStorage.key(i);
            const value = sessionStorage.getItem(key);
            if (!value) continue;
            
            if (key.toLowerCase().includes('token') || key.toLowerCase().includes('auth')) {
                if (value.length > 50 && value.includes('.')) {
                    authToken = value.startsWith('Bearer ') ? value : 'Bearer ' + value;
                    console.log('[字幕脚本] 从 sessionStorage 找到 token:', key);
                    return;
                }
            }
        }
    }
    
    // 启动时先找一次
    findTokenInStorage();
    
    const originalFetch = window.fetch;
    window.fetch = function(...args) {
        const url = args[0];
        const options = args[1] || {};
        
        // 提取 targetId
        if (typeof url === 'string' && url.includes('recordingsv2')) {
            const match = url.match(/target[Ii]d=(\d+)/);
            if (match) {
                currentTargetId = match[1];
                console.log('[字幕脚本] 检测到当前主播 ID:', currentTargetId);
            }
        }
        
        // 提取 Authorization token
        if (options.headers) {
            if (options.headers.Authorization) {
                authToken = options.headers.Authorization;
            } else if (options.headers.authorization) {
                authToken = options.headers.authorization;
            } else if (typeof options.headers.get === 'function') {
                const auth = options.headers.get('Authorization');
                if (auth) authToken = auth;
            }
        }
        
        return originalFetch.apply(this, args);
    };
    
    // 拦截 XMLHttpRequest，提取 token
    const originalXHR = window.XMLHttpRequest;
    window.XMLHttpRequest = function() {
        const xhr = new originalXHR();
        const originalSetRequestHeader = xhr.setRequestHeader;
        
        xhr.setRequestHeader = function(name, value) {
            if (name.toLowerCase() === 'authorization' && !authToken) {
                authToken = value;
                console.log('[字幕脚本] 从 XHR 提取到 token');
            }
            return originalSetRequestHeader.call(this, name, value);
        };
        
        return xhr;
    };

    // ==================== 样式 ====================
    GM_addStyle(`
        /* 浮动按钮 - 很小，不影响体验 */
        .sr-float-btn {
            position: fixed;
            right: 16px;
            bottom: 16px;
            width: 44px;
            height: 44px;
            border-radius: 50%;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            cursor: pointer;
            font-size: 18px;
            z-index: 99999;
            box-shadow: 0 4px 15px rgba(102, 126, 234, 0.4);
            transition: all 0.3s;
            display: flex;
            align-items: center;
            justify-content: center;
            opacity: 0.7;
        }
        .sr-float-btn:hover {
            opacity: 1;
            transform: scale(1.1);
            box-shadow: 0 6px 20px rgba(102, 126, 234, 0.5);
        }

        /* 提示气泡 */
        .sr-tooltip {
            position: fixed;
            right: 70px;
            bottom: 24px;
            background: rgba(0, 0, 0, 0.9);
            color: white;
            padding: 8px 14px;
            border-radius: 6px;
            font-size: 13px;
            z-index: 99998;
            opacity: 0;
            transition: opacity 0.3s;
            pointer-events: none;
            white-space: nowrap;
        }
        .sr-tooltip.show {
            opacity: 1;
        }

        /* Toast 提示 */
        .sr-toast {
            position: fixed;
            top: 20px;
            left: 50%;
            transform: translateX(-50%);
            background: rgba(0, 0, 0, 0.9);
            color: white;
            padding: 12px 24px;
            border-radius: 8px;
            z-index: 100000;
            font-size: 14px;
            animation: sr-fade-in 0.3s;
        }
        @keyframes sr-fade-in {
            from { opacity: 0; transform: translate(-50%, -10px); }
            to { opacity: 1; transform: translate(-50%, 0); }
        }
    `);

    // ==================== 浮动按钮 ====================
    function createFloatButton() {
        if (document.querySelector('.sr-float-btn')) return;

        const btn = document.createElement('button');
        btn.className = 'sr-float-btn';
        btn.innerHTML = '📝';
        btn.title = '字幕管理';
        
        // 提示气泡
        const tooltip = document.createElement('div');
        tooltip.className = 'sr-tooltip';
        tooltip.textContent = '同步录播并打开字幕管理';
        
        btn.addEventListener('mouseenter', () => {
            tooltip.classList.add('show');
        });
        btn.addEventListener('mouseleave', () => {
            tooltip.classList.remove('show');
        });
        
        btn.addEventListener('click', () => {
            showSyncMenu();
        });

        document.body.appendChild(btn);
        document.body.appendChild(tooltip);
    }

    // ==================== Toast 提示 ====================
    function showToast(message, duration = 3000) {
        const toast = document.createElement('div');
        toast.className = 'sr-toast';
        toast.textContent = message;
        document.body.appendChild(toast);
        setTimeout(() => toast.remove(), duration);
    }

    // ==================== 后端 API 请求 ====================
    async function apiRequest(path, options = {}) {
        const url = API_BASE + path;
        return new Promise((resolve, reject) => {
            GM_xmlhttpRequest({
                method: options.method || 'GET',
                url,
                headers: { 'Content-Type': 'application/json' },
                data: options.body ? JSON.stringify(options.body) : undefined,
                onload: (response) => {
                    if (response.status < 200 || response.status >= 300) {
                        reject(new Error(`HTTP ${response.status}`));
                        return;
                    }
                    try {
                        resolve(JSON.parse(response.responseText));
                    } catch (error) {
                        reject(error);
                    }
                },
                onerror: () => reject(new Error('无法连接本地服务')),
                ontimeout: () => reject(new Error('本地服务请求超时')),
            });
        });
    }

    // ==================== 从 DOM 检测当前展开的主播 ====================
    function detectCurrentStreamerFromDOM() {
        // 找到页面上第一个录播卡片
        const firstCard = document.querySelector('.recordings-recording-card');
        if (!firstCard) return null;

        // 从录播卡片往上找，找到主播头像
        let el = firstCard.parentElement;
        for (let i = 0; i < 20; i++) {
            if (!el) break;
            const avatarImg = el.querySelector('img[src*="profile_image"]');
            if (avatarImg) {
                const match = avatarImg.src.match(/profile_image\/(\d+)\//);
                if (match) {
                    console.log('[字幕脚本] 从 DOM 检测到当前主播:', match[1]);
                    return match[1];
                }
            }
            el = el.parentElement;
        }
        return null;
    }

    // ==================== 从 DOM 提取主播列表 ====================
    function extractStreamersFromDOM() {
        const streamerMap = new Map();
        
        // 方法1：通过头像图片提取（最可靠）
        const avatarImgs = document.querySelectorAll('img[src*="profile_image"]');
        console.log('[字幕脚本] 找到头像图片:', avatarImgs.length, '张');
        
        avatarImgs.forEach(img => {
            const src = img.src || '';
            const match = src.match(/profile_image\/(\d+)\//);
            if (!match) return;
            
            const targetid = match[1];
            if (streamerMap.has(targetid)) return;
            
            // 往上找主播项容器
            let el = img;
            let streamerName = '';
            let roomId = '';
            
            for (let i = 0; i < 10; i++) {
                el = el.parentElement;
                if (!el) break;
                
                // 找主播名字
                const nameEl = el.querySelector('.recordings-streamer-name-text');
                if (nameEl) {
                    const text = nameEl.textContent.trim();
                    // 格式：昵称 (数字ID)
                    const nameMatch = text.match(/(.+?)\s*\((\d+)\)/);
                    if (nameMatch) {
                        streamerName = nameMatch[1].trim();
                        roomId = nameMatch[2];
                    } else {
                        streamerName = text;
                    }
                    break;
                }
            }
            
            streamerMap.set(targetid, {
                targetid: targetid,
                name: streamerName || targetid,
                roomId: roomId,
                avatar: src,
            });
        });
        
        console.log('[字幕脚本] 提取到主播:', streamerMap.size, '个');
        return streamerMap;
    }

    // ==================== 从 API 获取录播列表（带重试） ====================
    async function fetchAllRecordingsFromAPI(targetId) {
        const allRecordings = [];
        const limit = 20;
        let offset = 0;
        let total = 0;
        const maxRetries = 3;
        let lastId = 0;
        let isIncremental = false;
        let shouldStop = false;
        
        console.log('[字幕脚本] 从 API 获取录播，主播 ID:', targetId);
        
        while (true) {
            const url = `${SR_API_BASE}/recordingsv2?targetid=${targetId}&offset=${offset}&limit=${limit}&sortby=id&sortdir=desc`;
            
            let lastError = null;
            let pageData = null;
            let success = false;
            
            // 重试机制
            for (let retry = 0; retry < maxRetries; retry++) {
                try {
                    const headers = {
                        'Content-Type': 'application/json',
                    };
                    
                    if (authToken) {
                        headers['Authorization'] = authToken;
                    }
                    
                    const response = await fetch(url, {
                        method: 'GET',
                        credentials: 'include',
                        headers: headers,
                    });
                    
                    if (!response.ok) {
                        throw new Error(`API 请求失败: ${response.status}`);
                    }
                    
                    pageData = await response.json();
                    total = pageData.count || 0;
                    success = true;
                    break;
                    
                } catch (e) {
                    lastError = e;
                    console.warn(`[字幕脚本] 请求失败 (第 ${retry + 1}/${maxRetries} 次):`, e.message);
                    
                    if (e.message.includes('502') || e.message.includes('429') || e.message.includes('503')) {
                        const waitTime = 2000 * (retry + 1);
                        console.log(`[字幕脚本] 等待 ${waitTime}ms 后重试...`);
                        await new Promise(resolve => setTimeout(resolve, waitTime));
                    } else {
                        break;
                    }
                }
            }
            
            if (!success) {
                console.error('[字幕脚本] 所有重试都失败了:', lastError);
                throw lastError;
            }
            
            // 处理这一页的数据
            if (pageData.data && pageData.data.length > 0) {
                // 第一页时，查询后端已同步的最大 id
                if (offset === 0) {
                    const firstRec = pageData.data[0];
                    const streamerName = firstRec.target_name || '';
                    const platform = firstRec.target_platform || 'douyin';
                    
                    try {
                        const lastIdResult = await apiRequest(`/recordings/last_id?streamer=${encodeURIComponent(streamerName)}&platform=${platform}`);
                        lastId = lastIdResult.last_id || 0;
                        if (lastId > 0) {
                            isIncremental = true;
                            console.log(`[字幕脚本] 增量同步模式，后端已同步到 id: ${lastId}`);
                        } else {
                            console.log('[字幕脚本] 全量同步模式（首次同步）');
                        }
                    } catch (e) {
                        console.warn('[字幕脚本] 查询 last_id 失败，将全量同步:', e);
                        isIncremental = false;
                    }
                }
                
                if (isIncremental) {
                    // 增量模式：只保留 id > lastId 的新录播
                    const newRecords = pageData.data.filter(rec => rec.id > lastId);
                    
                    if (newRecords.length > 0) {
                        allRecordings.push(...newRecords);
                        console.log(`[字幕脚本] 第 ${Math.floor(offset / limit) + 1} 页: ${newRecords.length} 条新增`);
                    }
                    
                    // 如果这一页有旧的，说明到边界了，停止
                    if (newRecords.length < pageData.data.length) {
                        console.log(`[字幕脚本] 遇到已同步的录播，停止获取。共新增 ${allRecordings.length} 场`);
                        shouldStop = true;
                    }
                } else {
                    // 全量模式：全部添加
                    allRecordings.push(...pageData.data);
                    console.log(`[字幕脚本] 获取第 ${Math.floor(offset / limit) + 1} 页: ${pageData.data.length} 条`);
                }
            } else {
                // 这一页没有数据了，停止
                shouldStop = true;
            }
            
            if (shouldStop) {
                break;
            }
            
            offset += limit;
            
            // 全量模式的额外停止条件
            if (!isIncremental) {
                if (allRecordings.length >= total || (allRecordings.length > 0 && allRecordings.length % limit !== 0)) {
                    break;
                }
            }
            
            // 防止无限循环
            if (offset > 10000) {
                console.warn('[字幕脚本] 超过最大限制，停止获取');
                break;
            }
            
            // 每页之间也稍微延迟一下
            await new Promise(resolve => setTimeout(resolve, 300));
        }
        
        console.log(`[字幕脚本] 共获取 ${allRecordings.length} 场录播`);
        return allRecordings;
    }
    // ==================== 同步所有主播的录播 ====================
    async function syncAllStreamers(streamerMap) {
        const allRecordings = [];
        const streamerArray = Array.from(streamerMap.values());

        console.log('[字幕脚本] 开始同步所有主播，共', streamerArray.length, '个');

        for (let i = 0; i < streamerArray.length; i++) {
            const streamer = streamerArray[i];
            showToast(`正在同步 ${i + 1}/${streamerArray.length}: ${streamer.name || streamer.targetid}`);

            try {
                const recordings = await fetchAllRecordingsFromAPI(streamer.targetid);
                allRecordings.push(...recordings);
                console.log(`[字幕脚本] 主播 ${streamer.name} 完成，${recordings.length} 场`);
            } catch (e) {
                console.error(`[字幕脚本] 主播 ${streamer.name} 同步失败:`, e);
            }

            // 主播之间延迟，避免请求太频繁
            await new Promise(resolve => setTimeout(resolve, 1000));
        }

        return allRecordings;
    }

    // ==================== API 数据转换 ====================
    function convertAPIRecording(rec, streamerMap) {
        const platform = rec.target_platform || rec.platform || 'unknown';
        const resolution = rec.resolutions && rec.resolutions.length > 0 
            ? rec.resolutions[0] + 'p' 
            : '';
        
        const downloadUrl = rec.sources && rec.sources.length > 0 
            ? rec.sources[0].downloadlink 
            : '';
        
        const filesize = rec.sources && rec.sources.length > 0 
            ? rec.sources[0].filesize 
            : 0;
        
        // 获取 targetid（兼容多种字段名）
        const targetId = String(
            rec.targetid || 
            rec.targetId || 
            rec.target_id || 
            rec.target || 
            rec.recordingrequest_id ||
            ''
        );
        
        // 直接构造头像 URL（格式固定）
        const avatarUrl = targetId 
            ? `https://img.streamrecorder.io/img2/profile_image/${targetId}/56x56.jpg` 
            : '';
        
        // 查找主播昵称（通过 targetid 匹配，失败则用 target_name）
        const streamerInfo = streamerMap ? streamerMap.get(targetId) : null;
        const streamerName = streamerInfo ? streamerInfo.name : '';
        const targetName = rec.target_name || rec.targetName || '';
        
        // 显示格式：有昵称就显示 "昵称 (房间号)"，没有就显示房间号
        const displayName = streamerName ? `${streamerName} (${targetName})` : targetName;
        
        // 调试：输出第一条录播的所有字段
        if (window._firstRecDebug !== true) {
            window._firstRecDebug = true;
            console.log('[字幕脚本] 第一条录播数据字段:', Object.keys(rec));
            console.log('[字幕脚本] targetId:', targetId);
            console.log('[字幕脚本] avatarUrl:', avatarUrl);
            console.log('[字幕脚本] streamerName:', streamerName);
            console.log('[字幕脚本] displayName:', displayName);
        }
        
        return {
            streamer: targetName || '未知主播',
            platform: platform,
            title: rec.streamtitle || rec.title || '无标题',
            video_url: `https://streamrecorder.io/recordings/${rec.id}`,
            duration: rec.duration || 0,
            resolution: resolution,
            thumbnail_url: rec.poster_small_270 || rec.thumbnail || '',
            recording_id: String(rec.id || ''),
            is_live: rec.status === 'running',
            record_time: rec.recorded_at || rec.record_time || '',
            download_url: downloadUrl,
            filesize: filesize || 0,
            avg_viewers: rec.avg_viewercount || rec.avg_viewers || 0,
            max_viewers: rec.max_viewercount || rec.max_viewers || 0,
            streamer_name: displayName,  // 直接存 "昵称 (房间号)" 格式
            streamer_avatar: avatarUrl,  // 直接构造的头像 URL
        };
    }

    // ==================== 同步模式选择菜单 ====================
    function showSyncMenu() {
        // 移除已存在的菜单
        const oldMenu = document.getElementById('sr-sync-menu');
        if (oldMenu) oldMenu.remove();

        const menu = document.createElement('div');
        menu.id = 'sr-sync-menu';
        menu.style.cssText = `
            position: fixed;
            bottom: 80px;
            right: 20px;
            background: #1a1a2e;
            border: 1px solid #667eea;
            border-radius: 8px;
            padding: 8px 0;
            z-index: 999999;
            box-shadow: 0 4px 12px rgba(0,0,0,0.5);
            min-width: 180px;
        `;

        const option1 = document.createElement('div');
        option1.textContent = '📋 同步全部主播';
        option1.style.cssText = 'padding: 10px 16px; cursor: pointer; color: #fff; font-size: 14px; transition: background 0.2s;';
        option1.addEventListener('mouseenter', () => option1.style.background = '#667eea33');
        option1.addEventListener('mouseleave', () => option1.style.background = 'transparent');
        option1.addEventListener('click', () => {
            menu.remove();
            syncAndOpen('all');
        });

        const option2 = document.createElement('div');
        option2.textContent = '🎯 只同步当前主播';
        option2.style.cssText = 'padding: 10px 16px; cursor: pointer; color: #fff; font-size: 14px; transition: background 0.2s;';
        option2.addEventListener('mouseenter', () => option2.style.background = '#667eea33');
        option2.addEventListener('mouseleave', () => option2.style.background = 'transparent');
        option2.addEventListener('click', () => {
            menu.remove();
            syncAndOpen('single');
        });

        menu.appendChild(option1);
        menu.appendChild(option2);
        document.body.appendChild(menu);

        // 点击其他地方关闭菜单
        setTimeout(() => {
            document.addEventListener('click', function closeMenu(e) {
                if (!menu.contains(e.target)) {
                    menu.remove();
                    document.removeEventListener('click', closeMenu);
                }
            });
        }, 10);
    }

    async function syncAndOpen(mode = 'all') {
        const btn = document.querySelector('.sr-float-btn');
        if (btn) {
            btn.style.pointerEvents = 'none';
            btn.style.opacity = '0.5';
        }

        // 点击时就打开窗口，防止被弹窗拦截
        const newWindow = window.open('', '_blank');
        if (newWindow) {
            newWindow.document.write('<div style="text-align:center;margin-top:100px;font-family:Arial;color:#666;">正在同步录播数据，请稍候...</div>');
        }

        try {
            // 先提取主播列表（头像、昵称、targetid）
            showToast('正在提取主播信息...');
            const streamerMap = extractStreamersFromDOM();
            console.log('[字幕脚本] 主播信息:', streamerMap);
            
            if (streamerMap.size === 0) {
                showToast('未找到主播列表，请确保在录播页面');
                if (newWindow) newWindow.close();
                return;
            }

            let apiData = [];
            let currentStreamerId = null;

            if (mode === 'single') {
                // 只同步当前展开的主播
                // 优先用 DOM 检测（最准确），兜底用 currentTargetId
                let targetId = detectCurrentStreamerFromDOM();
                
                if (!targetId) {
                    targetId = currentTargetId;
                }
                
                if (!targetId) {
                    showToast('未检测到当前展开的主播，请先点击展开一个主播');
                    if (newWindow) newWindow.close();
                    return;
                }

                showToast(`正在同步当前主播的录播...`);
                apiData = await fetchAllRecordingsFromAPI(targetId);
            } else {
                // 同步所有主播的录播
                showToast(`开始同步 ${streamerMap.size} 个主播的录播...`);
                apiData = await syncAllStreamers(streamerMap);
            }
            
            // 转换数据格式，匹配主播信息
            const recordings = apiData.map(rec => convertAPIRecording(rec, streamerMap));
            showToast(`共获取 ${recordings.length} 场录播，正在同步到后端...`);

            // 同步到后端
            const result = await apiRequest('/recordings/sync', {
                method: 'POST',
                body: { 
                    recordings: recordings,
                    auth_token: authToken ? authToken.replace('Bearer ', '') : '',
                    cookie: document.cookie
                },
            });

            console.log('[字幕脚本] 同步结果:', result);

            // 跳转到管理界面
            showToast(`同步完成（${result.synced} 场），正在打开管理界面...`);
            if (newWindow) {
                let url = WEB_URL;
                // 如果是单主播模式，带上主播房间号，打开后自动定位
                if (mode === 'single' && apiData.length > 0) {
                    const streamerName = apiData[0].target_name;
                    if (streamerName) {
                        url += '?streamer=' + encodeURIComponent(streamerName);
                    }
                }
                newWindow.location.href = url;
            }

        } catch (err) {
            console.error('[字幕脚本] 同步失败:', err);
            showToast('同步失败: ' + err, 5000);
            if (newWindow) newWindow.close();
        } finally {
            if (btn) {
                btn.style.pointerEvents = 'auto';
                btn.style.opacity = '0.7';
            }
        }
    }

    // ==================== 检查后端连接 ====================
    async function checkBackend() {
        try {
            const result = await apiRequest('/health');
            console.log('[字幕脚本] 后端服务已连接', result);
            return true;
        } catch (err) {
            console.warn('[字幕脚本] 后端服务未连接:', err.message);
            return false;
        }
    }

    // ==================== 初始化 ====================
    function init() {
        // 检查是否在录播页面
        if (!location.pathname.includes('recordings') && !location.pathname.includes('/user/')) {
            return;
        }

        // 创建浮动按钮
        createFloatButton();

        // 检查后端连接
        checkBackend();

        console.log('[字幕脚本 v3.2] 已加载（API 同步模式 + 重试机制 + DOM 检测主播）');
    }

    // 页面加载完成后初始化
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // 也延迟一下，确保页面完全加载
    setTimeout(init, 2000);

})();
