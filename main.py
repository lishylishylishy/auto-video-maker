import os, re, csv, json, io, requests, subprocess, edge_tts, urllib.parse, asyncio, time
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
        # 优先取容器时长，其次取音频流时长
        format_dur = data.get("format", {}).get("duration")
        if format_dur and float(format_dur) > 0:
            return float(format_dur)
        for s in data.get("streams", []):
            if s.get("codec_type") == "audio" and s.get("duration"):
                return float(s["duration"])
    except Exception:
        pass
    # 终极方案：解码统计时长
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
# ✂️ 2. 文本处理与翻译
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
    for _ in range(2):
        try:
            url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=en&dt=t&q={urllib.parse.quote(text)}"
            res = requests.get(url, headers=headers, timeout=8).json()
            en_text = "".join([item[0] for item in res[0] if item[0]])
            return en_text.strip()
        except Exception:
            time.sleep(1.5)
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
    """ 把位置描述转换为ASS字幕对齐方式+边距 """
    pos = str(pos_str).strip()
    # 默认：字幕底部居中，大字居中
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
        # 标准化转码：移除开头静默帧，写入标准时长标签
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

def generate_ass_subtitle(output_path, text_or_segments, font_size, color, alignment, margin_h, margin_v, duration, vid_w=1920, vid_h=1080, line_spacing=8):
    """ 生成标准ASS字幕文件，支持全程显示(标题)或按时间轴分段显示(长字幕) """
    ass_color = color
    if not color.startswith("&H"):
        ass_color = "&H00FFFFFF" if color == "white" else "&H00000000"
    
    ass_content = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {vid_w}
PlayResY: {vid_h}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,CustomFont,{font_size},{ass_color},&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,{alignment},{margin_h},{margin_h},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    def format_ass_time(seconds):
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = seconds % 60
        return f"{h}:{m:02d}:{s:05.2f}"

    if isinstance(text_or_segments, str):
        # 针对无声大字：全程显示
        dialog_text = text_or_segments.replace('\n', '\\N')
        ass_content += f"Dialogue: 0,{format_ass_time(0)},{format_ass_time(duration)},Default,,0,0,0,,{dialog_text}\n"
    else:
        # 针对有声字幕：按字数比例分配时间轴，先后显示
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

