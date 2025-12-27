import streamlit as st


def render_initial_input():
    """1.2 初始输入页面 [cite: 132]"""
    st.title("科研绘图助手 (DrawAgent)")
    col_in, col_out = st.columns([4, 6])

    with col_in:
        st.subheader("对话与逻辑控制区")
        user_text = st.text_area("在此输入内容", placeholder="粘贴论文摘要、实验步骤，或上传手绘草图...", height=300)
        file = st.file_uploader("点此上传附件", type=["jpg", "png"])

        if st.button("提交", use_container_width=True):

            if file:
                st.session_state.step = "FRAMEWORK"  # 有附件直接进框架还原 [cite: 88, 177]
            elif "开启精细化流程" in user_text:
                st.session_state.step = "LOGIC"
                st.session_state.user_mode = "P1"  # 详细描述党 [cite: 9, 173]
            else:
                st.session_state.step = "LOGIC"
                st.session_state.user_mode = "P2"  # 论文内容党 [cite: 25]


    with col_out:
        st.subheader("输出与演示区")
        st.info("无输出")


def render_logic_refinement():
    """2.1.2 逻辑梳理与回环修改页面 [cite: 194]"""
    st.subheader(f"模式选择: {st.session_state.user_mode} 模式")
    col_in, col_out = st.columns([4, 6])

    with col_in:
        st.write("已梳理逻辑，请检查右侧输出是否有疏漏。")
        st.text_area("在此输入修改意见", placeholder="例如：Step 2 的输入不对...")
        c1, c2 = st.columns(2)
        if c1.button("下一步"): st.session_state.step = "FRAMEWORK"
        if c2.button("提交修改"): st.toast("逻辑已更新")

    with col_out:
        st.markdown("### 1. 逻辑流验证 (Logical Flow)")
        st.code("[Step 1] 输入: 数据 -> 技术: CNN -> 输出: 特征图")
        st.markdown("### 2. 关键实体提取 (Key Entities)")
        st.write("- CNN模块, 连线, 池化层")
        st.markdown("### 3. 潜在逻辑缺口 (Gap Analysis)")
        st.error("变量 x 在 Step 3 中未定义")


def render_sketch_framework():
    """2.1.3 框架搭建界面 [cite: 237]"""
    st.subheader("框架搭建：布局确认")
    col_in, col_out = st.columns([4, 6])

    with col_in:
        st.write("可上传框架草图固定布局，若无草图点击下一步。")
        st.file_uploader("点此上传图片 (草图)", type=["png", "jpg"], key="sketch")
        if st.button("下一步"): st.session_state.step = "VISUAL"

    with col_out:
        st.write("（此处显示AI推演的布局拓扑结构...）")


def render_visual_supplement():
    """2.1.4 视觉元素补充界面 [cite: 280]"""
    st.subheader("视觉元素补充：上色与精修")
    col_in, col_out = st.columns([4, 6])

    with col_in:
        st.write("请填写颜色、风格需求（如：Science风格）。")
        st.text_input("风格指令", placeholder="配色方案、高亮组件...")
        if st.button("生成最终图片"): st.session_state.step = "FINAL"

    with col_out:
        st.image("https://via.placeholder.com/600x400?text=Preview+Image", caption="风格预览")


def render_output_page():
    """2.1.6 最终输出界面 [cite: 339]"""
    st.subheader("最终生成结果")
    col_in, col_out = st.columns([4, 6])

    with col_in:
        st.success("已生成 XML 代码！")
        st.write("请复制下方代码导入 Draw.io 转换为可编辑图表。")
        if st.button("返回首页"):
            st.session_state.step = "INITIAL"
            st.rerun()

    with col_out:
        st.text_area("Draw.io XML 代码", value="<mxGraphModel>...</mxGraphModel>", height=400)
        st.button("一键复制代码")