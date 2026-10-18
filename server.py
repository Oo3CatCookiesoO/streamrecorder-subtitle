#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
StreamRecorder 字幕生成后端服务
- FunASR + Paraformer 语音识别
- 临时下载录播后使用 ffmpeg 提取音频
- SQLite 字幕存储 + 搜索
"""

import os
import io
import re
import json
import time
import uuid
import asyncio
import subprocess
import sqlite3
import ipaddress
import shutil
import socket
from urllib.parse import urljoin, urlsplit
from typing import Optional, List, Dict, Any
from pathlib import Path
from contextlib import asynccontextmanager

import numpy as np
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
import requests
from pydantic import BaseModel

# ==================== 配置 ====================

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "subtitles.db"
MODELS_DIR = BASE_DIR / "models"

# 设置 modelscope 缓存目录到项目文件夹，避免下载到 C 盘
os.environ["MODELSCOPE_CACHE"] = str(MODELS_DIR)

# 模型名称映射
MODEL_NAMES = {
    "paraformer-zh": "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
    "paraformer-large": "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
    "qwen3-asr-0.6b": "Qwen/Qwen3-ASR-0.6B",
}

# ForcedAligner 模型（用于 Qwen3-ASR 的时间戳对齐）
ALIGNER_MODEL_NAME = "Qwen/Qwen3-ForcedAligner-0.6B"

# 音频参数
SAMPLE_RATE = 16000
CHANNELS = 1
SEGMENT_DURATION = 30  # 每段 30 秒

# StreamRecorder 认证 token（持久化存储）
import json
import os

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

def load_config():
    """加载配置文件"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_config(config):
    """保存配置文件"""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[保存配置失败] {e}")

config = load_config()
SR_AUTH_TOKEN = config.get("sr_auth_token", "")

# 从 cookie.txt 读取 cookie（优先用这个，避免 JSON 转义问题）
COOKIE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookie.txt")
if os.path.exists(COOKIE_FILE):
    try:
        with open(COOKIE_FILE, "r", encoding="utf-8") as f:
            SR_COOKIE = f.read().strip()
    except:
        SR_COOKIE = config.get("sr_cookie", "")
else:
    SR_COOKIE = config.get("sr_cookie", "")

print(f"[启动] SR_AUTH_TOKEN: {'有' if SR_AUTH_TOKEN else '无'}")
print(f"[启动] SR_COOKIE: {'有' if SR_COOKIE else '无'}")

# 全局变量
models = {}  # 模型缓存
punc_model = None  # 标点模型缓存
aligner_model = None  # ForcedAligner 时间戳对齐模型缓存（用于 Qwen3-ASR）
tasks = {}   # 任务状态

# ==================== 任务队列系统 ====================
import threading
task_queue = []  # 等待执行的任务ID列表，按添加顺序
queue_lock = threading.Lock()
pause_event = threading.Event()
pause_event.set()  # 默认非暂停状态（set=非暂停，clear=暂停）
queue_worker_thread = None
worker_running = True


# ==================== 数据库 ====================

def init_db():
    """初始化数据库"""
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    
    # 字幕表
    c.execute("""
        CREATE TABLE IF NOT EXISTS subtitles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            streamer TEXT NOT NULL,
            platform TEXT NOT NULL,
            title TEXT NOT NULL,
            video_url TEXT NOT NULL,
            model TEXT NOT NULL,
            duration REAL DEFAULT 0,
            recording_id TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(recording_id)
        )
    """)
    
    # 兼容旧数据库，添加 recording_id 字段
    c.execute("PRAGMA table_info(subtitles)")
    columns = [row[1] for row in c.fetchall()]
    if 'recording_id' not in columns:
        c.execute("ALTER TABLE subtitles ADD COLUMN recording_id TEXT DEFAULT ''")
        print("[数据库] 已添加 recording_id 字段")
    
    # 字幕片段表
    c.execute("""
        CREATE TABLE IF NOT EXISTS subtitle_segments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subtitle_id INTEGER NOT NULL,
            start_time REAL NOT NULL,
            end_time REAL NOT NULL,
            text TEXT NOT NULL,
            FOREIGN KEY (subtitle_id) REFERENCES subtitles(id)
        )
    """)
    
    # 全文搜索索引
    c.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS subtitle_fts USING fts5(
            text,
            content='subtitle_segments',
            content_rowid='id'
        )
    """)
    
    # 录播列表表
    c.execute("""
        CREATE TABLE IF NOT EXISTS recordings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            streamer TEXT NOT NULL,
            platform TEXT NOT NULL,
            title TEXT NOT NULL,
            video_url TEXT,
            duration REAL DEFAULT 0,
            resolution TEXT,
            thumbnail_url TEXT,
            recording_id TEXT,
            is_live INTEGER DEFAULT 0,
            record_time TIMESTAMP,
            download_url TEXT,
            filesize INTEGER DEFAULT 0,
            avg_viewers INTEGER DEFAULT 0,
            max_viewers INTEGER DEFAULT 0,
            streamer_name TEXT,
            streamer_avatar TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(platform, recording_id)
        )
    """)
    
    # 兼容旧数据库：添加新字段（如果不存在）
    try:
        c.execute("ALTER TABLE recordings ADD COLUMN record_time TIMESTAMP")
    except:
        pass
    try:
        c.execute("ALTER TABLE recordings ADD COLUMN download_url TEXT")
    except:
        pass
    try:
        c.execute("ALTER TABLE recordings ADD COLUMN filesize INTEGER DEFAULT 0")
    except:
        pass
    try:
        c.execute("ALTER TABLE recordings ADD COLUMN avg_viewers INTEGER DEFAULT 0")
    except:
        pass
    try:
        c.execute("ALTER TABLE recordings ADD COLUMN max_viewers INTEGER DEFAULT 0")
    except:
        pass
    try:
        c.execute("ALTER TABLE recordings ADD COLUMN streamer_name TEXT")
    except:
        pass
    try:
        c.execute("ALTER TABLE recordings ADD COLUMN streamer_avatar TEXT")
    except:
        pass
    try:
        c.execute("ALTER TABLE recordings ADD COLUMN is_watched INTEGER DEFAULT 0")
    except:
        pass
    
    # 触发器：插入时同步到 FTS
    c.execute("""
        CREATE TRIGGER IF NOT EXISTS subtitle_segments_ai AFTER INSERT ON subtitle_segments
        BEGIN
            INSERT INTO subtitle_fts(rowid, text) VALUES (new.id, new.text);
        END
    """)
    
    # 触发器：删除时同步到 FTS
    c.execute("""
        CREATE TRIGGER IF NOT EXISTS subtitle_segments_ad AFTER DELETE ON subtitle_segments
        BEGIN
            INSERT INTO subtitle_fts(subtitle_fts, rowid, text) VALUES('delete', old.id, old.text);
        END
    """)
    
    conn.commit()
    conn.close()


def get_db():
    """获取数据库连接"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


# ==================== 模型管理 ====================

def load_model(model_name: str):
    """加载模型（懒加载）"""
    if model_name in models:
        return models[model_name]
    
    print(f"[模型加载] 正在加载 {model_name} ...")
    
    from funasr import AutoModel
    
    model_dir = MODEL_NAMES.get(model_name, model_name)
    
    model = AutoModel(
        model=model_dir,
        model_revision="v2.0.4",
        device="cuda" if _has_gpu() else "cpu",
        pred_timestamp=True,
    )
    
    models[model_name] = model
    print(f"[模型加载] {model_name} 加载完成")
    return model


