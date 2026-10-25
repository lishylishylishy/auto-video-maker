import os, re, csv, json, io, requests, subprocess, edge_tts, urllib.parse, asyncio, time, shutil
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# ==============================================================================
# ⚙️ 主程序置顶配置区
# ==============================================================================
RULES_TAB_NAME  = "config"
SCRIPT_TAB_NAME = os.getenv("TARGET_TAB", "script")
SHEET_URL       = "https://docs.google.com/spreadsheets/d/1Y8YOEl5G2HV4C3WLZz9ttEEoByuWKGLqh_nmWvcb-_M/edit"
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "") 
GCP_SA_KEY      = os.getenv("GCP_SA_KEY", "")      
TEMP_DIR          = "temp_workspace"
OUTPUT_VIDEO_NAME = "final_output.mp4"
VALID_CHAPTERS = {"片头", "转场", "结尾", "男配音", "女配音"}

# ==============================================================================
# 🔐 1. Google API 鉴权与工具函数
# ==============================================================================
def get_google_credentials():
    if not GCP_SA_KEY:
        raise ValueError("未找到 GCP_SA_KEY 环境变量！")
    info = json.loads(GCP_SA_KEY)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly"
    ]
    return Credentials.from_service_account_info(info, scopes=scopes)

def get_google_sheet_tab_csv(sheet_url, tab_name):
    creds = get_google_credentials()
    gc = gspread.authorize(creds)
    sh = gc.open_by_url(sheet_url)
    worksheet = sh.worksheet(tab_name)
    records = worksheet.get_all_values()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerows(records)
    return output.getvalue()

def download_file_from_gdrive(file_id_or_url, output_path):
    match = re.search(r"(?:/file/d/|/d/|id=|folders/)([a-zA-Z0-9-_]+)", str(file_id_or_url))
    file_id = match.group(1) if match else str(file_id_or_url).strip()
    creds = get_google_credentials()
    drive_service = build('drive', 'v3', credentials=creds)
    request = drive_service.files().get_media(fileId=file_id)
    fh = io.FileIO(output_path, 'wb')
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return output_path

def get_audio_duration(file_path):
    """ 精准获取音频真实时长，失败直接报错，不做估算兜底 """
    if not os.path.exists(file_path) or os.path.getsize(file_path) < 1000:
        raise RuntimeError(f"音频文件不存在或损坏：{file_path}")
    try:
        cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_entries", "format=duration:stream=duration",
            file_path
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(res.stdout)
        format_dur = data.get("format", {}).get("duration")
        if format_dur and float(format_dur) > 0:
            return float(format_dur)
        for s in data.get("streams", []):
            if s.get("codec_type") == "audio" and s.get("duration"):
                return float(s["duration"])
    except Exception:
        pass
    try:
        cmd = ["ffmpeg", "-v", "error", "-i", file_path, "-f", "null", "-"]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        match = re.search(r"time=(\d+):(\d+):(\d+\.\d+)", res.stderr)
        if match:
            h, m, s = float(match.group(1)), float(match.group(2)), float(match.group(3))
            return h * 3600 + m * 60 + s
    except Exception:
        pass
    raise RuntimeError(f"无法获取音频时长：{file_path}")

