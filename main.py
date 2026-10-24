import os, re, csv, json, io, requests, subprocess, edge_tts, urllib.parse, asyncio
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# ==============================================================================
# ⚙️ 【主程序置顶配置区】
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
# 🔐 1. Google API 鉴权与下载中心 
# ==============================================================================
def get_google_credentials():
    if not GCP_SA_KEY:
        raise ValueError("❌ 未找到 GCP_SA_KEY 环境变量！")
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
    """精准测量音频时长"""
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
        print(f"⚠️ 无法检测音频时长 ({file_path}): {e}", flush=True)
    return 3.0

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
        print(f"⚠️ 自动翻译失败 ({text}): {e}", flush=True)
        return ""

def parse_rgb_to_ffmpeg(rgb_str):
    """将 RGB 转换为 FFmpeg 支持的十六进制颜色"""
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
    """解析像素级坐标与方位表达式（修复 +- 格式化 Bug）"""
    pos = str(pos_str).strip()
    
    if "绝对坐标:" in pos or "绝对坐标" in pos:
        raw_coords = pos.replace("绝对坐标:", "").replace("绝对坐标", "").strip()
        parts = [p.strip() for p in raw_coords.split(",") if p.strip()]
        if len(parts) >= 2:
            return f"x={parts[0]}:y={parts[1]}"
            
    elif "居中偏移:" in pos or "居中偏移" in pos:
        raw_offsets = pos.replace("居中偏移:", "").replace("居中偏移", "").strip()
        parts = [p.strip() for p in raw_offsets.split(",") if p.strip()]
        if len(parts) >= 2:
            try:
                ox = float(parts[0])
                oy = float(parts[1])
                ox_str = f"+{ox:g}" if ox > 0 else (f"{ox:g}" if ox < 0 else "")
                oy_str = f"+{oy:g}" if oy > 0 else (f"{oy:g}" if oy < 0 else "")
                return f"x=(W-w)/2{ox_str}:y=(H-h)/2{oy_str}"
            except ValueError: pass
            
    elif "底部偏移:" in pos or "底部偏移" in pos:
        raw_offsets = pos.replace("底部偏移:", "").replace("底部偏移", "").strip()
        parts = [p.strip() for p in raw_offsets.split(",") if p.strip()]
        if len(parts) >= 2:
            try:
                ox = float(parts[0])
                oy = float(parts[1])
                ox_str = f"+{ox:g}" if ox > 0 else (f"{ox:g}" if ox < 0 else "")
                oy_str = f"+{oy:g}" if oy > 0 else (f"{oy:g}" if oy < 0 else "")
                return f"x=(W-w)/2{ox_str}:y=H-h-80{oy_str}"
            except ValueError: pass

    if pos == "左上角": return "x=40:y=40"
    elif pos == "右上角": return "x=W-w-40:y=40"
    elif pos == "左下角": return "x=40:y=H-h-120"
    elif pos == "右下角": return "x=W-w-40:y=H-h-120"
    elif pos == "居中": return "x=(W-w)/2:y=(H-h)/2"
    elif pos == "顶部": return "x=(W-w)/2:y=60"
    elif pos == "底部": return "x=(W-w)/2:y=H-h-80"
    
    if is_subtitle:
        return "x=(W-w)/2:y=H-h-100"
    return "x=(W-w)/2:y=(H-h)/2"

# ==============================================================================
# 🛠️ 3. 解析与层级继承 (鲁棒级联查找)
# ==============================================================================
def get_param(keys, clip, style_cfg, global_cfg, default=""):
    """多 key 兼容与 script -> style -> global 级联查找算法"""
    if isinstance(keys, str):
        keys = [keys]
        
    if clip:
        for k in keys:
            val = str(clip.get(k, "")).strip()
            if val: return val
            
    if style_cfg:
        for k in keys:
            val = str(style_cfg.get(k, "")).strip()
            if val: return val
            
    if global_cfg:
        for k in keys:
            val = str(global_cfg.get(k, "")).strip()
            if val: return val
            
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
        
        # 将“全局配置”与“全局字幕”合并作为 unified global_cfg
        if obj_name in ["全局配置", "全局字幕"]:
            for k, v in row_dict.items():
                if v: global_cfg[k] = v
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
# 🎬 4. 素材处理与精准时长渲染
# ==============================================================================
def resolve_asset_path(asset_str):
    if not asset_str:
        return ""
        
    is_gdrive_id = bool(re.match(r'^[a-zA-Z0-9_-]{25,50}$', asset_str))
    if "drive.google.com" in asset_str or is_gdrive_id:
        dl_path = f"{TEMP_DIR}/gdrive_{abs(hash(asset_str))}.mp4"
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
            print(f"⚠️ Pixabay 检索 '{asset_str}' 失败: {e}", flush=True)
            
    return ""