def _has_gpu() -> bool:
    """检查是否有可用 GPU"""
    try:
        import torch
        return torch.cuda.is_available()
    except:
        return False


def load_punc_model():
    """加载标点模型（懒加载）"""
    global punc_model
    if punc_model is not None:
        return punc_model
    
    print("[模型加载] 正在加载标点模型 ct-punc ...")
    
    from funasr import AutoModel
    
    punc_model = AutoModel(
        model="ct-punc",
        model_revision="v2.0.4",
        device="cuda" if _has_gpu() else "cpu",
    )
    
    print("[模型加载] 标点模型 ct-punc 加载完成")
    return punc_model


# ==================== 下载录播并提取音频 ====================

def load_aligner_model():
    """加载 ForcedAligner 时间戳对齐模型（懒加载，用于 Qwen3-ASR）"""
    global aligner_model
    if aligner_model is not None:
        return aligner_model
    
    print(f"[模型加载] 正在加载 ForcedAligner 时间戳对齐模型 ...")
    
    from qwen_asr.inference.qwen3_forced_aligner import Qwen3ForcedAligner
    
    device = "cuda" if _has_gpu() else "cpu"
    aligner_model = Qwen3ForcedAligner.from_pretrained(
        ALIGNER_MODEL_NAME,
        device_map=device,
    )
    
    print(f"[模型加载] ForcedAligner 模型加载完成")
    return aligner_model


def stream_audio(video_url: str, cookie: str = "", auth_token: str = "", threads: int = 16):
    """
    先多线程下载视频，再流式提取音频
    返回 PCM 16kHz 单声道 s16le 格式的音频块生成器
    """
    import tempfile
    import os
    
    # 创建临时文件（在项目目录下，避免 C 盘空间不足）
    temp_root = str(BASE_DIR / "temp")
    os.makedirs(temp_root, exist_ok=True)
    temp_dir = tempfile.mkdtemp(prefix="sr_subtitle_", dir=temp_root)
    temp_file = os.path.join(temp_dir, "video.mp4")
    
    bundled_aria2c = BASE_DIR / "aria2-1.37.0-win-64bit-build1" / "aria2c.exe"
    aria2c_path = str(bundled_aria2c) if bundled_aria2c.is_file() else shutil.which("aria2c")
    if not aria2c_path:
        raise RuntimeError("未找到 aria2c：请保留随项目提供的 aria2 目录，或安装 aria2 并把 aria2c.exe 所在目录加入 PATH")
    
    try:
        # ===== 多线程下载 =====
        print(f"[下载] 开始多线程下载: {video_url[:80]}...")
        
        cmd = [
            aria2c_path,
            "--dir=" + temp_dir,
            "--out=video.mp4",
            f"--max-connection-per-server={threads}",
            f"--split={threads}",
            "--min-split-size=1M",
            "--continue=true",
            "--enable-http-keep-alive=true",
            "--enable-http-pipelining=true",
            "--console-log-level=warn",
            "--summary-interval=1",
            "--check-certificate=false",
            video_url
        ]
        
        # 添加 cookie header
        if cookie:
            header = f"Cookie: {cookie}"
            cmd.extend(["--header", header])
        
        if auth_token:
            header = f"Authorization: Bearer {auth_token}"
            cmd.extend(["--header", header])
        
        # 启动下载
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1
        )
        
        # 监控下载进度，打印所有输出
        while True:
            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break
            
            if line:
                # 用 errors='replace' 解码，避免 gbk 编码错误
                line = line.decode('gbk', errors='replace').strip()
                print(f"[aria2c] {line}")
                if "%" in line and "DL:" in line:
                    try:
                        match = re.search(r'\((\d+)%\)', line)
                        if match:
                            percent = int(match.group(1))
                            print(f"[下载] 进度: {percent}%")
                    except:
                        pass
        
        returncode = process.wait()
        
        if returncode != 0:
            raise Exception(f"下载失败，aria2c 返回码: {returncode}")
        
        if not os.path.exists(temp_file):
            raise Exception("下载失败，文件不存在")
        
        file_size = os.path.getsize(temp_file)
        print(f"[下载] 完成，文件大小: {file_size / 1024 / 1024:.1f} MB")
        
        # ===== 从本地文件提取音频 =====
        ffmpeg_cmd = [
            "ffmpeg",
            "-i", temp_file,
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", str(SAMPLE_RATE),
            "-ac", str(CHANNELS),
            "-f", "s16le",
            "-",
        ]
        
        print(f"[FFmpeg] 从本地文件提取音频")
        
        process = subprocess.Popen(
            ffmpeg_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=10**8,
        )
        
        # 后台线程读取 stderr，打印错误信息
        import threading
        def read_stderr():
            try:
                while True:
                    line = process.stderr.readline()
                    if not line:
                        break
                    try:
                        print(f"[FFmpeg] {line.decode('utf-8', errors='ignore').strip()}")
                    except:
                        pass
            except:
                pass
        
        stderr_thread = threading.Thread(target=read_stderr, daemon=True)
        stderr_thread.start()
        
        chunk_size = SAMPLE_RATE * 2 * 10  # 10 秒一块
        total_read = 0
        
        try:
            while True:
                chunk = process.stdout.read(chunk_size)
                if not chunk:
                    break
                total_read += len(chunk)
                yield chunk
        finally:
            print(f"[FFmpeg] 总共读取了 {total_read} 字节音频数据")
            try:
                process.kill()
                process.wait()
            except:
                pass
    
    finally:
        # 清理临时文件，包括 aria2c 可能留下的 .aria2 控制文件。
        try:
            shutil.rmtree(temp_dir)
            print(f"[清理] 临时文件已删除")
        except OSError as e:
            print(f"[清理] 临时文件未能删除，请手动检查 {temp_dir}: {e}")

def pcm_to_numpy(pcm_data: bytes) -> np.ndarray:
    """PCM bytes 转 numpy float32"""
    audio = np.frombuffer(pcm_data, dtype=np.int16)
    audio = audio.astype(np.float32) / 32768.0
    return audio


# ==================== ASR 识别 ====================

def transcribe_segment(model, audio: np.ndarray, start_time: float) -> List[Dict]:
    """识别一段音频，返回带时间戳的句子级字幕"""
    result = model.generate(
        input=audio,
        cache={},
        language="zh",
        use_itn=True,
        batch_size_s=30,
        pred_timestamp=True,
    )
    
    segments = []
    
    if result and len(result) > 0:
        res = result[0]
        print(f"[调试] result keys: {list(res.keys())}")
        text = res.get("text", "").strip()
        
        if not text:
            return segments
        
        # 获取字级时间戳
        timestamp = res.get("timestamp", [])
        print(f"[识别] 文本: {text[:50]}... 时间戳数量: {len(timestamp) if timestamp else 0}")
        if timestamp and len(timestamp) > 0:
            print(f"[识别] 时间戳示例: {timestamp[:3]}")
        
        # 尝试加标点并断句
        try:
            punc_model = load_punc_model()
            punc_result = punc_model.generate(input=text)
            if punc_result and len(punc_result) > 0:
                punc_text = punc_result[0].get("text", text)
            else:
                punc_text = text
        except Exception as e:
            print(f"[标点模型] 加标点失败: {e}，使用无标点版本")
            punc_text = text
        
        # 如果有字级时间戳，按句子断句
        if timestamp and len(timestamp) > 0:
            sentences = _split_sentences_with_timestamps(punc_text, timestamp, start_time)
            if sentences:
                return sentences
        
        # fallback：整段作为一条字幕
        duration = len(audio) / SAMPLE_RATE
        segments.append({
            "start": start_time,
            "end": start_time + duration,
            "text": punc_text,
        })
    
    return segments