def render_clip(index, clip, style_cfg, global_cfg, font_path, vid_w, vid_h, audio_file, output_path):
    print(f"[Render] 正在渲染第 {index} 个切片片段 [{clip['chapter']}]...", flush=True)
    
    spoken_text = clip["spoken_text"].strip()
    silent_text = clip["silent_text"].strip()
    
    # 时长严格控制：有声取真实音频时长+0.4s余量，无声取script秒数
    if spoken_text:
        audio_dur = get_audio_duration(audio_file)
        clip_duration = audio_dur + 0.4
    else:
        clip_duration = clip["duration"] if clip["duration"] else 3.0

    cmd = ["ffmpeg", "-y"]
    
    # 素材加载
    raw_l0 = get_param(["默认素材-layer0", "素材-layer0"], clip, style_cfg, global_cfg, default="")
    raw_l1 = get_param(["挂件-layer1", "挂件素材-layer1"], clip, style_cfg, global_cfg, default="")
    
    path_l0 = resolve_asset_path(raw_l0)
    path_l1 = resolve_asset_path(raw_l1)

    # Layer0 背景
    if path_l0 and os.path.exists(path_l0):
        if is_video_media(path_l0):
            cmd.extend(["-stream_loop", "-1", "-i", path_l0])
        else:
            cmd.extend(["-loop", "1", "-i", path_l0])
    else:
        bg_color_raw = get_param(["默认底色-layer0", "兜底底色"], clip, style_cfg, global_cfg, default="black")
        bg_color = parse_rgb_to_ffmpeg(bg_color_raw)
        cmd.extend(["-f", "lavfi", "-i", f"color=c={bg_color}:s={vid_w}x{vid_h}"])

    # Layer1 挂件
    has_layer1 = bool(path_l1 and os.path.exists(path_l1))
    if has_layer1:
        if is_video_media(path_l1):
            cmd.extend(["-stream_loop", "-1", "-i", path_l1])
        else:
            cmd.extend(["-loop", "1", "-i", path_l1])
    
    cmd.extend(["-i", audio_file])
    
    # BGM 分级循环
    bgm_level = "none"
    raw_bgm = get_param(["BGM垫音", "BGM路径"], clip, style_cfg, default="")
    if raw_bgm:
        bgm_level = "chapter"
    else:
        raw_bgm = get_param(["BGM垫音", "BGM路径"], global_cfg, default="")
        if raw_bgm:
            bgm_level = "global"
    
    path_bgm = resolve_asset_path(raw_bgm)
    has_bgm = bool(path_bgm and os.path.exists(path_bgm))
    if has_bgm:
        if bgm_level == "global":
            cmd.extend(["-stream_loop", "-1", "-i", path_bgm])
        else:
            cmd.extend(["-i", path_bgm])

    # 构建滤镜链
    filter_chains = []
    # 背景基础处理
    filter_chains.append(f"[0:v]setpts=PTS-STARTPTS,scale={vid_w}:{vid_h}:force_original_aspect_ratio=increase,crop={vid_w}:{vid_h}[bg_base]")
    
    # 挂件合成
    if has_layer1:
        chroma_rgb = get_param(["挂件-扣图RGB", "绿幕扣图RGB"], clip, style_cfg, global_cfg, default="0,0,255")
        chroma_color = parse_rgb_to_ffmpeg(chroma_rgb)
        overlay_pos_str = get_param(["挂件-位置", "贴图位置"], clip, style_cfg, global_cfg, default="居中")
        scale_str = get_param(["挂件-缩放", "贴图缩放"], clip, style_cfg, global_cfg, default="50%").replace("%", "").strip()
        try: scale_ratio = float(scale_str) / 100.0
        except ValueError: scale_ratio = 0.50
        target_overlay_w = int(vid_w * scale_ratio)
        pos_expr = ""
        pos = overlay_pos_str.strip()
        if pos in ["居中", "居中居中"]:
            pos_expr = "x=(W-w)/2:y=(H-h)/2"
        elif "底部" in pos:
            pos_expr = "x=(W-w)/2:y=H-h-80"
        elif "绝对坐标" in pos:
            match = re.search(r'(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)', pos)
            if match:
                pos_expr = f"x={match.group(1)}:y={match.group(2)}"
        elif "居中偏" in pos or "居中偏移" in pos:
            match = re.search(r'(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)', pos)
            if match:
                ox, oy = float(match.group(1)), float(match.group(2))
                pos_expr = f"x=(W-w)/2+{ox:g}:y=(H-h)/2+{oy:g}"
        else:
            pos_expr = "x=(W-w)/2:y=(H-h)/2"
        
        filter_chains.append(f"[1:v]setpts=PTS-STARTPTS,scale={target_overlay_w}:-1,colorkey={chroma_color}:0.3:0.2[l1]")
        filter_chains.append(f"[bg_base][l1]overlay={pos_expr}[bg_with_layer]")
        curr_v = "[bg_with_layer]"
        audio_in_idx = 2
        bgm_in_idx = 3
    else:
        curr_v = "[bg_base]"
        audio_in_idx = 1
        bgm_in_idx = 2

    # 无声大字（标题）
    if silent_text:
        title_text = wrap_text_by_punctuation(silent_text, max_len=14)
        title_size = get_param(["文字-字号"], clip, style_cfg, global_cfg, default="150")
        title_color = sanitize_color(get_param(["文字-颜色"], clip, style_cfg, global_cfg, default="yellow"))
        title_pos_str = get_param(["字幕位置"], clip, style_cfg, global_cfg, default="居中")
        align, mh, mv = calc_ass_position(title_pos_str, is_subtitle=False)
        
        title_ass_path = os.path.abspath(f"{TEMP_DIR}/title_{index}.ass")
        generate_ass_subtitle(title_ass_path, title_text, int(title_size), title_color, align, mh, mv, clip_duration, vid_w, vid_h)
        filter_chains.append(f"{curr_v}ass={title_ass_path}[v_with_title]")
        curr_v = "[v_with_title]"

    # 有声字幕（严格仅用全局配置）