async def generate_audio_or_silent(clip, style_cfg, global_cfg, audio_output_path):
    spoken_text = clip["spoken_text"].strip()
    
    # 只有当章节包含“有声文字”时才去生成语音
    if spoken_text:
        voice = get_param(["配音-音色代号", "男声代码", "女声代码"], clip, style_cfg, global_cfg, default="zh-CN-XiaoxiaoNeural")
        rate_raw = get_param(["配音-语速", "男声语速", "女声语速"], clip, style_cfg, global_cfg, default="1.0")
        
        try:
            rate_val = float(rate_raw)
            pct = int(round((rate_val - 1.0) * 100))
            rate_param = f"+{pct}%" if pct >= 0 else f"{pct}%"
        except Exception:
            rate_param = "+0%"
            
        await edge_tts.Communicate(spoken_text, voice, rate=rate_param).save(audio_output_path)
    else:
        # 无声切片：按照剧本或配置规定的秒数生成静音 MP3
        dur = clip["duration"] if clip["duration"] else 3.0
        cmd = [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-t", str(dur), "-c:a", "libmp3lame", audio_output_path
        ]
        subprocess.run(cmd, capture_output=True, check=True)

def render_clip(index, clip, style_cfg, global_cfg, font_path, audio_file, output_path):
    print(f"🎬 正在渲染第 {index} 个切片片段 [{clip['chapter']}]...", flush=True)
    
    spoken_text = clip["spoken_text"].strip()
    
    # 时长确定策略：有声文字取绝对语音时长，无声卡片取配置秒数
    if spoken_text:
        clip_duration = get_audio_duration(audio_file)
    else:
        if clip["duration"]:
            clip_duration = clip["duration"]
        else:
            dur_str = get_param(["章节秒数", "停留秒数"], clip, style_cfg, global_cfg, default="3.0")
            try: clip_duration = float(dur_str)
            except ValueError: clip_duration = 3.0
    
    cn_font_size = get_param(["文字-字号"], clip, style_cfg, global_cfg, default="45")
    font_color = get_param(["文字-颜色"], clip, style_cfg, global_cfg, default="white")
    
    # 优先展示有声文字，若没有则展示无声文字
    cn_text_raw = spoken_text if spoken_text else clip["silent_text"].strip()
    cn_text = wrap_text_by_punctuation(cn_text_raw, max_len=18)
    
    # 自动多语种翻译
    translated_en = translate_to_english(cn_text_raw)
    en_text = wrap_text_by_punctuation(translated_en, max_len=38)

    # 制作双排字幕混合文件（中文在上一排，英文在下一排，绝无重叠）
    combined_sub_text = f"{cn_text}\n{en_text}" if en_text else cn_text
    sub_file_path = os.path.abspath(f"{TEMP_DIR}/sub_{index}.txt")
    with open(sub_file_path, "w", encoding="utf-8") as f:
        f.write(combined_sub_text)

    font_path_clean = os.path.abspath(font_path).replace("\\", "/").replace(":", "\\:").replace("'", "'\\''")
    sub_clean = os.path.abspath(sub_file_path).replace("\\", "/").replace(":", "\\:").replace("'", "'\\''")

    cmd = ["ffmpeg", "-y"]
    
    raw_l0 = get_param(["默认素材-layer0", "素材-layer0", "背景素材"], clip, style_cfg, global_cfg, default="")
    raw_l1 = get_param(["挂件-layer1", "挂件素材-layer1", "贴图素材"], clip, style_cfg, global_cfg, default="")
    
    path_l0 = resolve_asset_path(raw_l0)
    path_l1 = resolve_asset_path(raw_l1)

    # Layer 0: 铺底视频/图片
    if path_l0 and os.path.exists(path_l0):
        cmd.extend(["-stream_loop", "-1", "-i", path_l0])
    else:
        bg_color = get_param(["默认底色-layer0", "兜底底色"], clip, style_cfg, global_cfg, default="black")
        cmd.extend(["-f", "lavfi", "-i", f"color=c={bg_color}:s=1920x1080"])

    has_layer1 = bool(path_l1 and os.path.exists(path_l1))
    if has_layer1:
        cmd.extend(["-stream_loop", "-1", "-i", path_l1])

    cmd.extend(["-i", audio_file])
    
    raw_bgm = get_param(["BGM垫音", "BGM路径"], clip, style_cfg, global_cfg, default="")
    path_bgm = resolve_asset_path(raw_bgm)
    has_bgm = bool(path_bgm and os.path.exists(path_bgm))
    if has_bgm:
        cmd.extend(["-stream_loop", "-1", "-i", path_bgm])

    # Layer 1: 挂件绿幕/蓝幕抠图与坐标控制
    if has_layer1:
        chroma_rgb = get_param(["挂件-扣图RGB", "挂件-抠图RGB", "绿幕扣图RGB"], clip, style_cfg, global_cfg, default="0,0,255")
        chroma_color = parse_rgb_to_ffmpeg(chroma_rgb)
        
        overlay_pos_str = get_param(["挂件-位置", "贴图位置"], clip, style_cfg, global_cfg, default="右下角")
        scale_str = get_param(["挂件-缩放", "贴图缩放"], clip, style_cfg, global_cfg, default="35%").replace("%", "").strip()
        try: scale_ratio = float(scale_str) / 100.0
        except ValueError: scale_ratio = 0.35
            
        target_overlay_w = int(1920 * scale_ratio)
        pos_expr = calculate_position_expr(overlay_pos_str, is_subtitle=False)

        filter_chain = (
            f"[0:v]setpts=PTS-STARTPTS,scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080[bg_base];"
            f"[1:v]setpts=PTS-STARTPTS,scale={target_overlay_w}:-1,colorkey={chroma_color}:0.3:0.2[l1];"
            f"[bg_base][l1]overlay={pos_expr}[bg];"
        )
        audio_in_idx = 2
        bgm_in_idx = 3
    else:
        filter_chain = (
            f"[0:v]setpts=PTS-STARTPTS,scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080[bg];"
        )
        audio_in_idx = 1
        bgm_in_idx = 2

    # Layer 2: 字幕排版渲染
    sub_pos_str = get_param(["字幕位置"], clip, style_cfg, global_cfg, default="居中")
    sub_pos_expr = calculate_position_expr(sub_pos_str, is_subtitle=True)
    
    filter_chain += (
        f"[bg]drawtext=fontfile='{font_path_clean}':textfile='{sub_clean}':fontcolor={font_color}:fontsize={cn_font_size}:line_spacing=10:{sub_pos_expr}[outv];"
    )

    # 音频混合
    bgm_vol = get_param(["BGM音量"], clip, style_cfg, global_cfg, default="0.12")
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
        "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        output_path
    ])
    
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"❌ Clip {index} FFmpeg 错误日志:\n{res.stderr}", flush=True)
        raise subprocess.CalledProcessError(res.returncode, cmd, output=res.stdout, stderr=res.stderr)

