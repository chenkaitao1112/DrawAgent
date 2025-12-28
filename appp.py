import streamlit as st

# 1. 页面配置
st.set_page_config(page_title="科研绘图助手 Demo", layout="wide")

# --- 初始化 Session State (存储全局状态) ---
# 用来记录当前进行到哪一步
if 'stage' not in st.session_state:
    st.session_state.stage = 'initial'  # 初始阶段
if 'user_input' not in st.session_state:
    st.session_state.user_input = ""
if 'logic_feedback' not in st.session_state:
    st.session_state.logic_feedback = ""

# --- 侧边栏：进度指示器 [cite: 264, 283, 284, 285] ---
with st.sidebar:
    st.title("项目进度")
    # 根据 stage 状态，显示不同的进度条和胶囊
    if st.session_state.stage == 'initial':
        st.info("📍 当前环节: 初始输入")
    else:
        # 阶梯状高亮显示，给予成就感 [cite: 299]
        st.success("✅ 已提交描述")
        if st.session_state.stage == 'logic_check':
            st.info("📍 当前环节: 逻辑梳理")
        elif st.session_state.stage in ['visual_fix', 'final']:
            st.success("✅ 已确认逻辑")
            st.info("📍 当前环节: 视觉元素补充")

# --- 主页面布局 ---
st.title("科研绘图助手 (DrawAgent)")

# 左右分栏：四六开 [cite: 144]
col_input, col_output = st.columns([4, 6])

# --- 左侧：对话与逻辑控制区 ---
with col_input:
    st.subheader("对话与逻辑控制区")

    # 阶段 1：初始输入 [cite: 132, 134, 137]
    if st.session_state.stage == 'initial':
        user_text = st.text_area(
            "在此输入内容",
            placeholder="粘贴论文摘要、实验步骤，或输入“开启精细化流程”...",  # [cite: 86, 134, 173]
            height=300
        )
        uploaded_file = st.file_uploader("点此上传附件", type=["jpg", "png"])  # [cite: 85, 138]

        if st.button("提交", use_container_width=True):  # [cite: 139, 149]
            st.session_state.user_input = user_text
            # 路由逻辑：判断进入哪个流程 [cite: 87, 88, 89]
            if "开启精细化流程" in user_text or uploaded_file:
                st.session_state.stage = 'logic_check'
            else:
                st.session_state.stage = 'logic_check'  # 简化演示，统一先去逻辑梳理
            st.rerun()

    # 阶段 2：逻辑梳理与回环修改 [cite: 157, 194, 216]
    elif st.session_state.stage == 'logic_check':
        st.write("已梳理逻辑，请检查右侧输出是否有疏漏。")  # [cite: 200, 373]
        feedback = st.text_input("如果有误请在此输入修改意见", key="feedback_input")  # [cite: 214, 385]

        c1, c2 = st.columns(2)
        with c1:
            if st.button("下一步", use_container_width=True):  # [cite: 215, 220]
                st.session_state.stage = 'visual_fix'
                st.rerun()
        with c2:
            if st.button("提交修改", use_container_width=True):  # [cite: 215, 221]
                st.session_state.logic_feedback = feedback
                st.toast("已记录修改意见，重新梳理中...")

    # 阶段 3：视觉元素补充 [cite: 280, 291]
    elif st.session_state.stage == 'visual_fix':
        st.write("请填写任何需要补充的内容（配色、风格、高亮组件）。")  # [cite: 286, 300]
        visual_desc = st.text_area("样式指令输入", placeholder="例如：使用Science杂志红蓝配色")  # [cite: 301]

        if st.button("生成最终图表", use_container_width=True):  # [cite: 302]
            st.session_state.stage = 'final'
            st.rerun()

# --- 右侧：输出与演示区 ---
with col_output:
    st.subheader("输出与演示区")

    if st.session_state.stage == 'initial':
        st.info("等待输入内容...")  # [cite: 150]

    elif st.session_state.stage == 'logic_check':
        # 模拟 AI 生成的逻辑流内容 [cite: 203, 227, 229]
        st.markdown("### 1. 逻辑流验证 (Logical Flow)")
        st.code("[Step 1] 输入: 原始文本 -> 技术: 特征提取 -> 输出: 向量矩阵")
        st.markdown("### 2. 关键实体提取 (Key Entities)")
        st.write("- 模块 A: Encoder\n- 模块 B: Decoder")  # [cite: 230]
        st.markdown("### 3. 潜在逻辑缺口 (Gap Analysis)")
        st.error("检测到 Step 2 的输出在后续未被引用，请确认。")  # [cite: 231]

    elif st.session_state.stage == 'visual_fix':
        st.warning("框架已搭建完成，等待应用视觉样式...")  # [cite: 291, 304]

    elif st.session_state.stage == 'final':
        st.success("🎉 图表生成成功！")
        st.image("https://via.placeholder.com/600x400?text=Scientific+Diagram+Preview")  # 占位图 [cite: 320, 447]
        st.button("下载原图")  # [cite: 325, 448]
        st.divider()
        st.subheader("可编辑 XML 代码")  # [cite: 351, 423, 475]
        st.code("<mxGraphModel><root>...</root></mxGraphModel>", language="xml")