def transcribe_segment_qwen3(model, audio: np.ndarray, start_time: float) -> List[Dict]:
    """
    用 Qwen3-ASR 识别一段音频，返回带时间戳的句子级字幕
    （Qwen3-ASR 专用，需要配合 ForcedAligner 做时间戳对齐）
    """
    # 第一步：Qwen3-ASR 识别文字（自带标点）
    result = model.generate(
        input=audio,
        language="zh",
        use_itn=True,
    )

    segments = []

    if result and len(result) > 0:
        res = result[0]
        text = res.get("text", "").strip()

        if not text:
            return segments

        print(f"[Qwen3识别] 文本: {text[:50]}...")

        # 第二步：用 ForcedAligner 对齐时间戳
        char_timestamps = []
        try:
            aligner = load_aligner_model()
            align_results = aligner.align(
                audio=(audio, SAMPLE_RATE),
                text=text,
                language="Chinese",
            )

            # 把词级时间戳转换成字级时间戳（每个字均匀分配词的时长）
            if align_results and len(align_results) > 0:
                align_result = align_results[0]
                for item in align_result.items:
                    word = item.text
                    word_start = item.start_time  # 秒
                    word_end = item.end_time  # 秒
                    word_duration = word_end - word_start
                    char_count = len(word)
                    
                    if char_count > 0:
                        char_duration = word_duration / char_count
                        for i in range(char_count):
                            char_start_ms = (word_start + i * char_duration) * 1000.0
                            char_end_ms = (word_start + (i + 1) * char_duration) * 1000.0
                            char_timestamps.append([char_start_ms, char_end_ms])

            print(f"[Qwen3对齐] 字级时间戳数量: {len(char_timestamps)}")

        except Exception as e:
            import traceback
            print(f"[Qwen3对齐] 时间戳对齐失败: {e}")
            traceback.print_exc()
            char_timestamps = []

        # 第三步：如果有字级时间戳，按句子断句
        # Qwen3-ASR 本身已经带标点了，直接用 text 作为带标点文本
        if char_timestamps and len(char_timestamps) > 0:
            sentences = _split_sentences_with_timestamps(text, char_timestamps, start_time)
            if sentences:
                return sentences

        # fallback：整段作为一条字幕
        duration = len(audio) / SAMPLE_RATE
        segments.append({
            "start": start_time,
            "end": start_time + duration,
            "text": text,
        })

    return segments

def _split_sentences_with_timestamps(punc_text: str, timestamp: list, start_time: float) -> List[Dict]:
    """根据标点和字级时间戳，拆分成句子级字幕"""
    import re

    punctuations = r'[。，！？；：、,.!?;:]'
    sentences = []
    current_sentence = ""
    char_index = 0

    for char in punc_text:
        if re.match(punctuations, char):
            if current_sentence.strip():
                sentence_len = len(current_sentence)
                start_idx = char_index - sentence_len
                end_idx = char_index - 1
                if 0 <= start_idx < len(timestamp) and 0 <= end_idx < len(timestamp):
                    ts_s = timestamp[start_idx]
                    ts_e = timestamp[end_idx]
                    if isinstance(ts_s, (list, tuple)):
                        s = ts_s[0] / 1000.0
                        e = ts_e[1] / 1000.0 if isinstance(ts_e, (list, tuple)) else ts_e / 1000.0
                    else:
                        s = ts_s / 1000.0
                        e = ts_e / 1000.0
                    sentences.append({"start": start_time + s, "end": start_time + e, "text": current_sentence + char})
            current_sentence = ""
        else:
            current_sentence += char
            char_index += 1

    if current_sentence.strip():
        sentence_len = len(current_sentence)
        start_idx = char_index - sentence_len
        end_idx = char_index - 1
        if 0 <= start_idx < len(timestamp) and 0 <= end_idx < len(timestamp):
            ts_s = timestamp[start_idx]
            ts_e = timestamp[end_idx]
            if isinstance(ts_s, (list, tuple)):
                s = ts_s[0] / 1000.0
                e = ts_e[1] / 1000.0 if isinstance(ts_e, (list, tuple)) else ts_e / 1000.0
            else:
                s = ts_s / 1000.0
                e = ts_e / 1000.0
            sentences.append({"start": start_time + s, "end": start_time + e, "text": current_sentence})

    print(f"[断句] 文本长度:{len(punc_text)} 时间戳:{len(timestamp)} 句子数:{len(sentences)}")
    return sentences