# 有声字幕（严格仅用全局配置）
    if spoken_text:
        # 1. 先将长句切分为短句（中文每行最大20字，避免出画）
        cn_lines = wrap_text_by_punctuation(spoken_text, max_len=21).split('\n')
        is_bilingual = (get_param(["双语字幕"], global_cfg, default="开启").lower() in ["开启", "是", "true", "1", "on"])
        
        sub_segments = []
        for cn_line in cn_lines:
            if not cn_line.strip():
                continue
            if is_bilingual:
                # 2. 对切分好的中文短句单独进行英文翻译
                en_line = translate_to_english(cn_line)
                # 英文强制单行
                en_sub = en_line
                # 中文在上，英文在下
                sub_text = f"{cn_line}\\N{en_sub}"
            else:
                sub_text = cn_line
                
            sub_segments.append({
                "text": sub_text, 
                "char_count": len(cn_line)
            })
        
        sub_size = get_param(["文字-字号"], global_cfg, default="30")
        sub_color = sanitize_color(get_param(["文字-颜色"], global_cfg, default="white"))
        sub_pos_str = get_param(["字幕位置"], global_cfg, default="底部居中")
        align, mh, mv = calc_ass_position(sub_pos_str, is_subtitle=True)
        
        sub_ass_path = os.path.abspath(f"{TEMP_DIR}/sub_{index}.ass")
        # 传入 sub_segments 数组，由 generate_ass_subtitle 自动计算分段排期
        generate_ass_subtitle(sub_ass_path, sub_segments, int(sub_size), sub_color, align, mh, mv, clip_duration, vid_w, vid_h)
        
        filter_chains.append(f"{curr_v}ass={sub_ass_path}[outv]")
    else:
        filter_chains.append(f"{curr_v}null[outv]")

    # 音频混合
    bgm_vol = get_param(["BGM音量"], clip, style_cfg, global_cfg, default="1.0")
    try: vol_val = float(bgm_vol)
    except ValueError: vol_val = 1.0
    
    if has_bgm:
        filter_chains.append(f"[{audio_in_idx}:a]aformat=sample_rates=44100:channel_layouts=stereo[a_tts]")
        filter_chains.append(f"[{bgm_in_idx}:a]aformat=sample_rates=44100:channel_layouts=stereo,volume={vol_val}[a_bgm]")
        filter_chains.append("[a_tts][a_bgm]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[outa]")
    else:
        filter_chains.append(f"[{audio_in_idx}:a]aformat=sample_rates=44100:channel_layouts=stereo[outa]")

    filter_complex = ";".join(filter_chains)
    
    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", "[outv]", "-map", "[outa]",
        "-t", f"{clip_duration:.2f}",
        "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "44100", "-ac", "2",
        output_path
    ])
    
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"Clip {index} FFmpeg 错误日志:\n{res.stderr}", flush=True)
        raise subprocess.CalledProcessError(res.returncode, cmd, output=res.stdout, stderr=res.stderr)