# ==============================================================================
# 🚀 5. 主程序入口
# ==============================================================================
async def main():
    os.makedirs(TEMP_DIR, exist_ok=True)
    print("🚀 开始读取 Google 表格配置...", flush=True)
    
    rules_csv = get_google_sheet_tab_csv(SHEET_URL, RULES_TAB_NAME)
    script_csv = get_google_sheet_tab_csv(SHEET_URL, SCRIPT_TAB_NAME)
    
    global_cfg, styles_cfg = parse_rules_tab(rules_csv)
    clips = parse_script_tab(script_csv)
    
    if not clips:
        raise ValueError(f"❌ 未在 Tab 页【{SCRIPT_TAB_NAME}】中解析出任何有效剧本内容。")
    
    drive_font_url = get_param(["字体", "全局字体"], {}, {}, global_cfg)
    font_path = f"{TEMP_DIR}/custom_font.ttf"
    if drive_font_url and not os.path.exists(font_path):
        print("⬇️ 正在下载 Google Drive 自定义字体...", flush=True)
        try:
            download_file_from_gdrive(drive_font_url, font_path)
        except Exception as e:
            print(f"⚠️ 下载自定义字体失败，将使用系统默认字体: {e}", flush=True)
            
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

    print("🧩 所有片段渲染完成，开始合并视频...", flush=True)
    list_file = f"{TEMP_DIR}/concat_list.txt"
    valid_segments = []
    with open(list_file, "w", encoding="utf-8") as f:
        for seg in video_segments:
            if os.path.exists(seg) and os.path.getsize(seg) > 0:
                clean_path = os.path.abspath(seg).replace("'", "'\\''")
                f.write(f"file '{clean_path}'\n")
                valid_segments.append(seg)

    if not valid_segments:
        raise RuntimeError("❌ 没有生成任何有效的视频片段！")

    concat_cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", list_file,
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        OUTPUT_VIDEO_NAME
    ]
    subprocess.run(concat_cmd, check=True)
    print("✅ 视频渲染成功导出！", flush=True)

if __name__ == "__main__":
    asyncio.run(main())