def transcribe_stream(video_url: str, model_name: str, cookie: str = "", threads: int = 16, 
                      auth_token: str = "", task_id: str = None, streamer: str = "", 
                      platform: str = "", title: str = "", recording_id: str = "",
                      video_duration: float = 0) -> Dict:
    """
    流式识别整个视频
    返回完整字幕数据
    """
    model = load_model(model_name)
    
    # 选择识别函数（根据模型类型）
    if model_name.startswith("qwen3"):
        transcribe_func = transcribe_segment_qwen3
    else:
        transcribe_func = transcribe_segment
    
    # 提前加载标点模型，避免拉流过程中加载导致连接超时
    try:
        load_punc_model()
    except Exception as e:
        print(f"[警告] 标点模型加载失败: {e}，将使用无标点模式")
    
    # 如果有 recording_id，先调用 stream API 获取完整的视频地址
    if recording_id and (auth_token or cookie):
        try:
            import requests
            stream_api_url = f"https://streamrecorder.io/recordings/stream/{recording_id}"
            headers = {
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            }
            if auth_token:
                headers["Authorization"] = f"Bearer {auth_token}"
            if cookie:
                headers["Cookie"] = cookie
            
            resp = requests.get(stream_api_url, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            
            # 优先用 mp4 下载地址（完整视频），其次用 m3u8 地址
            if data.get("sources") and len(data["sources"]) > 0:
                src = data["sources"][0]
                # 优先用 download_src（完整 mp4）
                if src.get("download_src"):
                    video_url = src["download_src"]
                    print(f"[获取播放信息] 成功，使用完整 MP4 地址")
                elif src.get("src"):
                    m3u8_src = src["src"]
                    if m3u8_src.startswith("/"):
                        video_url = f"https://streamrecorder.io{m3u8_src}"
                    else:
                        video_url = m3u8_src
                    print(f"[获取播放信息] 成功，使用 m3u8 地址: {video_url}")
            elif data.get("hls_master"):
                video_url = f"https://streamrecorder.io{data['hls_master']}"
                print(f"[获取播放信息] 成功，使用 m3u8 地址: {video_url}")
        except Exception as e:
            print(f"[获取播放信息失败] {e}")
            # 如果失败，还是用原来的地址试试
    
    all_segments = []
    current_time = 0.0
    buffer = np.array([], dtype=np.float32)
    segment_samples = SEGMENT_DURATION * SAMPLE_RATE
    
    total_duration = 0.0
    
    for pcm_chunk in stream_audio(video_url, cookie, auth_token, threads=threads):
        audio_chunk = pcm_to_numpy(pcm_chunk)
        buffer = np.concatenate([buffer, audio_chunk])
        
        # 更新任务进度
        if task_id and task_id in tasks:
            total_duration += len(audio_chunk) / SAMPLE_RATE
            if video_duration > 0:
                tasks[task_id]["progress"] = min(99, int(total_duration / video_duration * 100))
            else:
                tasks[task_id]["progress"] = min(99, int(total_duration / 60))
            tasks[task_id]["current_time"] = total_duration
            # 检查取消
            if tasks[task_id].get("cancelled"):
                raise InterruptedError("任务已取消")
        
        # 凑够一段就识别
        while len(buffer) >= segment_samples:
            segment = buffer[:segment_samples]
            buffer = buffer[segment_samples:]
            
            try:
                segments = transcribe_func(model, segment, current_time)
                all_segments.extend(segments)
            except Exception as e:
                print(f"[识别错误] {e}")
            
            current_time += SEGMENT_DURATION
    
    # 处理剩余的音频
    if len(buffer) > 0:
        try:
            segments = transcribe_func(model, buffer, current_time)
            all_segments.extend(segments)
        except Exception as e:
            print(f"[识别错误] {e}")
    
    total_duration = current_time + len(buffer) / SAMPLE_RATE
    
    # 保存到数据库
    subtitle_id = save_subtitle_to_db(
        streamer=streamer,
        platform=platform,
        title=title,
        video_url=video_url,
        model=model_name,
        duration=total_duration,
        segments=all_segments,
        recording_id=recording_id,
    )
    
    return {
        "subtitle_id": subtitle_id,
        "streamer": streamer,
        "platform": platform,
        "title": title,
        "model": model_name,
        "duration": total_duration,
        "segments": all_segments,
    }


# ==================== 数据库操作 ====================

def save_subtitle_to_db(streamer: str, platform: str, title: str, 
                        video_url: str, model: str, duration: float,
                        segments: List[Dict], recording_id: str = "") -> int:
    """保存字幕到数据库"""
    conn = get_db()
    c = conn.cursor()
    
    # 检查是否已存在（优先按 recording_id 查）
    if recording_id:
        c.execute(
            "SELECT id FROM subtitles WHERE recording_id = ?",
            (recording_id,)
        )
    else:
        c.execute(
            "SELECT id FROM subtitles WHERE streamer=? AND platform=? AND title=?",
            (streamer, platform, title)
        )
    row = c.fetchone()
    
    if row:
        # 已存在，先删除旧的
        subtitle_id = row["id"]
        c.execute("DELETE FROM subtitle_segments WHERE subtitle_id=?", (subtitle_id,))
        c.execute(
            "UPDATE subtitles SET model=?, duration=?, title=?, video_url=?, recording_id=?, created_at=CURRENT_TIMESTAMP WHERE id=?",
            (model, duration, title, video_url, recording_id, subtitle_id)
        )
    else:
        # 插入新记录
        c.execute(
            "INSERT INTO subtitles (streamer, platform, title, video_url, model, duration, recording_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (streamer, platform, title, video_url, model, duration, recording_id)
        )
        subtitle_id = c.lastrowid
    
    # 插入片段
    for seg in segments:
        # 去掉字之间的空格
        text = seg["text"].replace(" ", "")
        c.execute(
            "INSERT INTO subtitle_segments (subtitle_id, start_time, end_time, text) VALUES (?, ?, ?, ?)",
            (subtitle_id, seg["start"], seg["end"], text)
        )
    
    conn.commit()
    conn.close()
    
    return subtitle_id


def search_subtitles(keyword: str, streamer: str = None, platform: str = None, limit: int = None, only_unwatched: bool = False) -> List[Dict]:
    """搜索字幕"""
    conn = get_db()
    c = conn.cursor()
    
    query = """
        SELECT 
            s.id as subtitle_id,
            s.streamer,
            s.platform,
            s.title,
            s.duration,
            s.recording_id,
            r.id as rec_id,
            r.streamer_name,
            r.streamer_avatar,
            r.record_time,
            r.is_watched,
            seg.start_time,
            seg.end_time,
            seg.text
        FROM subtitle_segments seg
        JOIN subtitles s ON s.id = seg.subtitle_id
        LEFT JOIN recordings r ON r.recording_id = s.recording_id
        WHERE seg.text LIKE ?
    """
    
    params = [f'%{keyword}%']
    
    if streamer:
        query += " AND s.streamer = ?"
        params.append(streamer)
    
    if platform:
        query += " AND s.platform = ?"
        params.append(platform)

    if only_unwatched:
        query += " AND (r.is_watched = 0 OR r.is_watched IS NULL)"
    
    query += " ORDER BY s.streamer, r.record_time DESC, s.id, seg.start_time"
    if limit:
        query += " LIMIT ?"
        params.append(limit)
    
    c.execute(query, params)
    rows = c.fetchall()
    
    results = []
    for row in rows:
        results.append({
            "subtitle_id": row["subtitle_id"],
            "streamer": row["streamer"],
            "platform": row["platform"],
            "title": row["title"],
            "duration": row["duration"],
            "recording_id": row["recording_id"],
            "rec_id": row["rec_id"] if row["rec_id"] is not None else 0,
            "streamer_name": row["streamer_name"],
            "streamer_avatar": row["streamer_avatar"],
            "record_time": row["record_time"],
            "is_watched": row["is_watched"] if row["is_watched"] is not None else 0,
            "start_time": row["start_time"],
            "end_time": row["end_time"],
            "text": row["text"],
        })
    
    conn.close()
    return results


def get_subtitle(subtitle_id: int) -> Optional[Dict]:
    """获取字幕详情"""
    conn = get_db()
    c = conn.cursor()
    
    c.execute("SELECT * FROM subtitles WHERE id=?", (subtitle_id,))
    sub_row = c.fetchone()
    
    if not sub_row:
        conn.close()
        return None
    
    # 查询录播信息（主播昵称、头像、录播时间）
    streamer_name = None
    streamer_avatar = None
    record_time = None
    if sub_row["recording_id"]:
        c.execute("SELECT streamer_name, streamer_avatar, record_time FROM recordings WHERE recording_id = ?", 
                  (sub_row["recording_id"],))
        rec_row = c.fetchone()
        if rec_row:
            streamer_name = rec_row["streamer_name"]
            streamer_avatar = rec_row["streamer_avatar"]
            record_time = rec_row["record_time"]
    
    c.execute(
        "SELECT * FROM subtitle_segments WHERE subtitle_id=? ORDER BY start_time",
        (subtitle_id,)
    )
    seg_rows = c.fetchall()
    
    segments = []
    for row in seg_rows:
        segments.append({
            "start": row["start_time"],
            "end": row["end_time"],
            "text": row["text"],
        })
    
    result = {
        "id": sub_row["id"],
        "streamer": sub_row["streamer"],
        "platform": sub_row["platform"],
        "title": sub_row["title"],
        "video_url": sub_row["video_url"],
        "model": sub_row["model"],
        "duration": sub_row["duration"],
        "created_at": sub_row["created_at"],
        "recording_id": sub_row["recording_id"],
        "streamer_name": streamer_name,
        "streamer_avatar": streamer_avatar,
        "record_time": record_time,
        "segments": segments,
    }
    
    conn.close()
    return result


def list_streamers() -> List[Dict]:
    """获取所有主播列表"""
    conn = get_db()
    c = conn.cursor()
    
    c.execute("""
        SELECT streamer, platform, COUNT(*) as count, MAX(created_at) as last_time
        FROM subtitles
        GROUP BY streamer, platform
        ORDER BY last_time DESC
    """)
    rows = c.fetchall()
    
    results = []
    for row in rows:
        results.append({
            "streamer": row["streamer"],
            "platform": row["platform"],
            "count": row["count"],
            "last_time": row["last_time"],
        })
    
    conn.close()
    return results


def list_subtitles(streamer: str = None, platform: str = None) -> List[Dict]:
    """获取字幕列表"""
    conn = get_db()
    c = conn.cursor()
    
    query = "SELECT * FROM subtitles"
    params = []
    conditions = []
    
    if streamer:
        conditions.append("streamer = ?")
        params.append(streamer)
    
    if platform:
        conditions.append("platform = ?")
        params.append(platform)
    
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    
    query += " ORDER BY created_at DESC"
    
    c.execute(query, params)
    rows = c.fetchall()
    
    results = []
    for row in rows:
        results.append({
            "id": row["id"],
            "streamer": row["streamer"],
            "platform": row["platform"],
            "title": row["title"],
            "duration": row["duration"],
            "model": row["model"],
            "recording_id": row["recording_id"],
            "created_at": row["created_at"],
        })
    
    conn.close()
    return results


def generate_srt(segments: List[Dict]) -> str:
    """生成 SRT 格式字幕"""
    srt = ""
    for i, seg in enumerate(segments, 1):
        start = format_srt_time(seg["start"])
        end = format_srt_time(seg["end"])
        text = seg["text"].strip()
        srt += f"{i}\n{start} --> {end}\n{text}\n\n"
    return srt


def format_srt_time(seconds: float) -> str:
    """格式化时间为 SRT 格式"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ==================== 录播列表管理 ====================

def sync_recordings(recordings: List[Dict]) -> int:
    """同步录播列表（批量插入/更新）"""
    conn = get_db()
    c = conn.cursor()
    
    count = 0
    for rec in recordings:
        try:
            c.execute("""
                INSERT OR IGNORE INTO recordings 
                (streamer, platform, title, video_url, duration, resolution, 
                 thumbnail_url, recording_id, is_live, record_time, 
                 download_url, filesize, avg_viewers, max_viewers,
                 streamer_name, streamer_avatar)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                rec.get("streamer", ""),
                rec.get("platform", ""),
                rec.get("title", ""),
                rec.get("video_url", ""),
                rec.get("duration", 0),
                rec.get("resolution", ""),
                rec.get("thumbnail_url", ""),
                rec.get("recording_id", ""),
                1 if rec.get("is_live") else 0,
                rec.get("record_time", ""),
                rec.get("download_url", ""),
                rec.get("filesize", 0),
                rec.get("avg_viewers", 0),
                rec.get("max_viewers", 0),
                rec.get("streamer_name", ""),
                rec.get("streamer_avatar", ""),
            ))
            # 更新已存在的记录（保留 is_watched 不变）
            c.execute("""
                UPDATE recordings SET
                    streamer = ?, title = ?, video_url = ?, duration = ?, 
                    resolution = ?, thumbnail_url = ?, is_live = ?, 
                    record_time = ?, download_url = ?, filesize = ?,
                    avg_viewers = ?, max_viewers = ?, streamer_name = ?,
                    streamer_avatar = ?
                WHERE platform = ? AND recording_id = ?
            """, (
                rec.get("streamer", ""),
                rec.get("title", ""),
                rec.get("video_url", ""),
                rec.get("duration", 0),
                rec.get("resolution", ""),
                rec.get("thumbnail_url", ""),
                1 if rec.get("is_live") else 0,
                rec.get("record_time", ""),
                rec.get("download_url", ""),
                rec.get("filesize", 0),
                rec.get("avg_viewers", 0),
                rec.get("max_viewers", 0),
                rec.get("streamer_name", ""),
                rec.get("streamer_avatar", ""),
                rec.get("platform", ""),
                rec.get("recording_id", ""),
            ))
            count += 1
        except Exception as e:
            print(f"[同步录播] 跳过: {e}")
    
    conn.commit()
    conn.close()
    return count


