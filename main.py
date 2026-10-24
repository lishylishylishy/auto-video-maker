import os, re, csv, json, io, requests, subprocess, edge_tts, urllib.parse
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
    """精准测量音频时长，防止 FFmpeg 卡死死循环"""
    try:
        cmd = [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprintwrappers=1:nokey=1", file_path
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(res.stdout.strip())
    except Exception as e:
        print(f"⚠️ 无法检测音频时长，默认设为 3 秒: {e}", flush=True)
        return 3.0

# ==============================================================================
# ✂️ 2. 标点符号断句、翻译与格式化算法 (新增位置/颜色解析)
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
    if not rgb_str or not rgb_str.strip(): return "0x0000FF"
    cleaned = rgb_str.replace("，", ",").strip()
    parts = [p.strip() for p in cleaned.split(",")]
    if len(parts) == 3:
        try:
            r, g, b = [int(p) for p in parts]
            return f"0x{r:02X}{g:02X}{b:02X}"
        except ValueError: pass
    return "0x0000FF"

def calculate_position_expr(pos_str, default_y_bottom=True):
    """解析像素级坐标与方位表达式"""
    pos = str(pos_str).strip()
    if "绝对坐标:" in pos:
        coords = pos.replace("绝对坐标:", "").split(",")
        return f"x={coords[0].strip()}:y={coords[1].strip()}"
    elif "居中偏移:" in pos:
        offsets = pos.replace("居中偏移:", "").split(",")
        return f"x=(W-w)/2+{offsets[0].strip()}:y=(H-h)/2+{offsets[1].strip()}"
    elif "底部偏移:" in pos:
        offsets = pos.replace("底部偏移:", "").split(",")
        return f"x=(W-w)/2+{offsets[0].strip()}:y=H-h-80+{offsets[1].strip()}"
    
    if pos == "左上角": return "x=40:y=40"
    elif pos == "右上角": return "x=W-w-40:y=40"
    elif pos == "左下角": return "x=40:y=H-h-120"
    elif pos == "右下角": return "x=W-w-40:y=H-h-120"
    elif pos == "居中": return "x=(W-w)/2:y=(H-h)/2"
    elif pos == "顶部": return "x=(W-w)/2:y=60"
    elif pos == "底部": return "x=(W-w)/2:y=H-h-80"
    return "x=(W-w)/2:y=H-h-80" if default_y_bottom else "x=(W-w)/2:y=(H-h)/2"

# ==============================================================================
# 🛠️ 3. 解析与层级继承
# ==============================================================================
def get_cascade_param(key, script_clip, style_cfg, global_cfg, default=""):
    if script_clip and script_clip.get(key):
        return str(script_clip[key]).strip()
    if style_cfg and style_cfg.get(key):
        return str(style_cfg[key]).strip()
    if global_cfg and global_cfg.get(key):
        return str(global_cfg[key]).strip()
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
        
        # 核心修复：将“全局配置”和“全局字幕”合并作为统一的 global_cfg 兜底
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
        
        bg_layer0 = row_dict.get("默认素材-layer0", "").strip()
        bg_layer1 = row_dict.get("挂件素材-layer1", "").strip()
        duration_str = row_dict.get("章节秒数", "").strip()
        silent_text = row_dict.get("无声文字", "").strip()
        spoken_text = row_dict.get("有声文字", "").strip()
        
        if not spoken_text and not silent_text and not bg_layer0 and not bg_layer1:
            continue
            
        try:
            duration = float(duration_str) if duration_str else None
        except ValueError:
            duration = None

        # 修复点 1：将该行的全部列数据保留，确保 get_cascade_param 能读到剧本中覆盖的控制列
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
    spoken_text = clip["spoken_text"] or clip["silent_text"]
    
    if spoken_text:
        voice = get_cascade_param("配音-音色代号", clip, style_cfg, global_cfg, default="zh-CN-XiaoxiaoNeural")
        rate_raw = get_cascade_param("配音-语速", clip, style_cfg, global_cfg, default="1.0")
        
        try:
            rate_val = float(rate_raw)
            pct = int(round((rate_val - 1.0) * 100))
            rate_param = f"+{pct}%" if pct >= 0 else f"{pct}%"
        except Exception:
            rate_param = "+0%"
            
        await edge_tts.Communicate(spoken_text, voice, rate=rate_param).save(audio_output_path)
    else:
        dur = clip["duration"] if clip["duration"] else 3.0
        cmd = [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-t", str(dur), "-c:a", "aac", audio_output_path
        ]
        subprocess.run(cmd, capture_output=True, check=True)

def render_clip(index, clip, style_cfg, global_cfg, font_path, audio_file, output_path):
    print(f"🎬 正在渲染第 {index} 个切片片段 [{clip['chapter']}]...", flush=True)
    
    # 测量音频精确时长
    clip_duration = get_audio_duration(audio_file)
    
    cn_font_size = get_cascade_param("文字-字号", clip, style_cfg, global_cfg, default="45")
    font_color = get_cascade_param("文字-颜色", clip, style_cfg, global_cfg, default="white")
    
    # --- 强制双语字幕逻辑 ---
    cn_text_raw = clip["silent_text"] if clip["silent_text"] else clip["spoken_text"]
    cn_text = wrap_text_by_punctuation(cn_text_raw, max_len=18)
    
    # 无论如何都自动翻译并生成英文字幕
    translated_en = translate_to_english(cn_text_raw)
    en_text = wrap_text_by_punctuation(translated_en, max_len=38)

    # 写入中英文临时文件
    cn_sub_file = os.path.abspath(f"{TEMP_DIR}/sub_{index}_cn.txt")
    with open(cn_sub_file, "w", encoding="utf-8") as f:
        f.write(cn_text)
        
    en_sub_file = os.path.abspath(f"{TEMP_DIR}/sub_{index}_en.txt")
    with open(en_sub_file, "w", encoding="utf-8") as f:
        f.write(en_text)

    font_path_clean = os.path.abspath(font_path).replace("\\", "/").replace(":", "\\:").replace("'", "'\\''")
    cn_sub_clean = os.path.abspath(cn_sub_file).replace("\\", "/").replace(":", "\\:").replace("'", "'\\''")
    en_sub_clean = os.path.abspath(en_sub_file).replace("\\", "/").replace(":", "\\:").replace("'", "'\\''")

    cmd = ["ffmpeg", "-y"]
    
    # 读取素材
    raw_l0 = get_cascade_param("默认素材-layer0", clip, style_cfg, global_cfg, default="")
    raw_l1 = get_cascade_param("挂件素材-layer1", clip, style_cfg, global_cfg, default="")
    
    path_l0 = resolve_asset_path(raw_l0)
    path_l1 = resolve_asset_path(raw_l1)

    # 组装基础画面
    if path_l0 and os.path.exists(path_l0):
        cmd.extend(["-stream_loop", "-1", "-i", path_l0])
    else:
        cmd.extend(["-f", "lavfi", "-i", "color=c=black:s=1920x1080"])

    has_layer1 = bool(path_l1 and os.path.exists(path_l1))
    if has_layer1:
        cmd.extend(["-stream_loop", "-1", "-i", path_l1])

    cmd.extend(["-i", audio_file])
    
    raw_bgm = get_cascade_param("BGM垫音", clip, style_cfg, global_cfg, default="")
    path_bgm = resolve_asset_path(raw_bgm)
    has_bgm = bool(path_bgm and os.path.exists(path_bgm))
    if has_bgm:
        cmd.extend(["-stream_loop", "-1", "-i", path_bgm])

    # --- 修复后的挂件定位与绿幕抠图逻辑 ---
    if has_layer1:
        # 注意这里已经改成了匹配表格的“挂件-扣图RGB”、“挂件-位置”、“挂件-缩放”
        chroma_rgb = get_cascade_param("挂件-扣图RGB", clip, style_cfg, global_cfg, default="0,0,255")
        chroma_color = parse_rgb_to_ffmpeg(chroma_rgb)
        
        overlay_pos_str = get_cascade_param("挂件-位置", clip, style_cfg, global_cfg, default="右下角")
        scale_str = get_cascade_param("挂件-缩放", clip, style_cfg, global_cfg, default="35%").replace("%", "").strip()
        try: 
            scale_ratio = float(scale_str) / 100.0
        except ValueError: 
            scale_ratio = 0.35
            
        target_overlay_w = int(1920 * scale_ratio)
        pos_expr = calculate_position_expr(overlay_pos_str, default_y_bottom=False)

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

    # --- 修复后的字幕动态坐标排版 ---
    # 动态读取表格中的“字幕位置”列
    sub_pos_str = get_cascade_param("字幕位置", clip, style_cfg, global_cfg, default="底部")
    
    # 计算中文坐标。默认靠下
    cn_pos_expr = calculate_position_expr(sub_pos_str, default_y_bottom=True)
    
    # 英文字母强行排在中文下方（Y轴加上60像素的偏移量）
    en_pos_expr = cn_pos_expr.replace("y=", "y=60+") 
    en_font_size = max(20, int(int(cn_font_size) * 0.55))
    
    # 强制将中文与英文一起画到视频上
    filter_chain += (
        f"[bg]drawtext=fontfile='{font_path_clean}':textfile='{cn_sub_clean}':fontcolor={font_color}:fontsize={cn_font_size}:line_spacing=12:{cn_pos_expr}[v1];"
        f"[v1]drawtext=fontfile='{font_path_clean}':textfile='{en_sub_clean}':fontcolor={font_color}:fontsize={en_font_size}:line_spacing=8:{en_pos_expr}[outv];"
    )

    # 音频混合逻辑
    bgm_vol = get_cascade_param("BGM音量", clip, style_cfg, global_cfg, default="")
    if has_bgm and bgm_vol:
        filter_chain += f"[{audio_in_idx}:a]volume=1.0[a_tts];[{bgm_in_idx}:a]volume={bgm_vol}[a_bgm];[a_tts][a_bgm]amix=inputs=2:duration=first[outa]"
    elif has_bgm:
        filter_chain += f"[{audio_in_idx}:a]volume=1.0[a_tts];[{bgm_in_idx}:a]volume=1.0[a_bgm];[a_tts][a_bgm]amix=inputs=2:duration=first[outa]"
    else:
        filter_chain += f"[{audio_in_idx}:a]volume=1.0[outa]"

    # 执行渲染
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
    
    drive_font_url = get_cascade_param("字体", {}, {}, global_cfg)
    if not drive_font_url:
        raise ValueError("❌ 错误：全局配置中未找到【字体】链接！")
        
    font_path = f"{TEMP_DIR}/custom_font.ttf"
    if not os.path.exists(font_path):
        print("⬇️ 正在下载 Google Drive 自定义字体...", flush=True)
        download_file_from_gdrive(drive_font_url, font_path)

    video_segments = []
    
    for idx, clip in enumerate(clips, 1):
        v_seg = f"{TEMP_DIR}/clip_{idx}.mp4"
        a_seg = f"{TEMP_DIR}/clip_{idx}.m4a"
        
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
    import asyncio
    asyncio.run(main())