# ==============================================================================
# 🚀 5. 主程序入口
# ==============================================================================
async def main():
    os.makedirs(TEMP_DIR, exist_ok=True)
    print("开始读取 Google 表格配置...", flush=True)
    
    rules_csv = get_google_sheet_tab_csv(SHEET_URL, RULES_TAB_NAME)
    script_csv = get_google_sheet_tab_csv(SHEET_URL, SCRIPT_TAB_NAME)
    
    global_cfg, styles_cfg = parse_rules_tab(rules_csv)
    clips = parse_script_tab(script_csv)
    
    if not clips:
        raise ValueError(f"未在 Tab 页【{SCRIPT_TAB_NAME}】中解析出任何有效剧本内容。")
    
    # 字体加载：强制校验，失败直接终止
    # 1. 直接从 Google Sheets 拿到字体网盘链接
    drive_font_url = get_param(["字体"], global_cfg, default="")
    if not drive_font_url:
        raise ValueError("请在 Google Sheets 的全局配置中填写字体的 Google Drive 链接")

    font_path = os.path.abspath(f"{TEMP_DIR}/custom_font.ttf")
    print(f"正在从网盘下载字体...", flush=True)

    # 2. 直接按网盘链接下载
    download_file_from_gdrive(drive_font_url, font_path)

    # 3. 简单拦一下错（只要文件下出来了且不是0KB空文件，就放行）
    if not os.path.exists(font_path) or os.path.getsize(font_path) < 1000:
        raise RuntimeError("字体下载失败，请检查你的 Google Drive 链接是否公开且正确！")
    
    # 提前注册字体路径到环境变量，确保ASS滤镜能找到
    os.environ["FC_CONFIG_DIR"] = TEMP_DIR
    # 生成fonts.conf让FFmpeg识别自定义字体
    font_conf = f"""<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <dir>{os.path.dirname(font_path)}</dir>
  <match target="pattern">
    <test qual="any" name="family"><string>CustomFont</string></test>
    <edit name="file" mode="assign"><string>{font_path}</string></edit>
  </match>
</fontconfig>
"""
    with open(f"{TEMP_DIR}/fonts.conf", "w", encoding="utf-8") as f:
        f.write(font_conf)
    os.environ["FONTCONFIG_FILE"] = os.path.abspath(f"{TEMP_DIR}/fonts.conf")

    # 解析全局视频分辨率，支持表格自定义
    resolution_raw = get_param(["视频分辨率"], global_cfg, default="1920x1080")
    try:
        vid_w, vid_h = [int(x) for x in resolution_raw.lower().split("x")]
    except Exception:
        vid_w, vid_h = 1920, 1080

    # 渲染所有片段
    video_segments = []
    for idx, clip in enumerate(clips, 1):
        v_seg = f"{TEMP_DIR}/clip_{idx}.mp4"
        a_seg = f"{TEMP_DIR}/clip_{idx}.mp3"
        style_cfg = styles_cfg.get(clip["chapter"], {})
        await generate_audio_or_silent(clip, style_cfg, global_cfg, a_seg)
        render_clip(idx, clip, style_cfg, global_cfg, font_path, vid_w, vid_h, audio_file=a_seg, output_path=v_seg)
        video_segments.append(v_seg)


    print("所有片段渲染完成，开始合并视频...", flush=True)
    
    # 改用concat滤镜拼接，彻底解决时间戳错位导致的音画章节偏移
    concat_inputs = []
    filter_parts = []
    for i, seg in enumerate(video_segments):
        concat_inputs.extend(["-i", seg])
        filter_parts.append(f"[{i}:v][{i}:a]")
    
    concat_filter = "".join(filter_parts) + f"concat=n={len(video_segments)}:v=1:a=1[outv][outa]"
    
    final_cmd = ["ffmpeg", "-y"] + concat_inputs + [
        "-filter_complex", concat_filter,
        "-map", "[outv]", "-map", "[outa]",
        "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "44100", "-ac", "2",
        OUTPUT_VIDEO_NAME
    ]
    
    subprocess.run(final_cmd, check=True)
    print("视频渲染成功导出！", flush=True)

if __name__ == "__main__":
    asyncio.run(main())