def list_recordings(streamer: str = None, platform: str = None,
                    is_live: bool = None, is_subtitled: bool = None,
                    page: int = 1, page_size: int = 20) -> Dict:
    """获取录播列表（分页）"""
    conn = get_db()
    c = conn.cursor()
    
    # 先查总数
    count_query = "SELECT COUNT(*) as total FROM recordings"
    count_params = []
    conditions = []
    
    if streamer:
        conditions.append("streamer = ?")
        count_params.append(streamer)
    
    if platform:
        conditions.append("platform = ?")
        count_params.append(platform)
    
    if is_live is not None:
        conditions.append("is_live = ?")
        count_params.append(1 if is_live else 0)
    
    if is_subtitled is not None:
        if is_subtitled:
            count_query = "SELECT COUNT(DISTINCT r.id) as total FROM recordings r INNER JOIN subtitles s ON r.recording_id = s.recording_id"
        else:
            count_query = "SELECT COUNT(*) as total FROM recordings r WHERE NOT EXISTS (SELECT 1 FROM subtitles s WHERE s.recording_id = r.recording_id)"
        if conditions:
            count_query += " AND " + " AND ".join(["r." + c for c in conditions])
    elif conditions:
        count_query += " WHERE " + " AND ".join(conditions)
    
    c.execute(count_query, count_params)
    total = c.fetchone()["total"]
    
    # 查当前页数据
    query = "SELECT * FROM recordings"
    params = []
    conditions = []
    
    if streamer:
        conditions.append("streamer = ?")
        params.append(streamer)
    
    if platform:
        conditions.append("platform = ?")
        params.append(platform)
    
    if is_live is not None:
        conditions.append("is_live = ?")
        params.append(1 if is_live else 0)
    
    if is_subtitled is not None:
        if is_subtitled:
            query = "SELECT DISTINCT r.* FROM recordings r INNER JOIN subtitles s ON r.recording_id = s.recording_id"
        else:
            query = "SELECT r.* FROM recordings r WHERE NOT EXISTS (SELECT 1 FROM subtitles s WHERE s.recording_id = r.recording_id)"
        if conditions:
            query += " AND " + " AND ".join(["r." + c for c in conditions])
    elif conditions:
        query += " WHERE " + " AND ".join(conditions)
    
    # 排序：直播优先，然后按录制时间倒序，没有录制时间的按创建时间倒序
    if is_subtitled is not None:
        query += " ORDER BY r.is_live DESC, r.record_time DESC, r.created_at DESC"
    else:
        query += " ORDER BY is_live DESC, record_time DESC, created_at DESC"
    
    # 分页
    offset = (page - 1) * page_size
    query += " LIMIT ? OFFSET ?"
    params.append(page_size)
    params.append(offset)
    
    c.execute(query, params)
    rows = c.fetchall()
    
    results = []
    for row in rows:
        results.append({
            "id": row["id"],
            "streamer": row["streamer"],
            "platform": row["platform"],
            "title": row["title"],
            "video_url": row["video_url"],
            "duration": row["duration"],
            "resolution": row["resolution"],
            "thumbnail_url": row["thumbnail_url"],
            "recording_id": row["recording_id"],
            "is_live": bool(row["is_live"]),
            "record_time": row["record_time"],
            "download_url": row["download_url"],
            "filesize": row["filesize"],
            "avg_viewers": row["avg_viewers"],
            "max_viewers": row["max_viewers"],
            "streamer_name": row["streamer_name"],
            "streamer_avatar": row["streamer_avatar"],
            "is_watched": row["is_watched"],
            "created_at": row["created_at"],
        })
    
    conn.close()
    
    total_pages = (total + page_size - 1) // page_size
    
    return {
        "items": results,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


def get_recording_streamers() -> List[Dict]:
    """获取录播中的主播列表"""
    conn = get_db()
    c = conn.cursor()
    
    c.execute("""
        SELECT streamer, platform, COUNT(*) as count, 
               SUM(CASE WHEN is_live = 1 THEN 1 ELSE 0 END) as live_count,
               MAX(created_at) as last_sync,
               MAX(streamer_name) as streamer_name,
               MAX(streamer_avatar) as streamer_avatar
        FROM recordings
        GROUP BY streamer, platform
        ORDER BY live_count DESC, last_sync DESC
    """)
    rows = c.fetchall()
    
    results = []
    for row in rows:
        results.append({
            "streamer": row["streamer"],
            "platform": row["platform"],
            "count": row["count"],
            "live_count": row["live_count"],
            "last_sync": row["last_sync"],
            "streamer_name": row["streamer_name"],
            "streamer_avatar": row["streamer_avatar"],
        })
    
    conn.close()
    return results


# ==================== 任务管理 ====================

def queue_worker():
    """后台任务队列 worker 线程"""
    global worker_running
    import time
    while worker_running:
        # 如果暂停了，等待
        pause_event.wait()
        
        task_id = None
        with queue_lock:
            if task_queue:
                task_id = task_queue.pop(0)
        
        if task_id is None:
            time.sleep(0.5)
            continue
        
        if task_id not in tasks:
            continue
        
        task = tasks[task_id]
        if task.get('status') == 'cancelled':
            continue
        
        # 执行任务
        try:
            run_task(
                task_id=task_id,
                video_url=task.get('video_url', ''),
                model_name=task.get('model', 'paraformer-large'),
                cookie=task.get('cookie', SR_COOKIE),
                auth_token=task.get('auth_token', SR_AUTH_TOKEN),
                streamer=task.get('streamer', ''),
                platform=task.get('platform', ''),
                title=task.get('title', ''),
                recording_id=task.get('recording_id', ''),
                threads=task.get('threads', 16),
            )
        except Exception as e:
            if task_id in tasks:
                tasks[task_id]['status'] = 'failed'
                tasks[task_id]['error'] = str(e)
            print(f"[队列任务异常] {task_id}: {e}")


def ensure_worker():
    """确保 worker 线程在运行"""
    global queue_worker_thread
    if queue_worker_thread is None or not queue_worker_thread.is_alive():
        queue_worker_thread = threading.Thread(target=queue_worker, daemon=True)
        queue_worker_thread.start()
        print("[启动] 任务队列 worker 已启动")


def add_task_to_queue(task_info: dict) -> str:
    """添加任务到队列"""
    task_id = str(uuid.uuid4())
    task_info['id'] = task_id
    task_info['status'] = 'waiting'
    task_info['progress'] = 0
    tasks[task_id] = task_info
    with queue_lock:
        task_queue.append(task_id)
    ensure_worker()
    return task_id


def cancel_task(task_id: str) -> bool:
    """取消/移除任务"""
    if task_id not in tasks:
        return False
    
    task = tasks[task_id]
    
    # 如果正在运行，设置取消标志
    if task['status'] == 'running':
        task['cancelled'] = True
        task['status'] = 'cancelled'
        return True
    
    # 如果在等待中，从队列移除
    if task['status'] == 'waiting':
        with queue_lock:
            if task_id in task_queue:
                task_queue.remove(task_id)
        task['status'] = 'cancelled'
        return True
    
    # 已完成/失败/已取消的，标记为移除
    task['removed'] = True
    return True


def get_queue_status():
    """获取队列状态"""
    with queue_lock:
        waiting_ids = list(task_queue)
    
    result = []
    # 等待中的任务（按队列顺序）
    for tid in waiting_ids:
        if tid in tasks:
            t = tasks[tid]
            result.append({
                'id': tid,
                'status': 'waiting',
                'progress': 0,
                'title': t.get('title', ''),
                'streamer': t.get('streamer', ''),
                'recording_id': t.get('recording_id', ''),
                'model': t.get('model', ''),
            })
    
    # 正在运行的任务
    for tid, t in tasks.items():
        if t.get('status') == 'running' and not t.get('removed'):
            result.append({
                'id': tid,
                'status': 'running',
                'progress': t.get('progress', 0),
                'title': t.get('title', ''),
                'streamer': t.get('streamer', ''),
                'recording_id': t.get('recording_id', ''),
                'model': t.get('model', ''),
            })
    
    # 最近完成/失败/取消的任务（最多20个）
    recent = []
    for tid, t in tasks.items():
        if t.get('status') in ('completed', 'failed', 'cancelled') and not t.get('removed'):
            recent.append({
                'id': tid,
                'status': t['status'],
                'progress': t.get('progress', 0),
                'title': t.get('title', ''),
                'streamer': t.get('streamer', ''),
                'recording_id': t.get('recording_id', ''),
                'model': t.get('model', ''),
                'error': t.get('error', ''),
                'subtitle_id': t.get('subtitle_id'),
            })
    # 按完成时间倒序（简化：按字典顺序，因为uuid不保证顺序，但可以接受）
    recent = recent[-20:]
    
    return {
        'tasks': result,
        'recent': recent,
        'paused': not pause_event.is_set(),
        'queue_length': len(waiting_ids),
    }


def run_task(task_id: str, video_url: str, model_name: str, cookie: str,
             auth_token: str, streamer: str, platform: str, title: str, recording_id: str = "", threads: int = 16):
    """后台运行识别任务"""
    try:
        tasks[task_id]["status"] = "running"
        tasks[task_id]["progress"] = 0
        tasks[task_id]["cancelled"] = False
        
        # 从数据库获取视频总时长，用于进度计算
        video_duration = 0
        if recording_id:
            try:
                conn = get_db()
                row = conn.execute("SELECT duration FROM recordings WHERE recording_id = ?", (recording_id,)).fetchone()
                if row and row["duration"]:
                    video_duration = float(row["duration"])
                conn.close()
            except Exception as e:
                print(f"[进度] 获取视频时长失败: {e}")
        
        # 检查取消
        if tasks[task_id].get("cancelled"):
            tasks[task_id]["status"] = "cancelled"
            return

        result = transcribe_stream(
            video_url=video_url,
            model_name=model_name,
            cookie=cookie,
            auth_token=auth_token,
            task_id=task_id,
            streamer=streamer,
            platform=platform,
            title=title,
            recording_id=recording_id,
            video_duration=video_duration,
            threads=threads,
        )

        # 检查取消
        if tasks[task_id].get("cancelled"):
            tasks[task_id]["status"] = "cancelled"
            return
        
        tasks[task_id]["status"] = "completed"
        tasks[task_id]["progress"] = 100
        tasks[task_id]["result"] = result
        tasks[task_id]["subtitle_id"] = result["subtitle_id"]
        
    except InterruptedError:
        tasks[task_id]["status"] = "cancelled"
        print(f"[任务取消] {task_id}")
    except Exception as e:
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = str(e)
        print(f"[任务失败] {task_id}: {e}")


# ==================== FastAPI 应用 ====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期"""
    # 启动时初始化数据库
    init_db()
    print("[启动] 数据库初始化完成")
    print(f"[启动] GPU 可用: {_has_gpu()}")
    # 启动任务队列 worker
    ensure_worker()
    yield
    # 关闭时清理
    print("[关闭] 服务停止")


app = FastAPI(title="StreamRecorder Subtitle API", lifespan=lifespan)

# CORS 配置（允许油猴脚本访问）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件服务（前端页面）
WEB_DIR = BASE_DIR / "web"
if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
    
    @app.get("/")
    async def index():
        """前端页面"""
        return FileResponse(str(WEB_DIR / "index.html"))


# ==================== 请求模型 ====================

class TranscribeRequest(BaseModel):
    video_url: str = ""
    recording_id: str = ""
    model: str = "paraformer-large"
    cookie: str = ""
    streamer: str = ""
    platform: str = ""
    title: str = ""
    threads: int = 16


class BatchTranscribeItem(BaseModel):
    video_url: str = ""
    recording_id: str = ""
    streamer: str = ""
    platform: str = ""
    title: str = ""


class BatchTranscribeRequest(BaseModel):
    items: list
    model: str = "paraformer-large"
    threads: int = 16


class BatchDeleteRequest(BaseModel):
    ids: list


class BatchTranscribeItem(BaseModel):
    video_url: str = ""
    recording_id: str = ""
    streamer: str = ""
    platform: str = ""
    title: str = ""


class BatchTranscribeRequest(BaseModel):
    items: List[BatchTranscribeItem]
    model: str = "paraformer-large"
    threads: int = 16


class BatchDeleteRequest(BaseModel):
    ids: List[int]


class SearchRequest(BaseModel):
    keyword: str
    streamer: str = None
    platform: str = None
    limit: int = 50
    only_unwatched: bool = False


class RecordingItem(BaseModel):
    streamer: str
    platform: str
    title: str
    video_url: str = ""
    duration: float = 0
    resolution: str = ""
    thumbnail_url: str = ""
    recording_id: str = ""
    is_live: bool = False
    record_time: str = ""
    download_url: str = ""
    filesize: int = 0
    avg_viewers: int = 0
    max_viewers: int = 0
    streamer_name: str = ""
    streamer_avatar: str = ""


class SyncRecordingsRequest(BaseModel):
    recordings: List[RecordingItem]
    auth_token: str = ""
    cookie: str = ""


# ==================== API 接口 ====================

@app.get("/api/health")
async def health():
    """健康检查"""
    return {
        "status": "ok",
        "gpu_available": _has_gpu(),
        "loaded_models": list(models.keys()),
        "db_path": str(DB_PATH),
    }


@app.post("/api/transcribe")
async def transcribe(request: TranscribeRequest):
    """提交字幕生成任务（加入队列）"""
    if request.model not in MODEL_NAMES:
        raise HTTPException(400, f"不支持的模型: {request.model}")

    video_url = request.video_url
    auth_token = SR_AUTH_TOKEN
    if request.recording_id:
        video_url = f"https://streamrecorder.io/recordings/hls/{request.recording_id}/master.m3u8"
    if not video_url:
        raise HTTPException(400, "video_url 或 recording_id 不能为空")

    task_info = {
        "video_url": video_url,
        "model": request.model,
        "cookie": request.cookie or SR_COOKIE,
        "auth_token": auth_token,
        "streamer": request.streamer,
        "platform": request.platform,
        "title": request.title,
        "recording_id": request.recording_id,
        "threads": request.threads,
    }
    task_id = add_task_to_queue(task_info)
    return {"task_id": task_id, "status": "queued"}


@app.post("/api/transcribe/batch")
async def batch_transcribe(request: BatchTranscribeRequest):
    submitted = []
    skipped = []
    for item in request.items:
        if item.recording_id:
            conn = get_db()
            row = conn.execute("SELECT id FROM subtitles WHERE recording_id = ?", (item.recording_id,)).fetchone()
            conn.close()
            if row:
                skipped.append({"recording_id": item.recording_id, "title": item.title, "reason": "已有字幕"})
                continue
        already_queued = False
        for tid, t in tasks.items():
            if t.get("recording_id") == item.recording_id and t.get("status") in ("waiting", "running"):
                already_queued = True
                break
        if already_queued:
            skipped.append({"recording_id": item.recording_id, "title": item.title, "reason": "已在队列中"})
            continue
        video_url = item.video_url
        if item.recording_id:
            video_url = f"https://streamrecorder.io/recordings/hls/{item.recording_id}/master.m3u8"
        if not video_url:
            skipped.append({"recording_id": item.recording_id, "title": item.title, "reason": "无视频地址"})
            continue
        task_info = {
            "video_url": video_url, "model": request.model, "cookie": SR_COOKIE,
            "auth_token": SR_AUTH_TOKEN, "streamer": item.streamer, "platform": item.platform,
            "title": item.title, "recording_id": item.recording_id, "threads": request.threads,
        }
        task_id = add_task_to_queue(task_info)
        submitted.append({"task_id": task_id, "recording_id": item.recording_id, "title": item.title})
    return {"submitted": submitted, "skipped": skipped}


@app.get("/api/queue")
async def get_queue():
    return get_queue_status()


@app.post("/api/queue/pause")
async def pause_queue():
    pause_event.clear()
    return {"status": "paused"}


@app.post("/api/queue/resume")
async def resume_queue():
    pause_event.set()
    return {"status": "resumed"}


@app.delete("/api/queue/{task_id}")
async def remove_task(task_id: str):
    if task_id not in tasks:
        raise HTTPException(404, "任务不存在")
    cancel_task(task_id)
    return {"status": "ok"}


@app.post("/api/queue/{task_id}/retry")
async def retry_task(task_id: str):
    if task_id not in tasks:
        raise HTTPException(404, "任务不存在")
    old_task = tasks[task_id]
    task_info = {
        "video_url": old_task.get("video_url", ""), "model": old_task.get("model", "paraformer-large"),
        "cookie": old_task.get("cookie", SR_COOKIE), "auth_token": old_task.get("auth_token", SR_AUTH_TOKEN),
        "streamer": old_task.get("streamer", ""), "platform": old_task.get("platform", ""),
        "title": old_task.get("title", ""), "recording_id": old_task.get("recording_id", ""),
        "threads": old_task.get("threads", 16),
    }
    new_id = add_task_to_queue(task_info)
    return {"task_id": new_id, "status": "queued"}


@app.delete("/api/subtitles/batch")
async def batch_delete_subtitles(request: BatchDeleteRequest):
    conn = get_db()
    deleted = 0
    for sid in request.ids:
        try:
            conn.execute("DELETE FROM subtitle_segments WHERE subtitle_id = ?", (sid,))
            conn.execute("DELETE FROM subtitles WHERE id = ?", (sid,))
            deleted += 1
        except Exception as e:
            print(f"[批量删除] {sid} 失败: {e}")
    conn.commit()
    conn.close()
    return {"deleted": deleted}

@app.get("/api/task/{task_id}")
async def get_task(task_id: str):
    """查询任务状态"""
    if task_id not in tasks:
        raise HTTPException(404, "任务不存在")
    
    task = tasks[task_id]
    result = {
        "id": task["id"],
        "status": task["status"],
        "progress": task.get("progress", 0),
        "title": task.get("title", ""),
        "streamer": task.get("streamer", ""),
        "model": task.get("model", ""),
    }
    
    if task["status"] == "completed":
        result["subtitle_id"] = task.get("subtitle_id")
    elif task["status"] == "failed":
        result["error"] = task.get("error", "")
    
    return result


@app.get("/api/subtitle/{subtitle_id}")
async def get_subtitle_api(subtitle_id: int):
    """获取字幕详情"""
    result = get_subtitle(subtitle_id)
    if not result:
        raise HTTPException(404, "字幕不存在")
    return result


@app.get("/api/subtitle/{subtitle_id}/srt")
async def get_subtitle_srt(subtitle_id: int):
    """下载 SRT 字幕文件"""
    result = get_subtitle(subtitle_id)
    if not result:
        raise HTTPException(404, "字幕不存在")
    
    srt = generate_srt(result["segments"])
    
    from fastapi.responses import Response
    filename = f"{result['title']}.srt"
    return Response(
        content=srt,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/streamers")
async def get_streamers():
    """获取主播列表"""
    return list_streamers()


@app.get("/api/subtitles")
async def get_subtitles(streamer: str = None, platform: str = None):
    """获取字幕列表"""
    return list_subtitles(streamer=streamer, platform=platform)


@app.post("/api/search")
async def search(request: SearchRequest):
    """搜索字幕"""
    if not request.keyword:
        raise HTTPException(400, "keyword 不能为空")
    
    results = search_subtitles(
        keyword=request.keyword,
        streamer=request.streamer,
        platform=request.platform,
        limit=request.limit,
        only_unwatched=request.only_unwatched,
    )
    
    return {"results": results, "count": len(results)}


@app.delete("/api/subtitle/{subtitle_id}")
async def delete_subtitle(subtitle_id: int):
    """删除字幕"""
    conn = get_db()
    c = conn.cursor()
    
    c.execute("DELETE FROM subtitle_segments WHERE subtitle_id=?", (subtitle_id,))
    c.execute("DELETE FROM subtitles WHERE id=?", (subtitle_id,))
    
    conn.commit()
    conn.close()
    
    return {"status": "ok"}


# ==================== 录播列表 API ====================

@app.post("/api/recordings/sync")
async def sync_recordings_api(request: SyncRecordingsRequest):
    """同步录播列表"""
    global SR_AUTH_TOKEN, SR_COOKIE, config
    config_changed = False
    if request.auth_token and not SR_AUTH_TOKEN:
        SR_AUTH_TOKEN = request.auth_token
        config["sr_auth_token"] = request.auth_token
        config_changed = True
        print(f"[同步] 已保存 SR_AUTH_TOKEN")
    if request.cookie and not SR_COOKIE:
        # 只有当前没有 cookie 的时候才更新，避免覆盖手动配置的完整 cookie
        SR_COOKIE = request.cookie
        config["sr_cookie"] = request.cookie
        config_changed = True
        print(f"[同步] 已保存 SR_COOKIE")
    if config_changed:
        save_config(config)
    count = sync_recordings([r.model_dump() for r in request.recordings])
    return {"status": "ok", "synced": count}


@app.get("/api/recordings/last_id")
async def get_last_recording_id(streamer: str = "", platform: str = "douyin"):
    """获取指定主播的最大 recording_id（用于增量同步）"""
    conn = get_db()
    c = conn.cursor()
    try:
        if streamer:
            c.execute("""
                SELECT MAX(CAST(recording_id AS INTEGER)) 
                FROM recordings 
                WHERE streamer = ? AND platform = ?
            """, (streamer, platform))
        else:
            c.execute("SELECT MAX(CAST(recording_id AS INTEGER)) FROM recordings")
        
        row = c.fetchone()
        last_id = row[0] if row and row[0] else 0
        return {"last_id": int(last_id)}
    except Exception as e:
        print(f"[查询last_id] 错误: {e}")
        return {"last_id": 0}
    finally:
        conn.close()


@app.get("/api/recordings")
async def get_recordings(
    streamer: str = None, 
    platform: str = None,
    is_live: bool = None,
    is_subtitled: bool = None,
    page: int = 1,
    page_size: int = 20
):
    """获取录播列表（分页）"""
    return list_recordings(
        streamer=streamer,
        platform=platform,
        is_live=is_live,
        is_subtitled=is_subtitled,
        page=page,
        page_size=page_size,
    )


@app.get("/api/recordings/streamers")
async def api_get_recording_streamers():
    """获取录播中的主播列表"""
    return get_recording_streamers()





@app.post("/api/recordings/{rec_id}/watch")
async def toggle_watch(rec_id: int):
    """标记/取消标记录播为已看"""
    conn = get_db()
    c = conn.cursor()
    # 切换状态
    c.execute("UPDATE recordings SET is_watched = CASE WHEN is_watched = 1 THEN 0 ELSE 1 END WHERE id = ?", (rec_id,))
    conn.commit()
    # 返回新状态
    c.execute("SELECT is_watched FROM recordings WHERE id = ?", (rec_id,))
    row = c.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="录播不存在")
    return {"is_watched": bool(row[0])}

# ==================== 视频代理 ====================

def _proxy_target(url: str) -> tuple[str, bool]:
    """只代理公开 HTTPS 主机；仅向 StreamRecorder 主机发送账号 Cookie。"""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        raise ValueError("只允许 HTTPS 视频地址")
    try:
        if parsed.port not in (None, 443):
            raise ValueError("不允许使用非标准端口")
    except ValueError:
        raise ValueError("视频地址端口无效") from None
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise ValueError("不允许访问本地主机")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("不允许直接访问 IP 地址")
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        raise ValueError("无法解析视频地址") from None
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("不允许访问内网地址")
    return host, host == "streamrecorder.io" or host.endswith(".streamrecorder.io")


@app.get("/api/video/proxy")
async def video_proxy(url: str):
    """代理视频请求，解决跨域和Cookie认证问题"""
    if not SR_COOKIE:
        raise HTTPException(status_code=400, detail="未配置Cookie，请先在cookie.txt中设置")
    try:
        _proxy_target(url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        import httpx
        
        async def stream_content():
            current_url = url
            async with httpx.AsyncClient(timeout=60.0) as client:
                for _ in range(6):
                    try:
                        _, send_cookie = _proxy_target(current_url)
                    except ValueError as exc:
                        yield f"代理地址无效: {exc}".encode()
                        return
                    headers = {
                        "User-Agent": "Mozilla/5.0",
                        "Referer": "https://streamrecorder.io/",
                        "Origin": "https://streamrecorder.io",
                    }
                    if send_cookie:
                        headers["Cookie"] = SR_COOKIE
                    async with client.stream("GET", current_url, headers=headers, follow_redirects=False) as resp:
                        if resp.status_code in (301, 302, 303, 307, 308):
                            location = resp.headers.get("location")
                            if not location:
                                yield "上游重定向缺少地址".encode()
                                return
                            current_url = urljoin(current_url, location)
                            continue
                        if resp.status_code >= 400:
                            yield f"上游返回错误: {resp.status_code}".encode()
                            return
                        async for chunk in resp.aiter_bytes(chunk_size=65536):
                            if chunk:
                                yield chunk
                        return
                yield "上游重定向次数过多".encode()
        
        return StreamingResponse(
            stream_content(),
            media_type="application/octet-stream",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"代理内部错误: {str(e)}")


# ==================== 启动 ====================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)