def is_video_media(filepath):
    try:
        cmd = [
            "ffprobe", "-v", "quiet", "-select_streams", "v:0",
            "-show_entries", "stream=nb_frames,duration",
            "-of", "json", filepath
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        info = json.loads(res.stdout)
        streams = info.get("streams", [])
        if streams:
            nb_frames = int(streams[0].get("nb_frames", 0) or 0)
            duration = float(streams[0].get("duration", 0) or 0)
            if nb_frames > 1 or duration > 0.1:
                return True
    except Exception:
        pass
    return False

# ==============================================================================
# ✂️ 2. 文本处理与样式解析
# ==============================================================================
def wrap_text_by_punctuation(text, max_len=18):
    if not text or len(text) <= max_len:
        return text or ""
    pattern = r'([，。！？；：, \.\!\?\;\:])'
    tokens = re.split(pattern, text)
    lines = []
    current_line = ""
    for i in range(0, len(tokens), 2):
        chunk = tokens[i]
        punct = tokens[i+1] if i + 1 < len(tokens) else ""
        part = chunk + punct
        if len(current_line) + len(part) <= max_len:
            current_line += part
        else:
            if current_line:
                lines.append(current_line.strip())
            if len(part) > max_len:
                for k in range(0, len(part), max_len):
                    sub = part[k:k+max_len]
                    if len(sub) == max_len:
                        lines.append(sub)
                    else:
                        current_line = sub
            else:
                current_line = part
    if current_line:
        lines.append(current_line.strip())
    return "\n".join(lines)

def translate_to_english(text):
    if not text or not text.strip():
        return ""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    for attempt in range(2):
        try:
            url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=en&dt=t&q={urllib.parse.quote(text)}"
            res = requests.get(url, headers=headers, timeout=8).json()
            en_text = "".join([item[0] for item in res[0] if item[0]])
            return en_text.strip()
        except Exception:
            time.sleep(1.0)
            continue
    return ""

def sanitize_color(color_str):
    if not color_str or not str(color_str).strip():
        return "white"
    c = str(color_str).strip()
    if c.lower() == "grey":
        return "gray"
    if "," in c:
        parts = [p.strip() for p in c.replace("，", ",").split(",")]
        if len(parts) == 3:
            try:
                r, g, b = [int(p) for p in parts]
                return f"&H00{b:02X}{g:02X}{r:02X}"
            except ValueError: pass
    return c

def parse_ass_color(color_input, default_ass="&H00FFFFFF"):
    """ 将用户输入的颜色转为标准 ASS 颜色码 (&HAABBGGRR) """
    if not color_input or not str(color_input).strip():
        return default_ass
    c = str(color_input).strip()
    if c.startswith("&H"):
        return c
    sc = sanitize_color(c)
    if sc.startswith("&H"):
        return sc
    
    color_map = {
        "white": "&H00FFFFFF",
        "black": "&H00000000",
        "yellow": "&H0000FFFF",
        "red": "&H000000FF",
        "green": "&H0000FF00",
        "blue": "&H00FF0000",
        "gray": "&H00808080",
        "cyan": "&H00FFFF00",
        "magenta": "&H00FF00FF",
    }
    return color_map.get(sc.lower(), default_ass)

def parse_bool_to_int(val_str, default=0):
    if not val_str:
        return default
    s = str(val_str).strip().lower()
    if s in ["1", "true", "是", "开启", "yes", "on"]:
        return 1
    if s in ["0", "false", "否", "关闭", "no", "off"]:
        return 0
    try:
        return 1 if int(float(s)) > 0 else 0
    except ValueError:
        return default

def parse_rgb_to_ffmpeg(rgb_str):
    if not rgb_str or not str(rgb_str).strip(): return "0x0000FF"
    cleaned = str(rgb_str).replace("，", ",").strip()
    parts = [p.strip() for p in cleaned.split(",")]
    if len(parts) == 3:
        try:
            r, g, b = [int(p) for p in parts]
            return f"0x{r:02X}{g:02X}{b:02X}"
        except ValueError: pass
    return "0x0000FF"

def calc_ass_position(pos_str, is_subtitle=False):
    pos = str(pos_str).strip()
    alignment = 2 if is_subtitle else 5
    margin_v = 80 if is_subtitle else 0
    margin_h = 0

    if pos in ["居中", "居中居中"]:
        alignment = 5
        margin_v = 0
    elif "底部" in pos:
        alignment = 2
        margin_v = 80
        if "偏移" in pos or "偏" in pos:
            match = re.search(r'(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)', pos)
            if match:
                margin_h = int(float(match.group(1)))
                margin_v = 80 + int(float(match.group(2)))
    elif pos == "顶部":
        alignment = 8
        margin_v = 60
    elif pos == "左上角":
        alignment = 7
        margin_h = 40
        margin_v = 40
    elif pos == "右上角":
        alignment = 9
        margin_h = 40
        margin_v = 40
    elif pos == "左下角":
        alignment = 1
        margin_h = 40
        margin_v = 120
    elif pos == "右下角":
        alignment = 3
        margin_h = 40
        margin_v = 120
    elif "绝对坐标" in pos:
        match = re.search(r'(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)', pos)
        if match:
            alignment = 7
            margin_h = int(float(match.group(1)))
            margin_v = int(float(match.group(2)))
    elif "居中偏" in pos or "居中偏移" in pos:
        match = re.search(r'(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)', pos)
        if match:
            alignment = 5
            margin_h = int(float(match.group(1)))
            margin_v = int(float(match.group(2)))
    
    return alignment, margin_h, margin_v

# ==============================================================================
# 🛠️ 3. 配置解析与级联继承
# ==============================================================================
def get_param(keys, *dict_layers, default=""):
    if isinstance(keys, str):
        keys = [keys]
    for d in dict_layers:
        if d and isinstance(d, dict):
            for k in keys:
                val = str(d.get(k, "")).strip()
                if val:
                    return val
    return default

def parse_rules_tab(csv_text):
    lines = [line.strip() for line in csv_text.strip().splitlines() if line.strip()]
    rows = list(csv.reader(lines))
    if not rows: return {}, {}
    header = [c.strip() for c in rows[0]]
    global_cfg, styles_cfg = {}, {}
    for row in rows[1:]:
        if not row or not row[0].strip(): continue
        obj_name = row[0].strip()
        if "说明" in obj_name or "【" in obj_name: continue
        row_dict = {header[i]: row[i].strip() for i in range(1, len(header)) if i < len(row)}
        if obj_name == "全局配置":
            global_cfg = row_dict
        else:
            styles_cfg[obj_name] = row_dict
    return global_cfg, styles_cfg

def parse_script_tab(csv_text):
    lines = [line.strip() for line in csv_text.strip().splitlines() if line.strip()]
    rows = list(csv.reader(lines))
    if not rows: return []
    header = [c.strip() for c in rows[0]]
    clips = []
    for row in rows[1:]:
        if not row: continue
        row_dict = {header[i]: row[i].strip() for i in range(len(header)) if i < len(row)}
        chapter = row_dict.get("章节", "").strip()
        if chapter not in VALID_CHAPTERS:
            continue
        duration_str = row_dict.get("章节秒数", "").strip()
        silent_text = row_dict.get("无声文字", "").strip()
        spoken_text = row_dict.get("有声文字", "").strip()
        bg_layer0 = row_dict.get("默认素材-layer0", "").strip()
        bg_layer1 = row_dict.get("挂件素材-layer1", "").strip() or row_dict.get("挂件-layer1", "").strip()
        if not spoken_text and not silent_text and not bg_layer0 and not bg_layer1:
            continue
        try:
            duration = float(duration_str) if duration_str else None
        except ValueError:
            duration = None
        clip_data = row_dict.copy()
        clip_data.update({
            "bg_layer0": bg_layer0,
            "bg_layer1": bg_layer1,
            "duration": duration,
            "chapter": chapter,
            "silent_text": silent_text,
            "spoken_text": spoken_text
        })
        clips.append(clip_data)
    return clips

# ==============================================================================
# 🎬 4. 素材处理与片段渲染
# ==============================================================================
def resolve_asset_path(asset_str):
    if not asset_str:
        return ""
    is_gdrive_id = bool(re.match(r'^[a-zA-Z0-9_-]{25,50}$', asset_str))
    if "drive.google.com" in asset_str or is_gdrive_id:
        dl_path = f"{TEMP_DIR}/gdrive_{abs(hash(asset_str))}"
        if not os.path.exists(dl_path):
            download_file_from_gdrive(asset_str, dl_path)
        return dl_path
    if os.path.exists(asset_str):
        return asset_str
    if PIXABAY_API_KEY and not asset_str.endswith(".mp4") and not asset_str.endswith(".png") and not asset_str.endswith(".jpg"):
        dl_path = f"{TEMP_DIR}/px_{abs(hash(asset_str))}.mp4"
        if os.path.exists(dl_path):
            return dl_path
        try:
            px_url = f"https://pixabay.com/api/videos/?key={PIXABAY_API_KEY}&q={urllib.parse.quote(asset_str)}&per_page=3"
            res = requests.get(px_url, timeout=10).json()
            hits = res.get("hits", [])
            if hits:
                video_url = hits[0]["videos"]["large"]["url"]
                v_res = requests.get(video_url, timeout=30)
                with open(dl_path, "wb") as f:
                    f.write(v_res.content)
                return dl_path
        except Exception:
            pass
    return ""

async def generate_audio_or_silent(clip, style_cfg, global_cfg, audio_output_path):
    spoken_text = clip["spoken_text"].strip()
    if spoken_text:
        voice = get_param(["配音-音色代号"], clip, style_cfg, global_cfg, default="zh-CN-YunxiNeural")
        rate_raw = get_param(["配音-语速"], clip, style_cfg, global_cfg, default="1.0")
        try:
            rate_val = float(rate_raw)
            pct = int(round((rate_val - 1.0) * 100))
            rate_param = f"+{pct}%" if pct >= 0 else f"{pct}%"
        except Exception:
            rate_param = "+0%"
        raw_tts_path = audio_output_path + ".raw.mp3"
        await edge_tts.Communicate(spoken_text, voice, rate=rate_param).save(raw_tts_path)
        std_cmd = [
            "ffmpeg", "-y", "-i", raw_tts_path,
            "-af", "silenceremove=start_periods=1:start_duration=0.05:start_threshold=-50dB",
            "-c:a", "libmp3lame", "-b:a", "192k",
            "-ar", "44100", "-ac", "2",
            "-write_xing", "1",
            audio_output_path
        ]
        subprocess.run(std_cmd, capture_output=True, check=True)
        if os.path.exists(raw_tts_path):
            os.remove(raw_tts_path)
    else:
        dur = clip["duration"] if clip["duration"] else 3.0
        cmd = [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-t", str(dur), "-c:a", "libmp3lame", "-ar", "44100", "-ac", "2", "-b:a", "192k", audio_output_path
        ]
        subprocess.run(cmd, capture_output=True, check=True)

def generate_ass_subtitle(output_path, text_or_segments, font_size, color, alignment, margin_h, margin_v, duration, vid_w=1920, vid_h=1080, bold=0, outline=0, shadow=0, outline_color="black", line_spacing=8):
    """ 生成标准 ASS 字幕文件，所有样式参数 100% 动态传参注入 """
    ass_color = parse_ass_color(color, default_ass="&H00FFFFFF")
    ass_outline_color = parse_ass_color(outline_color, default_ass="&H00000000")
    
    real_font_name = os.getenv("REAL_FONT_NAME", "Arial")

    ass_content = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {vid_w}
PlayResY: {vid_h}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{real_font_name},{font_size},{ass_color},&H000000FF,{ass_outline_color},&H00000000,{bold},0,0,0,100,100,0,0,1,{outline},{shadow},{alignment},{margin_h},{margin_h},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    def format_ass_time(seconds):
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = seconds % 60
        return f"{h}:{m:02d}:{s:05.2f}"

    if isinstance(text_or_segments, str):
        dialog_text = text_or_segments.replace('\n', '\\N')
        ass_content += f"Dialogue: 0,{format_ass_time(0)},{format_ass_time(duration)},Default,,0,0,0,,{dialog_text}\n"
    else:
        total_chars = sum(seg["char_count"] for seg in text_or_segments) or 1
        current_time = 0.0
        for seg in text_or_segments:
            seg_duration = (seg["char_count"] / total_chars) * duration
            start_time = format_ass_time(current_time)
            end_time = format_ass_time(current_time + seg_duration)
            dialog_text = seg["text"].replace('\n', '\\N')
            ass_content += f"Dialogue: 0,{start_time},{end_time},Default,,0,0,0,,{dialog_text}\n"
            current_time += seg_duration
            
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(ass_content)
    return output_path
    
def escape_ff_path(p):
    return p.replace('\\', '/').replace(':', '\\:')

def render_clip(index, clip, style_cfg, global_cfg, vid_w, vid_h, audio_file, output_path):
    print(f"[Render] 正在渲染第 {index} 个切片片段 [{clip['chapter']}]...", flush=True)
    
    spoken_text = clip["spoken_text"].strip()
    silent_text = clip["silent_text"].strip()
    
    if spoken_text:
        audio_dur = get_audio_duration(audio_file)
        clip_duration = audio_dur + 0.4
    else:
        clip_duration = clip["duration"] if clip["duration"] else 3.0

    cmd = ["ffmpeg", "-y"]
    
    raw_l0 = get_param(["默认素材-layer0", "素材-layer0"], clip, style_cfg, global_cfg, default="")
    raw_l1 = get_param(["挂件-layer1", "挂件素材-layer1"], clip, style_cfg, global_cfg, default="")
    
    path_l0 = resolve_asset_path(raw_l0)
    path_l1 = resolve_asset_path(raw_l1)

    if path_l0 and os.path.exists(path_l0):
        if is_video_media(path_l0):
            cmd.extend(["-stream_loop", "-1", "-i", path_l0])
        else:
            cmd.extend(["-loop", "1", "-i", path_l0])
    else:
        bg_color_raw = get_param(["默认底色-layer0", "兜底底色"], clip, style_cfg, global_cfg, default="black")
        bg_color = parse_rgb_to_ffmpeg(bg_color_raw)
        cmd.extend(["-f", "lavfi", "-i", f"color=c={bg_color}:s={vid_w}x{vid_h}"])

    has_layer1 = bool(path_l1 and os.path.exists(path_l1))
    if has_layer1:
        if is_video_media(path_l1):
            cmd.extend(["-stream_loop", "-1", "-i", path_l1])
        else:
            cmd.extend(["-loop", "1", "-i", path_l1])
    
    cmd.extend(["-i", audio_file])
    
    bgm_level = "none"
    raw_bgm = get_param(["BGM垫音", "BGM路径"], clip, style_cfg, default="")
    if raw_bgm:
        bgm_level = "chapter"
    else:
        raw_bgm = get_param(["BGM垫音", "BGM路径"], global_cfg, default="")
        if raw_bgm:
            bgm_level = "global"
    
    path_bgm = resolve_asset_path(raw_bgm)
