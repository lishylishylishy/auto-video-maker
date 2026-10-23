import streamlit as st
import requests
import time
import zipfile
import io
import streamlit.components.v1 as components

st.set_page_config(page_title="Auto Video Studio", layout="wide", initial_sidebar_state="expanded")

GITHUB_REPO = "lishylishylishy/auto-video-maker"
WORKFLOW_FILE = "render.yml"
SHEET_URL = "https://docs.google.com/spreadsheets/d/1Y8YOEl5G2HV4C3WLZz9ttEEoByuWKGLqh_nmWvcb-_M/edit"

try:
    GITHUB_TOKEN = st.secrets["GITHUB_TOKEN"]
except Exception:
    st.error("GITHUB_TOKEN not found in Secrets.")
    st.stop()

with st.sidebar:
    st.markdown("## 🎬 Render Console")
    st.caption("Edit script on the right, specify target tab, and trigger render.")
    st.markdown("---")
    
    target_tab = st.text_input("Target Sheet Tab", value="script", help="e.g., script, draft_1")
    start_btn = st.button("🚀 Start Rendering", type="primary", use_container_width=True)
    
    # 状态与日志实时显示容器
    status_box = st.empty()
    log_box = st.empty()

    if start_btn:
        if not target_tab.strip():
            st.warning("Please enter a tab name.")
        else:
            dispatch_url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{WORKFLOW_FILE}/dispatches"
            headers = {
                "Accept": "application/vnd.github.v3+json",
                "Authorization": f"token {GITHUB_TOKEN}"
            }
            payload = {"ref": "main", "inputs": {"sheet_tab": target_tab.strip()}}
            
            status_box.info("⚡ Dispatching workflow trigger...")
            resp = requests.post(dispatch_url, headers=headers, json=payload)
                
            if resp.status_code != 204:
                status_box.error(f"Failed to trigger. Code: {resp.status_code}")
            else:
                time.sleep(3)
                runs_url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/runs"
                
                # 获取最新 Run ID
                runs_resp = requests.get(runs_url, headers=headers).json()
                if not runs_resp.get("workflow_runs"):
                    status_box.error("Cannot fetch task status.")
                    st.stop()
                    
                run_id = runs_resp["workflow_runs"][0]["id"]
                jobs_url = f"{runs_url}/{run_id}/jobs"

                # 实时轮询状态与步骤日志
                while True:
                    # 1. 查询整体状态
                    status_resp = requests.get(f"{runs_url}/{run_id}", headers=headers).json()
                    status = status_resp.get("status")
                    conclusion = status_resp.get("conclusion")

                    # 2. 查询具体 Steps 进展
                    jobs_resp = requests.get(jobs_url, headers=headers).json()
                    step_logs = []
                    if jobs_resp.get("jobs"):
                        current_job = jobs_resp["jobs"][0]
                        for step in current_job.get("steps", []):
                            icon = "⏳" if step["status"] == "in_progress" else ("✅" if step["conclusion"] == "success" else "⏸️")
                            step_logs.append(f"{icon} {step['name']}")

                    # 在网页上实时更新进度
                    status_box.info(f"🔄 Status: **{status.upper()}**")
                    with log_box.container():
                        st.markdown("**Live Action Steps:**")
                        st.code("\n".join(step_logs) if step_logs else "Initializing steps...")

                    if status == "completed":
                        if conclusion == "success":
                            status_box.success("🎉 Cloud execution completed!")
                            break
                        else:
                            status_box.error("❌ Cloud task failed. Check GitHub logs.")
                            st.stop()
                            
                    time.sleep(8) # 每 8 秒拉取一次最新步骤进度
                
                # 3. 任务成功，拉取产物
                status_box.info("📥 Fetching rendered output...")
                artifacts_url = f"{runs_url}/{run_id}/artifacts"
                arts_resp = requests.get(artifacts_url, headers=headers).json()
                
                if arts_resp.get("total_count", 0) > 0:
                    download_url = arts_resp["artifacts"][0]["archive_download_url"]
                    zip_resp = requests.get(download_url, headers=headers)
                    try:
                        with zipfile.ZipFile(io.BytesIO(zip_resp.content)) as z:
                            video_bytes = z.read("final_output.mp4")
                            status_box.success("🎉 Render Complete!")
                            st.video(video_bytes)
                            st.download_button("⬇️ Download Video", data=video_bytes, file_name=f"Render_{target_tab}.mp4", mime="video/mp4", use_container_width=True)
                    except Exception as e:
                        status_box.error(f"Failed to parse video: {e}")
                else:
                    status_box.error("Output file not found in artifacts.")

# 主区域全屏显示 Google Sheet 编辑器
components.iframe(SHEET_URL, height=850, scrolling=True)
