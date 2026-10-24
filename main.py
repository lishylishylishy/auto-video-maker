import os, re, csv, json, io, requests, subprocess, edge_tts, urllib.parse, asyncio
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# ==============================================================================
# ⚙️ 【主程序置顶配置区】
# ==============================================================================
RULES_TAB_NAME  = "config"         # 🌟 [*] config 控制页签
SCRIPT_TAB_NAME = os.getenv("TARGET_TAB", "script") # 🌟 [*] script 脚本页签
SHEET_URL       = "https://docs.google.com/spreadsheets/d/1Y8YOEl5G2HV4C3WLZz9ttEEoByuWKGLqh_nmWvcb-_M/edit"

PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "") 
GCP_SA_KEY      = os.getenv("GCP_SA_KEY", "")      

TEMP_DIR          = "temp_workspace"
OUTPUT_VIDEO_NAME = "final_output.mp4"

# 🌟 [*] 与 config 表格 A 列严格一致的 5 个章节名称
VALID_CHAPTERS = {"片头", "转场", "结尾", "男配音", "女配音"}

# ==============================================================================
# 🔐 1. Google API 鉴权与下载中心
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
    """ 🌟 [*] 精准测量 TTS 生成的 MP3 时长，绝对防止提前断音 """
    try:
        cmd = [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprintwrappers=1:nokey=1", file_path
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        val = float(res.stdout.strip())
        if val > 0:
            return val
    except Exception as e:
        print(f"无法检测音频时长 ({file_path}): {e}", flush=True)
    return 3.0

def is_video_media(filepath):
    """ 🌟 [*] 判断素材是视频还是静态图片 """
    try:
        cmd = [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
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
# ✂️ 2. 标点符号断句、翻译与格式化算法
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
    try:
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=en&dt=t&q={urllib.parse.quote(text)}"
        res = requests.get(url, timeout=5).json()
        en_text = "".join([item[0] for item in res[0] if item[0]])
        return en_text.strip()
    except Exception as e:
        print(f"自动翻译失败 ({text}): {e}", flush=True)
        return ""

def sanitize_color(color_str):
    """ 🌟 [*] 修复 grey 字母拼写与 RGB 色值转换 """
    if not color_str or not str(color_str).strip():
        return "white"
    c = str(color_str).strip()
    if c.lower() == "grey":
        return "gray"
    if "," in c:
        return parse_rgb_to_ffmpeg(c)
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

def calculate_position_expr(pos_str, is_subtitle=False):
    """ 🌟 [*] 全能坐标解析器（支持 居中、底部居中、居中偏移:X,Y、绝对坐标:X,Y） """
    pos = str(pos_str).strip()
    if not pos:
        return "x=(W-w)/2:y=H-h-80" if is_subtitle else "x=(W-w)/2:y=(H-h)/2"

    if "绝对坐标" in pos:
        match = re.search(r'(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)', pos)
        if match:
            return f"x={match.group(1)}:y={match.group(2)}"

    if "居中偏" in pos or "居中偏移" in pos:
        match = re.search(r'(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)', pos)
        if match:
            ox, oy = float(match.group(1)), float(match.group(2))
            ox_str = f"+{ox:g}" if ox > 0 else (f"{ox:g}" if ox < 0 else "")
            oy_str = f"+{oy:g}" if oy > 0 else (f"{oy:g}" if oy < 0 else "")
            return f"x=(W-w)/2{ox_str}:y=(H-h)/2{oy_str}"

    if "底部偏" in pos or "底部偏移" in pos:
        match = re.search(r'(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)', pos)
        if match:
            ox, oy = float(match.group(1)), float(match.group(2))
            ox_str = f"+{ox:g}" if ox > 0 else (f"{ox:g}" if ox < 0 else "")
            oy_str = f"+{oy:g}" if oy > 0 else (f"{oy:g}" if oy < 0 else "")
            return f"x=(W-w)/2{ox_str}:y=H-h-80{oy_str}"

    if pos in ["居中", "居中居中"]: return "x=(W-w)/2:y=(H-h)/2"
    if "底部" in pos: return "x=(W-w)/2:y=H-h-80"
    if pos == "顶部": return "x=(W-w)/2:y=60"
    if pos == "左上角": return "x=40:y=40"
    if pos == "右上角": return "x=W-w-40:y=40"
    if pos == "左下角": return "x=40:y=H-h-120"
    if pos == "右下角": return "x=W-w-40:y=H-h-120"

    return "x=(W-w)/2:y=H-h-80" if is_subtitle else "x=(W-w)/2:y=(H-h)/2"

# ==============================================================================
# 🛠️ 3. 解析与 3 层级联继承算法 (彻底杜绝硬编码覆盖)
# ==============================================================================
def get_param(keys, *dict_layers, default=""):
    """
    🌟 [*] 核心级联函数：传入 (*dict_layers) 优先级队列
    顺序：script 当前行 -> config 章节控制行 -> config 全局配置行
    只要在某个层级找到有有效值，立即使用，绝不遮蔽！
    """
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
    """ 🌟 [*] 读取 config 页签：精准区分 全局配置 行 与 章节控制 行 """
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
    """ 🌟 [*] 读取 script 页签 """
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
# 🎬 4. 素材处理与精准时长渲染
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
        except Exception as e:
            print(f"Pixabay 检索 '{asset_str}' 失败: {e}", flush=True)
            
    return ""

async def generate_audio_or_silent(clip, style_cfg, global_cfg, audio_output_path):
    spoken_text = clip["spoken_text"].strip()
    
    if spoken_text:
        # 🌟 [*] 音色与语速读取：script -> config 章节行 -> config 全局配置行
        voice = get_param(["配音-音色代号"], clip, style_cfg, global_cfg, default="zh-CN-XiaoxiaoNeural")
        rate_raw = get_param(["配音-语速"], clip, style_cfg, global_cfg, default="1.0")
        
        try:
            rate_val = float(rate_raw)
            pct = int(round((rate_val - 1.0) * 100))
            rate_param = f"+{pct}%" if pct >= 0 else f"{pct}%"
        except Exception:
            rate_param = "+0%"
            
        await edge_tts.Communicate(spoken_text, voice, rate=rate_param).save(audio_output_path)
    else:
        # 🌟 [*] 秒数读取：script 章节秒数 -> config 停留秒数 -> 全局配置
        dur = clip["duration"] if clip["duration"] else None
        if not dur:
            dur_str = get_param(["章节秒数", "停留秒数"], clip, style_cfg, global_cfg, default="3.0")
            try: dur = float(dur_str)
            except ValueError: dur = 3.0
            
        cmd = [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-t", str(dur), "-c:a", "libmp3lame", "-ar", "44100", "-ac", "2", audio_output_path
        ]
        subprocess.run(cmd, capture_output=True, check=True)

def render_clip(index, clip, style_cfg, global_cfg, font_path, audio_file, output_path):
    print(f"[Render] 正在渲染第 {index} 个切片片段 [{clip['chapter']}]...", flush=True)
    
    spoken_text = clip["spoken_text"].strip()
    silent_text = clip["silent_text"].strip()
    
    # 🌟 [*] 时长判定核心：有声文字固定取语音实际长度；无声卡片依次级联读取表格秒数
    if spoken_text:
        clip_duration = get_audio_duration(audio_file) + 0.4
    else:
        if clip["duration"]:
            clip_duration = clip["duration"]
        else:
            dur_str = get_param(["章节秒数", "停留秒数"], clip, style_cfg, global_cfg, default="3.0")
            try: clip_duration = float(dur_str)
            except ValueError: clip_duration = 3.0

    font_path_clean = os.path.abspath(font_path).replace("\\", "/").replace(":", "\\:").replace("'", "'\\''")

    cmd = ["ffmpeg", "-y"]
    
    # 🌟 [*] 图层素材查找：script 行 -> config 章节行 -> config 全局配置行
    raw_l0 = get_param(["默认素材-layer0", "素材-layer0"], clip, style_cfg, global_cfg, default="")
    raw_l1 = get_param(["挂件-layer1", "挂件素材-layer1"], clip, style_cfg, global_cfg, default="")
    
    path_l0 = resolve_asset_path(raw_l0)
    path_l1 = resolve_asset_path(raw_l1)

    # Layer 0 背景
    if path_l0 and os.path.exists(path_l0):
        if is_video_media(path_l0):
            cmd.extend(["-stream_loop", "-1", "-i", path_l0])
        else:
            cmd.extend(["-loop", "1", "-i", path_l0])
    else:
        bg_color_raw = get_param(["默认底色-layer0", "兜底底色"], clip, style_cfg, global_cfg, default="black")
        bg_color = sanitize_color(bg_color_raw)
        cmd.extend(["-f", "lavfi", "-i", f"color=c={bg_color}:s=1920x1080"])

    # Layer 1 挂件
    has_layer1 = bool(path_l1 and os.path.exists(path_l1))
    if has_layer1:
        if is_video_media(path_l1):
            cmd.extend(["-stream_loop", "-1", "-i", path_l1])
        else:
            cmd.extend(["-loop", "1", "-i", path_l1])

    cmd.extend(["-i", audio_file])
    
    raw_bgm = get_param(["BGM垫音", "BGM路径"], clip, style_cfg, global_cfg, default="")
    path_bgm = resolve_asset_path(raw_bgm)
    has_bgm = bool(path_bgm and os.path.exists(path_bgm))
    if has_bgm:
        if is_video_media(path_bgm):
            cmd.extend(["-stream_loop", "-1", "-i", path_bgm])
        else:
            cmd.extend(["-i", path_bgm])

    # 🌟 [*] 挂件抠图/缩放/位置控制：script -> config 章节行 -> config 全局配置行
    if has_layer1:
        chroma_rgb = get_param(["挂件-扣图RGB", "绿幕扣图RGB"], clip, style_cfg, global_cfg, default="0,0,255")
        chroma_color = parse_rgb_to_ffmpeg(chroma_rgb)
        
        overlay_pos_str = get_param(["挂件-位置", "贴图位置"], clip, style_cfg, global_cfg, default="居中")
        scale_str = get_param(["挂件-缩放", "贴图缩放"], clip, style_cfg, global_cfg, default="50%").replace("%", "").strip()
        try: scale_ratio = float(scale_str) / 100.0
        except ValueError: scale_ratio = 0.50
            
        target_overlay_w = int(1920 * scale_ratio)
        pos_expr = calculate_position_expr(overlay_pos_str, is_subtitle=False)

        filter_chain = (
            f"[0:v]setpts=PTS-STARTPTS,scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080[bg_base];"
            f"[1:v]setpts=PTS-STARTPTS,scale={target_overlay_w}:-1,colorkey={chroma_color}:0.3:0.2[l1];"
            f"[bg_base][l1]overlay={pos_expr}[bg_curr];"
        )
        audio_in_idx = 2
        bgm_in_idx = 3
    else:
        filter_chain = (
            f"[0:v]setpts=PTS-STARTPTS,scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080[bg_curr];"
        )
        audio_in_idx = 1
        bgm_in_idx = 2

    curr_v = "[bg_curr]"

    # --------------------------------------------------------------------------
    # 🌟 A. 渲染【无声文字】（标题/PPT大字）
    # 样式优先级：script -> config 章节行 (如片头 150号字/yellow) -> 全局配置
    # --------------------------------------------------------------------------
    if silent_text:
        title_formatted = wrap_text_by_punctuation(silent_text, max_len=14)
        title_file = os.path.abspath(f"{TEMP_DIR}/title_{index}.txt")
        with open(title_file, "w", encoding="utf-8") as f:
            f.write(title_formatted)
        title_clean = title_file.replace("\\", "/").replace(":", "\\:").replace("'", "'\\''")
        
        title_size = get_param(["文字-字号"], clip, style_cfg, global_cfg, default="150")
        title_color = sanitize_color(get_param(["文字-颜色"], clip, style_cfg, global_cfg, default="yellow"))
        title_pos_str = get_param(["字幕位置", "挂件-位置"], clip, style_cfg, global_cfg, default="居中")
        title_pos_expr = calculate_position_expr(title_pos_str, is_subtitle=False)
        
        filter_chain += f"{curr_v}drawtext=fontfile='{font_path_clean}':textfile='{title_clean}':fontcolor={title_color}:fontsize={title_size}:line_spacing=15:{title_pos_expr}[v_title];"
        curr_v = "[v_title]"

    # --------------------------------------------------------------------------
    # 🌟 B. 渲染【有声文字】（口播配音字幕）
    # 样式优先级：script -> config 章节行 -> config 全局配置 (30号字/white/底部居中)
    # --------------------------------------------------------------------------
    if spoken_text:
        cn_sub = wrap_text_by_punctuation(spoken_text, max_len=20)
        
        is_bilingual = (get_param(["双语字幕"], clip, style_cfg, global_cfg, default="开启").lower() in ["开启", "是", "true", "1", "on"])
        if is_bilingual:
            en_raw = translate_to_english(spoken_text)
            en_sub = wrap_text_by_punctuation(en_raw, max_len=40)
            sub_text_content = f"{cn_sub}\n{en_sub}" if en_sub else cn_sub
        else:
            sub_text_content = cn_sub
            
        sub_file = os.path.abspath(f"{TEMP_DIR}/sub_{index}.txt")
        with open(sub_file, "w", encoding="utf-8") as f:
            f.write(sub_text_content)
        sub_clean = sub_file.replace("\\", "/").replace(":", "\\:").replace("'", "'\\''")
        
        sub_size = get_param(["文字-字号"], clip, style_cfg, global_cfg, default="30")
        sub_color = sanitize_color(get_param(["文字-颜色"], clip, style_cfg, global_cfg, default="white"))
        sub_pos_str = get_param(["字幕位置"], clip, style_cfg, global_cfg, default="底部居中")
        sub_pos_expr = calculate_position_expr(sub_pos_str, is_subtitle=True)
        
        filter_chain += f"{curr_v}drawtext=fontfile='{font_path_clean}':textfile='{sub_clean}':fontcolor={sub_color}:fontsize={sub_size}:line_spacing=8:{sub_pos_expr}[outv];"
    else:
        filter_chain += f"{curr_v}null[outv];"

    bgm_vol = get_param(["BGM音量"], clip, style_cfg, global_cfg, default="1.0")
    if has_bgm and bgm_vol:
        filter_chain += f"[{audio_in_idx}:a]volume=1.0[a_tts];[{bgm_in_idx}:a]volume={bgm_vol}[a_bgm];[a_tts][a_bgm]amix=inputs=2:duration=first[outa]"
    elif has_bgm:
        filter_chain += f"[{audio_in_idx}:a]volume=1.0[a_tts];[{bgm_in_idx}:a]volume=1.0[a_bgm];[a_tts][a_bgm]amix=inputs=2:duration=first[outa]"
    else:
        filter_chain += f"[{audio_in_idx}:a]volume=1.0[outa]"

    cmd.extend([
        "-filter_complex", filter_chain,
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
    
    drive_font_url = get_param(["字体"], {}, {}, global_cfg)
    font_path = f"{TEMP_DIR}/custom_font.ttf"
    if drive_font_url and not os.path.exists(font_path):
        print("正在下载 Google Drive 自定义字体...", flush=True)
        try:
            download_file_from_gdrive(drive_font_url, font_path)
        except Exception as e:
            print(f"下载自定义字体失败，将使用系统默认字体: {e}", flush=True)
            
    if not os.path.exists(font_path) or os.path.getsize(font_path) == 0:
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

    video_segments = []
    
    for idx, clip in enumerate(clips, 1):
        v_seg = f"{TEMP_DIR}/clip_{idx}.mp4"
        a_seg = f"{TEMP_DIR}/clip_{idx}.mp3"
        
        style_cfg = styles_cfg.get(clip["chapter"], {})
        
        await generate_audio_or_silent(clip, style_cfg, global_cfg, a_seg)
        render_clip(idx, clip, style_cfg, global_cfg, font_path, audio_file=a_seg, output_path=v_seg)
        video_segments.append(v_seg)

    print("所有片段渲染完成，开始合并视频...", flush=True)
    list_file = f"{TEMP_DIR}/concat_list.txt"
    valid_segments = []
    with open(list_file, "w", encoding="utf-8") as f:
        for seg in video_segments:
            if os.path.exists(seg) and os.path.getsize(seg) > 0:
                clean_path = os.path.abspath(seg).replace("'", "'\\''")
                f.write(f"file '{clean_path}'\n")
                valid_segments.append(seg)

    if not valid_segments:
        raise RuntimeError("没有生成任何有效的视频片段！")

    concat_cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", list_file,
        "-c", "copy",
        OUTPUT_VIDEO_NAME
    ]
    subprocess.run(concat_cmd, check=True)
    print("视频渲染成功导出！", flush=True)

if __name__ == "__main__":
    asyncio.run(main())
