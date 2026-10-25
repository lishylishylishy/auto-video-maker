import os, re, csv, json, io, requests, subprocess, edge_tts, urllib.parse, asyncio, time
import os, re, csv, json, io, requests, subprocess, edge_tts, urllib.parse, asyncio, time, shutil
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
@@ -65,7 +65,6 @@ def get_audio_duration(file_path):
]
res = subprocess.run(cmd, capture_output=True, text=True, check=True)
data = json.loads(res.stdout)
        # 优先取容器时长，其次取音频流时长
format_dur = data.get("format", {}).get("duration")
if format_dur and float(format_dur) > 0:
return float(format_dur)
@@ -74,7 +73,6 @@ def get_audio_duration(file_path):
return float(s["duration"])
except Exception:
pass
    # 终极方案：解码统计时长
try:
cmd = ["ffmpeg", "-v", "error", "-i", file_path, "-f", "null", "-"]
res = subprocess.run(cmd, capture_output=True, text=True, check=True)
@@ -106,7 +104,7 @@ def is_video_media(filepath):
return False

# ==============================================================================
# ✂️ 2. 文本处理与翻译
# ✂️ 2. 文本处理与样式解析
# ==============================================================================
def wrap_text_by_punctuation(text, max_len=18):
if not text or len(text) <= max_len:
@@ -169,6 +167,43 @@ def sanitize_color(color_str):
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
@@ -181,9 +216,7 @@ def parse_rgb_to_ffmpeg(rgb_str):
return "0x0000FF"

def calc_ass_position(pos_str, is_subtitle=False):
    """ 把位置描述转换为ASS字幕对齐方式+边距 """
pos = str(pos_str).strip()
    # 默认：字幕底部居中，大字居中
