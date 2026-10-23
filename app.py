import streamlit as st
import requests
import time
import zipfile
import io
import streamlit.components.v1 as components

# 页面基础配置：宽屏，极简标题
st.set_page_config(page_title="Video Engine", layout="wide")

# =========================
# ⚙️ 全局配置
# =========================
GITHUB_REPO = "lishylishylishy/auto-video-maker"
WORKFLOW_FILE = "render.yml"
# 只保留基础链接，不带 gid，方便自由切换 tab
SHEET_URL = "https://docs.google.com/spreadsheets/d/1Y8YOEl5G2HV4C3WLZz9ttEEoByuWKGLqh_nmWvcb-_M/edit"

try:
    GITHUB_TOKEN = st.secrets["GITHUB_TOKEN"]
except Exception:
    st.error("未找到 GITHUB_TOKEN，请在 Secrets 中配置。")
    st.stop()

# =========================
# 🎨 左右分栏布局 (1:2.5 比例)
# =========================
col_control, col_sheet = st.columns([1, 2.5])

with col_control:
    st.markdown("### 视频渲染控制台")
    st.markdown("在右侧直接编辑剧本草稿。确认无误后，在此处指定需要渲染的标签页名称。")
    st.markdown("---")
    
    # 核心功能：输入具体的 Tab 名称
    target_tab = st.text_input("目标渲染标签页 (Sheet Tab)", value="script", help="输入你要渲染的底部标签页名称，如 script 或 draft_1")
    
    start_btn = st.button("执行渲染", type="primary", use_container_width=True)
    
    if start_btn:
        if not target_tab.strip():
            st.warning("请填写标签页名称")
        else:
            dispatch_url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{WORKFLOW_FILE}/dispatches"
            headers = {
                "Accept": "application/vnd.github.v3+json",
                "Authorization": f"token {GITHUB_TOKEN}"
            }
            # 将用户填写的 Tab 名称作为参数发送给 GitHub
            payload = {
                "ref": "main",
                "inputs": {"sheet_tab": target_tab.strip()}
            }
            
            with st.spinner("唤醒云端服务器..."):
                resp = requests.post(dispatch_url, headers=headers, json=payload)
                
            if resp.status_code != 204:
                st.error(f"触发失败，错误码: {resp.status_code}")
            else:
                time.sleep(5)
                runs_url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/runs"
                
                with st.spinner("云端渲染中，请保持网页打开..."):
                    runs_resp = requests.get(runs_url, headers=headers).json()
                    if not runs_resp.get("workflow_runs"):
                        st.error("未能获取任务状态")
                        st.stop()
                        
                    run_id = runs_resp["workflow_runs"][0]["id"]
                    
                    while True:
                        status_resp = requests.get(f"{runs_url}/{run_id}", headers=headers).json()
                        status = status_resp.get("status")
                        conclusion = status_resp.get("conclusion")
                        
                        if status == "completed":
                            if conclusion == "success":
                                break
                            else:
                                st.error("渲染报错，请检查后台日志。")
                                st.stop()
                        time.sleep(15)
                
                with st.spinner("渲染完成，拉取视频中..."):
                    artifacts_url = f"{runs_url}/{run_id}/artifacts"
                    arts_resp = requests.get(artifacts_url, headers=headers).json()
                    
                    if arts_resp.get("total_count", 0) > 0:
                        download_url = arts_resp["artifacts"][0]["archive_download_url"]
                        zip_resp = requests.get(download_url, headers=headers)
                        
                        try:
                            with zipfile.ZipFile(io.BytesIO(zip_resp.content)) as z:
                                video_bytes = z.read("final_output.mp4")
                                st.success("渲染成功")
                                st.video(video_bytes)
                                st.download_button("下载成片", data=video_bytes, file_name=f"成品_{target_tab}.mp4", mime="video/mp4", use_container_width=True)
                        except Exception as e:
                            st.error(f"解析失败: {e}")
                    else:
                        st.error("未找到输出文件。")

# 右侧全屏显示表格
with col_sheet:
    components.iframe(SHEET_URL, height=850, scrolling=True)