alignment = 2 if is_subtitle else 5
margin_v = 80 if is_subtitle else 0
margin_h = 0
@@ -344,7 +377,6 @@ async def generate_audio_or_silent(clip, style_cfg, global_cfg, audio_output_pat
rate_param = "+0%"
raw_tts_path = audio_output_path + ".raw.mp3"
await edge_tts.Communicate(spoken_text, voice, rate=rate_param).save(raw_tts_path)
        # 标准化转码：移除开头静默帧，写入标准时长标签
std_cmd = [
"ffmpeg", "-y", "-i", raw_tts_path,
"-af", "silenceremove=start_periods=1:start_duration=0.05:start_threshold=-50dB",
@@ -364,13 +396,11 @@ async def generate_audio_or_silent(clip, style_cfg, global_cfg, audio_output_pat
]
subprocess.run(cmd, capture_output=True, check=True)

def generate_ass_subtitle(output_path, text_or_segments, font_size, color, alignment, margin_h, margin_v, duration, vid_w=1920, vid_h=1080, line_spacing=8):
    """ 生成标准ASS字幕文件，支持全程显示(标题)或按时间轴分段显示(长字幕) """
    ass_color = color
    if not color.startswith("&H"):
        ass_color = "&H00FFFFFF" if color == "white" else "&H00000000"
def generate_ass_subtitle(output_path, text_or_segments, font_size, color, alignment, margin_h, margin_v, duration, vid_w=1920, vid_h=1080, bold=0, outline=0, shadow=0, outline_color="black", line_spacing=8):
    """ 生成标准 ASS 字幕文件，所有样式参数 100% 动态传参注入 """
    ass_color = parse_ass_color(color, default_ass="&H00FFFFFF")
    ass_outline_color = parse_ass_color(outline_color, default_ass="&H00000000")

# 动态获取环境变量中的真实字体名，兜底使用 Arial
real_font_name = os.getenv("REAL_FONT_NAME", "Arial")

ass_content = f"""[Script Info]
@@ -381,7 +411,7 @@ def generate_ass_subtitle(output_path, text_or_segments, font_size, color, align

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{real_font_name},{font_size},{ass_color},&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,2,0,{alignment},{margin_h},{margin_h},{margin_v},1
Style: Default,{real_font_name},{font_size},{ass_color},&H000000FF,{ass_outline_color},&H00000000,{bold},0,0,0,100,100,0,0,1,{outline},{shadow},{alignment},{margin_h},{margin_h},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
@@ -393,11 +423,9 @@ def format_ass_time(seconds):
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
@@ -412,15 +440,15 @@ def format_ass_time(seconds):
f.write(ass_content)
return output_path

def escape_ff_path(p):#增加识别字体代码
    return p.replace('\\', '/').replace(':', '\\:')#增加识别字体代码
def escape_ff_path(p):
    return p.replace('\\', '/').replace(':', '\\:')

def render_clip(index, clip, style_cfg, global_cfg, vid_w, vid_h, audio_file, output_path):
print(f"[Render] 正在渲染第 {index} 个切片片段 [{clip['chapter']}]...", flush=True)

spoken_text = clip["spoken_text"].strip()
silent_text = clip["silent_text"].strip()

    # 时长严格控制：有声取真实音频时长+0.4s余量，无声取script秒数
if spoken_text:
audio_dur = get_audio_duration(audio_file)
clip_duration = audio_dur + 0.4
@@ -429,14 +457,12 @@ def render_clip(index, clip, style_cfg, global_cfg, vid_w, vid_h, audio_file, ou

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
@@ -447,7 +473,6 @@ def render_clip(index, clip, style_cfg, global_cfg, vid_w, vid_h, audio_file, ou
bg_color = parse_rgb_to_ffmpeg(bg_color_raw)
cmd.extend(["-f", "lavfi", "-i", f"color=c={bg_color}:s={vid_w}x{vid_h}"])

    # Layer1 挂件
has_layer1 = bool(path_l1 and os.path.exists(path_l1))
if has_layer1:
if is_video_media(path_l1):
@@ -457,7 +482,6 @@ def render_clip(index, clip, style_cfg, global_cfg, vid_w, vid_h, audio_file, ou

cmd.extend(["-i", audio_file])

    # BGM 分级循环
bgm_level = "none"
raw_bgm = get_param(["BGM垫音", "BGM路径"], clip, style_cfg, default="")
if raw_bgm:
@@ -475,12 +499,9 @@ def render_clip(index, clip, style_cfg, global_cfg, vid_w, vid_h, audio_file, ou
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
@@ -517,24 +538,40 @@ def render_clip(index, clip, style_cfg, global_cfg, vid_w, vid_h, audio_file, ou
audio_in_idx = 1
bgm_in_idx = 2

    # 无声大字（标题）
    # --- 无声大字（彻底由配置驱动，缺失则优雅归 0） ---
if silent_text:
title_text = wrap_text_by_punctuation(silent_text, max_len=14)
        title_size = get_param(["文字-字号"], clip, style_cfg, global_cfg, default="150")
        title_color = sanitize_color(get_param(["文字-颜色"], clip, style_cfg, global_cfg, default="yellow"))
        title_pos_str = get_param(["字幕位置"], clip, style_cfg, global_cfg, default="居中")
        title_size = get_param(["文字-字号", "字号"], clip, style_cfg, global_cfg, default="150")
        title_color = get_param(["文字-颜色", "颜色"], clip, style_cfg, global_cfg, default="yellow")
        title_bold_raw = get_param(["文字-加粗", "加粗"], clip, style_cfg, global_cfg, default="0")
        title_outline_raw = get_param(["文字-描边", "描边"], clip, style_cfg, global_cfg, default="0")
        title_outline_color_raw = get_param(["文字-描边颜色", "描边颜色"], clip, style_cfg, global_cfg, default="black")
        title_shadow_raw = get_param(["文字-阴影", "阴影"], clip, style_cfg, global_cfg, default="0")
        title_pos_str = get_param(["字幕位置", "文字位置"], clip, style_cfg, global_cfg, default="居中")
        
        bold_val = parse_bool_to_int(title_bold_raw, default=0)
        try: outline_val = int(float(title_outline_raw))
        except ValueError: outline_val = 0
        try: shadow_val = int(float(title_shadow_raw))
        except ValueError: shadow_val = 0
        
align, mh, mv = calc_ass_position(title_pos_str, is_subtitle=False)

title_ass_path = os.path.abspath(f"{TEMP_DIR}/title_{index}.ass")
        generate_ass_subtitle(title_ass_path, title_text, int(title_size), title_color, align, mh, mv, clip_duration, vid_w, vid_h)
        generate_ass_subtitle(
            title_ass_path, title_text, int(float(title_size)), title_color, 
            align, mh, mv, clip_duration, vid_w, vid_h, 
            bold=bold_val, outline=outline_val, shadow=shadow_val, outline_color=title_outline_color_raw
        )
        
ff_ass_title = escape_ff_path(title_ass_path)
        filter_chains.append(f"{curr_v}ass={ff_ass_title}[v_with_title]")
        ff_fonts_dir = escape_ff_path(os.path.abspath(TEMP_DIR))
        # 挂载 :fontsdir={ff_fonts_dir} 保证 libass 引擎直接找到临时字体库
        filter_chains.append(f"{curr_v}ass={ff_ass_title}:fontsdir={ff_fonts_dir}[v_with_title]")
curr_v = "[v_with_title]"

    # 有声字幕（严格仅用全局配置）
# 有声字幕（严格仅用全局配置）
    # --- 有声字幕（彻底由配置驱动，缺失则优雅归 0） ---
if spoken_text:
        # 1. 先将长句切分为短句（中文每行最大20字，避免出画）
cn_lines = wrap_text_by_punctuation(spoken_text, max_len=21).split('\n')
is_bilingual = (get_param(["双语字幕"], global_cfg, default="开启").lower() in ["开启", "是", "true", "1", "on"])

@@ -543,12 +580,8 @@ def render_clip(index, clip, style_cfg, global_cfg, vid_w, vid_h, audio_file, ou
if not cn_line.strip():
continue
if is_bilingual:
                # 2. 对切分好的中文短句单独进行英文翻译
en_line = translate_to_english(cn_line)
                # 英文强制单行
                en_sub = en_line
                # 中文在上，英文在下
                sub_text = f"{cn_line}\\N{en_sub}"
                sub_text = f"{cn_line}\\N{en_line}"
else:
sub_text = cn_line

@@ -557,21 +590,36 @@ def render_clip(index, clip, style_cfg, global_cfg, vid_w, vid_h, audio_file, ou
"char_count": len(cn_line)
})

        sub_size = get_param(["文字-字号"], global_cfg, default="30")
        sub_color = sanitize_color(get_param(["文字-颜色"], global_cfg, default="white"))
        sub_pos_str = get_param(["字幕位置"], global_cfg, default="底部居中")
        sub_size = get_param(["文字-字号", "字号"], global_cfg, default="30")
        sub_color = get_param(["文字-颜色", "颜色"], global_cfg, default="white")
        sub_bold_raw = get_param(["文字-加粗", "加粗"], global_cfg, default="0")
        sub_outline_raw = get_param(["文字-描边", "描边"], global_cfg, default="0")
        sub_outline_color_raw = get_param(["文字-描边颜色", "描边颜色"], global_cfg, default="black")
        sub_shadow_raw = get_param(["文字-阴影", "阴影"], global_cfg, default="0")
        sub_pos_str = get_param(["字幕位置", "位置"], global_cfg, default="底部居中")
        
        bold_val = parse_bool_to_int(sub_bold_raw, default=0)
        try: outline_val = int(float(sub_outline_raw))
        except ValueError: outline_val = 0
        try: shadow_val = int(float(sub_shadow_raw))
        except ValueError: shadow_val = 0
        
align, mh, mv = calc_ass_position(sub_pos_str, is_subtitle=True)

sub_ass_path = os.path.abspath(f"{TEMP_DIR}/sub_{index}.ass")
        # 传入 sub_segments 数组，由 generate_ass_subtitle 自动计算分段排期
        generate_ass_subtitle(sub_ass_path, sub_segments, int(sub_size), sub_color, align, mh, mv, clip_duration, vid_w, vid_h)
        generate_ass_subtitle(
            sub_ass_path, sub_segments, int(float(sub_size)), sub_color, 
            align, mh, mv, clip_duration, vid_w, vid_h,
            bold=bold_val, outline=outline_val, shadow=shadow_val, outline_color=sub_outline_color_raw
        )

ff_ass_sub = escape_ff_path(sub_ass_path)
        filter_chains.append(f"{curr_v}ass={ff_ass_sub}[outv]")
        ff_fonts_dir = escape_ff_path(os.path.abspath(TEMP_DIR))
        # 挂载 :fontsdir={ff_fonts_dir} 保证 libass 引擎直接找到临时字体库
        filter_chains.append(f"{curr_v}ass={ff_ass_sub}:fontsdir={ff_fonts_dir}[outv]")
else:
filter_chains.append(f"{curr_v}null[outv]")

    # 音频混合
bgm_vol = get_param(["BGM音量"], clip, style_cfg, global_cfg, default="1.0")
try: vol_val = float(bgm_vol)
except ValueError: vol_val = 1.0
@@ -604,6 +652,11 @@ def render_clip(index, clip, style_cfg, global_cfg, vid_w, vid_h, audio_file, ou
# ==============================================================================
async def main():
os.makedirs(TEMP_DIR, exist_ok=True)
    
    # 修正 BtbN 静态 FFmpeg 无法读取系统 Fontconfig 配置的问题
    os.environ["FONTCONFIG_PATH"] = "/etc/fonts"
    os.environ["FONTCONFIG_FILE"] = "/etc/fonts/fonts.conf"
    
print("开始读取 Google 表格配置...", flush=True)

rules_csv = get_google_sheet_tab_csv(SHEET_URL, RULES_TAB_NAME)
@@ -615,64 +668,54 @@ async def main():
if not clips:
raise ValueError(f"未在 Tab 页【{SCRIPT_TAB_NAME}】中解析出任何有效剧本内容。")

# ================= 核心重构：多链接字体下载与系统底层注册 =================
    # 1. 直接从 Google Sheets 拿到字体网盘链接（支持多个，用逗号/分号/空格隔开）
    # ================= 动态字体下载与安装 =================
drive_font_url_raw = get_param(["字体"], global_cfg, default="")
if not drive_font_url_raw:
raise ValueError("请在 Google Sheets 的全局配置中填写字体的 Google Drive 链接")

    # 使用正则拆分多个链接
font_urls = [url.strip() for url in re.split(r'[,;|\s]+', drive_font_url_raw) if url.strip()]

    # 准备 Linux 系统级字体库目录
    import shutil
linux_font_dir = os.path.expanduser("~/.local/share/fonts")
os.makedirs(linux_font_dir, exist_ok=True)

    primary_font_name = "Arial" # 兜底名
    primary_font_name = ""

    # 2. 循环遍历所有字体链接，依次下载并存入系统
for idx, url in enumerate(font_urls):
try:
print(f"正在从网盘下载第 {idx+1} 个字体...", flush=True)
temp_dl_path = os.path.abspath(f"{TEMP_DIR}/downloaded_font_{idx}.ttf")

            # 使用你写好的安全下载函数
download_file_from_gdrive(url, temp_dl_path)

if not os.path.exists(temp_dl_path) or os.path.getsize(temp_dl_path) < 1000:
print(f"警告：第 {idx+1} 个字体下载失败或文件无效，已跳过。", flush=True)
continue
                
            # 直接复制进入 Linux 操作系统底层字体库
            
            # 存入系统字体目录
system_font_path = os.path.join(linux_font_dir, f"custom_font_{idx}.ttf")
shutil.copy(temp_dl_path, system_font_path)

            # 如果是第一个成功安装的字体，提取它的真实名称作为 ASS 模板的全局主调字体
            if primary_font_name == "Arial":
                fc_res = subprocess.run(["fc-query", "-f", "%{family}\n", system_font_path], capture_output=True, text=True)
                real_font = fc_res.stdout.strip().split(',')[0].strip()
                if real_font:
                    primary_font_name = real_font
            # 精准解析字体真名
            fc_res = subprocess.run(["fc-query", "-f", "%{family}\n", system_font_path], capture_output=True, text=True)
            families = [f.strip() for f in fc_res.stdout.strip().split(',') if f.strip()]
            
            if families and not primary_font_name:
                primary_font_name = families[0]
                print(f"第 {idx+1} 个字体注册成功！识别到内部真名为: [{primary_font_name}]", flush=True)
except Exception as e:
            print(f"处理字体链接失败，跳过该字体: {e}", flush=True)
            print(f"处理字体链接失败: {e}", flush=True)

    # 3. 字体全部下完后，强制刷新系统级字体缓存，让 FFmpeg 无条件扫描到这些新字体
    print("正在向操作系统底层注册所有下载的中文字体...", flush=True)
subprocess.run(["fc-cache", "-fv"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    os.environ["REAL_FONT_NAME"] = primary_font_name
    print(f"所有动态字体已注册完毕！首选主字体真名为: {os.environ['REAL_FONT_NAME']}", flush=True)
    # ====================================================================
    os.environ["REAL_FONT_NAME"] = primary_font_name if primary_font_name else "Arial"
    print(f"【最终样式】ASS 字幕注入的主字体真名为: [{os.environ['REAL_FONT_NAME']}]", flush=True)

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
@@ -682,10 +725,8 @@ async def main():
render_clip(idx, clip, style_cfg, global_cfg, vid_w, vid_h, audio_file=a_seg, output_path=v_seg)
video_segments.append(v_seg)


print("所有片段渲染完成，开始合并视频...", flush=True)

    # 改用concat滤镜拼接，彻底解决时间戳错位导致的音画章节偏移
concat_inputs = []
filter_parts = []
for i, seg in enumerate(video_segments):
